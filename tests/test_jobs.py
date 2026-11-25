import time

from app.core.jobs import JobManager


def wait_until_finished(manager: JobManager, job_id: str):
    for _ in range(200):
        job = manager.get(job_id)
        if job and job.status in {"done", "failed"}:
            return job
        time.sleep(0.005)
    raise AssertionError("background job did not finish")


def test_trimmed_completed_job_does_not_leave_stale_active_pointer():
    manager = JobManager(workers=1, keep=1)

    def runner(_progress):
        return "2026-10-05"

    try:
        first = manager.submit("daily-news", "2026-10-05", runner)
        assert wait_until_finished(manager, first.id).status == "done"

        second = manager.submit("daily-news", "2026-10-06", runner)
        assert wait_until_finished(manager, second.id).status == "done"

        third = manager.submit("daily-news", "2026-10-07", runner)
        assert wait_until_finished(manager, third.id).status == "done"
        assert manager.get(first.id) is None

        retried = manager.submit("daily-news", "2026-10-05", runner)
        assert retried.id != first.id
        assert wait_until_finished(manager, retried.id).status == "done"
    finally:
        manager._pool.shutdown(wait=True)


def test_job_reads_are_snapshots_not_mutable_worker_state():
    manager = JobManager(workers=1)
    try:
        first = manager.submit("daily-news", "2026-10-05", lambda _progress: "2026-10-05")
        first.status = "tampered"
        assert manager.get(first.id).status != "tampered"
        assert wait_until_finished(manager, first.id).status == "done"
    finally:
        manager._pool.shutdown(wait=True)
