from dataclasses import replace

import pytest

from app.ai.engines import AIEngineError
from app.core.config import settings
from app.extraction import ad_ocr

TENDER_TEXT = "PROCUREMENT NOTICE\nSupply of insulin pens 100 IU/ml. Bids close 09.11.2026."


@pytest.fixture
def linux_server(monkeypatch):
    """Simulate the Vercel server: no Windows OCR, Gemini key configured."""
    monkeypatch.setattr(ad_ocr, "_windows_ocr_available", lambda: False)
    monkeypatch.setattr(ad_ocr, "settings", replace(settings, gemini_api_key="test-key"))


def images(tmp_path, count):
    paths = []
    for index in range(count):
        path = tmp_path / f"ad{index}.jpg"
        path.write_bytes(b"\xff\xd8fake-jpeg-" + str(index).encode())
        paths.append(path)
    return paths


def fake_parse(answer):
    """Patch GeminiEngine.parse; `answer(batch_size)` returns {image number: text}."""
    calls = []

    def parse(self, *, instructions, content, response_model):
        size = len(content) // 2  # one label part and one image part per image
        calls.append(size)
        texts = answer(size)
        return response_model(images=[{"image": n, "text": t} for n, t in texts.items()])

    return parse, calls


def test_gemini_is_used_when_windows_ocr_is_unavailable(linux_server):
    assert ad_ocr.ocr_engine() == "gemini"
    assert ad_ocr.ocr_available()


def test_no_engine_when_windows_ocr_and_gemini_key_are_missing(monkeypatch):
    monkeypatch.setattr(ad_ocr, "_windows_ocr_available", lambda: False)
    monkeypatch.setattr(ad_ocr, "settings", replace(settings, gemini_api_key=""))
    assert not ad_ocr.ocr_available()
    with pytest.raises(ad_ocr.OcrUnavailable):
        ad_ocr.recognize([])


def test_ai_ocr_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(ad_ocr, "_windows_ocr_available", lambda: False)
    monkeypatch.setattr(
        ad_ocr, "settings", replace(settings, gemini_api_key="k", ai_ocr_enabled=False)
    )
    assert ad_ocr.ocr_engine() is None


def test_ai_ocr_batches_images_and_keeps_text_verbatim(linux_server, monkeypatch, tmp_path):
    parse, calls = fake_parse(lambda size: {n: TENDER_TEXT for n in range(1, size + 1)})
    monkeypatch.setattr(ad_ocr.GeminiEngine, "parse", parse)
    paths = images(tmp_path, 19)

    results = ad_ocr.recognize(paths)

    assert sorted(calls) == [1, 6, 6, 6]  # 19 images in 4 requests, not 19
    assert set(results) == set(paths)
    assert all(value == (TENDER_TEXT, "") for value in results.values())


def test_image_missing_from_ai_answer_is_an_error_not_dropped(linux_server, monkeypatch, tmp_path):
    # The model answers for image 1 only; image 2 must be flagged for manual review.
    parse, _ = fake_parse(lambda size: {1: TENDER_TEXT})
    monkeypatch.setattr(ad_ocr.GeminiEngine, "parse", parse)
    first, second = images(tmp_path, 2)

    results = ad_ocr.recognize([first, second])

    assert results[first] == (TENDER_TEXT, "")
    assert results[second][0] == ""
    assert "no result" in results[second][1]


def test_failed_ai_request_is_retried_then_reported(linux_server, monkeypatch, tmp_path):
    attempts = []

    def failing(self, **kwargs):
        attempts.append(1)
        raise AIEngineError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(ad_ocr.GeminiEngine, "parse", failing)
    paths = images(tmp_path, 3)

    results = ad_ocr.recognize(paths)

    assert len(attempts) == 2  # first try plus one retry
    assert all(text == "" and error.startswith("AI OCR failed") for text, error in results.values())


def test_retry_recovers_after_rate_limit(linux_server, monkeypatch, tmp_path):
    state = {"calls": 0}

    def flaky(self, *, instructions, content, response_model):
        state["calls"] += 1
        if state["calls"] == 1:
            raise AIEngineError("429 RESOURCE_EXHAUSTED")
        return response_model(images=[{"image": 1, "text": TENDER_TEXT}])

    monkeypatch.setattr(ad_ocr.GeminiEngine, "parse", flaky)
    (path,) = images(tmp_path, 1)

    assert ad_ocr.recognize([path]) == {path: (TENDER_TEXT, "")}


def test_unreadable_image_is_reported(linux_server, monkeypatch, tmp_path):
    parse, calls = fake_parse(lambda size: {n: "" for n in range(1, size + 1)})
    monkeypatch.setattr(ad_ocr.GeminiEngine, "parse", parse)
    missing = tmp_path / "missing.jpg"

    results = ad_ocr.recognize([missing])

    assert calls == []
    assert "could not be read" in results[missing][1]
