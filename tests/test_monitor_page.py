from dataclasses import replace
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.epaper_monitor import COLOMBO
from app.main import app

client = TestClient(app)


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
