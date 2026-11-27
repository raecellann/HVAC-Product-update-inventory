"""Stealth probe against supplyhouse.com using playwright-stealth."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

TEST_SKU = sys.argv[1] if len(sys.argv) > 1 else "HC44GE209"


def main() -> None:
    notes = [f"SKU: {TEST_SKU}"]
    stealth = Stealth()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 900},
        )
        stealth.apply_stealth_sync(ctx)
        page = ctx.new_page()

        search_url = f"https://www.supplyhouse.com/search?q={TEST_SKU}"
        notes.append(f"GET {search_url}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

        for i in range(30):
            time.sleep(1)
            title = page.title()
            if "Just a moment" not in title and title:
                notes.append(f"  cleared after {i+1}s: {title!r}")
                break
        else:
            notes.append(f"  STILL CHALLENGED after 30s: {page.title()!r}")

        notes.append(f"  final url: {page.url}")
        (LOGS / "probe_supplyhouse_search.html").write_text(page.content(), encoding="utf-8")

        product_links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/p/"]')).slice(0,10).map(a => a.href)"""
        )
        notes.append(f"  product links on search results: {len(product_links)}")
        for href in product_links[:5]:
            notes.append(f"    {href}")

        if product_links:
            page.goto(product_links[0], wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            notes.append(f"product page: {page.url!r}  title={page.title()!r}")
            (LOGS / "probe_supplyhouse_product.html").write_text(page.content(), encoding="utf-8")

            body_text = page.inner_text("body")
            for needle in ["Alternate", "Replaces", "Replaced by", "Also known", "Cross Reference", "cross-reference", "OEM Part", "Manufacturer Part"]:
                idx = body_text.lower().find(needle.lower())
                if idx >= 0:
                    notes.append(f"  HIT {needle!r}: ...{body_text[max(0,idx-40):idx+180]}...")

        browser.close()

    out = "\n".join(notes)
    (LOGS / "probe_supplyhouse_notes.txt").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
