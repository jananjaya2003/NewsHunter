import json
from datetime import datetime

from app.core.schemas import MedicalMatchBatch, MedicalMatchDecision
from app.monitor import epaper_monitor
from app.monitor.epaper_monitor import (
    COLOMBO,
    PUBLICATIONS,
    EpaperError,
    Story,
    find_latest_edition,
    medical_service_match,
    plain_text,
    select_medical_stories,
)


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


def test_ai_failure_keeps_local_medical_matches_with_visible_warning():
    medical = candidate(103, "New diabetes clinic", "Patients can obtain screening.")
    unrelated = candidate(104, "Trade meeting held", "Delegates discussed exports.")
    status = {}
    selected = select_medical_stories(
        [medical, unrelated], engine=FakeMatcher(fail=True), status=status
    )
    assert [story.story_id for story in selected] == [103]
    assert selected[0].review_required
    assert selected[0].match_source == "local-rules"
    assert "AI could not classify 2 item(s)" in status["matching_warning"]
    assert status["matching_retryable"] == "1"


def test_partial_ai_batch_failure_is_reported_and_retryable():
    class PartialFailureMatcher(FakeMatcher):
        def parse(self, *, instructions, content, response_model):
            supplied = json.loads(content)
            if any(item["story_id"] == 12 for item in supplied):
                raise RuntimeError("second batch unavailable")
            return super().parse(
                instructions=instructions,
                content=content,
                response_model=response_model,
            )

    size = epaper_monitor.AI_MATCH_BATCH_SIZE
    stories = [
        candidate(story_id, f"Article {story_id}", "General report.") for story_id in range(size)
    ] + [candidate(12, "Hospital wards reopen", "Patients return for treatment.")]
    status = {}

    selected = select_medical_stories(stories, engine=PartialFailureMatcher(), status=status)

    assert status["matching_method"] == "AI partially available + local safety review"
    assert "1 item(s)" in status["matching_warning"]
    # The AI-failed hospital item is still kept by the local medical rules.
    assert [story.story_id for story in selected] == [12]
    assert selected[0].review_required


GEMINI_429 = (
    "Gemini request failed: 429 RESOURCE_EXHAUSTED. Generate content requests per minute "
    "limit exceeded on Free Tier.\nPlease retry in 48.47944454s. 'retryDelay': '48s'"
)


def test_rate_limit_delay_reads_provider_hint():
    assert epaper_monitor._rate_limit_delay(RuntimeError(GEMINI_429)) == 49.47944454
    assert epaper_monitor._rate_limit_delay(RuntimeError("429 Too Many Requests")) == 30.0
    assert epaper_monitor._rate_limit_delay(RuntimeError("Server disconnected")) is None


def test_rate_limited_batch_waits_and_keeps_ai_decision(monkeypatch):
    class RateLimitedMatcher(FakeMatcher):
        def __init__(self):
            super().__init__(included_ids={1})
            self.calls = 0

        def parse(self, *, instructions, content, response_model):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(GEMINI_429)
            return super().parse(
                instructions=instructions, content=content, response_model=response_model
            )

    waits = []
    monkeypatch.setattr(epaper_monitor.time, "sleep", waits.append)
    matcher = RateLimitedMatcher()
    status = {}

    selected = select_medical_stories(
        [candidate(1, "Hospital wards reopen", "Patients return for treatment.")],
        engine=matcher,
        status=status,
        budget_seconds=300,
    )

    assert matcher.calls == 2
    assert waits and waits[0] > 40
    assert [story.story_id for story in selected] == [1]
    assert "matching_warning" not in status or "could not classify" not in status["matching_warning"]


def test_rate_limit_longer_than_budget_keeps_items_for_review(monkeypatch):
    class AlwaysLimited(FakeMatcher):
        def parse(self, *, instructions, content, response_model):
            raise RuntimeError(GEMINI_429)

    monkeypatch.setattr(epaper_monitor.time, "sleep", lambda seconds: None)
    status = {}

    selected = select_medical_stories(
        [candidate(1, "Hospital wards reopen", "Patients return for treatment.")],
        engine=AlwaysLimited(),
        status=status,
        budget_seconds=30,
    )

    # Recall first: the rate-limited hospital item still reaches human review.
    assert [story.story_id for story in selected] == [1]
    assert selected[0].review_required
    assert "could not classify 1 item(s)" in status["matching_warning"]


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


def test_generate_report_for_date_checks_only_the_requested_edition(monkeypatch, tmp_path):
    requested = datetime(2026, 10, 4, tzinfo=COLOMBO)
    calls = []

    def fake_run_edition_check(output_root, publication, edition_date, progress=None, force=False):
        calls.append((output_root, publication.key, edition_date, force))
        return tmp_path / "manifest.json", edition_date

    monkeypatch.setattr(epaper_monitor, "run_edition_check", fake_run_edition_check)

    report_path, report_date = epaper_monitor.generate_report_for_date(
        tmp_path,
        PUBLICATIONS["sunday-observer"],
        requested,
    )

    assert report_path == tmp_path / "manifest.json"
    assert report_date.date() == requested.date()
    assert calls == [(tmp_path, "sunday-observer", requested.date(), False)]
