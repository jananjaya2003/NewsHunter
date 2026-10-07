from app.agent import deduplicate_stories
from app.schemas import Category, ExtractedStory


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
