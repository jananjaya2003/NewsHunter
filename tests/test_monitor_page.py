import json
import re
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import requests
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.monitor.epaper_monitor import COLOMBO

client = TestClient(app)

SAVED_MANIFEST = {
    "cache_schema_version": 2,
    "publication": "Daily News",
    "publication_key": "daily-news",
    "edition_date": "2026-10-15",
    "generated_at": "2026-10-15T20:00:00+05:30",
    "complete": True,
    "article_count": 1,
    "category_counts": {"facility": 1},
    "matching_method": "Local medical keyword rules",
    "matching_model": "",
    "matching_warning": "",
    "warnings": [],
    "stats": {"pages": 12, "articles": 40, "adverts_read": 30},
    "pages": [],
    "unscreened": [],
    "articles": [{
        "uid": "story-12",
        "kind": "story",
        "story_id": 12,
        "page_id": 3,
        "page_number": "3",
        "title": "Hospital expands emergency care",
        "excerpt": "The Teaching Hospital Karapitiya opened a new unit.",
        "text": "The Teaching Hospital Karapitiya opened a new unit.",
        "image_url": "",
        "source_url": "https://epaperst.lakehouse.lk/home/ArticleView?eid=1",
        "categories": ["facility"],
        "primary_category": "facility",
        "priority": 70,
        "strength": 2,
        "is_tender": False,
        "borderline": False,
        "signals": ["hospital"],
        "institutions": ["Teaching Hospital Karapitiya"],
        "dates_mentioned": [],
        "matched_terms": ["hospital"],
        "match_source": "local-rules",
        "match_reason": "Medical services reported.",
        "match_confidence": 0.0,
        "review_required": True,
        "notes": [],
        "rect": [0, 0, 0, 0],
        "page_width": 0,
        "page_height": 0,
    }],
}


def test_monitor_page_lists_both_newspapers():
    response = client.get("/monitor")
    assert response.status_code == 200
    assert "Daily News" in response.text
    assert "Sunday Observer" in response.text
    assert "Check now" in response.text
    assert 'type="date"' in response.text
    assert 'name="edition_date"' in response.text


def test_monitor_rejects_unknown_newspaper():
    response = client.post(
        "/monitor/check",
        data={"publication": "not-a-newspaper"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_check_now_redirects_to_selected_publication_results(monkeypatch):
    def fake_generate_latest_report(output_root, publication):
        assert publication.key == "sunday-observer"
        return Path(output_root) / "2026-10-04" / "index.html", datetime(
            2026, 10, 4, tzinfo=COLOMBO
        )

    monkeypatch.setattr("app.main.generate_latest_report", fake_generate_latest_report)
    response = client.post(
        "/monitor/check",
        data={"publication": "sunday-observer"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        "/monitor?publication=sunday-observer&edition_date=2026-10-04"
    )


def test_selected_edition_date_is_checked_exactly(monkeypatch):
    def fake_generate_report_for_date(output_root, publication, issue_date):
        assert publication.key == "sunday-observer"
        assert issue_date.strftime("%Y-%m-%d") == "2026-10-04"
        return Path(output_root) / "2026-10-04" / "index.html", issue_date

    monkeypatch.setattr("app.main.generate_report_for_date", fake_generate_report_for_date)
    response = client.post(
        "/monitor/check",
        data={"publication": "sunday-observer", "edition_date": "2026-10-04"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        "/monitor?publication=sunday-observer&edition_date=2026-10-04"
    )


def test_invalid_or_future_edition_dates_are_rejected():
    invalid = client.post(
        "/monitor/check",
        data={"publication": "daily-news", "edition_date": "2026-2-04"},
    )
    assert invalid.status_code == 400

    future = client.post(
        "/monitor/check",
        data={"publication": "daily-news", "edition_date": "2999-01-01"},
    )
    assert future.status_code == 400


def test_failed_selected_date_check_preserves_date_for_retry(monkeypatch):
    def fail_generate_report_for_date(output_root, publication, issue_date):
        raise RuntimeError("No edition was found for 04/10/2026.")

    monkeypatch.setattr("app.main.generate_report_for_date", fail_generate_report_for_date)
    response = client.post(
        "/monitor/check",
        data={"publication": "sunday-observer", "edition_date": "2026-10-04"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "requested_date=2026-10-04" in response.headers["location"]
    assert "No%20edition%20was%20found" in response.headers["location"]
    result_page = client.get(response.headers["location"])
    assert result_page.status_code == 200
    assert 'id="edition-date" name="edition_date" type="date" value="2026-10-04"' in result_page.text
    assert "Check incomplete; human review required." in result_page.text


def test_selected_date_displays_its_saved_article_results(monkeypatch, tmp_path):
    report_dir = tmp_path / "daily-news" / "2026-10-15"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(json.dumps(SAVED_MANIFEST), encoding="utf-8")
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", tmp_path)

    response = client.get(
        "/monitor",
        params={"publication": "daily-news", "edition_date": "2026-10-15"},
    )

    assert response.status_code == 200
    assert "Daily News · 2026-10-15" in response.text
    assert "Hospital expands emergency care" in response.text
    assert "1 item" in response.text
    assert "Hospitals &amp; medical centres" in response.text
    assert "Institution:" in response.text


def test_results_offer_category_choice_viewer_and_full_text(monkeypatch, tmp_path):
    report_dir = tmp_path / "daily-news" / "2026-10-15"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(json.dumps(SAVED_MANIFEST), encoding="utf-8")
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", tmp_path)

    page = client.get(
        "/monitor", params={"publication": "daily-news", "edition_date": "2026-10-15"}
    ).text

    for hook in ('id="categories-all"', 'id="categories-none"', 'class="tile-tick"',
                 'data-testid="view-article"', 'data-testid="viewer"', 'id="unviewed-only"',
                 'class="viewed-badge"', 'id="bulk-stamp"', 'id="edit-stamp"'):
        assert hook in page
    # The viewer shows the saved text verbatim, with a warning to verify it.
    data = page.split('id="edition-data">', 1)[1].split("</script>", 1)[0]
    item = json.loads(data)["items"][0]
    assert item["text"] == SAVED_MANIFEST["articles"][0]["text"]
    assert "Machine-read text can contain errors" in page
    # Versioned script URL, so browsers load a changed script instead of a cached one.
    assert re.search(r'monitor\.js\?v=[0-9a-f]{12}"', page)


def test_security_headers_are_present():
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert (
        "'unsafe-inline'"
        not in response.headers["content-security-policy"]
        .split("script-src", 1)[1]
        .split(";", 1)[0]
    )


def test_authenticated_cross_site_post_is_blocked(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, admin_password="test-password"),
    )
    response = client.post(
        "/monitor/check",
        data={"publication": "not-a-newspaper"},
        auth=("admin", "test-password"),
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_authenticated_same_origin_post_is_allowed(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, admin_password="test-password"),
    )
    response = client.post(
        "/monitor/check",
        data={"publication": "not-a-newspaper"},
        auth=("admin", "test-password"),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 400


def _wait_for_job(job_id):
    for _ in range(200):
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in {"done", "failed"}:
            return data
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_background_check_reports_progress_and_result(monkeypatch):
    def fake_generate_report_for_date(output_root, publication, issue_date, progress=None, force=False):
        progress("ocr", 3, 10, "Reading text from advert 3 of 10")
        return Path(output_root) / "manifest.json", issue_date

    monkeypatch.setattr("app.main.generate_report_for_date", fake_generate_report_for_date)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": "2026-10-15"}
    )
    assert response.status_code == 202
    job = _wait_for_job(response.json()["id"])
    assert job["status"] == "done"
    assert job["result_url"] == "/monitor?publication=daily-news&edition_date=2026-10-15"


def test_background_check_failure_is_visible(monkeypatch):
    def failing(output_root, publication, issue_date, progress=None, force=False):
        raise RuntimeError("No Daily News edition is published for Sunday 11 October 2026.")

    monkeypatch.setattr("app.main.generate_report_for_date", failing)
    response = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": "2026-10-11"}
    )
    job = _wait_for_job(response.json()["id"])
    assert job["status"] == "failed"
    assert "No Daily News edition" in job["error"]


def test_tomorrows_early_edition_can_be_requested(monkeypatch):
    monkeypatch.setattr(main_module, "colombo_today", lambda: date(2026, 10, 25))
    calls = []

    def fake(output_root, publication, issue_date, progress=None, force=False):
        calls.append(issue_date.date())
        return Path(output_root) / "manifest.json", issue_date

    monkeypatch.setattr("app.main.generate_report_for_date", fake)
    tomorrow = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": "2026-10-26"}
    )
    assert tomorrow.status_code == 202
    _wait_for_job(tomorrow.json()["id"])
    assert calls == [date(2026, 10, 26)]
    later = client.post(
        "/api/check", data={"publication": "daily-news", "edition_date": "2026-10-27"}
    )
    assert later.status_code == 400


def test_availability_lists_publisher_dates_and_saved_results(monkeypatch, tmp_path):
    report_dir = tmp_path / "daily-news" / "2026-10-15"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(json.dumps(SAVED_MANIFEST), encoding="utf-8")
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", tmp_path)
    monkeypatch.setattr(
        main_module.availability,
        "month",
        lambda publication, year, month: [date(2026, 10, 14), date(2026, 10, 15)],
    )
    data = client.get(
        "/api/availability", params={"publication": "daily-news", "month": "2026-10"}
    ).json()
    assert data["dates"] == ["2026-10-14", "2026-10-15"]
    assert data["checked"] == {"2026-10-15": {"complete": True, "count": 1, "tenders": 0}}
    bad = client.get("/api/availability", params={"publication": "daily-news", "month": "x"})
    assert bad.status_code == 400


def test_availability_failure_explains_publisher_problem(monkeypatch):
    def blocked(publication, year, month):
        raise requests.ConnectionError("connection reset by peer")

    monkeypatch.setattr(main_module.availability, "month", blocked)
    response = client.get(
        "/api/availability", params={"publication": "daily-news", "month": "2026-10"}
    )
    assert response.status_code == 502
    assert "refused or dropped" in response.json()["detail"]


def test_image_proxy_only_serves_items_from_a_saved_result(monkeypatch, tmp_path):
    report_dir = tmp_path / "daily-news" / "2026-10-15"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(json.dumps(SAVED_MANIFEST), encoding="utf-8")
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", tmp_path)
    assert client.get("/api/image/daily-news/2026-10-15/story-999").status_code == 404
    # The saved item has no publisher image, so nothing is fetched.
    assert client.get("/api/image/daily-news/2026-10-15/story-12").status_code == 404
    assert client.get("/api/image/daily-news/2026-10-16/story-12").status_code == 404


def test_edition_api_returns_machine_readable_results(monkeypatch, tmp_path):
    report_dir = tmp_path / "daily-news" / "2026-10-15"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(json.dumps(SAVED_MANIFEST), encoding="utf-8")
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", tmp_path)
    data = client.get("/api/editions/daily-news/2026-10-15").json()
    assert data["articles"][0]["institutions"] == ["Teaching Hospital Karapitiya"]
    assert client.get("/api/editions/daily-news/2026-10-14").status_code == 404
