import copy
import json
import os
from datetime import timedelta

import pymupdf
import requests
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.monitor import result_store
from app.monitor.epaper_monitor import PUBLICATIONS, colombo_today, image_cache_path, manifest_path
from tests.test_monitor_page import SAVED_MANIFEST

client = TestClient(app)
DAILY = PUBLICATIONS["daily-news"]
EDITION = colombo_today() - timedelta(days=3)
IMAGE_URL = "https://d1dtajqni3g43z.cloudfront.net/DNSO/2026/10/15/a.jpg"
BROKEN_URL = "https://d1dtajqni3g43z.cloudfront.net/DNSO/2026/10/15/b.jpg"


class MemoryBackend:
    def __init__(self):
        self.files = {}

    def get(self, pathname):
        return self.files.get(pathname)

    def put(self, pathname, body, content_type):
        self.files[pathname] = bytes(body)

    def list(self, prefix):
        return [name for name in self.files if name.startswith(prefix)]

    def delete(self, pathnames):
        for name in pathnames:
            self.files.pop(name, None)


def wide_jpeg(width=4200):
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, 20), False)
    pixmap.clear_with(200)
    return pixmap.tobytes("jpg")


def save_manifest(root, edition=EDITION, image_urls=(IMAGE_URL,), tenders=0):
    manifest = copy.deepcopy(SAVED_MANIFEST)
    manifest["edition_date"] = edition.isoformat()
    manifest["category_counts"] = {"facility": 1, "medical_tender": tenders}
    template = manifest["articles"][0]
    manifest["articles"] = []
    for index, url in enumerate(image_urls):
        item = dict(template, uid=f"advert-{index}", image_url=url)
        manifest["articles"].append(item)
    manifest["article_count"] = len(manifest["articles"])
    path = manifest_path(root, DAILY, edition)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_publish_uploads_result_resized_images_and_index(tmp_path):
    save_manifest(tmp_path, tenders=2)
    backend = MemoryBackend()

    summary = result_store.publish_edition(
        tmp_path, DAILY, EDITION, backend=backend, fetch_image=lambda url: wide_jpeg()
    )

    assert summary["images_uploaded"] == 1 and summary["image_failures"] == []
    stored = backend.files[result_store.image_blob(DAILY, EDITION, IMAGE_URL)]
    assert pymupdf.Pixmap(stored).width <= result_store.MAX_IMAGE_WIDTH
    index = json.loads(backend.files[result_store.INDEX])
    assert index["editions"]["daily-news"][EDITION.isoformat()]["tenders"] == 2
    again = result_store.publish_edition(
        tmp_path, DAILY, EDITION, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    assert again["images_uploaded"] == 0


class StaleIndexBackend(MemoryBackend):
    """Blob's CDN can serve an old index right after it is overwritten."""

    def __init__(self):
        super().__init__()
        self.served_index = None

    def get(self, pathname):
        if pathname == result_store.INDEX:
            return self.served_index
        return super().get(pathname)


def test_stale_index_read_does_not_drop_uploaded_editions(tmp_path):
    # Reproduces 2026-10-27: two editions vanished from the site's calendar.
    backend = StaleIndexBackend()
    first, second = EDITION, EDITION - timedelta(days=1)
    for edition in (first, second):
        save_manifest(tmp_path, edition=edition, tenders=1)
        result_store.publish_edition(
            tmp_path, DAILY, edition, backend=backend, fetch_image=lambda url: wide_jpeg()
        )
    editions = json.loads(backend.files[result_store.INDEX])["editions"]["daily-news"]
    assert set(editions) == {first.isoformat(), second.isoformat()}


def test_index_recovers_uploaded_result_missing_locally(tmp_path):
    backend = StaleIndexBackend()
    other_pc, this_pc = tmp_path / "other", tmp_path / "this"
    older = EDITION - timedelta(days=2)
    save_manifest(other_pc, edition=older, tenders=3)
    result_store.publish_edition(
        other_pc, DAILY, older, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    save_manifest(this_pc)
    result_store.publish_edition(
        this_pc, DAILY, EDITION, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    editions = json.loads(backend.files[result_store.INDEX])["editions"]["daily-news"]
    assert editions[older.isoformat()]["tenders"] == 3


def test_unreadable_uploaded_result_is_reported_not_hidden(tmp_path):
    backend = MemoryBackend()
    broken = EDITION - timedelta(days=2)
    backend.files[result_store.manifest_blob(DAILY, broken)] = b"{not json"
    save_manifest(tmp_path)
    summary = result_store.publish_edition(
        tmp_path, DAILY, EDITION, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    assert any(broken.isoformat() in warning for warning in summary["warnings"])


def test_image_failure_is_reported_and_result_still_shared(tmp_path):
    save_manifest(tmp_path, image_urls=(IMAGE_URL, BROKEN_URL))
    backend = MemoryBackend()

    def fetch(url):
        if url == BROKEN_URL:
            raise OSError("publisher reset the connection")
        return wide_jpeg()

    summary = result_store.publish_edition(
        tmp_path, DAILY, EDITION, backend=backend, fetch_image=fetch
    )

    # Nothing is silently lost: the failure is listed and the item stays in the result.
    assert summary["image_failures"] == [BROKEN_URL]
    shared = json.loads(backend.files[result_store.manifest_blob(DAILY, EDITION)])
    assert [item["image_url"] for item in shared["articles"]] == [IMAGE_URL, BROKEN_URL]
    index = json.loads(backend.files[result_store.INDEX])
    assert index["editions"]["daily-news"][EDITION.isoformat()]["missing_images"] == 1


def test_old_editions_are_pruned(tmp_path):
    backend = MemoryBackend()
    old = colombo_today() - timedelta(days=result_store.KEEP_DAYS + 5)
    recent = colombo_today() - timedelta(days=1)
    # An edition uploaded while it was still recent, now past the keep window.
    backend.put(result_store.manifest_blob(DAILY, old), b"{}", "application/json")
    backend.put(result_store.image_blob(DAILY, old, IMAGE_URL), b"jpg", "image/jpeg")
    backend.put(
        result_store.INDEX,
        json.dumps({"editions": {"daily-news": {old.isoformat(): {"count": 1}}}}).encode(),
        "application/json",
    )
    save_manifest(tmp_path, edition=recent)
    summary = result_store.publish_edition(
        tmp_path, DAILY, recent, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    assert summary["removed_editions"] == [f"daily-news {old.isoformat()}"]
    assert result_store.manifest_blob(DAILY, old) not in backend.files
    assert result_store.image_blob(DAILY, old, IMAGE_URL) not in backend.files
    assert result_store.manifest_blob(DAILY, recent) in backend.files


def test_editions_past_keep_window_are_not_uploaded(tmp_path):
    old = colombo_today() - timedelta(days=result_store.KEEP_DAYS + 1)
    save_manifest(tmp_path, edition=old)
    backend = MemoryBackend()
    try:
        result_store.publish_edition(tmp_path, DAILY, old, backend=backend)
    except result_store.StoreError as exc:
        assert "Older than" in str(exc)
    else:
        raise AssertionError("old edition should be refused")
    assert backend.files == {}


def test_remote_results_copy_manifest_and_image(tmp_path):
    source, site = tmp_path / "pc", tmp_path / "site"
    save_manifest(source)
    backend = MemoryBackend()
    result_store.publish_edition(
        source, DAILY, EDITION, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    remote = result_store.RemoteResults(site, backend=backend)

    remote.ensure_manifest(DAILY, EDITION)
    assert manifest_path(site, DAILY, EDITION).is_file()
    image = remote.ensure_image(DAILY, EDITION, IMAGE_URL)
    assert image == image_cache_path(site / "_cache", IMAGE_URL) and image.is_file()
    assert remote.ensure_image(DAILY, EDITION, BROKEN_URL) is None


def remote_site(monkeypatch, tmp_path):
    backend = MemoryBackend()
    save_manifest(tmp_path / "pc", tenders=1)
    result_store.publish_edition(
        tmp_path / "pc", DAILY, EDITION, backend=backend, fetch_image=lambda url: wide_jpeg()
    )
    site = tmp_path / "site"
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", site)
    monkeypatch.setattr(
        main_module, "REMOTE", result_store.RemoteResults(site, backend=backend)
    )

    def publisher_must_not_be_called(*args, **kwargs):
        raise AssertionError("the online site must not contact the publisher")

    monkeypatch.setattr(main_module.availability, "month", publisher_must_not_be_called)
    monkeypatch.setattr(main_module, "_download_publisher_image", publisher_must_not_be_called)
    return backend


def test_online_site_calendar_lists_uploaded_editions(monkeypatch, tmp_path):
    remote_site(monkeypatch, tmp_path)
    data = client.get(
        "/api/availability", params={"publication": "daily-news", "month": EDITION.strftime("%Y-%m")}
    ).json()
    assert data["mode"] == "saved"
    assert data["dates"] == [EDITION.isoformat()]
    assert data["checked"][EDITION.isoformat()] == {"complete": True, "count": 1, "tenders": 1}


def test_online_site_shows_uploaded_result_and_image(monkeypatch, tmp_path):
    remote_site(monkeypatch, tmp_path)
    page = client.get("/monitor", params={"publication": "daily-news", "edition_date": EDITION.isoformat()})
    assert page.status_code == 200
    assert 'data-testid="remote-note"' in page.text
    assert "Hospital expands emergency care" in page.text
    assert 'data-testid="check-now"' in page.text
    assert 'data-testid="check-latest"' not in page.text
    image = client.get(f"/api/image/daily-news/{EDITION.isoformat()}/advert-0")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"


def test_online_site_serves_uploaded_result_as_json(monkeypatch, tmp_path):
    remote_site(monkeypatch, tmp_path)
    response = client.get(f"/api/editions/daily-news/{EDITION.isoformat()}")
    assert response.status_code == 200
    assert response.json()["category_counts"]["medical_tender"] == 1


def blocked_publisher(*args, **kwargs):
    response = requests.Response()
    response.status_code = 403
    raise requests.HTTPError("403 Forbidden", response=response)


def online_check_site(monkeypatch, tmp_path, check):
    backend = remote_site(monkeypatch, tmp_path)
    monkeypatch.setattr(main_module, "_publisher_blocked_until", 0.0)
    calls = []

    def recording(*args, **kwargs):
        calls.append(args[2])
        return check(*args, **kwargs)

    monkeypatch.setattr(main_module, "run_edition_check", recording)
    return backend, calls


UNCHECKED = EDITION - timedelta(days=1)


def test_online_site_needs_a_date_to_check(monkeypatch, tmp_path):
    online_check_site(monkeypatch, tmp_path, blocked_publisher)
    response = client.post("/api/check", data={"publication": "daily-news"})
    assert response.status_code == 400
    assert "Pick a date" in response.json()["detail"]


def test_blocked_publisher_queues_check_for_office_pc(monkeypatch, tmp_path):
    backend, calls = online_check_site(monkeypatch, tmp_path, blocked_publisher)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": UNCHECKED.isoformat()}
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued" and "office PC" in data["message"]
    assert result_store.request_blob(DAILY, UNCHECKED) in backend.files
    calendar = client.get(
        "/api/availability", params={"publication": "daily-news", "month": UNCHECKED.strftime("%Y-%m")}
    ).json()
    assert calendar["requested"][UNCHECKED.isoformat()]["status"] == "queued"
    # A second click skips the publisher for a while but still queues.
    other = UNCHECKED - timedelta(days=1)
    again = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": other.isoformat()}
    )
    assert again.status_code == 202 and calls == [UNCHECKED]
    assert result_store.request_blob(DAILY, other) in backend.files


def test_reachable_publisher_checks_online_and_saves_result(monkeypatch, tmp_path):
    site = tmp_path / "site"

    def check(root, publication, edition_date, force=False):
        save_manifest(root, edition=edition_date, tenders=2)
        cached = image_cache_path(root / "_cache", IMAGE_URL)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(wide_jpeg())

    backend, _ = online_check_site(monkeypatch, tmp_path, check)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": UNCHECKED.isoformat()}
    )
    assert response.status_code == 200 and response.json()["status"] == "done"
    assert result_store.manifest_blob(DAILY, UNCHECKED) in backend.files
    assert result_store.request_blob(DAILY, UNCHECKED) not in backend.files
    calendar = client.get(
        "/api/availability", params={"publication": "daily-news", "month": UNCHECKED.strftime("%Y-%m")}
    ).json()
    assert calendar["checked"][UNCHECKED.isoformat()]["tenders"] == 2
    assert manifest_path(site, DAILY, UNCHECKED).is_file()


def test_saved_complete_result_opens_without_rechecking(monkeypatch, tmp_path):
    _, calls = online_check_site(monkeypatch, tmp_path, blocked_publisher)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": EDITION.isoformat()}
    )
    assert response.status_code == 200 and response.json()["status"] == "done"
    assert calls == []


def test_day_without_edition_is_explained_not_queued(monkeypatch, tmp_path):
    def no_paper(*args, **kwargs):
        raise main_module.EpaperError("No Daily News edition is published for Sunday 25 October 2026.")

    backend, _ = online_check_site(monkeypatch, tmp_path, no_paper)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": UNCHECKED.isoformat()}
    )
    assert response.status_code == 404 and "No Daily News edition" in response.json()["detail"]
    assert result_store.request_blob(DAILY, UNCHECKED) not in backend.files


def test_unfinished_online_check_is_queued_not_dropped(monkeypatch, tmp_path):
    def too_slow(*args, **kwargs):
        raise main_module.EpaperError("The newspaper check reached its time limit and is incomplete")

    backend, _ = online_check_site(monkeypatch, tmp_path, too_slow)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": UNCHECKED.isoformat()}
    )
    assert response.status_code == 202
    assert result_store.request_blob(DAILY, UNCHECKED) in backend.files


def test_unsaveable_request_is_reported_for_review(monkeypatch, tmp_path):
    backend, _ = online_check_site(monkeypatch, tmp_path, blocked_publisher)

    def broken_put(*args):
        raise result_store.StoreError("storage down")

    monkeypatch.setattr(backend, "put", broken_put)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": UNCHECKED.isoformat()}
    )
    assert response.status_code == 502
    assert "needs review" in response.json()["detail"]


def test_dates_past_keep_window_are_refused_online(monkeypatch, tmp_path):
    online_check_site(monkeypatch, tmp_path, blocked_publisher)
    old = result_store.oldest_kept_date() - timedelta(days=1)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": old.isoformat()}
    )
    assert response.status_code == 400 and "office PC" in response.json()["detail"]


def test_request_worker_runs_and_clears_requests(tmp_path):
    from app.monitor import request_worker

    backend = MemoryBackend()
    result_store.request_check(DAILY, EDITION, backend, force=True, reason="HTTP 403")
    seen = []

    def check(root, publication, edition_date, force=False):
        seen.append((edition_date, force))
        save_manifest(root, edition=edition_date)

    failed = request_worker.run_pending(
        tmp_path, backend=backend, check=check
    )
    assert failed == 0 and seen == [(EDITION, True)]
    assert result_store.manifest_blob(DAILY, EDITION) in backend.files
    assert result_store.request_blob(DAILY, EDITION) not in backend.files


def test_request_worker_failure_stays_visible(monkeypatch, tmp_path):
    from app.monitor import request_worker

    backend = MemoryBackend()
    result_store.request_check(DAILY, EDITION, backend, force=False, reason="HTTP 403")

    def check(*args, **kwargs):
        raise RuntimeError("publisher reset the connection")

    assert request_worker.run_pending(tmp_path, backend=backend, check=check) == 1
    entry = result_store.pending_requests(backend)[0]
    assert entry["status"] == "failed" and "reset the connection" in entry["error"]
    # Failed requests are not retried automatically; the site offers Check now again.
    assert request_worker.run_pending(tmp_path, backend=backend, check=check) == 0


def test_unreadable_request_is_shown_as_failed(tmp_path):
    backend = MemoryBackend()
    backend.files[result_store.request_blob(DAILY, EDITION)] = b"{broken"
    entry = result_store.pending_requests(backend)[0]
    assert entry["status"] == "failed" and entry["edition_date"] == EDITION.isoformat()


def test_online_site_reports_unchecked_date_and_missing_image(monkeypatch, tmp_path):
    backend = remote_site(monkeypatch, tmp_path)
    page = client.get("/monitor", params={"publication": "daily-news", "edition_date": (EDITION - timedelta(days=1)).isoformat()})
    assert page.status_code == 200 and "Hospital expands" not in page.text
    backend.delete([result_store.image_blob(DAILY, EDITION, IMAGE_URL)])
    image = client.get(f"/api/image/daily-news/{EDITION.isoformat()}/advert-0")
    assert image.status_code == 404
    assert "source link" in image.json()["detail"]


def test_office_pc_check_uploads_result_for_online_site(monkeypatch, tmp_path):
    # Runs the real import inside the monitor's upload step (it broke once silently).
    from app.monitor import epaper_monitor

    calls = []
    monkeypatch.setattr(result_store, "enabled", lambda: True)
    monkeypatch.setattr(
        result_store, "publish_edition",
        lambda root, publication, checked: calls.append(checked) or {"image_failures": []},
    )
    assert epaper_monitor._upload_for_online_site(tmp_path, DAILY, EDITION) == 0
    assert calls == [EDITION]


def test_failed_upload_is_reported_not_hidden(monkeypatch, tmp_path):
    from app.monitor import epaper_monitor

    def failing(*args):
        raise result_store.StoreError("no token")

    monkeypatch.setattr(result_store, "enabled", lambda: True)
    monkeypatch.setattr(result_store, "publish_edition", failing)
    assert epaper_monitor._upload_for_online_site(tmp_path, DAILY, EDITION) == 3


def test_cloud_block_explains_how_to_get_results(monkeypatch):
    import requests

    response = requests.Response()
    response.status_code = 403
    blocked = requests.HTTPError("403 Client Error: Forbidden", response=response)
    monkeypatch.setattr(main_module, "IS_VERCEL", True)
    assert "office PC" in main_module._publisher_failure(blocked)
    monkeypatch.setattr(main_module, "IS_VERCEL", False)
    assert main_module._publisher_failure(blocked) == "publisher answered HTTP 403"


def test_office_pc_reads_only_blob_keys_from_pulled_env(monkeypatch, tmp_path):
    pulled = tmp_path / ".env.vercel"
    pulled.write_text(
        'VERCEL="1"\nBLOB_STORE_ID="store_abc"\nVERCEL_OIDC_TOKEN="oidc-token"\n'
        'GEMINI_API_KEY="[SENSITIVE]"\n',
        encoding="utf-8",
    )
    for key in ("BLOB_READ_WRITE_TOKEN", "VERCEL_BLOB_READ_WRITE_TOKEN", "BLOB_STORE_ID",
                "VERCEL_OIDC_TOKEN", "VERCEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(result_store, "IS_VERCEL", False)
    monkeypatch.setattr(result_store, "PULLED_ENV", pulled)

    assert result_store.enabled()
    assert os.environ["BLOB_STORE_ID"] == "store_abc"
    assert os.environ["VERCEL_OIDC_TOKEN"] == "oidc-token"
    # VERCEL=1 would switch the office PC into online-site mode; it must not leak in.
    assert "VERCEL" not in os.environ


def test_no_blob_credentials_means_uploads_are_off(monkeypatch, tmp_path):
    for key in ("BLOB_READ_WRITE_TOKEN", "VERCEL_BLOB_READ_WRITE_TOKEN", "BLOB_STORE_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(result_store, "PULLED_ENV", tmp_path / "missing.env")
    assert not result_store.enabled()


def test_online_site_uses_store_id_credentials(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("BLOB_STORE_ID", "store_abc")
    monkeypatch.setattr(result_store, "IS_VERCEL", True)
    assert result_store.remote_mode()
