"""Text recognition for image-only newspaper objects (tender notices, adverts).

The publisher supplies advertisements as images without text, so tender and
procurement notices are invisible to text matching. The Windows built-in OCR
engine reads them locally. Where it is unavailable (the Linux Vercel server),
Gemini transcribes the images instead. Results are cached per publisher object.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from app.ai.engines import AIEngineError, GeminiEngine
from app.core.config import settings

LOGGER = logging.getLogger(__name__)
SCRIPT = Path(__file__).resolve().parent / "ocr_windows.ps1"
# Several images per request keeps a free-tier key (5 requests/minute) usable.
AI_OCR_BATCH_SIZE = 6
AI_OCR_MAX_BATCH_BYTES = 15 * 1024 * 1024
AI_OCR_INSTRUCTIONS = """You transcribe newspaper advertisement and notice images.
For every numbered image, return all printed text exactly as it appears, top to
bottom, one line per printed line. Do not summarise, translate, correct or add
anything that is not visible. Keep names, numbers, dates, quantities, units,
reference numbers and medicine or device names exactly. Write [illegible] for
text that cannot be read. Return empty text only when the image has no text,
such as a photograph or logo. Return exactly one entry per image number."""


class OcrUnavailable(RuntimeError):
    pass


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    # WinRT projections are only available in Windows PowerShell 5.1.
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _windows_ocr_available() -> bool:
    return _powershell() is not None and SCRIPT.is_file()


def _ai_ocr_available() -> bool:
    return settings.ai_ocr_enabled and bool(settings.gemini_api_key)


def ocr_engine() -> str | None:
    """Name of the OCR engine used on this computer, or None if there is none."""
    if _windows_ocr_available():
        return "windows"
    if _ai_ocr_available():
        return "gemini"
    return None


def ocr_available() -> bool:
    return ocr_engine() is not None


def _run_batch(executable: str, paths: list[Path], timeout: float) -> dict[str, tuple[str, str]]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump([str(path.resolve()) for path in paths], handle)
        list_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-ListPath",
                str(list_path),
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        list_path.unlink(missing_ok=True)
    results: dict[str, tuple[str, str]] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        results[str(item.get("path"))] = (str(item.get("text") or ""), str(item.get("error") or ""))
    if completed.returncode != 0 and not results:
        message = completed.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise OcrUnavailable(f"Windows OCR failed: {message or completed.returncode}")
    return results


class _ImageText(BaseModel):
    image: int = Field(description="The image number given before the image.")
    text: str = Field(description="All printed text in the image, verbatim.")


class _ImageTextBatch(BaseModel):
    images: list[_ImageText]


def _mime_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _ai_batches(paths: list[Path]) -> tuple[list[list[tuple[Path, bytes]]], dict[Path, tuple[str, str]]]:
    """Group images into requests; unreadable or oversized images get an error instead."""
    batches: list[list[tuple[Path, bytes]]] = []
    errors: dict[Path, tuple[str, str]] = {}
    current: list[tuple[Path, bytes]] = []
    size = 0
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors[path] = ("", f"The advert image could not be read for OCR: {exc}"[:200])
            continue
        if len(data) > AI_OCR_MAX_BATCH_BYTES:
            errors[path] = ("", "The advert image is too large for AI OCR.")
            continue
        if current and (
            len(current) >= AI_OCR_BATCH_SIZE or size + len(data) > AI_OCR_MAX_BATCH_BYTES
        ):
            batches.append(current)
            current, size = [], 0
        current.append((path, data))
        size += len(data)
    if current:
        batches.append(current)
    return batches, errors


def _ai_batch(engine: GeminiEngine, batch: list[tuple[Path, bytes]]) -> dict[Path, tuple[str, str]]:
    from google.genai import types

    content: list = []
    for number, (_, data) in enumerate(batch, start=1):
        content.append(types.Part.from_text(text=f"Image {number}:"))
        content.append(types.Part.from_bytes(data=data, mime_type=_mime_type(data)))
    try:
        parsed = engine.parse(
            instructions=AI_OCR_INSTRUCTIONS, content=content, response_model=_ImageTextBatch
        )
    except AIEngineError as exc:
        return {path: ("", f"AI OCR failed: {exc}"[:200]) for path, _ in batch}
    texts: dict[int, list[str]] = {}
    for item in parsed.images:
        texts.setdefault(item.image, []).append(item.text)
    results: dict[Path, tuple[str, str]] = {}
    for number, (path, _) in enumerate(batch, start=1):
        if number in texts:
            results[path] = ("\n".join(texts[number]), "")
        else:
            # A missing answer is an error, so the image is listed for manual review.
            results[path] = ("", "AI OCR returned no result for this image.")
    return results


def _recognize_with_ai(paths: list[Path], workers: int, progress) -> dict[Path, tuple[str, str]]:
    engine = GeminiEngine(
        settings.gemini_api_key,
        settings.ocr_gemini_model,
        settings.gemini_fallback_models,
        timeout_seconds=settings.monitor_ai_timeout_seconds,
    )
    batches, results = _ai_batches(paths)
    done = len(results)
    # Two parallel requests stay inside the free-tier per-minute limit.
    with ThreadPoolExecutor(max_workers=max(1, min(2, workers, len(batches) or 1))) as pool:
        futures = {pool.submit(_ai_batch, engine, batch): batch for batch in batches}
        for future in as_completed(futures):
            results.update(future.result())
            done += len(futures[future])
            if progress:
                progress(done, len(paths))
    # One retry for batches that failed as a whole (usually rate limits).
    retry = [path for path in paths if results[path][1].startswith("AI OCR failed")]
    if retry:
        LOGGER.warning("Retrying AI OCR for %d image(s)", len(retry))
        for batch in _ai_batches(retry)[0]:
            for path, value in _ai_batch(engine, batch).items():
                if not value[1]:
                    results[path] = value
    return results


def recognize(
    paths: list[Path],
    workers: int = 3,
    timeout: float = 300,
    progress=None,
) -> dict[Path, tuple[str, str]]:
    """Return {path: (text, error)} for each image. Missing results carry an error."""
    engine = ocr_engine()
    if engine is None:
        raise OcrUnavailable(
            "No OCR engine is available: Windows OCR is not available on this computer "
            "and no Gemini API key is configured."
        )
    if not paths:
        return {}
    if engine == "gemini":
        return _recognize_with_ai(paths, workers, progress)
    executable = _powershell()
    # Small chunks keep progress reporting smooth; each chunk is one process.
    chunk_size = max(1, min(20, -(-len(paths) // workers)))
    chunks = [paths[index : index + chunk_size] for index in range(0, len(paths), chunk_size)]
    combined: dict[str, tuple[str, str]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
        futures = {pool.submit(_run_batch, executable, chunk, timeout): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            try:
                combined.update(future.result())
            except (OcrUnavailable, subprocess.TimeoutExpired, OSError) as exc:
                LOGGER.warning("OCR batch failed: %s", exc)
                for path in chunk:
                    combined.setdefault(str(path.resolve()), ("", f"OCR failed: {exc}"[:200]))
            done += len(chunk)
            if progress:
                progress(done, len(paths))
    return {
        path: combined.get(str(path.resolve()), ("", "OCR returned no result for this image."))
        for path in paths
    }
