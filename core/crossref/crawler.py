"""Orchestrator: iterate SKUs, call sources in order, cache every attempt."""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .sources import Source, SourceResult

log = logging.getLogger("crossref.crawler")


@dataclass
class CrawlRow:
    product_id: str
    sku: str
    title: str = ""


@dataclass
class CrawlResult:
    product_id: str
    sku: str
    title: str
    status: str                    # found | not_found | skipped
    source: str | None = None
    url: str | None = None
    upcs: list[str] = field(default_factory=list)
    error: str | None = None


class Crawler:
    """Runs a list of Sources against CrawlRows with SQLite caching.

    The cache is keyed by (sku, source) and records every attempt — successful
    or not — so a resumed run skips SKUs it has already tried. Pass
    `use_cache=False` to force a re-fetch (e.g. after fixing an extractor).
    """

    def __init__(
        self,
        sources: list[Source],
        cache_path: Path,
        sleep_seconds: float = 1.0,
    ):
        if not sources:
            raise ValueError("Crawler needs at least one source.")
        self.sources = sources
        self.cache_path = cache_path
        self.sleep_seconds = sleep_seconds
        self._init_cache()

    def _init_cache(self) -> None:
        with self._db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS lookups (
                    sku          TEXT NOT NULL,
                    source       TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    url          TEXT,
                    upcs         TEXT,
                    error        TEXT,
                    looked_up_at TEXT NOT NULL,
                    PRIMARY KEY (sku, source)
                )
                """
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.cache_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _cached(self, sku: str, source_name: str) -> SourceResult | None:
        with self._db() as db:
            row = db.execute(
                "SELECT status, url, upcs, error FROM lookups WHERE sku=? AND source=?",
                (sku.lower(), source_name),
            ).fetchone()
        if not row:
            return None
        status, url, upcs_raw, error = row
        upcs = [u for u in (upcs_raw or "").split(",") if u]
        return SourceResult(source=source_name, url=url, upcs=upcs,
                            status=status, error=error)

    def _write_cache(self, sku: str, result: SourceResult) -> None:
        with self._db() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO lookups
                    (sku, source, status, url, upcs, error, looked_up_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sku.lower(),
                    result.source,
                    result.status,
                    result.url,
                    ",".join(result.upcs) if result.upcs else None,
                    result.error,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )

    def lookup_one(self, row: CrawlRow, *, use_cache: bool = True) -> CrawlResult:
        if not row.sku:
            return CrawlResult(row.product_id, row.sku, row.title, status="skipped",
                               error="no sku")

        for src in self.sources:
            cached = self._cached(row.sku, src.name) if use_cache else None
            if cached is not None:
                log.debug("CACHE %s via %s -> %s", row.sku, src.name, cached.status)
                result = cached
            else:
                log.info("QUERY %s via %s", row.sku, src.name)
                result = src.lookup(row.sku)
                self._write_cache(row.sku, result)
                time.sleep(self.sleep_seconds)

            if result.status == "found":
                return CrawlResult(
                    product_id=row.product_id, sku=row.sku, title=row.title,
                    status="found", source=result.source, url=result.url,
                    upcs=list(result.upcs),
                )

        # No source turned up a UPC for this SKU.
        return CrawlResult(row.product_id, row.sku, row.title, status="not_found")

    def lookup_many(
        self,
        rows: list[CrawlRow],
        *,
        use_cache: bool = True,
        progress_cb=None,
    ) -> Iterator[CrawlResult]:
        total = len(rows)
        for i, row in enumerate(rows, start=1):
            result = self.lookup_one(row, use_cache=use_cache)
            if progress_cb:
                progress_cb(i, total, result)
            yield result
