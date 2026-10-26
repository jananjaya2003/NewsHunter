import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.core.config import settings
from app.extraction import ad_ocr
from app.monitor import epaper_monitor
from app.monitor.epaper_monitor import (
    PUBLICATIONS,
    AvailabilityCache,
    EpaperClient,
    EpaperError,
    PageInfo,
    PageObject,
    Story,
    check_edition,
    load_manifest,
    run_edition_check,
    write_manifest,
)

EDITION = date(2026, 10, 23)
DAILY = PUBLICATIONS["daily-news"]


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    monkeypatch.setattr(
        epaper_monitor, "settings", replace(settings, gemini_api_key="", openai_api_key="")
    )


class FakeClient:
    """A two-page edition: one news page and one advertisement page."""

    publication = DAILY

    def __init__(self, ocr_texts=None, broken_story_ids=()):
        self.ocr_texts = ocr_texts or {}
        self.broken_story_ids = set(broken_story_ids)
        self.pages_list = [
            PageInfo(1, "1", "23/10/2026", 1006, 1644, "https://example.test/p1.jpg"),
            PageInfo(2, "20", "23/10/2026", 1006, 1644, "https://example.test/p20.jpg"),
        ]

    def initialise(self):
        return None

    def latest_edition_date(self):
        return EDITION

    def pages(self, issue_date):
        if issue_date != EDITION:
            raise EpaperError("No Daily News edition is published for that date.")
        return self.pages_list

    def page_objects(self, page):
        if page.page_id == 1:
            return [
                PageObject(101, 2, 0, 0, 500, 800),
                PageObject(102, 2, 500, 0, 1000, 800),
                PageObject(103, 2, 0, 800, 1000, 1600),
                PageObject(901, 4, 20, 20, 480, 400),  # photo inside story 101
            ]
        return [PageObject(950, 4, 40, 40, 500, 1500), PageObject(951, 4, 500, 40, 980, 700)]

    def story(self, page, story_id):
        if story_id in self.broken_story_ids:
            raise EpaperError("unreadable")
        stories = {
            101: ("New ICU opens at Teaching Hospital", "Patients will receive intensive care."),
            102: ("Cricket final today", "The match starts at 2 pm."),
            103: ("", "Dengue patients increased at the Base Hospital this week."),
        }
        title, body = stories[story_id]
        return Story(
            story_id=story_id,
            page_id=page.page_id,
            page_number=page.page_number,
            title=title or f"Headline unavailable (article {story_id}, page {page.page_number})",
            body=body,
            image_url=f"https://example.test/s{story_id}.jpg",
            matched_terms=[],
            object_id=story_id,
            source_note="" if title else "The publisher supplied no headline.",
        )

    def picture_url(self, picture_id):
        return f"https://example.test/ad{picture_id}.jpg"

    def download_image(self, url, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(url.encode())


def fake_ocr(texts):
    def recognize(paths, workers=3, timeout=300, progress=None):
        result = {}
        for path in paths:
            url = Path(path).read_bytes().decode()
            object_id = int(url.rsplit("ad", 1)[1].split(".")[0])
            value = texts.get(object_id, ("", ""))
            result[path] = value if isinstance(value, tuple) else (value, "")
        return result

    return recognize


TENDER_TEXT = (
    "PROCUREMENT NOTICE\nNATIONAL HOSPITAL OF SRI LANKA\nINVITATION FOR BIDS\n"
    "Supply of infusion pumps and patient monitors. Bids close on 09.11.2026."
)


def test_tender_advert_is_found_through_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ad_ocr, "recognize", fake_ocr({950: TENDER_TEXT, 951: ""}))

    result = check_edition(DAILY, EDITION, tmp_path, client=FakeClient())

    by_uid = {item["uid"]: item for item in result.items}
    tender = by_uid["advert-950"]
    assert tender["primary_category"] == "medical_tender"
    assert tender["institutions"] == ["NATIONAL HOSPITAL OF SRI LANKA"]
    assert tender["dates_mentioned"] == ["09.11.2026"]
    assert tender["review_required"]
    assert any("OCR" in note for note in tender["notes"])
    assert result.items[0]["uid"] == "advert-950"  # medical tenders rank first
    assert "story-101" in by_uid
    assert "story-102" not in by_uid  # unrelated news is excluded
    # A picture with no text (photo or logo) is not a candidate.
    assert "advert-951" not in by_uid
    assert result.stats["story_photos_skipped"] == 1
    assert result.unscreened == []


def test_story_without_headline_uses_opening_words_and_is_still_screened():
    client = fake_client([EDITION])
    body = "Dengue patients increased at the Base Hospital this week, officials said on Friday."
    client.session.story_payload = {
        "StoryContent": [{"Headlines": [], "Body": f"<p>{body}</p>"}],
        "filepathstorypic": "https://example.test/s1.jpg",
    }
    story = client.story(PageInfo(1, "2", "23/10/2026"), 77)
    assert story.title.startswith("Dengue patients increased at the Base Hospital")
    assert len(story.title.split()) <= 14
    assert story.extraction_warning == ""
    assert "no headline" in story.source_note
    assert "dengue" in story.matched_terms


def test_story_without_text_is_flagged_for_review():
    client = fake_client([EDITION])
    client.session.story_payload = {"StoryContent": [{"Headlines": ["Notice"], "Body": ""}]}
    story = client.story(PageInfo(1, "2", "23/10/2026"), 78)
    assert "no article text" in story.extraction_warning
    selected = epaper_monitor.select_medical_stories([story])
    assert [item.story_id for item in selected] == [78]


def test_ads_are_listed_as_unscreened_when_ocr_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: False)

    result = check_edition(DAILY, EDITION, tmp_path, client=FakeClient())

    assert {item["object_id"] for item in result.unscreened} == {950, 951}
    assert all("OCR unavailable" in item["reason"] for item in result.unscreened)
    assert any("Windows OCR is not available" in warning for warning in result.warnings)


def test_ocr_error_is_reported_not_silently_dropped(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(
        ad_ocr, "recognize", fake_ocr({950: ("", "Image too large for OCR"), 951: ""})
    )
    result = check_edition(DAILY, EDITION, tmp_path, client=FakeClient())
    assert [(item["object_id"], item["reason"]) for item in result.unscreened] == [
        (950, "Image too large for OCR")
    ]


def test_unreadable_article_stays_visible_for_review(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ad_ocr, "recognize", fake_ocr({}))
    result = check_edition(DAILY, EDITION, tmp_path, client=FakeClient(broken_story_ids={102}))
    item = next(item for item in result.items if item["uid"] == "story-102")
    assert item["review_required"]
    assert any("did not return this article" in note for note in item["notes"])


def test_without_ai_key_all_results_need_review_and_warning_is_shown(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ad_ocr, "recognize", fake_ocr({950: TENDER_TEXT}))
    result = check_edition(DAILY, EDITION, tmp_path, client=FakeClient())
    assert result.items and all(item["review_required"] for item in result.items)
    assert "AI review is not configured" in result.matching_warning
    assert not result.matching_retryable


def test_ocr_text_is_cached_per_object(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    calls = []
    recognize = fake_ocr({950: TENDER_TEXT})

    def counting(paths, **kwargs):
        calls.append(len(paths))
        return recognize(paths, **kwargs)

    monkeypatch.setattr(ad_ocr, "recognize", counting)
    check_edition(DAILY, EDITION, tmp_path, client=FakeClient())
    check_edition(DAILY, EDITION, tmp_path, client=FakeClient())
    # Both results (text and no text) are cached, so nothing is read twice.
    assert calls == [2]


def test_wrong_date_fails_with_clear_message(tmp_path):
    with pytest.raises(EpaperError, match="No Daily News edition"):
        check_edition(DAILY, date(2026, 10, 25), tmp_path, client=FakeClient())


def test_manifest_round_trip_and_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ad_ocr, "recognize", fake_ocr({950: TENDER_TEXT}))
    path, checked = run_edition_check(tmp_path, DAILY, EDITION, client=FakeClient())
    manifest = load_manifest(tmp_path, DAILY, EDITION)
    assert checked == EDITION and path.is_file()
    assert manifest["complete"]
    assert manifest["category_counts"]["medical_tender"] == 1

    # A complete saved result is reused without contacting the publisher.
    class Unreachable(FakeClient):
        def initialise(self):
            raise AssertionError("publisher should not be contacted")

    assert run_edition_check(tmp_path, DAILY, EDITION, client=Unreachable())[1] == EDITION


def test_failed_check_keeps_previous_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ad_ocr, "recognize", fake_ocr({950: TENDER_TEXT}))
    run_edition_check(tmp_path, DAILY, EDITION, client=FakeClient())
    before = (tmp_path / "daily-news" / "2026-10-23" / "manifest.json").read_text(encoding="utf-8")

    class Broken(FakeClient):
        def page_objects(self, page):
            raise EpaperError("publisher down")

        def pages(self, issue_date):
            raise EpaperError("publisher down")

    with pytest.raises(EpaperError):
        run_edition_check(tmp_path, DAILY, EDITION, client=Broken(), force=True)
    after = (tmp_path / "daily-news" / "2026-10-23" / "manifest.json").read_text(encoding="utf-8")
    assert before == after


def test_early_upload_is_marked_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(ad_ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(ad_ocr, "recognize", fake_ocr({}))
    monkeypatch.setattr(epaper_monitor, "colombo_today", lambda: EDITION - timedelta(days=1))
    result = check_edition(DAILY, EDITION, tmp_path, client=FakeClient())
    assert any("early upload" in warning for warning in result.warnings)
    manifest = json.loads(write_manifest(tmp_path, result).read_text(encoding="utf-8"))
    assert not manifest["complete"]


class FakeResponse:
    def __init__(self, payload):
        self.content = json.dumps(payload).encode()

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.content)


class FakeSession:
    """Mimics the publisher: returns the nearest edition on or before a date."""

    def __init__(self, editions):
        self.editions = sorted(editions)
        self.headers = {}
        self.cookies = {}
        self.story_payload = {}



    def get(self, url, params=None, timeout=None):
        return FakeResponse(self.story_payload)

    def post(self, url, json=None, timeout=None):
        day, month, year = (int(part) for part in json["editiondate"].split("/"))
        requested = date(year, month, day)
        earlier = [value for value in self.editions if value <= requested]
        if not earlier:
            return FakeResponse([])
        found = earlier[-1].strftime("%d/%m/%Y")
        return FakeResponse([{"PageId": 1, "PageNo": "1", "EditionDate": found}])


def fake_client(editions):
    client = EpaperClient(publication=DAILY)
    session = FakeSession(editions)
    client.session = session
    client._worker_session = lambda: session
    return client


def test_publisher_substituted_date_is_rejected_with_nearest_edition():
    client = fake_client([date(2026, 10, 24)])
    with pytest.raises(EpaperError, match="nearest earlier edition is Saturday 24 October 2026"):
        client.pages(date(2026, 10, 25))
    assert client.pages(date(2026, 10, 24))[0].edition_date == "24/10/2026"


def test_available_dates_lists_only_exact_editions():
    editions = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 22)]
    client = fake_client(editions)
    assert client.available_dates(date(2026, 10, 18), date(2026, 10, 23)) == editions


def test_latest_edition_includes_tomorrows_early_upload(monkeypatch):
    monkeypatch.setattr(epaper_monitor, "colombo_today", lambda: date(2026, 10, 25))
    client = fake_client([date(2026, 10, 24), date(2026, 10, 26)])
    assert client.latest_edition_date() == date(2026, 10, 26)


def test_availability_cache_reuses_settled_months(monkeypatch, tmp_path):
    monkeypatch.setattr(epaper_monitor, "colombo_today", lambda: date(2026, 10, 25))
    calls = []

    def factory(publication):
        calls.append(publication.key)
        return fake_client([date(2026, 9, 1), date(2026, 9, 2)])

    cache = AvailabilityCache(tmp_path / "availability.json", fresh_seconds=0)
    first = cache.month(DAILY, 2026, 9, client_factory=factory)
    second = cache.month(DAILY, 2026, 9, client_factory=factory)
    assert first == second == [date(2026, 9, 1), date(2026, 9, 2)]
    assert calls == ["daily-news"]
    assert cache.month(DAILY, 2027, 1, client_factory=factory) == []
