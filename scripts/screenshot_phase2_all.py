"""Phase 2 screenshot sweep — every redesigned page at desktop + mobile."""
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

# ---- inventory preview ---------------------------------------------------
RUN_INV = {
    "id": 42, "created_at": "2026-11-23T13:54:30Z", "kind": "preview",
    "file_name": "EQ8SGBP514YEZY_daily.xlsx", "status": "succeeded",
    "pushed": 0, "user_errors": 0, "parent_run_id": None, "applied_at": None,
    "details_purged": 0,
    "counts": {"excel_rows": 68211, "matched": 61034, "will_change": 179,
               "unmatched": 7177, "bad_qty": 2, "duplicate": 3, "untracked": 1,
               "title_mismatches": 0},
    "warnings": [], "details_available": True,
    "can_apply": True, "apply_blocked_reason": "", "apply_disabled_in_dev": False,
}
AGG_INV = {
    "match_breakdown": {"matched": 61034, "unmatched": 7177, "bad_qty": 2, "duplicate": 3, "untracked": 1},
    "change_direction": {"up": 92, "down": 50, "to_zero": 37, "back_in_stock": 12, "no_change": 60843, "unknown": 0},
    "price_direction": {},
    "summary": {"items_changing": 191, "going_out_of_stock": 37, "back_in_stock": 12,
                "price_up": 0, "price_down": 0, "unmatched_skus": 7177, "big_price_jump_count": 0},
}
PREVIEW_INV_ROWS = {
    "rows": [
        {"sku": "VC6930ZZ32", "title": "VC6930ZZ32 | TRANE | 24V 2/3W Modulating ACT",
         "image_url": None, "shopify_qty": 0, "new_qty": 137, "diff": 137},
        {"sku": "P48690", "title": "P48690 | AAON | BlowerPulley BK90 x 1\"",
         "image_url": None, "shopify_qty": 8, "new_qty": 127, "diff": 119},
        {"sku": "BRG-3012", "title": "BRG-3012 | Motor Bearing 1/2\"",
         "image_url": None, "shopify_qty": 12, "new_qty": 0, "diff": -12},
    ],
    "total": 191, "page": 1, "per_page": 50,
}

# ---- price preview -------------------------------------------------------
RUN_PRICES = {
    **RUN_INV, "id": 43, "kind": "preview_prices",
    "counts": {"excel_rows": 68211, "matched": 61034, "will_change": 37,
               "unmatched": 7177, "bad_price": 0, "duplicate": 0},
}
AGG_PRICES = {
    "match_breakdown": {"matched": 61034, "unmatched": 7177, "bad_qty": 0, "duplicate": 0, "untracked": 0},
    "change_direction": {},
    "price_direction": {"up": 24, "down": 13, "no_change": 60997, "unknown": 0, "big_jump_count": 2},
    "summary": {"items_changing": 37, "going_out_of_stock": 0, "back_in_stock": 0,
                "price_up": 24, "price_down": 13, "unmatched_skus": 7177, "big_price_jump_count": 2},
}
PREVIEW_PRICE_ROWS = {
    "rows": [
        {"sku": "VC6930ZZ32", "title": "VC6930ZZ32 | TRANE | 24V ACT",
         "image_url": None, "shopify_price": "142.00", "new_price": "158.00", "diff": 16.00,
         "product_id": "x", "variant_id": "y"},
        {"sku": "P48690", "title": "P48690 | AAON | BlowerPulley",
         "image_url": None, "shopify_price": "72.00", "new_price": "68.50", "diff": -3.50,
         "product_id": "x", "variant_id": "y"},
        {"sku": "FLT-1480", "title": "FLT-1480 | Filter 16x20x1 MERV 8",
         "image_url": None, "shopify_price": "12.00", "new_price": "17.50", "diff": 5.50,
         "product_id": "x", "variant_id": "y"},
    ],
    "total": 37, "page": 1, "per_page": 50,
}

# ---- combined preview -----------------------------------------------------
RUN_BOTH = {
    **RUN_INV, "id": 44, "kind": "preview_both",
    "counts": {
        "inventory": RUN_INV["counts"],
        "prices":    RUN_PRICES["counts"],
        "has_prices": True,
        "excel_rows": 68211, "matched": 61034, "will_change": 216,
        "unmatched": 7177, "bad_qty": 2, "duplicate": 3, "untracked": 1,
        "title_mismatches": 0,
    },
}
AGG_BOTH = {
    "match_breakdown": AGG_INV["match_breakdown"],
    "change_direction": AGG_INV["change_direction"],
    "price_direction": AGG_PRICES["price_direction"],
    "summary": {"items_changing": 191 + 37, "going_out_of_stock": 37, "back_in_stock": 12,
                "price_up": 24, "price_down": 13, "unmatched_skus": 7177, "big_price_jump_count": 2},
}

# ---- apply result (success) ----------------------------------------------
RUN_APPLY_OK = {
    "id": 45, "created_at": "2026-11-23T14:00:00Z", "kind": "apply_both",
    "file_name": "EQ8SGBP514YEZY_daily.xlsx", "status": "succeeded",
    "pushed": 228, "user_errors": 0, "parent_run_id": 44, "applied_at": None,
    "details_purged": 0,
    "counts": {},  # apply-only runs rely on summary from parent
    "warnings": [], "details_available": False,
    "can_apply": False, "apply_blocked_reason": "", "apply_disabled_in_dev": False,
}

# ---- apply result (failure) ----------------------------------------------
RUN_APPLY_FAIL = {
    **RUN_APPLY_OK, "id": 46, "status": "failed", "pushed": 50, "user_errors": 11,
    "error": "Shopify API returned 429 after 3 retries. 50 rows pushed before the stop.",
}

# ---- empty history --------------------------------------------------------
RUNS_LIST_FULL = {"runs": [
    {"id": 45, "created_at": "2026-11-23T14:00:00Z", "kind": "apply_both",
     "file_name": "EQ8SGBP514YEZY_daily.xlsx", "status": "succeeded", "pushed": 228, "user_errors": 0,
     "parent_run_id": 44, "applied_at": "2026-11-23T14:00:00Z",
     "details_purged": 0, "counts": {}, "warnings": [], "details_available": False},
    {"id": 44, "created_at": "2026-11-23T13:54:30Z", "kind": "preview_both",
     "file_name": "EQ8SGBP514YEZY_daily.xlsx", "status": "succeeded", "pushed": 0, "user_errors": 0,
     "parent_run_id": None, "applied_at": "2026-11-23T14:00:00Z", "details_purged": 0,
     "counts": RUN_BOTH["counts"], "warnings": [], "details_available": True},
    {"id": 40, "created_at": "2026-11-22T14:02:51Z", "kind": "cache_refresh",
     "file_name": None, "status": "succeeded", "pushed": 0, "user_errors": 0,
     "parent_run_id": None, "applied_at": None, "details_purged": 0,
     "counts": {"sku_count": 70501, "duplicate_count": 297, "blank_sku_count": 3129, "untracked_count": 3326},
     "warnings": [], "details_available": False},
]}
RUNS_EMPTY = {"runs": []}

# ---- missing-column variant -----------------------------------------------
RUN_MISSING_PRICE = {**RUN_PRICES, "status": "failed",
                     "warnings": ["missing_price_column"],
                     "error": "Price column 'final_price' not found."}


def mk_overrides(run_id, run, agg, preview_rows, problems=None, flags=None):
    return {
        "/api/dev/flags":                 flags or FLAGS,
        f"/api/run/{run_id}/aggregates":  agg,
        f"/api/run/{run_id}/preview-prices": preview_rows,
        f"/api/run/{run_id}/preview":     preview_rows,
        f"/api/run/{run_id}/problems":    problems or {"rows": [], "total": 0, "page": 1, "per_page": 50, "truncated_kinds": [], "per_kind_cap": 500},
        f"/api/run/{run_id}":             run,
    }


async def shoot(browser, name, vp, overrides, pre=None, path="/"):
    context = await browser.new_context(viewport=VIEWPORTS[vp])

    async def handler(route):
        url = route.request.url
        # Longest-match so /api/run/x/aggregates isn't shadowed by /api/run/x
        hits = [p for p in overrides.keys() if p in url]
        if hits:
            best = max(hits, key=len)
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(overrides[best]))
            return
        await route.continue_()

    await context.route("**/api/**", handler)
    page = await context.new_page()
    await page.goto(f"{BASE}{path}", wait_until="networkidle")
    await page.wait_for_timeout(500)
    if pre:
        try:
            await pre(page)
            await page.wait_for_timeout(400)
        except Exception as e:
            print(f"  (pre failed: {e})")
    out = OUT / f"{name}__{vp}.png"
    await page.screenshot(path=str(out), full_page=True)
    print(f"wrote {out.name}")
    await context.close()


async def click_push(page):
    await page.click("#push-btn")
    await page.wait_for_selector(".modal-dialog--confirm", timeout=3000)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for vp in VIEWPORTS:
            print(f"-- {vp} --")

            # preview.html — inventory (default)
            ov = mk_overrides(42, RUN_INV, AGG_INV, PREVIEW_INV_ROWS)
            await shoot(browser, "preview-inv-default", vp, ov, path="/preview.html?id=42")

            # preview-prices.html — prices
            ov = mk_overrides(43, RUN_PRICES, AGG_PRICES, PREVIEW_PRICE_ROWS)
            await shoot(browser, "preview-prices-default", vp, ov, path="/preview-prices.html?id=43")

            # preview-prices with confirm dialog (triggers big-jump friction)
            await shoot(browser, "preview-prices-confirm", vp, ov, pre=click_push, path="/preview-prices.html?id=43")

            # preview-both.html
            ov_both = {
                "/api/dev/flags": FLAGS,
                "/api/run/44/aggregates": AGG_BOTH,
                "/api/run/44/preview-prices": PREVIEW_PRICE_ROWS,
                "/api/run/44/preview": PREVIEW_INV_ROWS,
                "/api/run/44/problems": {"rows": [], "total": 0, "page": 1, "per_page": 50, "truncated_kinds": [], "per_kind_cap": 500},
                "/api/run/44": RUN_BOTH,
            }
            await shoot(browser, "preview-both-default", vp, ov_both, path="/preview-both.html?id=44")

            # preview-prices with missing-column prompt
            ov = mk_overrides(43, RUN_MISSING_PRICE, AGG_PRICES, PREVIEW_PRICE_ROWS)
            await shoot(browser, "preview-prices-missing-column", vp, ov, path="/preview-prices.html?id=43")

            # run.html — apply_both succeeded (Result step 5)
            ov_apply = {
                "/api/dev/flags": FLAGS,
                "/api/run/45/aggregates": AGG_BOTH,
                "/api/run/44/aggregates": AGG_BOTH,
                "/api/run/45/preview": PREVIEW_INV_ROWS,
                "/api/run/44/preview": PREVIEW_INV_ROWS,
                "/api/run/45/problems": {"rows": [], "total": 0, "page": 1, "per_page": 50, "truncated_kinds": [], "per_kind_cap": 500},
                "/api/run/44/problems": {"rows": [], "total": 0, "page": 1, "per_page": 50, "truncated_kinds": [], "per_kind_cap": 500},
                "/api/run/45": RUN_APPLY_OK,
                "/api/run/44": RUN_BOTH,
            }
            await shoot(browser, "run-apply-success", vp, ov_apply, path="/run.html?id=45")

            # run.html — apply failed
            ov_apply_fail = {**ov_apply, "/api/run/46": RUN_APPLY_FAIL, "/api/run/46/aggregates": AGG_BOTH}
            await shoot(browser, "run-apply-failed", vp, ov_apply_fail, path="/run.html?id=46")

            # history.html — populated
            await shoot(browser, "history-populated", vp,
                        {"/api/dev/flags": FLAGS, "/api/runs": RUNS_LIST_FULL},
                        path="/history.html")

            # history.html — empty
            await shoot(browser, "history-empty", vp,
                        {"/api/dev/flags": FLAGS, "/api/runs": RUNS_EMPTY},
                        path="/history.html")

            # prices.html — upload shortcut
            await shoot(browser, "prices-shortcut", vp,
                        {"/api/dev/flags": FLAGS, "/api/runs": RUNS_LIST_FULL},
                        path="/prices.html")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
