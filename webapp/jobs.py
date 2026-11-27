"""Single-slot background job runner.

Only one job of any kind (preview / apply / cache_refresh) can run at a time.
Every start endpoint tries `try_start`; if it returns None, respond 409.
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)


class JobBusy(Exception):
    pass


class JobCancelled(Exception):
    """Raised cooperatively when progress_cb notices a cancel request.

    A long-running job must call progress_cb() periodically to be cancellable —
    threads can't be killed in Python, so we rely on the job yielding.
    """
    pass


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._current: str | None = None

    # ------------------------------------------------------------ public API

    def try_start(
        self,
        kind: str,
        fn: Callable[..., Any],
        *,
        run_id: int | None = None,
        **kwargs,
    ) -> str | None:
        """Start `fn(progress_cb=..., **kwargs)` on the single worker.

        Returns the job_id, or None if another job is already active.
        The wrapped function receives a `progress_cb(done, total, message)`
        kwarg so it can push live progress into the status dict.
        """
        with self._lock:
            if self._current is not None:
                return None
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "state": "running",
                "progress": {"done": 0, "total": 0, "message": "Starting"},
                "started_at": _now(),
                "finished_at": None,
                "error": None,
                "run_id": run_id,
            }
            self._current = job_id

        call_kwargs = dict(kwargs)
        if run_id is not None:
            call_kwargs["run_id"] = run_id

        def _wrapper() -> None:
            try:
                fn(progress_cb=self._progress_cb(job_id), **call_kwargs)
                self._finish(job_id, "succeeded")
            except JobCancelled:
                log.info("Job %s (%s) cancelled by user", job_id, kind)
                self._finish(job_id, "cancelled", error="Cancelled by user")
            except Exception as e:  # noqa: BLE001 — surface anything to the UI
                log.exception("Job %s (%s) failed", job_id, kind)
                self._finish(job_id, "failed", error=str(e))

        self._executor.submit(_wrapper)
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            if self._current is None:
                return None
            j = self._jobs.get(self._current)
            return dict(j) if j else None

    def is_busy(self) -> bool:
        with self._lock:
            return self._current is not None

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns True if a running job was flagged.

        The job keeps going until its next progress_cb call — that's where the
        JobCancelled exception is raised. Jobs that don't call progress_cb
        can't be cancelled this way.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job["state"] != "running":
                return False
            self._cancel_flags[job_id] = True
            return True

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ internals

    def _progress_cb(self, job_id: str) -> Callable[..., None]:
        def cb(done: int = 0, total: int = 0, message: str = "") -> None:
            cancelled = False
            with self._lock:
                if self._cancel_flags.get(job_id):
                    cancelled = True
                else:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job["progress"] = {"done": done, "total": total, "message": message}
            if cancelled:
                raise JobCancelled("Cancelled by user")
        return cb

    def _finish(self, job_id: str, state: str, error: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["state"] = state
                job["finished_at"] = _now()
                if error:
                    job["error"] = error
            self._cancel_flags.pop(job_id, None)
            if self._current == job_id:
                self._current = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
