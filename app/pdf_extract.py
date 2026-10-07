from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class PageText:
    number: int
    text: str


@dataclass(frozen=True)
class TextChunk:
    page_numbers: tuple[int, ...]
    text: str


class PdfExtractionError(ValueError):
    pass


def extract_pages(path: Path) -> list[PageText]:
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfExtractionError("The uploaded file is not a readable PDF.") from exc

    pages: list[PageText] = []
    try:
        if document.page_count == 0:
            raise PdfExtractionError("The PDF contains no pages.")
        for index, page in enumerate(document):
            # sort=True improves reading order for many multi-column text PDFs.
            text = page.get_text("text", sort=True)
            cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
            if cleaned:
                pages.append(PageText(number=index + 1, text=cleaned))
    finally:
        document.close()

    if not pages or sum(len(page.text) for page in pages) < 200:
        raise PdfExtractionError(
            "No readable text was found. This may be an image-only PDF that requires OCR."
        )
    return pages


def chunk_pages(pages: list[PageText], max_chars: int) -> list[TextChunk]:
    if max_chars < 1000:
        raise ValueError("max_chars must be at least 1000")

    chunks: list[TextChunk] = []
    current_parts: list[str] = []
    current_pages: list[int] = []
    current_size = 0

    for page in pages:
        marker = f"\n--- PAGE {page.number} ---\n"
        text = page.text

        # Extremely long pages are split, but every piece retains its page marker.
        segments = [text[i : i + max_chars] for i in range(0, len(text), max_chars)] or [""]
        for segment in segments:
            rendered = marker + segment
            if current_parts and current_size + len(rendered) > max_chars:
                chunks.append(TextChunk(tuple(current_pages), "".join(current_parts)))
                current_parts = []
                current_pages = []
                current_size = 0
            current_parts.append(rendered)
            if page.number not in current_pages:
                current_pages.append(page.number)
            current_size += len(rendered)

    if current_parts:
        chunks.append(TextChunk(tuple(current_pages), "".join(current_parts)))
    return chunks
