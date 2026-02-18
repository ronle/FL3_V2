import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# 1. Get signal_evaluations full schema (to understand trade_placed column)
print("=" * 80)
print("signal_evaluations SCHEMA")
print("=" * 80)
cur.execute("""
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_name = 'signal_evaluations' 
ORDER BY ordinal_position
""")
for r in cur.fetchall():
    print(f"  {r[0]:25s} {r[1]:20s} null={r[2]:3s} default={r[3]}")

# 2. Get paper_trades_log schema
print()
print("=" * 80)
print("paper_trades_log SCHEMA")
print("=" * 80)
cur.execute("""
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_name = 'paper_trades_log' 
ORDER BY ordinal_position
""")
for r in cur.fetchall():
    print(f"  {r[0]:25s} {r[1]:20s} null={r[2]:3s} default={r[3]}")

# 3. Check what the Google Sheets dashboard columns look like
# We know the dashboard.py writes to sheets - check what data it sends
print()
print("=" * 80)
print("DASHBOARD SHEET TAB STRUCTURE (from code)")
print("=" * 80)
print("Active Signals tab headers: Time, Symbol, Score, RSI, Ratio, Notional, Price, Action")
print("Positions tab headers: Symbol, Entry, Current, P/L %, Status")
print("Closed tab headers: Time, Symbol, Entry, Exit, P/L %, Result")
print("(These are from dashboard.py clear_daily method)")

# 4. Check what ETFs commonly appear in our signals
print()
print("=" * 80)
print("POTENTIAL ETFs IN PASSED SIGNALS (all time)")
print("=" * 80)
# Common ETF patterns - symbols 3 chars ending in specific patterns
cur.execute("""
SELECT symbol, COUNT(*) as cnt
FROM signal_evaluations
WHERE passed_all_filters = true
AND symbol IN (
    'SPY','QQQ','IWM','DIA','XLE','XLF','XLK','XLV','XLI','XLU','XLP','XLY','XLB','XLRE',
    'VTI','VOO','VXX','UVXY','SQQQ','TQQQ','SPXU','SPXS',
    'GLD','SLV','USO','UNG','TLT','HYG','LQD','JNK',
    'EEM','EFA','VWO','IEMG','ARKK','ARKG','ARKW','ARKF',
    'ITB','XHB','XOP','XBI','XRT','XME','KWEB','MCHI','FXI',
    'SOXX','SMH','HACK','BOTZ','ROBO','IBB','XLC',
    'IYR','VNQ','GDXJ','GDX','JETS','KRE','KBE'
)
GROUP BY symbol
ORDER BY cnt DESC
""")
rows = cur.fetchall()
print(f"ETFs that have passed filters historically:")
for r in rows:
    print(f"  {r[0]:6s} passed {r[1]} times")

# 5. Check what ETFs appeared in today's evaluations (not just passed)
print()
print("ALL ETF-like symbols evaluated today (with scores >= 10):")
cur.execute("""
SELECT symbol, score_total, passed_all_filters, rejection_reason
FROM signal_evaluations
WHERE DATE(detected_at) = '2026-02-03'
AND score_total >= 10
AND symbol IN (
    'ITB','XHB','XOP','XBI','XRT','XME','KWEB','MCHI','FXI',
    'SOXX','SMH','HACK','BOTZ','ROBO','IBB','XLC',
    'IYR','VNQ','GDXJ','GDX','JETS','KRE','KBE',
    'IBIT','BITO','GBTC','ETHE'
)
ORDER BY detected_at
""")
for r in cur.fetchall():
    p = "PASS" if r[2] else "FAIL"
    print(f"  {r[0]:6s} score={r[1]} {p} {r[3] or ''}")

# 6. Check position_manager.py for where it writes to DB on close
# Already verified - it calls close_signal_in_db which writes to active_signals (doesn't exist)
# And it does NOT write to paper_trades_log

# 7. Count how many positions were actually opened yesterday vs today
print()
print("=" * 80)
print("POSITION SIZING REFERENCE")
print("=" * 80)
print("From config.py: MAX_CONCURRENT_POSITIONS = 5")
print("From config.py: MAX_POSITION_SIZE_PCT = 0.10 (10%)")
print("From Alpaca: ~$100K account = ~$10K per position")
print()
print("Yesterday (Feb 2): 5 signals passed, SPY was one (ETF)")
print("Today (Feb 3): 5 signals passed, ITB is one (ETF)")
print("AMZN held overnight from yesterday")
print("Total positions now: 6 (AMZN from yesterday + 5 new today)")
print("MAX_CONCURRENT_POSITIONS = 5 but we have 6!")

cur.close()
conn.close()
print("\nDone.")
