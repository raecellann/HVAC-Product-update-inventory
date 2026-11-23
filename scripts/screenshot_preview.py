"""Capture the redesigned preview.html at multiple states."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots-phase2"
OUT.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:5173"

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 900},
    "mobile":  {"width":  390, "height": 844},
}

FLAGS = {"disable_apply": False, "confirm_big_oos_pct": 0.2, "confirm_big_price_jump_pct": 0.3}
FLAGS_DEV = {"disable_apply": True,  "confirm_big_oos_pct": 0.2, "confirm_big_price_jump_pct": 0.3}

RUN = {
    "id": 42,
    "created_at": "2026-11-23T13:54:30Z",
    "kind": "preview",
    "file_name": "EQ8SGBP514YEZY_daily_20261119183001.xlsx",
    "status": "succeeded",
    "pushed": 0, "user_errors": 0,
    "parent_run_id": None,
    "applied_at": None,
    "details_purged": 0,
    "counts": {"excel_rows": 68211, "matched": 61034, "will_change": 179,
               "unmatched": 7177, "bad_qty": 2, "duplicate": 3, "untracked": 1,
               "title_mismatches": 0},
    "warnings": [],
    "details_available": True,
    "can_apply": True,
    "apply_blocked_reason": "",
    "apply_disabled_in_dev": False,
}

AGG_NORMAL = {
    "match_breakdown": {"matched": 61034, "unmatched": 7177, "bad_qty": 2, "duplicate": 3, "untracked": 1},
    "change_direction": {"up": 92, "down": 50, "to_zero": 37, "back_in_stock": 12, "no_change": 60843, "unknown": 0},
    "price_direction": {},
    "summary": {"items_changing": 191, "going_out_of_stock": 37, "back_in_stock": 12,
                "price_up": 0, "price_down": 0, "unmatched_skus": 7177, "big_price_jump_count": 0},
}

AGG_FRICTION = {
    "match_breakdown": {"matched": 1000, "unmatched": 20, "bad_qty": 0, "duplicate": 0, "untracked": 0},
    "change_direction": {"up": 0, "down": 0, "to_zero": 300, "back_in_stock": 0, "no_change": 700, "unknown": 0},
    "price_direction": {},
    "summary": {"items_changing": 300, "going_out_of_stock": 300, "back_in_stock": 0,
                "price_up": 0, "price_down": 0, "unmatched_skus": 20, "big_price_jump_count": 0},
}

PREVIEW_ROWS = [
    {"sku": "VC6930ZZ32", "title": "VC6930ZZ32 | TRANE | 24V 2/3W Modulating ACT",
     "image_url": None, "shopify_qty": 0, "new_qty": 137, "diff": 137},
    {"sku": "P48690", "title": "P48690 | AAON | BlowerPulley BK90 x 1\"",
     "image_url": None, "shopify_qty": 8, "new_qty": 127, "diff": 119},
    {"sku": "4098-9714", "title": "4098-9714 | Firelite Smoke Detector",
     "image_url": None, "shopify_qty": 31, "new_qty": 148, "diff": 117},
    {"sku": "BRG-3012", "title": "BRG-3012 | Motor Bearing 1/2\"",
     "image_url": None, "shopify_qty": 12, "new_qty": 0, "diff": -12},
    {"sku": "FLT-1480", "title": "FLT-1480 | Filter 16x20x1 MERV 8",
     "image_url": None, "shopify_qty": 0, "new_qty": 0, "diff": 0},
]

PROBLEMS = {
    "rows": [
        {"kind": "unmatched",     "row": 142, "sku": "ZZ-UNKNOWN-1",  "title": "", "value_raw": "12", "reason": "No matching SKU in Shopify."},
        {"kind": "unmatched",     "row": 143, "sku": "ZZ-UNKNOWN-2",  "title": "", "value_raw": "8",  "reason": "No matching SKU in Shopify."},
        {"kind": "duplicate_sku", "row": 912, "sku": "DUP-ABC",       "title": "", "value_raw": "3",  "reason": "This SKU is used by more than one Shopify variant."},
        {"kind": "bad_qty",       "row": 44,  "sku": "BAD-123",       "title": "", "value_raw": "abc","reason": "Quantity couldn't be read (bad quantity: 'abc')."},
        {"kind": "untracked",     "row": 221, "sku": "UNTRK-789",     "title": "", "value_raw": "5",  "reason": "Shopify isn't tracking inventory for this product."},
    ],
    "total": 5, "page": 1, "per_page": 50,
    "truncated_kinds": [], "per_kind_cap": 500,
}


def preview_list(rows_subset=None, total=None):
    rs = rows_subset if rows_subset is not None else PREVIEW_ROWS
    return {"rows": rs, "total": total if total is not None else len(PREVIEW_ROWS),
            "page": 1, "per_page": 50}


async def shoot(browser, name, vp, overrides, pre=None, path="/preview.html?id=42"):
    context = await browser.new_context(viewport=VIEWPORTS[vp])

    async def handler(route):
        url = route.request.url
        for pat, body in overrides.items():
            if pat in url:
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                return
        await route.continue_()

    await context.route("**/api/**", handler)
    page = await context.new_page()
    await page.goto(f"{BASE}{path}", wait_until="networkidle")
    await page.wait_for_timeout(500)
    if pre:
        await pre(page)
        await page.wait_for_timeout(400)
    out = OUT / f"{name}__{vp}.png"
    await page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out.name}")
    await context.close()


async def click_push(page):
    await page.click("#push-btn")
    await page.wait_for_selector(".modal-dialog--confirm", timeout=3000)


async def click_tile_oos(page):
    await page.click('[data-filter="out_of_stock"]')


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for vp in VIEWPORTS:
            print(f"-- {vp} --")

            base_overrides = {
                "/api/dev/flags":             FLAGS,
                "/api/run/42/aggregates":     AGG_NORMAL,
                "/api/run/42/preview":        preview_list(),
                "/api/run/42/problems.csv":   {},  # unused
                "/api/run/42/problems":       PROBLEMS,
                "/api/run/42":                RUN,
            }

            # 01 — default
            await shoot(browser, "preview-01-default", vp, base_overrides)

            # 02 — tile clicked (out of stock filter active)
            await shoot(browser, "preview-02-filter-oos", vp, base_overrides, pre=click_tile_oos)

            # 03 — confirm dialog open (normal)
            await shoot(browser, "preview-03-confirm", vp, base_overrides, pre=click_push)

            # 04 — confirm dialog with PUSH friction (high OOS ratio)
            friction = {**base_overrides, "/api/run/42/aggregates": AGG_FRICTION,
                         "/api/run/42": {**RUN, "counts": {**RUN["counts"], "matched": 1000, "will_change": 300, "unmatched": 20, "bad_qty": 0, "duplicate": 0, "untracked": 0}}}
            await shoot(browser, "preview-04-confirm-friction", vp, friction, pre=click_push)

            # 05 — dev mode (push disabled)
            dev = {**base_overrides, "/api/dev/flags": FLAGS_DEV,
                    "/api/run/42": {**RUN, "apply_disabled_in_dev": True}}
            await shoot(browser, "preview-05-dev-disabled", vp, dev)

            # 06 — missing price column (shouldn't apply to inventory preview;
            # still worth capturing the alert behaviour when a generic warning arrives).
            with_warning = {**base_overrides,
                            "/api/run/42": {**RUN, "warnings": ["missing_price_column"]}}
            await shoot(browser, "preview-06-with-warning", vp, with_warning)

            # 07 — empty state (no changes)
            empty_agg = {**AGG_NORMAL,
                         "change_direction": {"up":0,"down":0,"to_zero":0,"back_in_stock":0,"no_change":61034,"unknown":0},
                         "summary": {**AGG_NORMAL["summary"], "items_changing": 0, "going_out_of_stock": 0, "back_in_stock": 0}}
            empty = {**base_overrides,
                     "/api/run/42/aggregates": empty_agg,
                     "/api/run/42/preview":   preview_list(rows_subset=[], total=0)}
            await shoot(browser, "preview-07-no-changes", vp, empty)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
