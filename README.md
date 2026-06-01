# MoneyAja

Automation rekap pengeluaran pribadi dari e-statement PDF wondr (BNI). Parser deterministik
mengekstrak transaksi dari PDF, mengklasifikasi kategori berbasis aturan, dedup, lalu
mengeluarkan JSON/CSV. Filosofi: **jujur soal duit** — fakta dicatat otomatis, interpretasi
yang ambigu diserahkan ke manusia.

## Pipeline

```
PDF e-statement → parse → klasifikasi → dedup → data/transactions.json → CSV
```

## Setup

```bash
pip install -r requirements.txt
cp config.example.json config.json   # lalu isi password e-statement
```

Taruh PDF e-statement di `samples/` (atau path mana pun).

## Pemakaian

```bash
# verifikasi 1 file terisolasi (rekonsiliasi vs total cetak BNI, tidak menulis store)
python src/parser.py samples/<file>.pdf

# gabungkan ke store (dedup otomatis untuk periode overlap)
python src/store.py samples/<file>.pdf

# laporan: bulanan kalender + siklus transfer
python src/income.py

# export CSV
python src/export_csv.py

# recheck end-to-end (jalankan sebelum lanjut fitur)
python tests/recheck.py
```

## Struktur

| File | Peran |
|------|-------|
| `src/schema.py`     | skema 14 field transaksi (+ id/arah) |
| `src/parser.py`     | PDF → transaksi, validasi saldo berantai, rekonsiliasi vs total BNI |
| `src/classify.py`   | klasifikasi kategori berbasis aturan (urutan prioritas) |
| `src/store.py`      | dedup (hash id) + penyimpanan JSON |
| `src/income.py`     | deteksi kiriman ortu + laporan bulanan & siklus |
| `src/export_csv.py` | export CSV netral (quoting aman) |
| `tests/recheck.py`  | uji invariant end-to-end |

## Catatan privasi

`config.json` (password), `samples/` (PDF asli), `data/` & `output/` (transaksi pribadi)
**di-gitignore** — tidak ikut ter-commit.
```
