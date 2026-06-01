"""Bot Telegram + webhook (Fase 6, §2 CLAUDE.md) — untuk deploy ke Cloud Run.

Alur: user share PDF e-statement ke bot → Telegram panggil /webhook → bot unduh PDF →
parse → deteksi kiriman ortu → klasifikasi → dedup terhadap Google Sheet → append baris
baru ke Sheet → balas ringkasan + WARNING anomali (jujur soal duit, §6).

State: TIDAK pakai file lokal (serverless ephemeral). **Google Sheet = sumber kebenaran**:
dedup baca kolom `id` yang sudah ada di Sheet, lalu append yang baru. Tidak menimpa baris
lama → tag manual `catatan` (§8) aman.

Konfigurasi via ENV (Cloud Run) atau fallback config.json (lokal):
    BOT_TOKEN          token dari @BotFather
    PDF_PASSWORD       password e-statement
    SHEET_ID           id Google Sheet
    GOOGLE_SA_JSON     isi JSON service account (Cloud Run) ATAU path file (lokal)
    NAMA_KIRIMAN_ORTU  (opsional) nama pengirim, dipisah koma
    WEBHOOK_SECRET     (opsional) token rahasia header webhook Telegram
"""

from __future__ import annotations

import io
import json
import os

import gspread
import requests
from google.oauth2.service_account import Credentials

from parser import parse
from classify import apply as classify_apply
from income import apply_detection
from schema import COLUMN_ORDER, Transaction

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
WORKSHEET = "Transaksi"
_ROOT = os.path.join(os.path.dirname(__file__), "..")


# ---------------------------------------------------------------- konfigurasi
def _cfg(key: str, default=None):
    """Ambil dari ENV (UPPER) dulu, lalu config.json (lower)."""
    env = os.environ.get(key.upper())
    if env:
        return env
    try:
        with open(os.path.join(_ROOT, "config.json"), encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except Exception:
        return default


def _credentials():
    raw = _cfg("google_sa_json")
    if not raw:
        raise RuntimeError("GOOGLE_SA_JSON / google_sa_json belum diset.")
    # ENV bisa berisi ISI JSON (Cloud Run) atau PATH file (lokal).
    if raw.strip().startswith("{"):
        info = json.loads(raw)
    else:
        # path: coba apa adanya, lalu relatif ke root proyek
        path = raw if os.path.exists(raw) else os.path.join(_ROOT, raw)
        if not os.path.exists(path):
            raise RuntimeError(f"google_sa_json bukan JSON valid / file tak ada: {raw[:40]}")
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _worksheet():
    sheet_id = _cfg("sheet_id")
    gc = gspread.authorize(_credentials())
    sh = gc.open_by_key(sheet_id)
    try:
        return sh.worksheet(WORKSHEET)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=WORKSHEET, rows=100, cols=len(COLUMN_ORDER))


# ------------------------------------------------------------- inti pemrosesan
def _rupiah(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def process_pdf(data: bytes) -> str:
    """Proses PDF (bytes) → append transaksi baru ke Sheet → kembalikan teks ringkasan.

    Reusable: dipakai webhook DAN uji lokal. Tidak menyentuh filesystem.
    """
    res = parse(io.BytesIO(data), password=_cfg("pdf_password"))
    if not res.transactions:
        return ("⚠️ Tidak ada transaksi terbaca. Pastikan PDF e-statement wondr yang benar "
                "(ber-text-layer & password cocok).")

    apply_detection(res.transactions)       # §7 kiriman ortu (sebelum klasifikasi)
    classify_apply(res.transactions, only_empty=True)  # §5

    ws = _worksheet()
    # dedup: id yang sudah ada di Sheet
    existing = set()
    header_ok = False
    try:
        for r in ws.get_all_records():
            rid = str(r.get("id", "")).strip()
            if rid:
                existing.add(rid)
        header_ok = True
    except Exception:
        header_ok = False

    # kalau Sheet masih kosong, tulis header dulu
    if not header_ok or ws.row_count == 0 or not ws.acell("A1").value:
        ws.update(values=[COLUMN_ORDER], range_name="A1")
        ws.freeze(rows=1)

    baru = [t for t in res.transactions if t.id not in existing]
    duplikat = len(res.transactions) - len(baru)

    if baru:
        rows = [[t.to_dict().get(c, "") for c in COLUMN_ORDER] for t in baru]
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    # ringkasan periode (dari yang baru diproses)
    masuk = sum(t.nominal for t in res.transactions if t.nominal > 0)
    keluar = sum(-t.nominal for t in res.transactions if t.nominal < 0) + \
        sum(t.biaya_admin for t in res.transactions)

    lines = [
        "✅ E-statement diproses.",
        f"Transaksi di file : {len(res.transactions)}",
        f"Ditambah ke Sheet : {len(baru)}",
        f"Duplikat (di-skip): {duplikat}",
        f"Pemasukan  : Rp{_rupiah(masuk)}",
        f"Pengeluaran: Rp{_rupiah(keluar)}",
    ]
    if res.warnings:
        lines.append("")
        lines.append(f"⚠️ {len(res.warnings)} PERINGATAN (cek manual):")
        for w in res.warnings[:10]:
            lines.append(f"• {w}")
    return "\n".join(lines)


# -------------------------------------------------------------------- Telegram
def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{_cfg('bot_token')}/{method}"


def tg_send(chat_id, text: str) -> None:
    try:
        requests.post(_api("sendMessage"), json={"chat_id": chat_id, "text": text},
                      timeout=30)
    except Exception:
        pass


def tg_download(file_id: str) -> bytes:
    info = requests.get(_api("getFile"), params={"file_id": file_id}, timeout=30).json()
    path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{_cfg('bot_token')}/{path}"
    return requests.get(url, timeout=60).content


def handle_update(update: dict) -> None:
    """Tangani satu update Telegram (dipakai webhook & long-poll)."""
    msg = update.get("message") or update.get("channel_post") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return
    doc = msg.get("document")
    if doc and str(doc.get("file_name", "")).lower().endswith(".pdf"):
        tg_send(chat_id, "⏳ Lagi proses e-statement...")
        try:
            data = tg_download(doc["file_id"])
            tg_send(chat_id, process_pdf(data))
        except Exception as e:
            tg_send(chat_id, f"❌ Gagal proses: {type(e).__name__}: {e}")
    elif msg.get("text", "").startswith("/start"):
        tg_send(chat_id, "Halo! Kirim/share file PDF e-statement wondr ke sini, "
                         "nanti kurekap otomatis ke Google Sheet. 📄")
    else:
        tg_send(chat_id, "Kirim file PDF e-statement wondr ya (bukan teks). 📄")


# ------------------------------------------------------------- Flask (Cloud Run)
def create_app():
    from flask import Flask, request

    app = Flask(__name__)

    @app.get("/")
    def health():
        return "ok", 200

    @app.post("/webhook")
    def webhook():
        secret = _cfg("webhook_secret")
        if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            return "forbidden", 403
        handle_update(request.get_json(force=True, silent=True) or {})
        return "ok", 200

    return app


app = create_app()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
