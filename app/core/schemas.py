from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    public_health = "Public Health"
    healthcare_services = "Healthcare Services"
    hospitals = "Hospitals & Clinics"
    medicines = "Medicines & Pharmacy"
    workforce = "Healthcare Workforce"
    policy = "Health Policy & Financing"
    research = "Medical Research & Education"
    other = "Other Medical"


class ExtractedStory(BaseModel):
    title: str = Field(description="Short factual headline based only on the source")
    byline: str = Field(description="Author/byline exactly when visible, otherwise empty")
    category: Category
    summary: str = Field(description="Two or three concise sentences in original wording")
    key_people: list[str]
    key_numbers: list[str]
    page_numbers: list[int]
    confidence: float = Field(ge=0, le=1)
    sensitive: bool = Field(
        description="True for allegations, courts, elections, health, finance, crime or safety"
    )
    medical_evidence: str = Field(
        description="An exact 2-to-18 word source phrase proving medical relevance"
    )
    medical_relevance: str = Field(
        description="One sentence explaining why healthcare is the article's main subject"
    )


class ChunkResult(BaseModel):
    stories: list[ExtractedStory]
    extraction_warnings: list[str]


class MedicalMatchDecision(BaseModel):
    story_id: int
    include: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="Concise source-grounded reason for the decision")
    topics: list[str] = Field(description="Medical topics explicitly present in the item")
    medical_evidence: str = Field(
        description="Exact 2-to-18 word source phrase proving relevance, or empty when excluded"
    )


class MedicalMatchBatch(BaseModel):
    decisions: list[MedicalMatchDecision]


class IssueBrief(BaseModel):
    overview: str = Field(description="A neutral three-to-five sentence edition overview")
    top_story_titles: list[str] = Field(description="Up to ten exact titles from supplied stories")
    editor_notes: list[str] = Field(description="Accuracy or ambiguity warnings for the editor")
