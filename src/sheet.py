"""Sinkronisasi store JSON → Google Sheet (§9 CLAUDE.md).

"Wajah" data. Otak (parser/store) tetap terpisah — modul ini hanya menampilkan.

Sinkron menghormati edit manual (§8): kolom `catatan` yang KAMU ketik di Sheet TIDAK
ditimpa saat sync ulang (di-baca balik per `id`, lalu dipertahankan). Field lain
(nominal, kategori hasil aturan, dst) selalu diperbarui dari store.

Butuh (semua di config.json — gitignored, jadi kredensial tidak ke-commit):
    "google_sa_json": "path/ke/service-account.json",
    "sheet_id": "<id dari URL Google Sheet>"

Setup service account: lihat panduan di README / chat.
"""

from __future__ import annotations

import json
import os

import gspread
from google.oauth2.service_account import Credentials

from schema import COLUMN_ORDER

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_STORE = "data/transactions.json"
DEFAULT_WORKSHEET = "Transaksi"


def _client(sa_json: str):
    if not os.path.exists(sa_json):
        raise FileNotFoundError(
            f"File service account tidak ada: {sa_json}\n"
            "Cek 'google_sa_json' di config.json (lihat panduan setup)."
        )
    creds = Credentials.from_service_account_file(sa_json, scopes=SCOPES)
    return gspread.authorize(creds)


def _load_rows(store_path: str) -> list[dict]:
    with open(store_path, encoding="utf-8") as f:
        return json.load(f)


def sync(store_path: str = DEFAULT_STORE, *, sa_json: str, sheet_id: str,
         worksheet: str = DEFAULT_WORKSHEET) -> int:
    """Tulis seluruh store ke Sheet. Pertahankan `catatan` yang diedit di Sheet (§8).

    Return: jumlah baris transaksi yang ditulis.
    """
    rows = _load_rows(store_path)

    gc = _client(sa_json)
    try:
        sh = gc.open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound:
        raise SystemExit(
            f"Sheet id '{sheet_id}' tidak ditemukan / belum di-share ke service account.\n"
            "Pastikan: (1) sheet_id benar, (2) Sheet sudah di-Share ke email service account "
            "(client_email di file JSON) sebagai Editor."
        )

    try:
        ws = sh.worksheet(worksheet)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet, rows=len(rows) + 10,
                              cols=len(COLUMN_ORDER))

    # Baca balik `catatan` yang sudah diketik di Sheet (per id) → jangan ditimpa (§8).
    catatan_sheet: dict[str, str] = {}
    try:
        for r in ws.get_all_records():
            rid = str(r.get("id", "")).strip()
            note = str(r.get("catatan", "")).strip()
            if rid and note:
                catatan_sheet[rid] = note
    except Exception:
        pass  # sheet kosong / belum ada header → tidak ada yang dipertahankan

    # Susun baris: data dari store, tapi catatan dari Sheet menang bila ada.
    matrix = [COLUMN_ORDER]
    for d in rows:
        if catatan_sheet.get(d["id"]) and not d.get("catatan"):
            d = {**d, "catatan": catatan_sheet[d["id"]]}
        matrix.append([d.get(c, "") for c in COLUMN_ORDER])

    ws.clear()
    ws.update(values=matrix, range_name="A1", value_input_option="USER_ENTERED")
    # baris header tebal + freeze
    try:
        ws.freeze(rows=1)
        ws.format("A1:Z1", {"textFormat": {"bold": True}})
    except Exception:
        pass

    return len(rows)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    cfg = json.load(open("config.json", encoding="utf-8"))

    sa_json = cfg.get("google_sa_json")
    sheet_id = cfg.get("sheet_id")
    if not sa_json or not sheet_id:
        print("Belum dikonfigurasi. Isi di config.json:")
        print('  "google_sa_json": "path/ke/service-account.json",')
        print('  "sheet_id": "<id dari URL Google Sheet>"')
        print("Lihat panduan setup (README / chat).")
        sys.exit(1)

    n = sync(sa_json=sa_json, sheet_id=sheet_id)
    print(f"✅ {n} transaksi disinkronkan ke Google Sheet (worksheet '{DEFAULT_WORKSHEET}').")
