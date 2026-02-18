import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# Check if yesterday's SPY passed - it's in the ETF exclusion list now
print("=" * 80)
print("FEB 2 PASSED SIGNALS - ETF CHECK")
print("=" * 80)
print("SPY passed on Feb 2 - ETF filter should have blocked this!")
print("AMZN passed on Feb 2 at 09:31 - was this traded?")
print("MA passed on Feb 2 at 09:55")
print("VNET passed on Feb 2 at 10:34")
print("IBM passed on Feb 2 at 10:35")
print()

# Check what version was running yesterday - were the bug fixes deployed?
# The etf check looks at signal_evaluations.rejection_reason
cur.execute("""
SELECT symbol, detected_at, rejection_reason
FROM signal_evaluations
WHERE DATE(detected_at) = '2026-02-02' 
AND rejection_reason LIKE '%ETF%'
LIMIT 5
""")
etf_rejections = cur.fetchall()
print(f"ETF rejections on Feb 2: {len(etf_rejections)}")
for r in etf_rejections:
    print(f"  {r[0]} {str(r[1])[:19]} reason={r[2]}")

# Check today's ETF rejections
cur.execute("""
SELECT symbol, detected_at, rejection_reason
FROM signal_evaluations
WHERE DATE(detected_at) = '2026-02-03' 
AND rejection_reason LIKE '%ETF%'
LIMIT 10
""")
etf_rejections_today = cur.fetchall()
print(f"\nETF rejections on Feb 3: {len(etf_rejections_today)}")
for r in etf_rejections_today:
    print(f"  {r[0]} {str(r[1])[:19]} reason={r[2]}")

# Key question: Did the position_manager's sync_on_startup pick up AMZN today?
# We can't tell directly from DB, but we can check if AMZN was evaluated today
cur.execute("""
SELECT symbol, detected_at, score_total, passed_all_filters, rejection_reason
FROM signal_evaluations
WHERE symbol = 'AMZN' AND DATE(detected_at) = '2026-02-03'
ORDER BY detected_at
""")
amzn_today = cur.fetchall()
print(f"\nAMZN evaluations today: {len(amzn_today)}")
for r in amzn_today:
    p = "PASS" if r[3] else "FAIL"
    print(f"  {str(r[1])[:19]} score={r[2]} {p} {r[4] or ''}")

# Check the dashboard module's close_signal_in_db function
# Look at what tables the close writes to
print()
print("=" * 80)
print("CHECKING update_signal_trade_placed / close_signal writes")
print("=" * 80)

# There's no active_signals table - check what dashboard.py actually writes to
# Let's look for any table with trade_placed column
cur.execute("""
SELECT table_name, column_name 
FROM information_schema.columns 
WHERE column_name IN ('trade_placed', 'entry_price', 'exit_price', 'closed_at')
AND table_schema = 'public'
ORDER BY table_name, column_name
""")
for r in cur.fetchall():
    print(f"  {r[0]}.{r[1]}")

# Check uoa_triggers_v2 for AMZN 
print()
print("=" * 80)
print("UOA TRIGGERS FOR AMZN (Feb 2)")
print("=" * 80)
cur.execute("""
SELECT column_name FROM information_schema.columns 
WHERE table_name = 'uoa_triggers_v2' ORDER BY ordinal_position
""")
cols = [r[0] for r in cur.fetchall()]
print(f"uoa_triggers_v2 columns: {cols}")

cur.close()
conn.close()
print("\nDone.")
