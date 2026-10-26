import copy
import json
from datetime import timedelta

import pymupdf
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
    assert 'data-testid="check-now"' not in page.text
    image = client.get(f"/api/image/daily-news/{EDITION.isoformat()}/advert-0")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"


def test_online_site_refuses_checks_with_explanation(monkeypatch, tmp_path):
    remote_site(monkeypatch, tmp_path)
    response = client.post("/api/check", data={"publication": "daily-news"})
    assert response.status_code == 409
    assert "office PC" in response.json()["detail"]


def test_online_site_reports_unchecked_date_and_missing_image(monkeypatch, tmp_path):
    backend = remote_site(monkeypatch, tmp_path)
    page = client.get("/monitor", params={"publication": "daily-news", "edition_date": (EDITION - timedelta(days=1)).isoformat()})
    assert page.status_code == 200 and "Hospital expands" not in page.text
    backend.delete([result_store.image_blob(DAILY, EDITION, IMAGE_URL)])
    image = client.get(f"/api/image/daily-news/{EDITION.isoformat()}/advert-0")
    assert image.status_code == 404
    assert "source link" in image.json()["detail"]
