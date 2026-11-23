"""Read-only profile of the supplier priced Excel.

Reports distribution of cost tiers, free_shipping flags, shipping_fee
values, and shows sample rows from each tier. No Shopify calls.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "EQ8SGBP514YEZY_daily_20261119183001_priced.xlsx"


def q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main() -> None:
    wb = load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["Daily Inventory"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header)}
    print("Sheet columns:", list(header))
    print()

    total = 0
    under150 = 0
    over150 = 0
    fs_yes = 0
    fs_no = 0
    fs_blank = 0
    sfee_counts: dict[str, int] = {}
    under_sample = []
    over_sample = []
    truck_sample = []
    exact_markup_count = 0
    nonexact_markup_count = 0

    for r in rows:
        if r is None:
            continue
        item = r[idx["Item"]]
        cost = r[idx["sell_price"]]
        if item is None or cost is None:
            continue
        try:
            cost_d = Decimal(str(cost))
        except Exception:
            continue

        total += 1
        pm = r[idx["price_plus_markup"]]
        fs = r[idx["free_shipping"]]
        sf = r[idx["shipping_fee"]]
        fp = r[idx["final_price"]]
        tr = r[idx["Truck Shipment"]]

        if fs == "Yes":
            fs_yes += 1
        elif fs == "No":
            fs_no += 1
        else:
            fs_blank += 1

        # Check: is price_plus_markup == cost * 1.30 exactly (to 2dp)?
        if pm is not None:
            try:
                pm_d = Decimal(str(pm))
                expected = q2(cost_d * Decimal("1.30"))
                if q2(pm_d) == expected:
                    exact_markup_count += 1
                else:
                    nonexact_markup_count += 1
            except Exception:
                pass

        if cost_d < Decimal("150"):
            under150 += 1
            if len(under_sample) < 5:
                under_sample.append((item, cost, pm, fs, sf, fp))
        else:
            over150 += 1
            if len(over_sample) < 8:
                over_sample.append((item, cost, pm, fs, sf, fp, tr))

        if sf is not None:
            key = str(sf)
            sfee_counts[key] = sfee_counts.get(key, 0) + 1

        if tr and str(tr).strip() and len(truck_sample) < 5:
            truck_sample.append((item, cost, pm, fs, sf, fp, tr))

    wb.close()

    print(f"Total priced rows: {total}")
    print(f"  cost <  $150 : {under150}")
    print(f"  cost >= $150 : {over150}")
    print()
    print(f"free_shipping = Yes   : {fs_yes}")
    print(f"free_shipping = No    : {fs_no}")
    print(f"free_shipping = blank : {fs_blank}")
    print()
    print(f"price_plus_markup matches cost*1.30 (2dp): {exact_markup_count}")
    print(f"price_plus_markup mismatches             : {nonexact_markup_count}")
    print()
    print(f"Distinct shipping_fee values: {len(sfee_counts)}")
    print("Top 15 shipping_fee buckets (value, count):")
    for v, c in sorted(sfee_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"  {v!r:20s} -> {c}")
    print()
    print("--- Sample: cost < $150 (item, cost, price_plus_markup, free_ship, shipping_fee, final_price)")
    for s in under_sample:
        print(" ", s)
    print()
    print("--- Sample: cost >= $150 (item, cost, pm, fs, sf, fp, truck)")
    for s in over_sample:
        print(" ", s)
    print()
    print("--- Sample: truck shipment")
    for s in truck_sample:
        print(" ", s)


if __name__ == "__main__":
    main()
