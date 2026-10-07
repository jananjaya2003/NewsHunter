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
