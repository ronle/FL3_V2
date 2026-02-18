"""
Backfill Account B Signals tab from trade logs.
10 signals were lost due to format bug on revision 00088.
"""
import json
import subprocess
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1EtTES7bk7swkzG5cgPbtotxDy9-EJS6opUnUOw8B-uY"
TAB_NAME = "Account B Signals"

# Trades from Cloud Run logs (revision 00088, 2026-02-18)
# Format: (timestamp, symbol, score, engulfing_strength, price, shares, vol_ratio)
TRADES = [
    ("2026-02-18 14:22:20", "MOD",  13, "strong",   219.33, 45,   0.5),
    ("2026-02-18 14:23:21", "DHT",  11, "moderate",  16.72, 598,  8.1),
    ("2026-02-18 14:24:21", "AMAT", 11, "strong",   368.87, 27,   0.8),
    ("2026-02-18 14:24:24", "APA",  12, "moderate",  28.75, 347,  0.7),
    ("2026-02-18 14:24:26", "MHK",  15, "weak",     132.54, 75,   0.4),
    ("2026-02-18 14:24:29", "YEXT", 11, "moderate",   5.55, 1800, 0.0),
    ("2026-02-18 14:24:32", "STOK", 13, "weak",      31.21, 320,  0.3),
    ("2026-02-18 14:24:35", "GKOS", 11, "moderate",  119.37, 83,  1.5),
    ("2026-02-18 14:25:21", "TW",   13, "moderate",  116.68, 85,  0.0),
    ("2026-02-18 14:25:24", "HTGC", 10, "moderate",   15.94, 626, 2.9),
]

def get_credentials():
    """Fetch credentials from GCP Secret Manager."""
    result = subprocess.run(
        "gcloud secrets versions access latest --secret=dashboard-credentials --project=fl3-v2-prod",
        capture_output=True, text=True, shell=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch credentials: {result.stderr}")
    return json.loads(result.stdout)

def main():
    print("Fetching credentials from Secret Manager...")
    creds_dict = get_credentials()
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID)
    tab = sheet.worksheet(TAB_NAME)

    # Check current contents
    existing = tab.get_all_values()
    print(f"Current rows in '{TAB_NAME}': {len(existing)}")

    # Add header if missing
    if not existing:
        header = ['Date/Time', 'Symbol', 'Score', 'Engulfing', 'Notional', 'Price', 'VolR', 'Action']
        tab.append_row(header, value_input_option='USER_ENTERED')
        print(f"Added header row")

    # Backfill signals
    # Account B layout: Date/Time, Symbol, Score, Engulfing, Notional, Price, VolR, Action
    rows = []
    for ts, symbol, score, eng, price, shares, vol_r in TRADES:
        notional = price * shares
        vol_str = f"{vol_r:.1f}x" if vol_r else "0.0x"
        row = [ts, symbol, score, eng, f"${notional:,.0f}", f"${price:.2f}", vol_str, "BUY"]
        rows.append(row)

    tab.append_rows(rows, value_input_option='USER_ENTERED')
    print(f"Backfilled {len(rows)} signals to '{TAB_NAME}'")

    for r in rows:
        print(f"  {r[1]:6s}  score={r[2]}  eng={r[3]:10s}  price={r[5]}  notional={r[4]}  vol={r[6]}")

if __name__ == "__main__":
    main()
