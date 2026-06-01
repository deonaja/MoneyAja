"""Klasifikasi kategori berbasis aturan (§5 CLAUDE.md). TANPA AI.

URUTAN = PRIORITAS. Aturan paling atas menang duluan. Aturan berbasis `jenis`
dicek SEBELUM aturan berbasis `merchant` — supaya transaksi sistem (Biaya, Bunga,
Transfer, Ewallet) terkunci sebelum tebakan merchant ikut campur (mengatasi jebakan
"Pembayaran" dobel makna, §6 trap #2).

Pencocokan keyword: **batas kata** (\\bKW\\b), case-insensitive — BUKAN substring polos.
Alasan (bug nyata di data April): substring "ES" salah menangkap "SHOES CLEAN", dan
"GOR" salah menangkap "BATAGOR"/"NASGOR". Batas kata memperbaiki ini tanpa magic number.

Daftar keyword sengaja sebagai data biasa (list) agar MUDAH ditambah (§5: "akan tumbuh").
"""

from __future__ import annotations

import re

# --- Aturan berbasis MERCHANT (keyword), urut prioritas #6..#12 (§5) ---
# Tambah kata baru cukup di list ini. Kategori baru = tambah entri (kategori, [kw...]).
MERCHANT_RULES: list[tuple[str, list[str]]] = [
    ("Tagihan & token", ["TOKEN LISTRIK", "PLN"]),
    ("Makan & minum", [
        "WARUNG", "RM", "NASI", "BAKSO", "AYAM", "BUBUR", "NASGOR", "WARTEG",
        "KANTIN", "ES", "BATAGOR", "COFFEE", "KOPI", "TEH", "DRINK", "FNB",
        "ANGKRINGAN",
        # --- tambahan keyword umum ---
        "WARKOP", "RESTORAN",
        "FC",  # konvensi QRIS Indonesia: "<nama> FC" lazimnya = fried chicken.
               # Sedikit ambigu — pantau kalau ada "FC" non-makanan, hapus dari sini.
        # --- makanan/minuman dari data nyata (jelas dari nama) ---
        "JUICE", "PECEL", "MENDOAN", "MARTABAK", "ROTI", "GUDEG", "KIMBAP",
        "COFFE",  # varian ejaan dari COFFEE (mis. "...COFFE MEKARWANG")
        "MCD",    # singkatan McDonald's
    ]),
    ("Belanja online", ["SHOPEE", "TOKOPEDIA", "LAZADA", "TIKTOK"]),
    ("Belanja harian", [
        "SUPERINDO", "INDOMARET", "ALFA", "MIDI", "ALGO MC88",
        # --- minimarket dari data nyata ---
        "ALFAMART", "YOMART", "MART",
        "IDM",  # kode QRIS Indomaret (mis. "IDM TBOC ..."). Singkatan — pantau false-match.
    ]),
    ("Jasa", ["LAUNDRY", "CAR WASH", "CUCI", "S'TEAM", "SHOES CLEAN"]),
    ("Olahraga & hobi", ["GOR", "BADMINTON", "VAPESTORE", "GAMING"]),
    ("Pulsa & data", ["MYTELKOMSEL", "TELKOMSEL", "XL", "INDOSAT"]),
    # --- kategori baru dari data 4 bulan ---
    ("Transportasi", ["KAI", "TRAVELOKA"]),  # KAI = Kereta Api Indonesia
    # "UNIVERSITY"/"PENDIDIKAN" — JANGAN pakai "TELKOM" (bentrok MYTELKOMSEL/Pulsa).
    ("Pendidikan", ["UNIVERSITY", "PENDIDIKAN"]),
]

FALLBACK = "Lainnya"


def _has_kw(text: str, keywords: list[str]) -> bool:
    """True jika salah satu keyword muncul sebagai KATA UTUH (case-insensitive)."""
    up = text.upper()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw.upper()) + r"\b", up):
            return True
    return False


def classify(tx) -> str:
    """Kembalikan kategori untuk 1 transaksi (§5). Tidak mengubah tx."""
    jenis = (tx.jenis or "").lower()
    merchant = tx.merchant or ""

    # #1 Pemasukan — kiriman ortu (di-set di Fase 5/§7; sebelum itu selalu False)
    if tx.is_kiriman_ortu:
        return "Pemasukan"
    # #2 Biaya bank — jenis = Biaya
    if jenis.startswith("biaya"):
        return "Biaya bank"
    # #3 Bunga — jenis = Lainnya + teks "Bunga"
    if jenis == "lainnya" and _has_kw(merchant, ["BUNGA"]):
        return "Bunga"
    # #4 Transfer — jenis = Transfer (dan bukan kiriman ortu; sudah ditangani #1)
    if jenis == "transfer":
        return "Transfer"
    # #5 Top-up e-wallet — jenis = Ewallet ATAU teks "TOP UP"
    if jenis == "ewallet" or _has_kw(merchant, ["TOP UP"]):
        return "Top-up e-wallet"
    # #5b Tarik tunai — jenis = Tarik Tunai (tarik ATM; bukan konsumsi, dipisah)
    if jenis == "tarik tunai":
        return "Tarik tunai"
    # #6..#12 — berbasis merchant
    for kategori, kws in MERCHANT_RULES:
        if _has_kw(merchant, kws):
            return kategori
    # fallback — kandidat AI (Fase 7)
    return FALLBACK


def apply(txs: list, only_empty: bool = True) -> int:
    """Isi `kategori` tiap transaksi. only_empty=True → hormati kategori yang sudah
    terisi (edit manual / hasil sebelumnya, §8). Kembalikan jumlah yang diisi."""
    n = 0
    for tx in txs:
        if only_empty and tx.kategori:
            continue
        tx.kategori = classify(tx)
        n += 1
    return n


if __name__ == "__main__":
    import sys
    import json
    from collections import Counter

    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, "src") if "src" not in sys.path else None

    from store import load_store

    txs = load_store()
    if not txs:
        print("Store kosong. Jalankan `python src/store.py` dulu.")
        sys.exit(0)

    # klasifikasi semua (overwrite) hanya untuk pratinjau — tidak menyimpan
    for tx in txs:
        tx.kategori = classify(tx)

    dist = Counter(t.kategori for t in txs)
    print("=== Distribusi kategori (preview, tidak disimpan) ===")
    for kat, c in dist.most_common():
        print(f"  {c:>3}  {kat}")
    print(f"  ---  total {len(txs)}")
    print()
    lainnya = [t for t in txs if t.kategori == FALLBACK]
    print(f"=== {len(lainnya)} jatuh ke 'Lainnya' (kandidat AI / tambah keyword) ===")
    for t in lainnya:
        print(f"  {t.tanggal} {t.jenis:<16} {t.merchant}")
