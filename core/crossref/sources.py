"""Source adapter for URL-template cross-reference lookups.

A single source class (TemplateSource) covers every supplier site whose
product URL is derivable from the SKU — the user supplies the template at
run time. Placeholders:
  {sku}        — SKU as-is
  {sku_lower}  — lowercased (e.g. alltemp.ca)
  {sku_upper}  — uppercased

Extraction: find any of the configured labels (case-insensitive) in the
page text, then collect GS1-valid codes (UPC-A, EAN-13, GTIN-14, EAN-8)
from a short window after the label.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("crossref.sources")

# Order matters — 14/13/12 before 8, so \b\d{8} doesn't swallow the first
# 8 digits of a longer code.
UPC_RE = re.compile(r"\b(?:\d{14}|\d{13}|\d{12}|\d{8})\b")

DEFAULT_LABELS: tuple[str, ...] = (
    "cross reference",
    "upc",
    "gtin",
    "ean",
    "barcode",
)


def gs1_valid(code: str) -> bool:
    """GS1 mod-10 check for UPC/EAN/GTIN codes."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits[-1]
    body_reversed = list(reversed(digits[:-1]))
    weighted = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body_reversed))
    computed = (10 - weighted % 10) % 10
    return check == computed


@dataclass
class SourceResult:
    """Outcome of one source lookup for one SKU."""
    source: str
    url: str | None
    upcs: list[str] = field(default_factory=list)
    status: str = "no_upc"  # one of: found, page_missing, no_upc, error
    error: str | None = None


class Source(Protocol):
    name: str

    def lookup(self, sku: str) -> SourceResult: ...


class TemplateSource:
    """Fetch a page built from a URL template and extract GS1 codes.

    The template is validated at construction — it MUST contain at least one
    of {sku}, {sku_lower}, {sku_upper}. The crawler never guesses a URL; if
    the template is wrong, misses just become `page_missing` or `no_upc`.
    """
    PLACEHOLDERS = ("{sku}", "{sku_lower}", "{sku_upper}")
    DEFAULT_UA = (
        "Mozilla/5.0 (compatible; HVAC-CrossRef-Crawler/1.0; "
        "contact the store owner if this misbehaves)"
    )
    WINDOW = 400

    def __init__(
        self,
        url_template: str,
        *,
        name: str | None = None,
        labels: Sequence[str] = DEFAULT_LABELS,
        session: requests.Session | None = None,
        timeout: int = 15,
        user_agent: str | None = None,
    ):
        if not any(p in url_template for p in self.PLACEHOLDERS):
            raise ValueError(
                f"URL template must contain one of {self.PLACEHOLDERS}; got {url_template!r}"
            )
        self.url_template = url_template
        # Default to a readable name derived from the hostname so the results
        # table shows "alltemp.ca" instead of a verbose URL template.
        self.name = name or _hostname_from_template(url_template)
        self.labels = tuple(label.lower() for label in labels)
        self.session = session or requests.Session()
        ua = user_agent or self.DEFAULT_UA
        self.session.headers.setdefault("User-Agent", ua)
        self.timeout = timeout

    def _url(self, sku: str) -> str:
        sku = sku.strip()
        return (
            self.url_template
            .replace("{sku_lower}", sku.lower())
            .replace("{sku_upper}", sku.upper())
            .replace("{sku}", sku)
        )

    def lookup(self, sku: str) -> SourceResult:
        url = self._url(sku)
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            return SourceResult(self.name, url, status="error", error=str(e))

        if r.status_code == 404:
            return SourceResult(self.name, url, status="page_missing")
        if r.status_code != 200:
            return SourceResult(self.name, url, status="error",
                                error=f"HTTP {r.status_code}")

        upcs = self._extract(r.text, self.labels, self.WINDOW)
        if not upcs:
            return SourceResult(self.name, url, status="no_upc")
        return SourceResult(self.name, url, upcs=upcs, status="found")

    @staticmethod
    def _extract(html: str, labels: Sequence[str], window: int) -> list[str]:
        """Find any label and collect valid GS1 codes near it.

        Deduplicates while preserving order. Rejects codes that fail the GS1
        checksum — keeps the false-positive rate near zero at the cost of
        occasionally missing a typo'd code on the site.
        """
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        lower = text.lower()

        earliest = -1
        for label in labels:
            idx = lower.find(label.lower())
            if idx >= 0 and (earliest == -1 or idx < earliest):
                earliest = idx
        if earliest < 0:
            return []

        scan = text[earliest: earliest + window]
        found: list[str] = []
        for match in UPC_RE.findall(scan):
            if gs1_valid(match) and match not in found:
                found.append(match)
        return found


def _hostname_from_template(url_template: str) -> str:
    m = re.match(r"https?://([^/]+)", url_template)
    return m.group(1) if m else url_template
