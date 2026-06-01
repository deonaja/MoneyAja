"""Skema data 1 transaksi (§4 CLAUDE.md) — 13 field.

Sumber kebenaran tunggal untuk bentuk data. Parser, klasifikasi, dedup, dan
output Sheet semuanya mengacu ke sini. Disimpan sebagai JSON (canonical store);
CSV/Sheet hanya export turunan.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict, fields


@dataclass
class Transaction:
    # --- fakta mentah dari PDF (deterministik, jangan ditebak) ---
    tanggal: str            # ISO "YYYY-MM-DD"
    jam: str                # "HH:MM:SS"
    jenis: str              # apa adanya dari PDF, mis. "Pembayaran Qris"
    merchant: str           # mentah-gabung, kota TIDAK dipisah
    nominal: int            # bertanda: - keluar, + masuk; tanpa koma/desimal
    saldo: int              # untuk validasi berantai

    # --- turunan / interpretasi (boleh dikoreksi tanpa merusak fakta) ---
    arah: str = ""          # "keluar" / "masuk" — turunan tanda nominal
    kategori: str = ""      # diisi tahap klasifikasi (§5)
    biaya_admin: int = 0    # biaya nempel yang digabung ke induk (§6)
    bank: str = "wondr"     # untuk masa depan (Livin nanti)
    is_kiriman_ortu: bool = False  # penanda pemasukan dari ortu (§7)
    catatan: str = ""       # tag manual oleh user (§8), mis. "titipan"
    id: str = ""            # sidik jari unik, penjaga duplikat (§6)

    # --- penanda anomali (bukan field skema §4, tapi penting untuk kejujuran) ---
    anomali_saldo: bool = False  # True jika validasi saldo berantai gagal di sini

    def __post_init__(self) -> None:
        if not self.arah:
            self.arah = "masuk" if self.nominal >= 0 else "keluar"
        if not self.id:
            self.id = self.compute_id()

    def compute_id(self) -> str:
        """Hash unik dari tanggal + jam + nominal + saldo (§6)."""
        raw = f"{self.tanggal}|{self.jam}|{self.nominal}|{self.saldo}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# Urutan kolom untuk export CSV/Sheet (§9). 13 field skema + anomali di akhir.
COLUMN_ORDER = [
    "tanggal", "jam", "jenis", "merchant", "nominal", "arah", "saldo",
    "kategori", "biaya_admin", "bank", "is_kiriman_ortu", "catatan", "id",
    "anomali_saldo",
]
