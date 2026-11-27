"""Shared dependencies: config, job runner."""
from __future__ import annotations

from core.config import Config, load_config

from .jobs import JobRunner

_cfg: Config | None = None
_runner: JobRunner | None = None


def get_cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner
