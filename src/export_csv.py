"""Export store JSON → CSV netral (§9 CLAUDE.md).

CSV = "bentuk netral" antara otak (parser/store) dan wajah (Sheet/Excel/dashboard).
Pakai modul csv standar → quoting otomatis untuk field bermasalah, mengatasi §6 #1
(merchant berkoma seperti "AYAM GORENG CJDW, JLN SUK" tidak merusak kolom).

Encoding utf-8-sig (BOM) agar Excel membuka UTF-8 dengan benar (nama Indonesia aman).
"""

from __future__ import annotations

import csv
import json
import os

from schema import COLUMN_ORDER

DEFAULT_STORE = "data/transactions.json"
DEFAULT_CSV = "output/transactions.csv"


def export_csv(store_path: str = DEFAULT_STORE, csv_path: str = DEFAULT_CSV) -> int:
    with open(store_path, encoding="utf-8") as f:
        rows = json.load(f)

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMN_ORDER, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return len(rows)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    store_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STORE
    csv_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CSV
    n = export_csv(store_path, csv_path)
    print(f"Export {n} transaksi → {csv_path}")
