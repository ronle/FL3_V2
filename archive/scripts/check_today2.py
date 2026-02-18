import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# Find the right table names
cur.execute("""
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND (table_name LIKE '%signal%' OR table_name LIKE '%trade%' OR table_name LIKE '%paper%')
ORDER BY table_name
""")
print("Relevant tables:")
for r in cur.fetchall():
    print(f"  {r[0]}")

# Check paper_trades_log
print()
print("=" * 80)
print("PAPER TRADES LOG")
print("=" * 80)
try:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'paper_trades_log' ORDER BY ordinal_position")
    cols = [r[0] for r in cur.fetchall()]
    print(f"Columns: {cols}")
    cur.execute("SELECT * FROM paper_trades_log WHERE DATE(created_at) = '2026-02-03' ORDER BY created_at")
    rows = cur.fetchall()
    print(f"Trades today: {len(rows)}")
    for r in rows:
        print(f"  {r}")
except Exception as e:
    print(f"Error: {e}")
    conn.rollback()

# Check AMZN specifically - it's not in today's passed signals but you have a position
print()
print("=" * 80)
print("AMZN INVESTIGATION")
print("=" * 80)
cur.execute("""
SELECT symbol, detected_at, score_total, rsi_14, trend, passed_all_filters, rejection_reason
FROM signal_evaluations
WHERE symbol = 'AMZN' AND DATE(detected_at) >= '2026-02-02'
ORDER BY detected_at DESC
LIMIT 10
""")
rows = cur.fetchall()
print(f"AMZN recent evaluations: {len(rows)}")
for r in rows:
    p = "PASS" if r[5] else "FAIL"
    print(f"  {r[0]} {str(r[1])[:19]} score={r[2]} RSI={r[3]} trend={r[4]} {p} {r[6] or ''}")

# Check ITB - it's an ETF!
print()
print("=" * 80)
print("ITB CHECK (ETF?)")
print("=" * 80)
cur.execute("""
SELECT symbol, detected_at, score_total, rsi_14, trend, passed_all_filters, rejection_reason
FROM signal_evaluations
WHERE symbol = 'ITB' AND DATE(detected_at) = '2026-02-03'
ORDER BY detected_at
""")
rows = cur.fetchall()
for r in rows:
    p = "PASS" if r[5] else "FAIL"
    print(f"  {r[0]} {str(r[1])[:19]} score={r[2]} RSI={r[3]} trend={r[4]} {p} {r[6] or ''}")

# All 6 positions - when were they signaled?
print()
print("=" * 80)
print("ALL 6 HELD SYMBOLS - SIGNAL DETAILS")
print("=" * 80)
for sym in ['BAC', 'DELL', 'ITB', 'AMZN', 'CRH', 'PHM']:
    cur.execute("""
    SELECT symbol, detected_at, score_total, rsi_14, trend, passed_all_filters, rejection_reason
    FROM signal_evaluations
    WHERE symbol = %s AND DATE(detected_at) >= '2026-02-02'
    AND passed_all_filters = true
    ORDER BY detected_at DESC
    LIMIT 3
    """, (sym,))
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:6s} {str(r[1])[:19]} score={r[2]} RSI={r[3]} trend={r[4]} PASSED")
    else:
        print(f"  {sym:6s} -- NO passed evaluations found in last 2 days")

cur.close()
conn.close()
print("\nDone.")
