"""
One-off script to backfill the "Closed Today" Google Sheets tab
with all historical closed trades from paper_trades_log.

Usage (local, requires Cloud SQL Auth Proxy on port 5433):
    python -m scripts.backfill_closed_sheet
"""

import os
import sys
import json
import logging

import psycopg2
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

# ---------- DB ----------

def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "")
    if not url:
        # Fetch from Secret Manager and convert socket URL to local proxy TCP
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
            name = f"projects/{project}/secrets/DATABASE_URL/versions/latest"
            resp = client.access_secret_version(request={"name": name})
            url = resp.payload.data.decode("UTF-8")
        except Exception as e:
            logger.error(f"Cannot get DATABASE_URL: {e}")
            sys.exit(1)
    # Convert Cloud SQL socket URL to TCP for local proxy
    if "cloudsql" in url:
        import re
        m = re.match(r"postgresql://([^:]+):([^@]+)@/([^?]+)", url)
        if m:
            user, password, db = m.group(1), m.group(2), m.group(3)
            url = f"postgresql://{user}:{password}@127.0.0.1:5433/{db}"
    return url.strip()


def fetch_closed_trades(db_url: str) -> list[dict]:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    # LEFT JOIN active_signals to recover score for crash-recovery trades (signal_score=0)
    cur.execute("""
        SELECT p.symbol, p.entry_price, p.exit_price, p.exit_time, p.shares,
               p.pnl, p.pnl_pct, p.exit_reason,
               COALESCE(NULLIF(p.signal_score, 0), a.score, 0) AS score
        FROM paper_trades_log p
        LEFT JOIN LATERAL (
            SELECT score FROM active_signals
            WHERE symbol = p.symbol
              AND detected_at::date = p.entry_time::date
            ORDER BY detected_at DESC
            LIMIT 1
        ) a ON true
        WHERE p.exit_time IS NOT NULL
        ORDER BY p.exit_time ASC
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ---------- Google Sheets ----------

def get_sheet():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    sheet_id = os.environ.get("DASHBOARD_SHEET_ID", "")

    if not sheet_id:
        logger.error("DASHBOARD_SHEET_ID not set")
        sys.exit(1)

    # Try Secret Manager first
    creds_json = None
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
        name = f"projects/{project}/secrets/dashboard-credentials/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        creds_json = resp.payload.data.decode("UTF-8")
        logger.info("Got credentials from Secret Manager")
    except Exception as e:
        logger.info(f"Secret Manager unavailable ({e}), trying local file")

    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_file:
            logger.error("No credentials available")
            sys.exit(1)
        creds = Credentials.from_service_account_file(creds_file, scopes=scopes)

    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


def backfill(trades: list[dict]):
    sheet = get_sheet()

    try:
        tab = sheet.worksheet("Closed Today")
    except Exception:
        tab = sheet.add_worksheet(title="Closed Today", rows=1000, cols=10)

    # Clear and write header
    tab.clear()
    header = ["Date/Time", "Symbol", "Score", "Shares", "Entry", "Exit", "P/L %", "$ P/L", "Result"]
    rows = [header]

    for t in trades:
        exit_time = t["exit_time"]
        if exit_time and exit_time.tzinfo is None:
            exit_time = pytz.utc.localize(exit_time)
        ts_str = exit_time.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S") if exit_time else ""

        entry = float(t["entry_price"]) if t["entry_price"] else 0
        exit_p = float(t["exit_price"]) if t["exit_price"] else 0
        shares = t["shares"] or 0
        score = t["score"] or 0
        pnl = float(t["pnl"]) if t["pnl"] else 0
        pnl_pct = float(t["pnl_pct"]) if t["pnl_pct"] else 0
        result = "WIN" if pnl_pct > 0 else "LOSS" if pnl_pct < 0 else "FLAT"

        rows.append([
            ts_str,
            t["symbol"],
            score,
            shares,
            f"${entry:.2f}",
            f"${exit_p:.2f}",
            f"{pnl_pct:+.2f}%",
            f"${pnl:+,.2f}",
            result,
        ])

    # Batch write all rows at once (much faster than append_row per trade)
    tab.update(f"A1:I{len(rows)}", rows, value_input_option="USER_ENTERED")
    logger.info(f"Wrote {len(rows) - 1} closed trades to 'Closed Today' tab")


# ---------- Main ----------

if __name__ == "__main__":
    db_url = _get_db_url()
    logger.info(f"Fetching closed trades from DB...")
    trades = fetch_closed_trades(db_url)
    logger.info(f"Found {len(trades)} closed trades")

    if not trades:
        logger.info("No trades to backfill")
        sys.exit(0)

    backfill(trades)
    logger.info("Done!")
