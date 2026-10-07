from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_engines import AIEngineError, GeminiEngine, OpenAIEngine, create_ai_engine
from app.config import Settings
from app.schemas import MedicalMatchBatch, MedicalMatchDecision


def settings_for(provider: str) -> Settings:
    return Settings(
        ai_provider=provider,
        gemini_api_key="gemini-test-key",
        gemini_model="gemini-test-model",
        openai_api_key="openai-test-key",
        openai_model="openai-test-model",
        database_path=Path("test.db"),
        upload_dir=Path("uploads"),
    )


def test_factory_selects_gemini():
    engine = create_ai_engine(settings_for("gemini"))
    assert isinstance(engine, GeminiEngine)
    assert engine.model == "gemini-test-model"
    assert "gemini-3.5-flash" in engine.fallback_models


def test_factory_selects_openai():
    engine = create_ai_engine(settings_for("openai"))
    assert isinstance(engine, OpenAIEngine)


def test_factory_rejects_unknown_provider():
    with pytest.raises(AIEngineError, match="Unsupported AI_PROVIDER"):
        create_ai_engine(settings_for("unknown"))


class ProviderError(Exception):
    def __init__(self, code):
        self.code = code


def test_gemini_fallback_only_for_model_or_transient_provider_errors():
    assert GeminiEngine._can_try_fallback(ProviderError(404))
    assert GeminiEngine._can_try_fallback(ProviderError(429))
    assert GeminiEngine._can_try_fallback(ProviderError(503))
    assert not GeminiEngine._can_try_fallback(ProviderError(400))
    assert not GeminiEngine._can_try_fallback(ProviderError(401))


def test_gemini_uses_fallback_model_after_transient_primary_failure(monkeypatch):
    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append(model)
            if model == "primary-model":
                raise ProviderError(503)
            return SimpleNamespace(
                parsed=MedicalMatchBatch(
                    decisions=[
                        MedicalMatchDecision(
                            story_id=1,
                            include=True,
                            confidence=0.9,
                            reason="Medical treatment update.",
                            topics=["treatment"],
                            medical_evidence="medical treatment update",
                        )
                    ]
                )
            )

    class FakeClient:
        def __init__(self, *, api_key):
            assert api_key == "test-key"
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    engine = GeminiEngine("test-key", "primary-model", ("fallback-model",))
    result = engine.parse(
        instructions="Classify.",
        content='[{"story_id": 1}]',
        response_model=MedicalMatchBatch,
    )

    assert result.decisions[0].include
    assert calls == ["primary-model", "fallback-model"]
    assert engine.last_model_used == "fallback-model"

    engine.parse(
        instructions="Classify again.",
        content='[{"story_id": 1}]',
        response_model=MedicalMatchBatch,
    )
    assert calls == ["primary-model", "fallback-model", "fallback-model"]
