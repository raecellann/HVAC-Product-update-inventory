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


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
        self._jobs: dict[str, dict[str, Any]] = {}
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

        def _wrapper() -> None:
            try:
                fn(progress_cb=self._progress_cb(job_id), **kwargs)
                self._finish(job_id, "succeeded")
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

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ internals

    def _progress_cb(self, job_id: str) -> Callable[..., None]:
        def cb(done: int = 0, total: int = 0, message: str = "") -> None:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["progress"] = {"done": done, "total": total, "message": message}
        return cb

    def _finish(self, job_id: str, state: str, error: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["state"] = state
                job["finished_at"] = _now()
                if error:
                    job["error"] = error
            if self._current == job_id:
                self._current = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
