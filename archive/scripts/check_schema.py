import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='fl3', user='FR3_User', password='di7UtK8E1[[137@F')
cur = conn.cursor()

# 1. Get signal_evaluations full schema
print("signal_evaluations columns:")
cur.execute("""
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_name = 'signal_evaluations' 
ORDER BY ordinal_position
""")
for r in cur.fetchall():
    print(f"  {r[0]:25s} {r[1]:20s} null={r[2]:3s} default={r[3]}")

# 2. Get paper_trades_log full schema
print()
print("paper_trades_log columns:")
cur.execute("""
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_name = 'paper_trades_log' 
ORDER BY ordinal_position
""")
for r in cur.fetchall():
    print(f"  {r[0]:25s} {r[1]:20s} null={r[2]:3s} default={r[3]}")

# 3. Check the Google Sheets dashboard code for date format
print()
print("Dashboard signal logging - checking format...")

# 4. Check if there's an ETF detection approach we can use
# Look at master_tickers for asset_type or similar
print()
print("master_tickers columns:")
cur.execute("""
SELECT column_name FROM information_schema.columns 
WHERE table_name = 'master_tickers' ORDER BY ordinal_position
""")
cols = [r[0] for r in cur.fetchall()]
print(f"  {cols}")

# Check if there's a type column
if 'type' in cols or 'asset_type' in cols or 'security_type' in cols:
    type_col = 'type' if 'type' in cols else ('asset_type' if 'asset_type' in cols else 'security_type')
    cur.execute(f"SELECT DISTINCT {type_col} FROM master_tickers LIMIT 20")
    print(f"  Distinct {type_col} values: {[r[0] for r in cur.fetchall()]}")

# Check master_tickers_basic too
print()
print("master_tickers_basic columns:")
cur.execute("""
SELECT column_name FROM information_schema.columns 
WHERE table_name = 'master_tickers_basic' ORDER BY ordinal_position
""")
cols2 = [r[0] for r in cur.fetchall()]
print(f"  {cols2}")

if 'type' in cols2:
    cur.execute("SELECT DISTINCT type FROM master_tickers_basic LIMIT 20")
    types = [r[0] for r in cur.fetchall()]
    print(f"  Distinct type values: {types}")
    
    # Check how ITB is classified
    cur.execute("SELECT ticker, type, name FROM master_tickers_basic WHERE ticker IN ('ITB','SPY','AAPL','BAC','XHB') ORDER BY ticker")
    print()
    print("  Sample classifications:")
    for r in cur.fetchall():
        print(f"    {r[0]:6s} type={r[1]:6s} name={r[2]}")

cur.close()
conn.close()
print("\nDone.")
