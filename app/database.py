from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication TEXT NOT NULL,
    edition_date TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('queued','processing','review','published','failed')),
    overview TEXT NOT NULL DEFAULT '',
    top_story_titles TEXT NOT NULL DEFAULT '[]',
    editor_notes TEXT NOT NULL DEFAULT '[]',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    byline TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    key_people TEXT NOT NULL DEFAULT '[]',
    key_numbers TEXT NOT NULL DEFAULT '[]',
    page_numbers TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL,
    sensitive INTEGER NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 1,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stories_issue ON stories(issue_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def create_issue(
        self,
        publication: str,
        edition_date: str,
        source_url: str,
        file_path: str,
        file_hash: str,
    ) -> int:
        now = _now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO issues
                    (publication, edition_date, source_url, file_path, file_hash,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (publication, edition_date, source_url, file_path, file_hash, now, now),
            )
            return int(cursor.lastrowid)

    def list_issues(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*,
                       COUNT(s.id) AS story_count,
                       COALESCE(SUM(s.approved), 0) AS approved_count
                FROM issues i LEFT JOIN stories s ON s.issue_id = i.id
                GROUP BY i.id ORDER BY i.edition_date DESC, i.id DESC
                """
            ).fetchall()
        return [self._issue_dict(row) for row in rows]

    def get_issue(self, issue_id: int, published_only: bool = False) -> dict[str, Any] | None:
        with self.connect() as connection:
            if published_only:
                row = connection.execute(
                    "SELECT * FROM issues WHERE id = ? AND status = 'published'", (issue_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM issues WHERE id = ?", (issue_id,)
                ).fetchone()
        return self._issue_dict(row) if row else None

    def get_issue_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM issues WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        return self._issue_dict(row) if row else None

    def get_stories(self, issue_id: int, approved_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT * FROM stories WHERE issue_id = ?
            ORDER BY CASE category
                WHEN 'Public Health' THEN 1
                WHEN 'Healthcare Services' THEN 2
                WHEN 'Hospitals & Clinics' THEN 3
                WHEN 'Medicines & Pharmacy' THEN 4
                WHEN 'Healthcare Workforce' THEN 5
                WHEN 'Health Policy & Financing' THEN 6
                WHEN 'Medical Research & Education' THEN 7
                ELSE 8 END, id
        """
        approved_query = """
            SELECT * FROM stories WHERE issue_id = ? AND approved = 1
            ORDER BY CASE category
                WHEN 'Public Health' THEN 1
                WHEN 'Healthcare Services' THEN 2
                WHEN 'Hospitals & Clinics' THEN 3
                WHEN 'Medicines & Pharmacy' THEN 4
                WHEN 'Healthcare Workforce' THEN 5
                WHEN 'Health Policy & Financing' THEN 6
                WHEN 'Medical Research & Education' THEN 7
                ELSE 8 END, id
        """
        with self.connect() as connection:
            rows = connection.execute(
                approved_query if approved_only else query, (issue_id,)
            ).fetchall()
        return [self._story_dict(row) for row in rows]

    def set_issue_status(self, issue_id: int, status: str, error_message: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE issues SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, error_message[:2000], _now(), issue_id),
            )

    def prepare_failed_issue_for_retry(self, issue_id: int, file_path: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE issues SET file_path = ?, status = 'queued', error_message = '',
                                  updated_at = ?
                WHERE id = ? AND status = 'failed'
                """,
                (file_path, _now(), issue_id),
            )
            return cursor.rowcount == 1

    def update_issue_overview(self, issue_id: int, overview: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE issues SET overview = ?, updated_at = ? WHERE id = ?",
                (overview, _now(), issue_id),
            )
            return cursor.rowcount == 1

    def replace_generated_content(
        self,
        issue_id: int,
        stories: list[dict[str, Any]],
        overview: str,
        top_story_titles: list[str],
        editor_notes: list[str],
    ) -> None:
        now = _now()
        with self.connect() as connection:
            connection.execute("DELETE FROM stories WHERE issue_id = ?", (issue_id,))
            for story in stories:
                connection.execute(
                    """
                    INSERT INTO stories
                        (issue_id, title, byline, category, summary, key_people,
                         key_numbers, page_numbers, confidence, sensitive,
                         needs_review, approved, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                    """,
                    (
                        issue_id,
                        story["title"],
                        story["byline"],
                        story["category"],
                        story["summary"],
                        json.dumps(story["key_people"], ensure_ascii=False),
                        json.dumps(story["key_numbers"], ensure_ascii=False),
                        json.dumps(story["page_numbers"]),
                        story["confidence"],
                        int(story["sensitive"]),
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE issues SET overview = ?, top_story_titles = ?, editor_notes = ?,
                                  status = 'review', error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    overview,
                    json.dumps(top_story_titles, ensure_ascii=False),
                    json.dumps(editor_notes, ensure_ascii=False),
                    now,
                    issue_id,
                ),
            )

    def update_story(
        self,
        issue_id: int,
        story_id: int,
        title: str,
        summary: str,
        category: str,
        approved: bool,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE stories
                SET title = ?, summary = ?, category = ?, approved = ?, updated_at = ?
                WHERE id = ? AND issue_id = ?
                """,
                (title, summary, category, int(approved), _now(), story_id, issue_id),
            )
            return cursor.rowcount == 1

    def approve_all(self, issue_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE stories SET approved = 1, updated_at = ? WHERE issue_id = ?",
                (_now(), issue_id),
            )

    def publish(self, issue_id: int) -> tuple[bool, str]:
        with self.connect() as connection:
            issue = connection.execute(
                "SELECT status FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()
            if not issue:
                return False, "Issue not found."
            if issue["status"] not in {"review", "published"}:
                return False, "Only an issue in review can be published."
            counts = connection.execute(
                """
                SELECT COUNT(*) total, COALESCE(SUM(approved), 0) approved
                FROM stories WHERE issue_id = ?
                """,
                (issue_id,),
            ).fetchone()
            if counts["total"] == 0:
                return False, "The issue has no stories."
            if counts["total"] != counts["approved"]:
                return False, "Every story must be approved by an editor."
            now = _now()
            connection.execute(
                """
                UPDATE issues SET status = 'published', published_at = COALESCE(published_at, ?),
                                  updated_at = ? WHERE id = ?
                """,
                (now, now, issue_id),
            )
        return True, "Published."

    @staticmethod
    def _issue_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["top_story_titles"] = json.loads(result.get("top_story_titles") or "[]")
        result["editor_notes"] = json.loads(result.get("editor_notes") or "[]")
        return result

    @staticmethod
    def _story_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field in ("key_people", "key_numbers", "page_numbers"):
            result[field] = json.loads(result.get(field) or "[]")
        result["approved"] = bool(result["approved"])
        result["sensitive"] = bool(result["sensitive"])
        result["needs_review"] = bool(result["needs_review"])
        return result
