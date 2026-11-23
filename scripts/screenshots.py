"""
Capture dashboard screenshots at desktop (1280) and mobile (390) widths
across every meaningful visual state.

States captured:
  01 idle            — dashboard after data loads
  02 skeleton        — immediate first paint (no data yet)
  03 file-picked     — upload zone showing a selected file
  04 mode-sheet      — mode-picker modal open
  05 job-running     — job status panel (simulated)
  06 cache-stale     — "This product list is stale" alert
  07 cache-missing   — "Product list not built yet" alert
  08 runs-empty      — "No runs yet" empty state
  09 cache-error     — alert error state

Usage: python scripts/screenshots.py  (expects dev servers on 5173/8001)
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots-phase1"
OUT.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:5173"

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 900},
    "mobile":  {"width":  390, "height": 844},  # iPhone 14 width
}


async def shot(page, name: str, viewport_name: str, *, full_page: bool = True):
    path = OUT / f"{name}__{viewport_name}.png"
    await page.screenshot(path=str(path), full_page=full_page)
    print(f"wrote {path.relative_to(OUT.parent.parent)}")


async def wait_for_network(page, selector=None):
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    if selector:
        try:
            await page.wait_for_selector(selector, timeout=5000)
        except Exception:
            pass


async def mock_api(context, overrides: dict | None = None):
    """Intercept /api/* and return predictable fixtures so screenshots are
    deterministic across runs. Pass overrides by endpoint path."""
    overrides = overrides or {}

    async def handler(route):
        req = route.request
        url = req.url
        for pat, body in overrides.items():
            if pat in url:
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(body),
                )
                return
        # Unknown API path — let it hit the dev server.
        await route.continue_()

    await context.route("**/api/**", handler)


FIX_CACHE_FRESH = {
    "id": 1,
    "refreshed_at": "2026-11-23T14:01:04Z",
    "sku_count": 70738,
    "duplicate_count": 297,
    "days_old": 0,
    "is_stale": False,
}
FIX_CACHE_STALE = {
    "id": 1,
    "refreshed_at": "2026-11-05T09:15:00Z",
    "sku_count": 68214,
    "duplicate_count": 304,
    "days_old": 18,
    "is_stale": True,
}
FIX_CACHE_MISSING = {}

FIX_RUNS = {
    "runs": [
        {
            "id": 42,
            "created_at": "2026-11-23T13:58:12Z",
            "kind": "apply_both",
            "file_name": "EQ8SGBP514YEZY_daily.xlsx",
            "status": "succeeded",
            "pushed": 142,
            "user_errors": 0,
            "parent_run_id": 41,
            "applied_at": "2026-11-23T13:58:12Z",
            "details_purged": 0,
            "counts": {"excel_rows": 68211, "matched": 61034, "will_change": 179, "unmatched": 7177},
            "warnings": [],
            "details_available": True,
        },
        {
            "id": 41,
            "created_at": "2026-11-23T13:54:30Z",
            "kind": "preview_both",
            "file_name": "EQ8SGBP514YEZY_daily.xlsx",
            "status": "succeeded",
            "pushed": 0,
            "user_errors": 0,
            "parent_run_id": None,
            "applied_at": "2026-11-23T13:58:12Z",
            "details_purged": 0,
            "counts": {
                "inventory": {"excel_rows": 68211, "matched": 61034, "will_change": 142, "unmatched": 7177, "bad_qty": 0, "duplicate": 0, "untracked": 0},
                "prices":    {"excel_rows": 68211, "matched": 61034, "will_change": 37,  "unmatched": 7177, "bad_price": 0, "duplicate": 0},
                "has_prices": True,
                "excel_rows": 68211, "matched": 61034, "will_change": 179,
                "unmatched": 7177, "bad_qty": 0, "duplicate": 0, "untracked": 0,
                "title_mismatches": 0,
            },
            "warnings": [],
            "details_available": True,
        },
        {
            "id": 40,
            "created_at": "2026-11-22T14:02:51Z",
            "kind": "cache_refresh",
            "file_name": None,
            "status": "succeeded",
            "pushed": 0, "user_errors": 0,
            "parent_run_id": None,
            "applied_at": None,
            "details_purged": 0,
            "counts": {"sku_count": 70501, "duplicate_count": 297, "blank_sku_count": 3129, "untracked_count": 3326},
            "warnings": [],
            "details_available": False,
        },
        {
            "id": 39,
            "created_at": "2026-11-21T14:01:12Z",
            "kind": "preview",
            "file_name": "3HVLUJ76N6EH1L_daily.xlsx",
            "status": "succeeded",
            "pushed": 0, "user_errors": 0,
            "parent_run_id": None,
            "applied_at": None,
            "details_purged": 0,
            "counts": {"excel_rows": 68103, "matched": 60872, "will_change": 211, "unmatched": 7231, "bad_qty": 0, "duplicate": 0, "untracked": 0},
            "warnings": [],
            "details_available": True,
        },
    ],
}
FIX_RUNS_EMPTY = {"runs": []}

FIX_FLAGS = {"disable_apply": True}

FIX_AGGREGATES = {
    "match_breakdown": {"matched": 61034, "unmatched": 7177, "bad_qty": 0, "duplicate": 0, "untracked": 0},
    "change_direction": {"up": 92, "down": 50, "to_zero": 37, "no_change": 60855, "unknown": 0},
}


async def capture_state(browser, name: str, viewport_name: str, *, url: str = "/", overrides=None, pre=None):
    context = await browser.new_context(viewport=VIEWPORTS[viewport_name])
    await mock_api(context, overrides or {})
    page = await context.new_page()
    await page.goto(f"{BASE}{url}")
    await wait_for_network(page)
    if pre:
        await pre(page)
    # Let fonts/skeleton settle.
    await page.wait_for_timeout(500)
    await shot(page, name, viewport_name)
    await context.close()


async def capture_skeleton(browser, viewport_name: str):
    # Intercept and delay the JSON responses so the first paint shows skeletons.
    context = await browser.new_context(viewport=VIEWPORTS[viewport_name])

    async def slow(route):
        await asyncio.sleep(10)
        await route.continue_()

    await context.route("**/api/**", slow)
    page = await context.new_page()
    await page.goto(f"{BASE}/", wait_until="domcontentloaded")
    await page.wait_for_timeout(600)  # let CSS/JS paint skeletons
    await shot(page, "02-skeleton", viewport_name)
    await context.close()


async def capture_file_picked(browser, viewport_name: str):
    context = await browser.new_context(viewport=VIEWPORTS[viewport_name])
    await mock_api(context, {
        "/api/dev/flags":    FIX_FLAGS,
        "/api/cache/status": FIX_CACHE_FRESH,
        "/api/runs":         FIX_RUNS,
        "/aggregates":       FIX_AGGREGATES,
    })
    page = await context.new_page()
    await page.goto(f"{BASE}/")
    await wait_for_network(page, selector="#upload-mount .upload-zone")

    # Attach a fake file to the hidden input.
    sample = Path(__file__).resolve().parent.parent / "test_items_30pct_not_synced.xlsx"
    file_input = await page.query_selector("#upload-input")
    await file_input.set_input_files(str(sample))
    await page.wait_for_selector(".upload-file-card")
    await page.wait_for_timeout(200)
    await shot(page, "03-file-picked", viewport_name)
    await context.close()


async def capture_mode_sheet(browser, viewport_name: str):
    context = await browser.new_context(viewport=VIEWPORTS[viewport_name])
    await mock_api(context, {
        "/api/dev/flags":    FIX_FLAGS,
        "/api/cache/status": FIX_CACHE_FRESH,
        "/api/runs":         FIX_RUNS,
        "/aggregates":       FIX_AGGREGATES,
    })
    page = await context.new_page()
    await page.goto(f"{BASE}/")
    await wait_for_network(page, selector="#upload-mount .upload-zone")

    sample = Path(__file__).resolve().parent.parent / "test_items_30pct_not_synced.xlsx"
    file_input = await page.query_selector("#upload-input")
    await file_input.set_input_files(str(sample))
    await page.wait_for_selector(".upload-file-card")
    # Open the mode sheet by clicking the primary CTA.
    await page.click("#upload-submit")
    await page.wait_for_selector("#mode-modal:not(.hidden)")
    await page.wait_for_timeout(200)
    await shot(page, "04-mode-sheet", viewport_name)
    await context.close()


async def capture_job_running(browser, viewport_name: str):
    context = await browser.new_context(viewport=VIEWPORTS[viewport_name])
    await mock_api(context, {
        "/api/dev/flags":    FIX_FLAGS,
        "/api/cache/status": FIX_CACHE_FRESH,
        "/api/runs":         FIX_RUNS,
        "/aggregates":       FIX_AGGREGATES,
    })
    page = await context.new_page()
    await page.goto(f"{BASE}/")
    await wait_for_network(page, selector="#upload-mount .upload-zone")

    # Simulate a running job by invoking the component directly.
    await page.evaluate("""
      (async () => {
        const { mountJobStatus } = await import('/src/components/jobStatus.js');
        const host = document.getElementById('job-status');
        host.classList.remove('hidden');
        const j = mountJobStatus(host);
        j.show({ title: 'Preparing preview', message: 'Matching SKUs against your Shopify catalog…', done: 23187, total: 68211 });
        window.__scrollTo = (y) => window.scrollTo(0, y);
      })();
    """)
    await page.wait_for_timeout(600)
    await shot(page, "05-job-running", viewport_name)
    await context.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for vp in VIEWPORTS:
            print(f"-- viewport: {vp} --")

            # 01 idle
            await capture_state(browser, "01-idle", vp, overrides={
                "/api/dev/flags":    FIX_FLAGS,
                "/api/cache/status": FIX_CACHE_FRESH,
                "/api/runs":         FIX_RUNS,
                "/aggregates":       FIX_AGGREGATES,
            })

            # 02 skeleton
            await capture_skeleton(browser, vp)

            # 03 file picked
            await capture_file_picked(browser, vp)

            # 04 mode sheet
            await capture_mode_sheet(browser, vp)

            # 05 job running
            await capture_job_running(browser, vp)

            # 06 cache stale
            await capture_state(browser, "06-cache-stale", vp, overrides={
                "/api/dev/flags":    FIX_FLAGS,
                "/api/cache/status": FIX_CACHE_STALE,
                "/api/runs":         FIX_RUNS,
                "/aggregates":       FIX_AGGREGATES,
            })

            # 07 cache missing
            await capture_state(browser, "07-cache-missing", vp, overrides={
                "/api/dev/flags":    FIX_FLAGS,
                "/api/cache/status": FIX_CACHE_MISSING,
                "/api/runs":         FIX_RUNS_EMPTY,
                "/aggregates":       FIX_AGGREGATES,
            })

            # 08 runs empty
            await capture_state(browser, "08-runs-empty", vp, overrides={
                "/api/dev/flags":    FIX_FLAGS,
                "/api/cache/status": FIX_CACHE_FRESH,
                "/api/runs":         FIX_RUNS_EMPTY,
                "/aggregates":       FIX_AGGREGATES,
            })

            # 09 cache error — fulfil as 500
            context = await browser.new_context(viewport=VIEWPORTS[vp])

            async def handler(route):
                url = route.request.url
                if "/api/cache/status" in url:
                    await route.fulfill(status=500, content_type="application/json",
                                         body=json.dumps({"detail": "Shopify is unreachable (try again in a minute)."}))
                    return
                for pat, body in {
                    "/api/dev/flags": FIX_FLAGS, "/api/runs": FIX_RUNS, "/aggregates": FIX_AGGREGATES,
                }.items():
                    if pat in url:
                        await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                        return
                await route.continue_()
            await context.route("**/api/**", handler)
            page = await context.new_page()
            await page.goto(f"{BASE}/")
            await wait_for_network(page)
            await page.wait_for_timeout(500)
            await shot(page, "09-cache-error", vp)
            await context.close()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
