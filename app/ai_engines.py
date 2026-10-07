from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from app.config import Settings

StructuredResult = TypeVar("StructuredResult", bound=BaseModel)


class AIEngineError(RuntimeError):
    """Raised when the configured AI engine cannot produce a valid result."""


class AIEngine(ABC):
    provider: str
    model: str

    @abstractmethod
    def parse(
        self,
        *,
        instructions: str,
        content: str,
        response_model: type[StructuredResult],
    ) -> StructuredResult:
        """Return model output validated against a Pydantic schema."""


class GeminiEngine(AIEngine):
    provider = "gemini"

    def __init__(self, api_key: str, model: str, fallback_models: tuple[str, ...] = ()):
        self.api_key = api_key
        self.model = model
        self.fallback_models = tuple(
            candidate
            for candidate in dict.fromkeys(fallback_models)
            if candidate and candidate != model
        )
        self.last_model_used: str | None = None

    @staticmethod
    def _can_try_fallback(exc: Exception) -> bool:
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if status is None:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
        try:
            status_number = int(status)
        except (TypeError, ValueError):
            status_number = None
        return (
            status_number == 404
            or status_number == 429
            or (status_number is not None and 500 <= status_number <= 599)
        )

    def parse(
        self,
        *,
        instructions: str,
        content: str,
        response_model: type[StructuredResult],
    ) -> StructuredResult:
        if not self.api_key:
            raise AIEngineError("GEMINI_API_KEY is not configured.")

        # Lazy imports keep the OpenAI fallback usable if Gemini is not selected.
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = None
        failures: list[str] = []
        configured_candidates = (self.model, *self.fallback_models)
        candidates = configured_candidates
        if self.last_model_used in configured_candidates:
            candidates = (self.last_model_used,) + tuple(
                candidate
                for candidate in configured_candidates
                if candidate != self.last_model_used
            )
        for index, candidate in enumerate(candidates):
            try:
                response = client.models.generate_content(
                    model=candidate,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=instructions,
                        response_mime_type="application/json",
                        response_schema=response_model,
                        temperature=0.1,
                    ),
                )
                self.last_model_used = candidate
                break
            except Exception as exc:
                failures.append(f"{candidate}: {exc}")
                has_next = index + 1 < len(candidates)
                if not has_next or not self._can_try_fallback(exc):
                    raise AIEngineError(f"Gemini request failed: {exc}") from exc
        if response is None:
            raise AIEngineError(
                "Gemini request failed for all configured models: " + "; ".join(failures)
            )

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, response_model):
            return parsed
        response_text = getattr(response, "text", None)
        if not response_text:
            raise AIEngineError("Gemini returned no structured text.")
        try:
            return response_model.model_validate_json(response_text)
        except Exception as exc:
            raise AIEngineError("Gemini returned invalid structured output.") from exc


class OpenAIEngine(AIEngine):
    provider = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def parse(
        self,
        *,
        instructions: str,
        content: str,
        response_model: type[StructuredResult],
    ) -> StructuredResult:
        if not self.api_key:
            raise AIEngineError("OPENAI_API_KEY is not configured.")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, max_retries=2, timeout=120)
        try:
            response = client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": content},
                ],
                text_format=response_model,
            )
        except Exception as exc:
            raise AIEngineError(f"OpenAI request failed: {exc}") from exc
        if response.output_parsed is None:
            raise AIEngineError("OpenAI returned no structured output.")
        return response.output_parsed


def create_ai_engine(config: Settings) -> AIEngine:
    if config.ai_provider == "gemini":
        return GeminiEngine(
            config.gemini_api_key,
            config.gemini_model,
            config.gemini_fallback_models,
        )
    if config.ai_provider == "openai":
        return OpenAIEngine(config.openai_api_key, config.openai_model)
    raise AIEngineError(
        f"Unsupported AI_PROVIDER '{config.ai_provider}'. Use 'gemini' or 'openai'."
    )
