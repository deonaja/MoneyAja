"""Fitur siklus pemasukan / kiriman ortu (§7 CLAUDE.md).

Dua hal:
  1. Deteksi `is_kiriman_ortu` (deterministik, bukan AI).
  2. Dua TAMPILAN laporan (bukan pengganti — keduanya jawab pertanyaan beda):
       - bulanan kalender  : "boros nggak bulan ini?"
       - siklus transfer    : "duit kiriman cukup sampai kiriman berikutnya?"

CATATAN asumsi (§7 awal "kiriman 1x/bulan" TERNYATA SALAH di data nyata: sebagian bulan
punya >1 kiriman dari pengirim yang sama). Aturan tetap berlaku & benar: **1 kiriman = 1
awal siklus**, jadi sebulan bisa punya >1 siklus. Transaksi menggantung sebelum kiriman
pertama di file = ekor siklus bulan SEBELUMNYA (ditutup saat user kirim e-statement bulan
itu; dedup §6 aman).

Konsekuensi benar (bukan bug): siklus terkini "setengah terbuka" sampai kiriman penutup
(bulan berikutnya) masuk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# --- Parameter deteksi ---
# Deteksi BERBASIS NAMA saja. Cadangan "nominal > ambang" (§7 versi awal) DIBUANG karena
# pada data nyata ia salah-tangkap pemasukan lain (mis. gaji bernominal besar) sebagai
# kiriman ortu. Nama jauh lebih akurat.
#
# Nama pengirim diambil dari config.json (`nama_kiriman_ortu`) — SENGAJA tidak di-hardcode
# di source agar tidak ada data pribadi yang ter-commit. Default: list kosong.
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")


def _load_nama_ortu() -> list:
    # ENV dulu (Cloud Run): "NAMA_KIRIMAN_ORTU=NAMA1,NAMA2"
    env = os.environ.get("NAMA_KIRIMAN_ORTU")
    if env:
        return [n.strip() for n in env.split(",") if n.strip()]
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("nama_kiriman_ortu", [])
    except Exception:
        return []


NAMA_ORTU = _load_nama_ortu()


def detect_kiriman_ortu(tx) -> bool:
    """True jika transaksi adalah kiriman ortu (§7): jenis=Transfer DAN uang MASUK
    DAN nama pengirim mengandung salah satu NAMA_ORTU. Guard `nominal > 0` mengunci
    definisi "Pemasukan dari ortu" — transfer keluar TIDAK ikut ter-flag."""
    if (tx.jenis or "").lower() != "transfer":
        return False
    if tx.nominal <= 0:                       # kiriman = uang masuk
        return False
    merchant = (tx.merchant or "").upper()
    return any(nama.upper() in merchant for nama in NAMA_ORTU)


def apply_detection(txs: list) -> int:
    """Set `is_kiriman_ortu` tiap transaksi. Kembalikan jumlah yang ter-flag.
    Dipanggil SEBELUM klasifikasi agar kiriman ortu jadi kategori 'Pemasukan' (§5 #1)."""
    n = 0
    for tx in txs:
        tx.is_kiriman_ortu = detect_kiriman_ortu(tx)
        if tx.is_kiriman_ortu:
            n += 1
    return n


# ============================ TAMPILAN 1: BULANAN KALENDER ============================

@dataclass
class RingkasanBulan:
    bulan: str            # "YYYY-MM"
    pemasukan: int
    pengeluaran: int      # termasuk biaya_admin tergabung
    net: int
    jumlah: int
    per_kategori: dict     # kategori -> total pengeluaran (uang keluar)


def _pengeluaran(tx) -> int:
    """Uang keluar dari 1 transaksi: |nominal| jika keluar, + biaya admin tergabung."""
    keluar = -tx.nominal if tx.nominal < 0 else 0
    return keluar + tx.biaya_admin


def ringkasan_bulanan(txs: list) -> list:
    """Tampilan kalender: kelompokkan per bulan (tanggal 1–akhir)."""
    bulan_map: dict[str, list] = {}
    for tx in txs:
        bulan_map.setdefault(tx.tanggal[:7], []).append(tx)

    hasil = []
    for bulan in sorted(bulan_map):
        grup = bulan_map[bulan]
        pemasukan = sum(t.nominal for t in grup if t.nominal > 0)
        pengeluaran = sum(_pengeluaran(t) for t in grup)
        per_kat: dict[str, int] = {}
        for t in grup:
            k = _pengeluaran(t)
            if k:
                per_kat[t.kategori] = per_kat.get(t.kategori, 0) + k
        hasil.append(RingkasanBulan(
            bulan=bulan, pemasukan=pemasukan, pengeluaran=pengeluaran,
            net=pemasukan - pengeluaran, jumlah=len(grup),
            per_kategori=dict(sorted(per_kat.items(), key=lambda x: -x[1])),
        ))
    return hasil


# ============================ TAMPILAN 2: SIKLUS TRANSFER ============================

@dataclass
class Siklus:
    label: str            # mis. "Siklus 2026-04" / "Menggantung (siklus bulan sebelumnya)"
    mulai: str            # tanggal kiriman ortu pembuka, atau "awal data"
    selesai: str          # tanggal sebelum kiriman berikutnya, atau "TERBUKA"
    kiriman: int          # nominal kiriman ortu pembuka (0 jika menggantung)
    pemasukan_lain: int   # pemasukan non-kiriman di dalam siklus
    pengeluaran: int
    sisa: int             # kiriman + pemasukan_lain - pengeluaran
    jumlah: int
    status: str           # "lengkap" / "terbuka" / "menggantung"


def _urut(txs: list) -> list:
    return sorted(txs, key=lambda t: (t.tanggal, t.jam))


def ringkasan_siklus(txs: list) -> list:
    """Tampilan siklus: dari kiriman ortu ke kiriman ortu berikutnya (§7)."""
    urut = _urut(txs)
    batas = [i for i, t in enumerate(urut) if t.is_kiriman_ortu]

    hasil = []

    # Ekor menggantung: transaksi sebelum kiriman ortu pertama = siklus bulan sebelumnya.
    head_end = batas[0] if batas else len(urut)
    if head_end > 0:
        seg = urut[:head_end]
        hasil.append(_buat_siklus(
            seg, label="Menggantung (masuk siklus bulan sebelumnya)",
            mulai="awal data", kiriman_tx=None,
            selesai=seg[-1].tanggal, status="menggantung"))

    # Tiap kiriman ortu membuka satu siklus sampai sebelum kiriman berikutnya.
    for pos, start in enumerate(batas):
        end = batas[pos + 1] if pos + 1 < len(batas) else len(urut)
        seg = urut[start:end]
        kiriman_tx = urut[start]
        terbuka = (pos + 1 == len(batas))   # siklus terakhir = setengah terbuka (§7)
        hasil.append(_buat_siklus(
            seg, label=f"Siklus {kiriman_tx.tanggal[:7]}",
            mulai=kiriman_tx.tanggal, kiriman_tx=kiriman_tx,
            selesai="TERBUKA (nunggu kiriman berikutnya)" if terbuka else seg[-1].tanggal,
            status="terbuka" if terbuka else "lengkap"))
    return hasil


def _buat_siklus(seg, label, mulai, kiriman_tx, selesai, status) -> Siklus:
    kiriman = kiriman_tx.nominal if kiriman_tx else 0
    pemasukan_lain = sum(
        t.nominal for t in seg if t.nominal > 0 and t is not kiriman_tx)
    pengeluaran = sum(_pengeluaran(t) for t in seg)
    return Siklus(
        label=label, mulai=mulai, selesai=selesai, kiriman=kiriman,
        pemasukan_lain=pemasukan_lain, pengeluaran=pengeluaran,
        sisa=kiriman + pemasukan_lain - pengeluaran, jumlah=len(seg), status=status)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, "src") if "src" not in sys.path else None
    from store import load_store

    txs = load_store()
    if not txs:
        print("Store kosong. Jalankan `python src/store.py` dulu.")
        sys.exit(0)

    # pastikan flag ter-set (store seharusnya sudah, tapi aman dipanggil ulang)
    n = apply_detection(txs)

    def rp(x): return f"{x:>14,}"

    print(f"Kiriman ortu terdeteksi: {n}")
    print()
    print("=" * 60)
    print("TAMPILAN 1 — BULANAN KALENDER  ('boros nggak bulan ini?')")
    print("=" * 60)
    for b in ringkasan_bulanan(txs):
        print(f"\n[{b.bulan}]  {b.jumlah} transaksi")
        print(f"  Pemasukan  {rp(b.pemasukan)}")
        print(f"  Pengeluaran{rp(b.pengeluaran)}")
        print(f"  Net        {rp(b.net)}")
        print("  Pengeluaran per kategori:")
        for kat, v in b.per_kategori.items():
            print(f"    {kat:<18}{rp(v)}")

    print()
    print("=" * 60)
    print("TAMPILAN 2 — SIKLUS TRANSFER  ('kiriman cukup sampai kiriman berikutnya?')")
    print("=" * 60)
    for s in ringkasan_siklus(txs):
        print(f"\n[{s.label}]  status: {s.status}")
        print(f"  {s.mulai}  →  {s.selesai}   ({s.jumlah} transaksi)")
        if s.kiriman:
            print(f"  Kiriman ortu   {rp(s.kiriman)}")
        if s.pemasukan_lain:
            print(f"  Pemasukan lain {rp(s.pemasukan_lain)}")
        print(f"  Pengeluaran    {rp(s.pengeluaran)}")
        print(f"  Sisa           {rp(s.sisa)}")
