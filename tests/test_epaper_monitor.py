import json
from datetime import datetime

import pytest

from app import epaper_monitor
from app.epaper_monitor import (
    COLOMBO,
    PUBLICATIONS,
    EpaperError,
    Story,
    find_latest_edition,
    medical_service_match,
    plain_text,
    select_medical_stories,
)
from app.schemas import MedicalMatchBatch, MedicalMatchDecision


class FakeMatcher:
    provider = "gemini"

    def __init__(self, included_ids=(), fail: bool = False):
        self.included_ids = set(included_ids)
        self.fail = fail

    def parse(self, *, instructions, content, response_model):
        if self.fail:
            raise RuntimeError("simulated API failure")
        supplied = json.loads(content)
        return MedicalMatchBatch(
            decisions=[
                MedicalMatchDecision(
                    story_id=item["story_id"],
                    include=item["story_id"] in self.included_ids,
                    confidence=0.9,
                    reason="Medical relevance checked.",
                    topics=["medicine"] if item["story_id"] in self.included_ids else [],
                    medical_evidence=(
                        (
                            "diabetes clinic"
                            if "diabetes clinic" in f"{item['title']} {item['body']}".lower()
                            else "named product will be available"
                        )
                        if item["story_id"] in self.included_ids
                        else ""
                    ),
                )
                for item in supplied
            ]
        )


def candidate(story_id: int, title: str, body: str) -> Story:
    return Story(
        story_id=story_id,
        page_id=10 + story_id,
        page_number=str(story_id),
        title=title,
        body=body,
        image_url=f"https://example.test/{story_id}.jpg",
        matched_terms=[],
    )


def test_plain_text_removes_markup_and_decodes_entities():
    assert plain_text("<p>Health &amp; medical care</p>") == "Health & medical care"


def test_plain_text_repairs_common_utf8_mojibake():
    assert plain_text("Limiting childrenâ€™s screen time") == "Limiting children’s screen time"


def test_matches_medical_service_headline():
    matched, terms = medical_service_match(
        "New hospital service opens for cancer patients",
        "The clinic will provide treatment and patient care.",
    )
    assert matched
    assert "hospital" in terms


def test_matches_public_health_screen_time_article():
    matched, terms = medical_service_match(
        "Limiting children's screen time",
        "Doctors warned of health risks and disease prevention concerns.",
    )
    assert matched
    assert "screen time" in terms


def test_retains_court_story_with_medical_content_for_review():
    matched, terms = medical_service_match(
        "CID to take charge after hospital discharge",
        "A medical panel submitted a treatment report to court after discussing services.",
    )
    assert matched
    assert "hospital" in terms


def test_excludes_illicit_drug_story():
    matched, _ = medical_service_match(
        "Suspect held for drug trafficking links with underworld",
        "Police seized illegal drugs.",
    )
    assert not matched


def test_excludes_illicit_drug_crime_despite_incidental_health_minister_words():
    matched, _ = medical_service_match(
        "Suspect held for drug trafficking links with underworld",
        "The Health Minister described new crime prevention legislation targeting narcotics networks.",
    )
    assert not matched


def test_excludes_incidental_health_word():
    matched, _ = medical_service_match(
        "Minister attends international trade briefing",
        "Delegates discussed finance, tourism, wellness and healthcare among many sectors.",
    )
    assert not matched


def test_retains_medicine_item_when_signal_is_only_in_body():
    matched, terms = medical_service_match(
        "Prices revised from next week",
        "The new schedule reduces the retail price of several medicines.",
    )
    assert matched
    assert "medicines" in terms


def test_retains_single_strong_clinical_body_signal():
    matched, terms = medical_service_match(
        "New community programme begins",
        "Residents will receive diabetes screening throughout the district.",
    )
    assert matched
    assert "diabetes" in terms


def test_ambiguous_single_body_words_do_not_match_unrelated_news():
    for body in (
        "The minister discussed crime prevention legislation.",
        "The military declared an emergency during the operation.",
        "Exporters attended a business clinic for small enterprises.",
        "Applicants passed the initial academic screening process.",
    ):
        matched, _ = medical_service_match("General news update", body)
        assert not matched


def test_retains_health_angle_in_drug_crime_story():
    matched, terms = medical_service_match(
        "Drug trafficking inquiry expands",
        "The report also describes addiction treatment and public health prevention.",
    )
    assert matched
    assert "addiction" in terms


def test_ai_matcher_can_retain_candidate_missed_by_keywords():
    story = candidate(
        101,
        "Important supply update",
        "The named product will be available to affected families next week.",
    )
    selected = select_medical_stories([story], engine=FakeMatcher(included_ids={101}))
    assert [item.story_id for item in selected] == [101]
    assert "gemini-ai" in selected[0].matched_terms
    assert selected[0].review_required
    assert selected[0].match_source == "gemini-unverified-review"


def test_local_safety_rule_retains_medical_item_when_ai_says_no():
    story = candidate(102, "Hospital service expands", "More patients will receive treatment.")
    selected = select_medical_stories([story], engine=FakeMatcher())
    assert [item.story_id for item in selected] == [102]
    assert "hospital" in selected[0].matched_terms
    assert selected[0].review_required
    assert selected[0].match_source == "local-safety-review"


def test_ai_and_source_evidence_confirm_local_medical_candidate():
    story = candidate(105, "New diabetes clinic", "Patients can obtain treatment.")
    selected = select_medical_stories([story], engine=FakeMatcher(included_ids={105}))
    assert [item.story_id for item in selected] == [105]
    assert not selected[0].review_required
    assert selected[0].match_source == "gemini-ai"


def test_ai_failure_stops_incomplete_report_instead_of_claiming_local_matches():
    medical = candidate(103, "New diabetes clinic", "Patients can obtain screening.")
    unrelated = candidate(104, "Trade meeting held", "Delegates discussed exports.")
    with pytest.raises(EpaperError, match="AI matching was incomplete"):
        select_medical_stories([medical, unrelated], engine=FakeMatcher(fail=True))


def test_partial_ai_batch_failure_rejects_entire_incomplete_run():
    class PartialFailureMatcher(FakeMatcher):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def parse(self, *, instructions, content, response_model):
            self.calls += 1
            supplied = json.loads(content)
            if any(item["story_id"] == 12 for item in supplied):
                raise RuntimeError("second batch unavailable")
            return super().parse(
                instructions=instructions,
                content=content,
                response_model=response_model,
            )

    stories = [
        candidate(story_id, f"Article {story_id}", "General report.") for story_id in range(13)
    ]
    status = {}

    with pytest.raises(EpaperError, match="AI matching was incomplete"):
        select_medical_stories(stories, engine=PartialFailureMatcher(), status=status)

    assert status["matching_method"] == "AI matching incomplete"


def test_transient_ai_batch_failure_recovers_before_report_generation():
    class RecoveringMatcher(FakeMatcher):
        def __init__(self):
            super().__init__(included_ids={12})
            self.failed_once = False

        def parse(self, *, instructions, content, response_model):
            supplied = json.loads(content)
            if any(item["story_id"] == 12 for item in supplied) and not self.failed_once:
                self.failed_once = True
                raise RuntimeError("temporary provider outage")
            return super().parse(
                instructions=instructions,
                content=content,
                response_model=response_model,
            )

    stories = [
        candidate(story_id, f"Article {story_id}", "General report.") for story_id in range(13)
    ]
    selected = select_medical_stories(stories, engine=RecoveringMatcher())

    assert [story.story_id for story in selected] == [12]


def test_failed_image_download_preserves_existing_report(monkeypatch, tmp_path):
    issue_date = datetime(2026, 10, 7, tzinfo=COLOMBO)
    report_dir = tmp_path / "2026-10-07"
    images_dir = report_dir / "articles"
    images_dir.mkdir(parents=True)
    existing_image = images_dir / "article-01-page-01.jpg"
    existing_index = report_dir / "index.html"
    existing_manifest = report_dir / "manifest.json"
    existing_image.write_bytes(b"existing-image")
    existing_index.write_text("existing-index", encoding="utf-8")
    existing_manifest.write_text("existing-manifest", encoding="utf-8")

    story = candidate(201, "Hospital update", "Patients receive treatment.")

    class FailingClient:
        def initialise(self):
            return None

        def pages(self, requested_date):
            return [object()]

        def download_image(self, url, destination):
            destination.write_bytes(b"partial")
            raise EpaperError("download failed")

    monkeypatch.setattr(epaper_monitor, "_collect_story_ids", lambda client, pages: [])
    monkeypatch.setattr(
        epaper_monitor,
        "_collect_matches",
        lambda client, pairs, status: (
            status.update(
                matching_method="GEMINI AI + local safety review",
                matching_model="fallback-model",
                matching_warning="",
            )
            or [story]
        ),
    )

    with pytest.raises(EpaperError, match="download failed"):
        epaper_monitor.generate_report(tmp_path, issue_date, client=FailingClient())

    assert existing_image.read_bytes() == b"existing-image"
    assert existing_index.read_text(encoding="utf-8") == "existing-index"
    assert existing_manifest.read_text(encoding="utf-8") == "existing-manifest"
    assert not list(images_dir.glob("*.part"))


def test_two_english_publications_are_available():
    assert PUBLICATIONS["daily-news"].edition_id == 1
    assert PUBLICATIONS["sunday-observer"].edition_id == 2


def test_latest_edition_looks_back_until_a_publication_date():
    class FakeClient:
        publication = PUBLICATIONS["sunday-observer"]

        def pages(self, issue_date):
            if issue_date.strftime("%Y-%m-%d") != "2026-10-04":
                raise EpaperError("No edition")
            return [object()]

    latest = find_latest_edition(
        FakeClient(),
        as_of=datetime(2026, 10, 7, tzinfo=COLOMBO),
    )
    assert latest.strftime("%Y-%m-%d") == "2026-10-04"
