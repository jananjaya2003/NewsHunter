from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    ai_provider: str = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    gemini_fallback_models: tuple[str, ...] = _as_csv(
        os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite")
    )
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    admin_password: str = os.getenv("ADMIN_PASSWORD", "").strip()
    keep_source_pdf: bool = _as_bool(os.getenv("KEEP_SOURCE_PDF"), False)
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "80"))
    max_chunk_chars: int = int(os.getenv("MAX_CHUNK_CHARS", "45000"))
    max_stories_per_chunk: int = int(os.getenv("MAX_STORIES_PER_CHUNK", "50"))
    database_path: Path = Path(os.getenv("DATABASE_PATH", "data/news_agent.db"))
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "data/uploads"))

    def prepare_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def active_model(self) -> str:
        if self.ai_provider == "gemini":
            return self.gemini_model
        if self.ai_provider == "openai":
            return self.openai_model
        return "unknown"

    @property
    def ai_configured(self) -> bool:
        if self.ai_provider == "gemini":
            return bool(self.gemini_api_key)
        if self.ai_provider == "openai":
            return bool(self.openai_api_key)
        return False


settings = Settings()
