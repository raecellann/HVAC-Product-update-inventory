from __future__ import annotations

import logging
import time
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import Config

log = logging.getLogger(__name__)


class ShopifyError(Exception):
    pass


class ShopifyThrottleError(Exception):
    """Raised when Shopify returns THROTTLED. Retried with backoff."""


class ShopifyUserError(ShopifyError):
    """Non-retryable error returned in the response (bad input, permissions, etc.)."""


class ShopifyClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "X-Shopify-Access-Token": cfg.access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    @retry(
        reraise=True,
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((ShopifyThrottleError, requests.exceptions.RequestException)),
    )
    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        resp = self.session.post(self.cfg.graphql_url, json=payload, timeout=60)

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "2"))
            log.warning("HTTP 429 from Shopify; sleeping %.1fs", retry_after)
            time.sleep(retry_after)
            raise ShopifyThrottleError("HTTP 429")

        if resp.status_code >= 500:
            raise ShopifyThrottleError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        if resp.status_code != 200:
            raise ShopifyUserError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        data = resp.json()

        if "errors" in data:
            errs = data["errors"]
            if isinstance(errs, list) and any(
                (e.get("extensions", {}) or {}).get("code") == "THROTTLED" for e in errs
            ):
                self._wait_for_throttle(data)
                raise ShopifyThrottleError(f"THROTTLED: {errs}")
            raise ShopifyUserError(f"GraphQL errors: {errs}")

        self._respect_cost(data)
        return data["data"]

    def _wait_for_throttle(self, data: dict) -> None:
        """When throttled, wait until bucket has refilled."""
        cost = (data.get("extensions") or {}).get("cost") or {}
        throttle = cost.get("throttleStatus") or {}
        restore_rate = throttle.get("restoreRate", 100)
        currently = throttle.get("currentlyAvailable", 0)
        maximum = throttle.get("maximumAvailable", 2000)
        needed = maximum - currently
        wait_s = max(1.0, needed / restore_rate)
        log.warning("Throttled; waiting %.1fs for bucket to refill", wait_s)
        time.sleep(wait_s)

    def _respect_cost(self, data: dict) -> None:
        """Proactive throttle: if bucket is getting low, slow down."""
        cost = (data.get("extensions") or {}).get("cost") or {}
        throttle = cost.get("throttleStatus") or {}
        currently = throttle.get("currentlyAvailable", 2000)
        maximum = throttle.get("maximumAvailable", 2000)
        if maximum and currently / maximum < 0.15:
            restore_rate = throttle.get("restoreRate", 100)
            wait_s = (maximum * 0.5 - currently) / max(restore_rate, 1)
            if wait_s > 0:
                log.info("Bucket low (%d/%d); pacing %.2fs", currently, maximum, wait_s)
                time.sleep(wait_s)
