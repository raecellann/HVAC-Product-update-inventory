"""Check what SKU 476-0788-644 looks like in base vs priced Excel."""
from pathlib import Path
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "3HVLUJ76N6EH1L.daily.20261115183002.xlsx"
PRICED = ROOT / "3HVLUJ76N6EH1L_daily_20261115183002_priced.xlsx"

TARGETS = {
    "476-0788-644",
    "952-2210-932",
    "950-3000-941",
}


def dump(path, label):
    print(f"\n=== {label}: {path.name} ===")
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    print("header:", header)
    try:
        item_idx = header.index("Item")
    except ValueError:
        item_idx = None
        for i, h in enumerate(header):
            if h and str(h).strip().lower() in {"item", "sku", "item code"}:
                item_idx = i
                break
    for row in rows:
        if not row or item_idx is None:
            continue
        v = row[item_idx]
        if v and str(v).strip() in TARGETS:
            print(row)
    wb.close()


dump(BASE, "BASE")
dump(PRICED, "PRICED")
