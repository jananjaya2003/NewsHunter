from pathlib import Path

import fitz

from app.pdf_extract import PageText, chunk_pages, extract_pages


def test_chunk_pages_preserves_page_markers():
    chunks = chunk_pages(
        [PageText(1, "A" * 800), PageText(2, "B" * 800)],
        max_chars=1000,
    )
    assert len(chunks) == 2
    assert chunks[0].page_numbers == (1,)
    assert "--- PAGE 1 ---" in chunks[0].text
    assert chunks[1].page_numbers == (2,)


def test_long_page_is_split_with_same_page_number():
    chunks = chunk_pages([PageText(7, "X" * 2100)], max_chars=1000)
    assert len(chunks) == 3
    assert all(chunk.page_numbers == (7,) for chunk in chunks)
    assert all("--- PAGE 7 ---" in chunk.text for chunk in chunks)


def test_extract_pages_reads_real_text_pdf(tmp_path: Path):
    path = tmp_path / "edition.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Daily News Test Edition\nCouncil approves a new transport plan for the city. " * 5,
    )
    document.save(path)
    document.close()

    pages = extract_pages(path)
    assert pages[0].number == 1
    assert "Daily News Test Edition" in pages[0].text
