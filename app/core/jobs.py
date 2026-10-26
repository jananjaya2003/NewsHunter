"""In-process background jobs for edition checks, with progress for the page."""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

LOGGER = logging.getLogger(__name__)

STAGES = ("connect", "pages", "articles", "adverts", "download", "ocr", "classify")


@dataclass
class Job:
    id: str
    publication: str
    requested_date: str
    status: str = "queued"
    stage: str = "queued"
    done: int = 0
    total: int = 0
    message: str = "Waiting to start"
    error: str = ""
    result_date: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["elapsed_seconds"] = round((self.finished_at or time.time()) - self.started_at)
        data["stage_index"] = STAGES.index(self.stage) if self.stage in STAGES else -1
        data["stage_count"] = len(STAGES)
        return data


Runner = Callable[[Callable[[str, int, int, str], None]], str]


class JobManager:
    def __init__(self, workers: int = 2, keep: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._active: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="edition-check")
        self._keep = keep

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, publication: str, requested_date: str, runner: Runner) -> Job:
        """Start a check, or return the running one for the same edition."""
        key = (publication, requested_date)
        with self._lock:
            active_id = self._active.get(key)
            if active_id and self._jobs[active_id].status in {"queued", "running"}:
                return self._jobs[active_id]
            job = Job(id=secrets.token_urlsafe(9), publication=publication, requested_date=requested_date)
            self._jobs[job.id] = job
            self._active[key] = job.id
            self._trim()
        self._pool.submit(self._run, job, runner)
        return job

    def _trim(self) -> None:
        finished = [job for job in self._jobs.values() if job.status in {"done", "failed"}]
        for job in sorted(finished, key=lambda item: item.started_at)[: max(0, len(finished) - self._keep)]:
            self._jobs.pop(job.id, None)

    def _run(self, job: Job, runner: Runner) -> None:
        def progress(stage: str, done: int, total: int, message: str) -> None:
            with self._lock:
                job.stage, job.done, job.total, job.message = stage, done, total, message

        with self._lock:
            job.status = "running"
            job.message = "Starting"
        try:
            result_date = runner(progress)
        except Exception as exc:
            LOGGER.exception("Edition check failed")
            with self._lock:
                job.status = "failed"
                job.error = str(exc) or exc.__class__.__name__
                job.finished_at = time.time()
            return
        with self._lock:
            job.status = "done"
            job.result_date = result_date
            job.message = "Check complete"
            job.finished_at = time.time()
