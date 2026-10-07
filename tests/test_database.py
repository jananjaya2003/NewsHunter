from pathlib import Path

from app.database import Database


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
