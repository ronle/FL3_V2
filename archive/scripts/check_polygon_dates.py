"""
Quick date check - what does Polygon have for the most recent data?
"""

import requests
from datetime import datetime, timedelta

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"

print("="*70)
print(f"CURRENT LOCAL TIME: {datetime.now()}")
print("="*70)

# Get AAOI data for the last 10 days
url = f"{BASE_URL}/v2/aggs/ticker/AAOI/range/1/day/2026-01-15/2026-01-30"
params = {"adjusted": "true", "sort": "asc", "apiKey": API_KEY}

response = requests.get(url, params=params)
if response.status_code == 200:
    data = response.json()
    bars = data.get("results", [])
    
    print(f"\nPolygon returned {len(bars)} days of data")
    print(f"Query count: {data.get('queryCount')}")
    print(f"Results count: {data.get('resultsCount')}")
    
    print("\nAll available AAOI daily bars:")
    print("-"*70)
    for bar in bars:
        ts_ms = bar.get("t", 0)
        dt = datetime.fromtimestamp(ts_ms / 1000)
        o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
        vol = bar.get("v", 0)
        change = ((c - o) / o * 100) if o else 0
        
        # Check if this looks like the big move
        is_big = "<<<" if change > 15 else ""
        
        print(f"{dt.strftime('%Y-%m-%d %a')} | O:${o:>6.2f} H:${h:>6.2f} L:${l:>6.2f} C:${c:>6.2f} | "
              f"{change:>+6.1f}% | Vol: {vol:>12,.0f} {is_big}")
    
    if bars:
        last_bar = bars[-1]
        last_ts = datetime.fromtimestamp(last_bar.get("t", 0) / 1000)
        print(f"\nMost recent data: {last_ts.strftime('%Y-%m-%d %A')}")
        print(f"Today's date: {datetime.now().strftime('%Y-%m-%d %A')}")
        
        if last_ts.date() < datetime.now().date():
            print("\n*** Polygon data is 1 day behind (normal for EOD data) ***")

# Also check what day of week Jan 27 and 28 are
print("\n" + "="*70)
print("CALENDAR CHECK:")
print("-"*70)
from datetime import date
for d in range(24, 31):
    dt = date(2026, 1, d)
    print(f"Jan {d}, 2026 = {dt.strftime('%A')}")
