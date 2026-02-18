import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# 1. Check AMZN signal history across both days
print("=" * 80)
print("AMZN FULL SIGNAL HISTORY (last 5 days)")
print("=" * 80)
cur.execute("""
SELECT symbol, detected_at, score_total, rsi_14, trend, passed_all_filters, rejection_reason
FROM signal_evaluations
WHERE symbol = 'AMZN' AND detected_at >= '2026-01-30'
ORDER BY detected_at
""")
for r in cur.fetchall():
    p = "PASS" if r[5] else "FAIL"
    print(f"  {str(r[1])[:19]}  score={r[2]}  RSI={r[3]}  trend={r[4]}  {p}  {r[6] or ''}")

# 2. Check ALL signal evaluations from yesterday
print()
print("=" * 80)
print("YESTERDAY (Feb 2) - PASSED SIGNALS")
print("=" * 80)
cur.execute("""
SELECT symbol, detected_at, score_total, rsi_14, trend, passed_all_filters
FROM signal_evaluations
WHERE DATE(detected_at) = '2026-02-02' AND passed_all_filters = true
ORDER BY detected_at
""")
rows = cur.fetchall()
print(f"Passed signals on Feb 2: {len(rows)}")
for r in rows:
    print(f"  {r[0]:6s}  {str(r[1])[:19]}  score={r[2]}  RSI={r[3]}  trend={r[4]}")

# 3. Check Jan 30 passed signals (first trading day)
print()
print("=" * 80)
print("JAN 30 - PASSED SIGNALS")
print("=" * 80)
cur.execute("""
SELECT symbol, detected_at, score_total, passed_all_filters
FROM signal_evaluations
WHERE DATE(detected_at) = '2026-01-30' AND passed_all_filters = true
ORDER BY detected_at
""")
rows = cur.fetchall()
print(f"Passed signals on Jan 30: {len(rows)}")
for r in rows:
    print(f"  {r[0]:6s}  {str(r[1])[:19]}  score={r[2]}")

# 4. Check ALL dates with signal evaluations
print()
print("=" * 80)
print("SIGNAL EVALUATIONS BY DATE")
print("=" * 80)
cur.execute("""
SELECT DATE(detected_at) as d, COUNT(*) as total, 
       SUM(CASE WHEN passed_all_filters THEN 1 ELSE 0 END) as passed
FROM signal_evaluations
GROUP BY DATE(detected_at)
ORDER BY d
""")
for r in cur.fetchall():
    print(f"  {r[0]}  total={r[1]}  passed={r[2]}")

# 5. Check paper_trades_log for ALL dates
print()
print("=" * 80)
print("PAPER TRADES LOG - ALL DATES")
print("=" * 80)
cur.execute("SELECT COUNT(*) FROM paper_trades_log")
total = cur.fetchone()[0]
print(f"Total records in paper_trades_log: {total}")
if total > 0:
    cur.execute("SELECT * FROM paper_trades_log ORDER BY created_at DESC LIMIT 5")
    for r in cur.fetchall():
        print(f"  {r}")

# 6. Check the dashboard table for AMZN
print()
print("=" * 80)
print("CHECKING FOR DASHBOARD/SIGNAL TABLES WITH AMZN")
print("=" * 80)
# List all tables
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
tables = [r[0] for r in cur.fetchall()]
print(f"All public tables: {tables}")

cur.close()
conn.close()
print("\nDone.")
