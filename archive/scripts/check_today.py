import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# 1. Today's signal evaluations
print("=" * 90)
print("SIGNAL EVALUATIONS - Feb 3, 2026")
print("=" * 90)
cur.execute("""
SELECT symbol, detected_at, score_total, rsi_14, trend, passed_all_filters, rejection_reason
FROM signal_evaluations
WHERE DATE(detected_at) = '2026-02-03'
ORDER BY detected_at
""")
rows = cur.fetchall()
print(f"Total evaluations today: {len(rows)}")
passed = [r for r in rows if r[5]]
failed = [r for r in rows if not r[5]]
print(f"Passed: {len(passed)}, Failed: {len(failed)}")
print()

print("--- PASSED SIGNALS ---")
for r in passed:
    sym = r[0]
    t = str(r[1])[11:19]
    sc = r[2]
    rsi = r[3]
    tr = r[4]
    print(f"  {sym:6s} {t} ET  score={sc}  RSI={rsi}  trend={tr}")

print()
print("--- FAILED SIGNALS (last 20) ---")
for r in failed[-20:]:
    sym = r[0]
    t = str(r[1])[11:19]
    sc = r[2]
    rsi = r[3]
    tr = r[4]
    rej = (r[6] or '')[:60]
    print(f"  {sym:6s} {t} ET  score={sc}  RSI={rsi}  trend={tr}  reason={rej}")

# 2. Active signals table
print()
print("=" * 90)
print("ACTIVE SIGNALS TABLE - Feb 3, 2026")
print("=" * 90)
cur.execute("""
SELECT symbol, detected_at, score, price_at_detection, entry_price, trade_placed, 
       notional, rsi, trend
FROM active_signals
WHERE DATE(detected_at) = '2026-02-03'
ORDER BY detected_at
""")
rows2 = cur.fetchall()
print(f"Total active signals: {len(rows2)}")
for r in rows2:
    sym = r[0]
    t = str(r[1])[11:19]
    sc = r[2]
    det_price = r[3]
    entry = r[4]
    traded = r[5]
    notional = r[6]
    rsi = r[7]
    tr = r[8]
    print(f"  {sym:6s} {t} ET  score={sc}  det_price=${det_price}  entry=${entry}  traded={traded}  notional=${notional:,.0f}  RSI={rsi}  trend={tr}")

# 3. Paper trades log
print()
print("=" * 90)
print("PAPER TRADES LOG - Feb 3, 2026")
print("=" * 90)
cur.execute("""
SELECT * FROM paper_trades_log
WHERE DATE(created_at) = '2026-02-03'
ORDER BY created_at
""")
rows3 = cur.fetchall()
print(f"Paper trades today: {len(rows3)}")
if rows3:
    # Get column names
    colnames = [desc[0] for desc in cur.description]
    print(f"  Columns: {colnames}")
    for r in rows3:
        print(f"  {r}")

cur.close()
conn.close()
print("\nDone.")
