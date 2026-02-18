import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# Check if active_signals exists
cur.execute("""
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name = 'active_signals'
""")
r = cur.fetchone()
print(f"active_signals table exists: {r is not None}")

if r:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'active_signals' ORDER BY ordinal_position")
    cols = [r[0] for r in cur.fetchall()]
    print(f"Columns: {cols}")
    
    cur.execute("SELECT * FROM active_signals WHERE DATE(detected_at) >= '2026-02-02' ORDER BY detected_at")
    rows = cur.fetchall()
    print(f"Records since Feb 2: {len(rows)}")
    for r in rows:
        print(f"  {r}")

# Also check signal_evaluations for trade_placed column  
print()
print("signal_evaluations.trade_placed for Feb 2-3:")
cur.execute("""
SELECT symbol, detected_at, score_total, passed_all_filters, trade_placed, entry_price
FROM signal_evaluations
WHERE DATE(detected_at) >= '2026-02-02' AND passed_all_filters = true
ORDER BY detected_at
""")
for r in cur.fetchall():
    print(f"  {r[0]:6s}  {str(r[1])[:19]}  score={r[2]}  traded={r[4]}  entry=${r[5]}")

cur.close()
conn.close()
print("\nDone.")
