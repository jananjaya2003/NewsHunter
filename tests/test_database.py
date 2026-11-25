import sqlite3
from pathlib import Path

from app.core.database import Database


def test_publish_requires_all_stories_approved(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    issue_id = database.create_issue("Paper", "2026-10-05", "", "paper.pdf", "abc")
    database.replace_generated_content(
        issue_id,
        [
            {
                "title": "Test story",
                "byline": "",
                "category": "Local",
                "summary": "A factual test summary.",
                "key_people": [],
                "key_numbers": [],
                "page_numbers": [1],
                "confidence": 0.9,
                "sensitive": False,
                "medical_evidence": "hospital patient services",
                "medical_relevance": "The story reports a hospital service change.",
            }
        ],
        "Overview.",
        ["Test story"],
        [],
    )

    ok, message = database.publish(issue_id)
    assert not ok
    assert "approved" in message

    database.approve_all(issue_id)
    ok, _ = database.publish(issue_id)
    assert ok
    assert database.get_issue(issue_id, published_only=True) is not None
    saved_story = database.get_stories(issue_id)[0]
    assert saved_story["medical_evidence"] == "hospital patient services"
    assert saved_story["medical_relevance"] == "The story reports a hospital service change."


def test_failed_issue_can_be_prepared_for_retry(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    issue_id = database.create_issue("Paper", "2026-10-05", "", "old.pdf", "retry-hash")
    database.set_issue_status(issue_id, "failed", "Temporary failure")

    assert database.prepare_failed_issue_for_retry(issue_id, "new.pdf")
    issue = database.get_issue(issue_id)
    assert issue["status"] == "queued"
    assert issue["file_path"] == "new.pdf"
    assert issue["error_message"] == ""


def test_initialize_migrates_legacy_story_table_without_losing_rows(tmp_path: Path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE issues (
            id INTEGER PRIMARY KEY, publication TEXT NOT NULL, edition_date TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN ('queued','processing','review','published','failed')),
            overview TEXT NOT NULL DEFAULT '', top_story_titles TEXT NOT NULL DEFAULT '[]',
            editor_notes TEXT NOT NULL DEFAULT '[]', error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT
        );
        CREATE TABLE stories (
            id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id),
            title TEXT NOT NULL, byline TEXT NOT NULL DEFAULT '', category TEXT NOT NULL,
            summary TEXT NOT NULL, key_people TEXT NOT NULL DEFAULT '[]',
            key_numbers TEXT NOT NULL DEFAULT '[]', page_numbers TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL, sensitive INTEGER NOT NULL DEFAULT 0,
            needs_review INTEGER NOT NULL DEFAULT 1, approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO issues
            (id, publication, edition_date, file_path, file_hash, status, created_at, updated_at)
        VALUES (1, 'Daily News', '2026-10-05', 'paper.pdf', 'legacy-hash', 'review', 'now', 'now');
        INSERT INTO stories
            (id, issue_id, title, category, summary, confidence, created_at, updated_at)
        VALUES (1, 1, 'Hospital service update', 'Healthcare Services', 'Summary.', 0.8, 'now', 'now');
        """
    )
    connection.close()

    database = Database(path)
    database.initialize()

    saved = database.get_stories(1)[0]
    assert saved["title"] == "Hospital service update"
    assert saved["medical_evidence"] == ""
    assert saved["medical_relevance"] == ""
