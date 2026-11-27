"""One-off probe: load a supplyhouse.com search for a single Carrier SKU
and dump the page so we can see what 'cross reference' looks like there.

Saves:
  logs/probe_supplyhouse_search.html
  logs/probe_supplyhouse_product.html (if a product page is reached)
  logs/probe_supplyhouse_notes.txt (summary)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

TEST_SKU = sys.argv[1] if len(sys.argv) > 1 else "HC44GE209"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def main() -> None:
    notes = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1366, "height": 900})
        # Mask webdriver flag
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()

        search_url = f"https://www.supplyhouse.com/search?q={TEST_SKU}"
        notes.append(f"GET {search_url}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        # Wait for CF challenge to pass — title changes away from "Just a moment..."
        for _ in range(20):
            time.sleep(1)
            if "Just a moment" not in page.title():
                break
        notes.append(f"  landed: {page.url}  title={page.title()!r}")

        (LOGS / "probe_supplyhouse_search.html").write_text(page.content(), encoding="utf-8")

        product_links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/p/"]')).slice(0,10).map(a => a.href)"""
        )
        notes.append(f"  product links on page: {len(product_links)}")
        for href in product_links[:5]:
            notes.append(f"    {href}")

        if product_links:
            target = product_links[0]
            notes.append(f"GET {target}")
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)
            notes.append(f"  landed: {page.url}  title={page.title()!r}")
            (LOGS / "probe_supplyhouse_product.html").write_text(page.content(), encoding="utf-8")

            body_text = page.inner_text("body")
            hits = []
            for needle in ["Alternate", "Replaces", "Replaced by", "Also known", "Cross Reference", "cross-reference", "OEM Part", "Manufacturer Part"]:
                idx = body_text.lower().find(needle.lower())
                if idx >= 0:
                    hits.append(f"  HIT  {needle!r}: ...{body_text[max(0,idx-40):idx+120]}...")
            if hits:
                notes.append("Keyword hits on product page:")
                notes.extend(hits)
            else:
                notes.append("No cross-reference keywords found on product page body text.")

        browser.close()

    (LOGS / "probe_supplyhouse_notes.txt").write_text("\n".join(notes), encoding="utf-8")
    print("\n".join(notes))


if __name__ == "__main__":
    main()
