"""Penyimpanan + penjaga duplikat (§6 CLAUDE.md).

Store kanonik = satu file JSON (data/transactions.json) berisi list transaksi.
JSON dipilih sebagai "bentuk netral" data antara (§9): otak (parser) terpisah dari
wajah (Sheet/dashboard). Sheet/CSV nanti hanya export turunan dari sini.

Penjaga duplikat: tiap transaksi punya `id` (hash tanggal+jam+nominal+saldo, §6).
Saat menelan e-statement yang periodenya overlap, baris dengan `id` yang SUDAH ADA
di-skip. Penting: record lama TIDAK ditimpa — jadi `kategori` dan `catatan` (tag
manual user, §8) tetap aman walau file overlap dikirim ulang.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from schema import Transaction

DEFAULT_STORE = "data/transactions.json"


@dataclass
class MergeStats:
    ditambah: int
    duplikat: int
    total: int


def load_store(path: str = DEFAULT_STORE) -> list[Transaction]:
    """Muat store JSON. Kembalikan list kosong jika belum ada."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [Transaction.from_dict(d) for d in raw]


def save_store(txs: list[Transaction], path: str = DEFAULT_STORE) -> None:
    """Tulis store JSON (urut kronologis untuk tampilan stabil)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ordered = _sort_for_display(txs)
    data = [t.to_dict() for t in ordered]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sort_for_display(txs: list[Transaction]) -> list[Transaction]:
    """Urut (tanggal, jam) untuk store/tampilan. Sort STABIL: transaksi ber-jam sama
    mempertahankan urutan masuknya (= urutan PDF asli), jadi tidak mengacak."""
    return sorted(txs, key=lambda t: (t.tanggal, t.jam))


def merge(existing: list[Transaction],
          incoming: list[Transaction]) -> tuple[list[Transaction], MergeStats]:
    """Gabung `incoming` ke `existing`, skip id yang sudah ada (§6).

    Record lama menang (tidak ditimpa) → kategori/catatan manual aman.
    """
    seen = {t.id for t in existing}
    merged = list(existing)
    ditambah = duplikat = 0
    for tx in incoming:
        if tx.id in seen:
            duplikat += 1
            continue
        merged.append(tx)
        seen.add(tx.id)
        ditambah += 1
    return merged, MergeStats(ditambah=ditambah, duplikat=duplikat, total=len(merged))


def ingest(transactions: list[Transaction],
           path: str = DEFAULT_STORE) -> MergeStats:
    """Telan list transaksi (hasil parser) ke store: klasifikasi → merge → save.

    Urutan: deteksi kiriman ortu (§7) → klasifikasi (§5) → merge → save.
    Deteksi DULU agar kiriman ortu jatuh ke kategori 'Pemasukan' (aturan §5 #1).
    Klasifikasi hanya untuk transaksi baru yang `kategori`-nya kosong;
    edit manual record lama tetap aman karena dedup tidak menimpa (§8).
    """
    from classify import apply as classify_apply
    from income import apply_detection

    apply_detection(transactions)
    classify_apply(transactions, only_empty=True)
    existing = load_store(path)
    merged, stats = merge(existing, transactions)
    save_store(merged, path)
    return stats


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import glob

    from parser import parse

    cfg = json.load(open("config.json", encoding="utf-8"))
    # default: PDF pertama di samples/ (tanpa hardcode nama file = tanpa bocor no. rekening)
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else \
        next(iter(sorted(glob.glob("samples/*.pdf"))), None)
    if not pdf_path:
        print("Pakai: python src/store.py <file.pdf>   (atau taruh PDF di samples/)")
        sys.exit(1)
    store_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_STORE

    res = parse(pdf_path, password=cfg.get("pdf_password"))

    if res.warnings:
        print(f"=== {len(res.warnings)} WARNING dari parser ===")
        for w in res.warnings:
            print(" -", w)
        print()

    stats = ingest(res.transactions, store_path)
    print(f"File         : {pdf_path}")
    print(f"Diparse      : {len(res.transactions)} transaksi")
    print(f"Ditambah     : {stats.ditambah}")
    print(f"Duplikat (skip): {stats.duplikat}")
    print(f"Total di store: {stats.total}")
    print(f"Store         : {store_path}")
