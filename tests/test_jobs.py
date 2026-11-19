"""Single-slot JobRunner."""
from __future__ import annotations

import threading
import time


def test_runner_rejects_second_start_while_busy():
    from webapp.jobs import JobRunner

    r = JobRunner()
    gate = threading.Event()

    def slow(progress_cb=None):
        gate.wait(5)

    jid1 = r.try_start("test", slow)
    assert jid1 is not None
    # Give the executor a moment to claim the slot
    time.sleep(0.05)
    jid2 = r.try_start("test", slow)
    assert jid2 is None, "second job should be rejected while first is running"

    gate.set()
    # Wait for completion
    for _ in range(50):
        if r.get(jid1)["state"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)

    jid3 = r.try_start("test", lambda progress_cb=None: None)
    assert jid3 is not None
    r.shutdown()


def test_runner_records_failure():
    from webapp.jobs import JobRunner

    r = JobRunner()

    def bad(progress_cb=None):
        raise RuntimeError("boom")

    jid = r.try_start("test", bad)
    for _ in range(50):
        s = r.get(jid)
        if s["state"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert s["state"] == "failed"
    assert "boom" in s["error"]
    r.shutdown()
