import json

import app.main as main_module
from app.monitor import summaries
from tests.test_monitor_page import SAVED_MANIFEST, client

ITEM = {
    "uid": "advert-1",
    "title": "District General Hospital Matara · Supply of surgical gloves",
    "page_number": "18",
    "excerpt": "Sealed bids are invited for the supply of surgical gloves.",
    "text": "Sealed bids are invited for the supply of 5000 boxes of surgical gloves. "
    "Bids close at 10.00 a.m. on 17.11.2026. Bid security Rs. 25,000.",
}


class FakeEngine:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def parse(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def ai_on(monkeypatch):
    monkeypatch.setattr(type(summaries.settings), "ai_configured", property(lambda self: True))


def test_ai_summary_is_cached_and_grounded(monkeypatch, tmp_path):
    ai_on(monkeypatch)
    engine = FakeEngine(summaries.ArticleSummary(
        summary="Matara hospital invites bids for 5000 boxes of surgical gloves.",
        key_points=["Closes 10.00 a.m. on 17.11.2026", "Bid security Rs. 25,000"],
    ))
    manifest = tmp_path / "manifest.json"
    first = summaries.summarise(ITEM, manifest, engine_factory=lambda config: engine)
    again = summaries.summarise(ITEM, manifest, engine_factory=lambda config: engine)
    assert first["source"] == "ai" and first["unverified"] == []
    assert again == first and engine.calls == 1


def test_numbers_not_in_the_text_are_flagged(monkeypatch, tmp_path):
    ai_on(monkeypatch)
    engine = FakeEngine(summaries.ArticleSummary(
        summary="Bids for 9000 boxes of gloves close on 17.11.2026.", key_points=[],
    ))
    result = summaries.summarise(ITEM, tmp_path / "manifest.json", engine_factory=lambda config: engine)
    assert result["unverified"] == ["9000"]
    assert "not found in the text: 9000" in result["note"]


def test_ai_failure_shows_labelled_opening_and_is_not_cached(monkeypatch, tmp_path):
    ai_on(monkeypatch)
    busy = FakeEngine(error=RuntimeError("429 RESOURCE_EXHAUSTED"))
    manifest = tmp_path / "manifest.json"
    result = summaries.summarise(ITEM, manifest, engine_factory=lambda config: busy)
    assert result["source"] == "extract" and result["summary"] == ITEM["excerpt"]
    assert "busy" in result["note"]
    assert not (tmp_path / "summaries.json").exists()


def test_item_without_text_is_not_sent_to_the_ai(monkeypatch, tmp_path):
    ai_on(monkeypatch)
    engine = FakeEngine(error=AssertionError("must not be called"))
    item = dict(ITEM, text="", excerpt="")
    result = summaries.summarise(item, tmp_path / "manifest.json", engine_factory=lambda config: engine)
    assert result["source"] == "extract" and engine.calls == 0
    assert "No machine-read text" in result["note"]


def test_summary_endpoint_serves_saved_articles(monkeypatch, tmp_path):
    report_dir = tmp_path / "daily-news" / "2026-10-15"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(json.dumps(SAVED_MANIFEST), encoding="utf-8")
    monkeypatch.setattr(main_module, "MONITOR_REPORT_ROOT", tmp_path)
    monkeypatch.setattr(type(summaries.settings), "ai_configured", property(lambda self: False))
    uid = SAVED_MANIFEST["articles"][0]["uid"]
    response = client.get(f"/api/summary/daily-news/2026-10-15/{uid}")
    assert response.status_code == 200
    assert response.json()["source"] == "extract" and "off" in response.json()["note"]
    assert client.get("/api/summary/daily-news/2026-10-15/missing").status_code == 404
