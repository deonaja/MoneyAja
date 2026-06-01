"""Parser PDF e-statement wondr (BNI) → list Transaction (§3, §4, §6 CLAUDE.md).

Deterministik. Tidak menebak. Tidak ada AI di sini.

Struktur sumber (setelah extract_text pdfplumber, tiap transaksi = 3 baris):
    DD Mon YYYY <jenis>
    <nominal> <saldo>
    HH:MM:SS WIB <merchant>

Tahapan:
    1. Kumpulkan baris semua halaman, buang elemen berulang (header/footer/kop).
    2. Kelompokkan jadi transaksi mentah (anchor = baris tanggal).
    3. Validasi saldo berantai (§6) — tandai anomali, jangan tolak.
    4. Gabung biaya admin "nempel" ke induk (§6) — hanya jika 3 syarat terpenuhi.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pdfplumber

from schema import Transaction

# --- regex inti ---
RE_DATE_JENIS = re.compile(r"^(\d{2}) (\w{3}) (\d{4})\s+(.+)$")
RE_NOMINAL_SALDO = re.compile(r"^([+-][\d,]+)\s+([\d,]+)$")
RE_TIME_MERCHANT = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+WIB\s*(.*)$")
RE_SALDO_AWAL = re.compile(r"^Saldo Awal\s+([\d,]+)$")
RE_SALDO_AKHIR = re.compile(r"^Saldo Akhir\s+([\d,]+)$")

# Bulan: dukung singkatan Indonesia & Inggris (PDF wondr pakai "Apr", dll).
BULAN = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Mei": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Agu": 8, "Ags": 8, "Sep": 9, "Oct": 10, "Okt": 10,
    "Nov": 11, "Dec": 12, "Des": 12,
}

# Baris sampah yang harus dibuang (cocokkan sebagai prefix/substring).
NOISE_PREFIX = (
    "Laporan Mutasi Rekening",
    "Periode:",
    "Tanggal & Waktu",
    "PT Bank Negara Indonesia",
    "peserta penjaminan",
    "Informasi Lainnya",
    "Saldo Awal Total",  # baris label ringkasan hal.1
)
RE_PAGE_FOOTER = re.compile(r"^\d+ dari \d+$")  # "1 dari 5"
# baris angka ringkasan hal.1, format: "<saldo_awal> +<pemasukan> -<pengeluaran> <saldo_akhir>"
# contoh bentuk: "1,000,000 +500,000 -300,000 1,200,000"
RE_SUMMARY_NUMS = re.compile(r"^([\d,]+)\s+\+([\d,]+)\s+-([\d,]+)\s+([\d,]+)$")
RE_NUMBERED_NOTE = re.compile(r"^\d+\.\s")  # "1. Apabila ..."


def _to_int(s: str) -> int:
    """'+1,200,000' -> 1200000 ; '-45,000' -> -45000 ; '1,234,567' -> 1234567."""
    return int(s.replace(",", "").replace("+", ""))


def _to_iso(dd: str, mon: str, yyyy: str) -> str:
    return f"{yyyy}-{BULAN[mon]:02d}-{int(dd):02d}"


@dataclass
class ParseResult:
    transactions: list
    saldo_awal: int | None
    saldo_akhir: int | None
    warnings: list
    total_pemasukan: int | None = None   # dari ringkasan cetak BNI (hal.1)
    total_pengeluaran: int | None = None  # dari ringkasan cetak BNI (hal.1)


def _clean_lines(pdf) -> tuple[list[str], dict]:
    """Ekstrak teks semua halaman, buang baris sampah berulang (§10).

    Sekalian tangkap jangkar (Saldo Awal/Akhir) + total cetak BNI (Total Pemasukan/
    Pengeluaran dari baris ringkasan hal.1) lalu buang barisnya, dan STOP membaca
    setelah blok "Informasi Lainnya" (boilerplate footer halaman akhir).
    """
    out: list[str] = []
    anchors = {
        "saldo_awal": None, "saldo_akhir": None,
        "total_pemasukan": None, "total_pengeluaran": None,
    }
    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw in text.split("\n"):
            line = raw.strip()
            if not line:
                continue
            # blok "Informasi Lainnya" + seterusnya = boilerplate, berhenti total.
            if line.startswith("Informasi Lainnya"):
                return out, anchors
            m = RE_SALDO_AWAL.match(line)
            if m:
                if anchors["saldo_awal"] is None:
                    anchors["saldo_awal"] = _to_int(m.group(1))
                continue  # baris jangkar, bukan transaksi
            m = RE_SALDO_AKHIR.match(line)
            if m:
                anchors["saldo_akhir"] = _to_int(m.group(1))
                continue
            m = RE_SUMMARY_NUMS.match(line)
            if m:  # ringkasan: saldo_awal | +pemasukan | -pengeluaran | saldo_akhir
                anchors["total_pemasukan"] = _to_int(m.group(2))
                anchors["total_pengeluaran"] = _to_int(m.group(3))
                continue
            if line.startswith(NOISE_PREFIX):
                continue
            if RE_PAGE_FOOTER.match(line):
                continue
            if RE_NUMBERED_NOTE.match(line):
                continue
            # baris alamat/nasabah hal.1 (mengandung nomor rekening / "Kantor Cabang")
            if "Kantor Cabang" in line or "TAPLUS" in line:
                continue
            if line.startswith(("JALAN", "UTARA,")):
                continue
            out.append(line)
    return out, anchors


def _group_transactions(lines: list[str]):
    """Kelompokkan baris jadi blok transaksi (anchor = baris tanggal).

    Return: list (tanggal_iso, jenis, blok_baris_setelahnya).
    """
    blocks = []
    n = len(lines)
    # cari indeks semua baris tanggal valid (bulan dikenal)
    date_idx = [
        k for k, ln in enumerate(lines)
        if (m := RE_DATE_JENIS.match(ln)) and m.group(2) in BULAN
    ]
    for pos, start in enumerate(date_idx):
        end = date_idx[pos + 1] if pos + 1 < len(date_idx) else n
        dd, mon, yyyy, jenis = RE_DATE_JENIS.match(lines[start]).groups()
        tanggal = _to_iso(dd, mon, yyyy)
        body = lines[start + 1:end]
        blocks.append((tanggal, jenis.strip(), body))
    return blocks


def _build_transaction(tanggal: str, jenis: str, body: list[str]) -> Transaction | None:
    """Dari blok baris bangun 1 Transaction. Toleran urutan & merchant multi-baris (§10)."""
    nominal = saldo = None
    jam = ""
    merchant_parts: list[str] = []

    for line in body:
        mns = RE_NOMINAL_SALDO.match(line)
        if mns and nominal is None:
            nominal = _to_int(mns.group(1))
            saldo = _to_int(mns.group(2))
            continue
        tm = RE_TIME_MERCHANT.match(line)
        if tm and not jam:
            jam = tm.group(1)
            if tm.group(2).strip():
                merchant_parts.append(tm.group(2).strip())
            continue
        # baris sisa = lanjutan nama merchant (merchant >2 baris, §10)
        merchant_parts.append(line)

    if nominal is None or saldo is None or not jam:
        return None  # blok tak lengkap → dilewati (pemanggil catat warning)

    return Transaction(
        tanggal=tanggal,
        jam=jam,
        jenis=jenis,
        merchant=" ".join(merchant_parts).strip(),
        nominal=nominal,
        saldo=saldo,
    )


def _validate_saldo_chain(txs: list[Transaction], saldo_awal: int | None,
                          warnings: list) -> None:
    """Cek saldo_sebelumnya + nominal == saldo_baris_ini (§6). Tandai, jangan tolak."""
    if saldo_awal is None:
        warnings.append("Saldo Awal tidak ditemukan — validasi berantai dilewati.")
        prev = None
    else:
        prev = saldo_awal
    for tx in txs:
        if prev is not None:
            expected = prev + tx.nominal
            if expected != tx.saldo:
                tx.anomali_saldo = True
                warnings.append(
                    f"Anomali saldo di {tx.tanggal} {tx.jam} ({tx.merchant}): "
                    f"harusnya {expected:,} tapi tercatat {tx.saldo:,} "
                    f"(selisih {tx.saldo - expected:+,})."
                )
        prev = tx.saldo


def _merge_biaya_nempel(txs: list[Transaction]) -> list[Transaction]:
    """Gabung baris Biaya yang nempel ke induk (§6). 3 syarat WAJIB semua terpenuhi:
       (a) jam sama persis dgn transaksi tepat di atasnya,
       (b) transaksi di atasnya pengeluaran (arah=keluar),
       (c) baris Biaya sendiri pengeluaran kecil.
       Jika tidak ketiganya → Biaya berdiri sendiri (tetap tercatat).
    """
    result: list[Transaction] = []
    for tx in txs:
        is_biaya = tx.jenis.lower().startswith("biaya")
        if (
            is_biaya
            and result
            and tx.arah == "keluar"          # (c)
            and result[-1].arah == "keluar"  # (b)
            and result[-1].jam == tx.jam     # (a)
        ):
            # id induk TIDAK berubah: biaya_admin bukan bagian hash (§6), jadi
            # dedup tetap stabil walau fee diserap. Sengaja tidak recompute id.
            result[-1].biaya_admin += abs(tx.nominal)
            continue  # baris Biaya diserap ke induk, tidak ditambahkan terpisah
        result.append(tx)
    return result


def parse(path: str, password: str | None = None) -> ParseResult:
    warnings: list = []
    with pdfplumber.open(path, password=password) as pdf:
        lines, anchors = _clean_lines(pdf)
    saldo_awal = anchors["saldo_awal"]
    saldo_akhir = anchors["saldo_akhir"]

    blocks = _group_transactions(lines)

    txs: list[Transaction] = []
    for tanggal, jenis, body in blocks:
        tx = _build_transaction(tanggal, jenis, body)
        if tx is None:
            warnings.append(f"Blok transaksi tak lengkap dilewati: {tanggal} {jenis}")
            continue
        txs.append(tx)

    # Validasi pakai urutan asli PDF (otoritatif). TIDAK di-sort: transaksi ber-jam
    # sama (mis. dua 00:00:00) urutannya hanya benar menurut PDF; sort bisa menukar
    # dan merusak rantai saldo.
    _validate_saldo_chain(txs, saldo_awal, warnings)

    # cek saldo akhir vs saldo transaksi terakhir
    if saldo_akhir is not None and txs and txs[-1].saldo != saldo_akhir:
        warnings.append(
            f"Saldo Akhir ({saldo_akhir:,}) != saldo transaksi terakhir "
            f"({txs[-1].saldo:,})."
        )

    txs = _merge_biaya_nempel(txs)

    # Rekonsiliasi otomatis vs total CETAK BNI (hal.1) — generik, bukan hardcode.
    # Kalau hitung sendiri != angka BNI, ada transaksi hilang/ganda/salah-baca → WARNING.
    total_masuk = anchors["total_pemasukan"]
    total_keluar = anchors["total_pengeluaran"]
    hitung_masuk = sum(t.nominal for t in txs if t.nominal > 0)
    hitung_keluar = sum(-t.nominal for t in txs if t.nominal < 0) + \
        sum(t.biaya_admin for t in txs)
    if total_masuk is not None and hitung_masuk != total_masuk:
        warnings.append(
            f"Total Pemasukan tidak cocok: hitung {hitung_masuk:,} vs cetak BNI "
            f"{total_masuk:,} (selisih {hitung_masuk - total_masuk:+,})."
        )
    if total_keluar is not None and hitung_keluar != total_keluar:
        warnings.append(
            f"Total Pengeluaran tidak cocok: hitung {hitung_keluar:,} vs cetak BNI "
            f"{total_keluar:,} (selisih {hitung_keluar - total_keluar:+,})."
        )

    return ParseResult(txs, saldo_awal, saldo_akhir, warnings,
                       total_pemasukan=total_masuk, total_pengeluaran=total_keluar)


if __name__ == "__main__":
    import sys

    # Console Windows default cp1252 → karakter non-ASCII (emoji, dll) crash.
    # Paksa UTF-8 agar pesan WARNING anomali tidak pernah bikin program tumbang.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import glob

    cfg = json.load(open("config.json", encoding="utf-8"))
    # default: PDF pertama di samples/ (tanpa hardcode nama file = tanpa bocor no. rekening)
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else \
        next(iter(sorted(glob.glob("samples/*.pdf"))), None)
    if not pdf_path:
        print("Pakai: python src/parser.py <file.pdf>   (atau taruh PDF di samples/)")
        sys.exit(1)

    res = parse(pdf_path, password=cfg.get("pdf_password"))

    print(f"File       : {pdf_path}")
    print(f"Saldo Awal : {res.saldo_awal:,}" if res.saldo_awal else "Saldo Awal : -")
    print(f"Saldo Akhir: {res.saldo_akhir:,}" if res.saldo_akhir else "Saldo Akhir: -")
    print(f"Transaksi  : {len(res.transactions)}")
    total_admin = sum(t.biaya_admin for t in res.transactions)
    print(f"Biaya admin tergabung: {total_admin:,}")

    # --- Rekonsiliasi vs total CETAK BNI (pengaman pra-merge) ---
    hitung_masuk = sum(t.nominal for t in res.transactions if t.nominal > 0)
    hitung_keluar = sum(-t.nominal for t in res.transactions if t.nominal < 0) + total_admin
    print()
    print("=== Rekonsiliasi vs total cetak BNI ===")

    def _baris(label, hitung, cetak):
        if cetak is None:
            return f"  {label:<12} hitung {hitung:>14,}  | cetak BNI: (tak ada di PDF)"
        tanda = "OK" if hitung == cetak else "BEDA!"
        return (f"  {label:<12} hitung {hitung:>14,}  | cetak BNI {cetak:>14,}  -> {tanda}")

    print(_baris("Pemasukan", hitung_masuk, res.total_pemasukan))
    print(_baris("Pengeluaran", hitung_keluar, res.total_pengeluaran))
    print()
    for t in res.transactions:
        flag = "  ⚠ ANOMALI" if t.anomali_saldo else ""
        adm = f"  (+adm {t.biaya_admin:,})" if t.biaya_admin else ""
        print(f"{t.tanggal} {t.jam}  {t.nominal:>12,}  {t.saldo:>12,}  "
              f"{t.jenis:<16} {t.merchant}{adm}{flag}")
    print()
    if res.warnings:
        print(f"=== {len(res.warnings)} WARNING ===")
        for w in res.warnings:
            print(" -", w)
    else:
        print("Tidak ada warning. Validasi saldo berantai LOLOS.")
