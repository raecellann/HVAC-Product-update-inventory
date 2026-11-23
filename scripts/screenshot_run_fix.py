"""Quick screenshot of the run page with high counts to verify thousands separators."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots-phase1"
OUT.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:5173"

RUN = {
    "id": 6,
    "created_at": "2026-11-19T11:49:00Z",
    "kind": "preview",
    "file_name": "3HVLUJ76N6EH1L.daily.20261115183002.xlsx",
    "status": "succeeded",
    "pushed": 0, "user_errors": 0,
    "parent_run_id": None,
    "applied_at": None,
    "details_purged": 0,
    "counts": {
        "excel_rows": 1005616,
        "matched": 48853,
        "will_change": 1413,
        "unmatched": 956763,
        "bad_qty": 0, "duplicate": 0, "untracked": 0,
    },
    "warnings": [],
    "details_available": True,
    "can_apply": False,
    "apply_blocked_reason": "",
    "apply_disabled_in_dev": True,
}
AGG = {
    "match_breakdown": {"matched": 48853, "unmatched": 956763, "bad_qty": 0, "duplicate": 0, "untracked": 0},
    "change_direction": {"up": 812, "down": 410, "to_zero": 191, "no_change": 47440, "unknown": 0},
}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for name, w, h in [("run-fix__desktop", 1280, 900), ("run-fix__mobile", 390, 844)]:
            context = await browser.new_context(viewport={"width": w, "height": h})

            async def h(route):
                url = route.request.url
                if "/api/run/6/aggregates" in url:
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(AGG))
                    return
                if "/api/run/6/preview" in url:
                    await route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps({"rows": [], "total": 0, "page": 1, "per_page": 50}))
                    return
                if "/api/run/6" in url:
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(RUN))
                    return
                if "/api/dev/flags" in url:
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps({"disable_apply": True}))
                    return
                await route.continue_()
            await context.route("**/api/**", h)

            page = await context.new_page()
            await page.goto(f"{BASE}/run.html?id=6", wait_until="networkidle")
            await page.wait_for_timeout(500)
            path = OUT / f"{name}.png"
            await page.screenshot(path=str(path), full_page=True)
            print(f"wrote {path.name}")
            await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
