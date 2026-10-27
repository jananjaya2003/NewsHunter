"""Short summaries of saved articles for the viewer.

Summaries are written by the AI from the article's own machine-read text and
cached next to the edition's result. They must never add facts: any number in
the summary that is not in the source text is flagged, and when the AI is
unavailable the opening of the notice is shown instead, labelled as such.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, Field

from app.ai.engines import create_ai_engine
from app.core.config import settings
from app.monitor.epaper_monitor import _atomic_write_text

LOGGER = logging.getLogger(__name__)
MAX_SOURCE_CHARS = 6000
_lock = threading.Lock()

INSTRUCTIONS = """You summarise one Sri Lankan newspaper article or tender notice for a
medical-supplies sales team. Use only facts stated in the supplied text. Never
guess or add names, dates, quantities, prices, dosages or deadlines. Copy
numbers, dates, times, reference numbers and amounts exactly as written. If
the text is garbled, say so instead of filling gaps. This is not medical advice."""


class ArticleSummary(BaseModel):
    summary: str = Field(
        description="Two or three plain sentences: who issued it, what it is about or what is "
        "being bought, and what readers must do. Only facts stated in the text."
    )
    key_points: list[str] = Field(
        description="Up to six short facts from the text, such as buyer, items or services, "
        "closing date and time, fees or bid security, where to get documents, contact. "
        "Omit anything the text does not state."
    )


def _cache_path(manifest_file: Path) -> Path:
    return manifest_file.with_name("summaries.json")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def unverified_numbers(summary: dict, source: str) -> list[str]:
    """Numbers in the summary that do not appear in the source text."""
    written = " ".join([summary.get("summary", ""), *summary.get("key_points", [])])
    present = _numbers(source)
    return sorted(_numbers(written) - present, key=lambda value: (len(value), value))


def _fallback(item: dict, reason: str) -> dict:
    opening = item.get("excerpt") or (item.get("text") or "")[:320]
    return {
        "source": "extract",
        "summary": opening or "No machine-readable text was saved for this item.",
        "key_points": [],
        "note": f"{reason} Showing the opening of the notice instead; read the image for details.",
    }


def summarise(item: dict, manifest_file: Path, engine_factory=create_ai_engine) -> dict:
    """Cached AI summary for one item, or a clearly labelled fallback."""
    uid = item.get("uid", "")
    source = (item.get("text") or "").strip()
    cache_file = _cache_path(manifest_file)
    with _lock:
        cached = _load(cache_file).get(uid)
    if cached and cached.get("text_length") == len(source):
        return cached["result"]
    if not source:
        return _fallback(item, "No machine-read text was saved for this item.")
    if not settings.ai_configured:
        return _fallback(item, "AI summaries are off (no API key).")
    content = f"Title: {item.get('title', '')}\nPage: {item.get('page_number', '')}\n\nText:\n{source[:MAX_SOURCE_CHARS]}"
    try:
        # The main model, not the checker's: the free tier limits each model per
        # minute, so summaries then never slow a running check. Long notices need
        # more than the default 30 s.
        engine = engine_factory(replace(
            settings, ai_request_timeout_seconds=max(settings.ai_request_timeout_seconds, 60),
        ))
        result = engine.parse(instructions=INSTRUCTIONS, content=content, response_model=ArticleSummary)
    except Exception as exc:  # noqa: BLE001 - AI SDKs raise varied errors; never block the viewer.
        LOGGER.warning("Summary failed for %s: %s", uid, exc)
        busy = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
        return _fallback(item, "The AI is busy; try again in a minute." if busy else "The AI summary failed.")
    summary = {"summary": result.summary.strip(), "key_points": [p.strip() for p in result.key_points if p.strip()][:6]}
    missing = unverified_numbers(summary, source)
    note = "AI summary of the machine-read text. Check names, dates and amounts against the image."
    if len(source) > MAX_SOURCE_CHARS:
        note += " The notice is long, so only its first part was summarised."
    if missing:
        note += f" These numbers were not found in the text: {', '.join(missing)}. Treat them as unverified."
    output = {"source": "ai", **summary, "note": note, "unverified": missing}
    with _lock:
        cache = _load(cache_file)
        cache[uid] = {"text_length": len(source), "result": output}
        try:
            _atomic_write_text(cache_file, json.dumps(cache, ensure_ascii=False, indent=1))
        except OSError as exc:
            LOGGER.warning("Summary cache not saved: %s", exc)
    return output
