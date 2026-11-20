"""Pick 10 items with a correct 30% margin that are NOT yet updated on Shopify.

Cross-references the priced Excel against the latest price_diff CSV so the
chosen items are both (a) marked up at exactly 30% from sell_price to
final_price, and (b) still showing a mismatch against Shopify's current price.
"""
from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parent.parent
PRICED_XLSX = ROOT / "3HVLUJ76N6EH1L_daily_20261115183002_priced.xlsx"
DIFF_CSV = ROOT / "logs" / "price_diff_2026-11-20_040141.csv"
OUT_XLSX = ROOT / "test_items_30pct_not_synced.xlsx"

TARGET_MARKUP = Decimal("1.30")
WANT = 10


def q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_diff(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            sku = (row.get("sku") or "").strip()
            if not sku:
                continue
            try:
                out[sku.lower()] = {
                    "current_price": Decimal(row["current_price"]),
                    "new_price": Decimal(row["new_price"]),
                }
            except Exception:
                continue
    return out


def main() -> None:
    diff = load_diff(DIFF_CSV)
    print(f"Loaded {len(diff)} mismatched SKUs from diff CSV")

    wb = load_workbook(PRICED_XLSX, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    idx = {name: i for i, name in enumerate(header)}

    picks: list[dict] = []
    for row in rows_iter:
        if not row:
            continue
        sku = row[idx["Item"]]
        if not sku:
            continue
        sku_s = str(sku).strip()
        key = sku_s.lower()
        if key not in diff:
            continue

        sell = row[idx["sell_price"]]
        final = row[idx["final_price"]]
        if sell is None or final is None:
            continue
        try:
            sell_d = Decimal(str(sell))
            final_d = Decimal(str(final))
        except Exception:
            continue
        if sell_d <= 0:
            continue

        expected = q2(sell_d * TARGET_MARKUP)
        if expected != q2(final_d):
            continue

        # Shopify price must still differ from the new final price
        d = diff[key]
        if q2(d["new_price"]) != q2(final_d):
            # Diff was computed from the same Excel, but be defensive
            continue
        if q2(d["current_price"]) == q2(d["new_price"]):
            continue

        picks.append({
            "sku": sku_s,
            "manu_name": row[idx["Manu name"]],
            "description": row[idx["description"]],
            "sell_price": sell_d,
            "final_price": final_d,
            "markup_pct": q2((final_d / sell_d - 1) * 100),
            "shopify_current_price": d["current_price"],
            "price_diff": q2(final_d - d["current_price"]),
        })

        if len(picks) >= WANT:
            break

    wb.close()

    print(f"Picked {len(picks)} items")

    out_wb = Workbook()
    out_ws = out_wb.active
    out_ws.title = "Test items"
    cols = [
        "SKU",
        "Manufacturer",
        "Description",
        "Sell price (cost)",
        "Final price (30% markup)",
        "Markup %",
        "Shopify current price",
        "Price diff (final - shopify)",
    ]
    out_ws.append(cols)
    for p in picks:
        out_ws.append([
            p["sku"],
            p["manu_name"],
            p["description"],
            float(p["sell_price"]),
            float(p["final_price"]),
            float(p["markup_pct"]),
            float(p["shopify_current_price"]),
            float(p["price_diff"]),
        ])
    out_wb.save(OUT_XLSX)
    print(f"Wrote {OUT_XLSX}")

    # Also print to stdout so user can see it immediately
    print()
    print(f"{'SKU':<22} {'sell':>10} {'final':>10} {'mkup%':>7} {'shopify':>10} {'diff':>10}")
    for p in picks:
        print(
            f"{p['sku']:<22} "
            f"{p['sell_price']:>10} "
            f"{p['final_price']:>10} "
            f"{p['markup_pct']:>7} "
            f"{p['shopify_current_price']:>10} "
            f"{p['price_diff']:>10}"
        )


if __name__ == "__main__":
    main()
