from types import SimpleNamespace

from app.ai.agent import NewspaperAgent, deduplicate_stories
from app.core.schemas import Category, ChunkResult, ExtractedStory
from app.extraction.pdf_extract import TextChunk


def story(title: str, page: int, confidence: float = 0.8) -> ExtractedStory:
    return ExtractedStory(
        title=title,
        byline="",
        category=Category.public_health,
        summary=f"Summary for {title}.",
        key_people=[],
        key_numbers=[],
        page_numbers=[page],
        confidence=confidence,
        sensitive=False,
        medical_evidence="hospital patient services",
        medical_relevance="The story concerns hospital patient services.",
    )


def test_deduplicate_merges_similar_titles_and_pages():
    result = deduplicate_stories(
        [
            story("Council approves new transport plan", 1),
            story("Council approves the new transport plan", 3, confidence=0.9),
        ]
    )
    assert len(result) == 1
    assert result[0].page_numbers == [1, 3]
    assert result[0].confidence == 0.9


def test_deduplicate_keeps_unrelated_stories():
    result = deduplicate_stories(
        [story("New export rules announced", 2), story("Team wins final", 8)]
    )
    assert len(result) == 2


def test_candidate_evidence_must_appear_on_a_cited_page():
    candidate = story("Hospital update", 2)
    candidate.medical_evidence = "hospital patient services"

    class FakeEngine:
        def parse(self, **kwargs):
            return ChunkResult(stories=[candidate], extraction_warnings=[])

    agent = NewspaperAgent(
        SimpleNamespace(max_stories_per_chunk=10), database=None, engine=FakeEngine()
    )
    result = agent._extract_chunk(
        TextChunk(
            page_numbers=(1, 2),
            text=(
                "--- PAGE 1 ---\nHospital patient services expanded.\n"
                "--- PAGE 2 ---\nA city council meeting was held."
            ),
        )
    )

    assert result.stories == []
    assert "cited page" in result.extraction_warnings[0]


def test_candidate_evidence_on_cited_page_is_preserved_across_repeated_markers():
    candidate = story("Hospital update", 2)
    candidate.medical_evidence = "hospital patient services"

    class FakeEngine:
        def parse(self, **kwargs):
            return ChunkResult(stories=[candidate], extraction_warnings=[])

    agent = NewspaperAgent(
        SimpleNamespace(max_stories_per_chunk=10), database=None, engine=FakeEngine()
    )
    result = agent._extract_chunk(
        TextChunk(
            page_numbers=(2,),
            text=(
                "--- PAGE 2 ---\nHospital patient\n"
                "--- PAGE 2 ---\nservices expanded."
            ),
        )
    )

    assert len(result.stories) == 1
    assert result.stories[0].page_numbers == [2]
