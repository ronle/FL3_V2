"""
Check if we have Jan 28 intraday data even if daily isn't ready yet
"""

import requests
from datetime import datetime

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"

print("="*70)
print("CHECKING JAN 28 (YESTERDAY) INTRADAY DATA")
print("="*70)

# Try to get minute bars for Jan 28
url = f"{BASE_URL}/v2/aggs/ticker/AAOI/range/1/minute/2026-01-28/2026-01-28"
params = {"adjusted": "true", "sort": "asc", "limit": 500, "apiKey": API_KEY}

response = requests.get(url, params=params)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    bars = data.get("results", [])
    print(f"Found {len(bars)} minute bars for Jan 28")
    
    if bars:
        # Show first and last few bars
        print("\nFirst 10 bars:")
        for bar in bars[:10]:
            ts_ms = bar.get("t", 0)
            dt = datetime.fromtimestamp(ts_ms / 1000)
            print(f"  {dt.strftime('%H:%M')} | O:${bar.get('o'):.2f} H:${bar.get('h'):.2f} "
                  f"L:${bar.get('l'):.2f} C:${bar.get('c'):.2f} | Vol: {bar.get('v', 0):,}")
        
        print(f"\n... ({len(bars)} total bars) ...")
        
        print("\nLast 10 bars:")
        for bar in bars[-10:]:
            ts_ms = bar.get("t", 0)
            dt = datetime.fromtimestamp(ts_ms / 1000)
            print(f"  {dt.strftime('%H:%M')} | O:${bar.get('o'):.2f} H:${bar.get('h'):.2f} "
                  f"L:${bar.get('l'):.2f} C:${bar.get('c'):.2f} | Vol: {bar.get('v', 0):,}")
        
        # Calculate OHLC from minute bars
        opens = [b.get('o') for b in bars if b.get('o')]
        highs = [b.get('h') for b in bars if b.get('h')]
        lows = [b.get('l') for b in bars if b.get('l')]
        closes = [b.get('c') for b in bars if b.get('c')]
        vols = [b.get('v', 0) for b in bars]
        
        if opens and closes:
            print(f"\nJan 28 Summary (from minute bars):")
            print(f"  Open: ${opens[0]:.2f}")
            print(f"  High: ${max(highs):.2f}")
            print(f"  Low: ${min(lows):.2f}")
            print(f"  Close: ${closes[-1]:.2f}")
            print(f"  Volume: {sum(vols):,}")
            print(f"  Change: {((closes[-1] - opens[0]) / opens[0] * 100):+.1f}%")
    else:
        print("No minute bars available for Jan 28 yet")
else:
    print(f"Error: {response.text}")

# Also try Jan 27 for comparison
print("\n" + "="*70)
print("JAN 27 INTRADAY (FOR COMPARISON)")
print("="*70)

url = f"{BASE_URL}/v2/aggs/ticker/AAOI/range/1/minute/2026-01-27/2026-01-27"
response = requests.get(url, params=params)

if response.status_code == 200:
    data = response.json()
    bars = data.get("results", [])
    print(f"Found {len(bars)} minute bars for Jan 27")
    
    if bars:
        opens = [b.get('o') for b in bars if b.get('o')]
        highs = [b.get('h') for b in bars if b.get('h')]
        lows = [b.get('l') for b in bars if b.get('l')]
        closes = [b.get('c') for b in bars if b.get('c')]
        vols = [b.get('v', 0) for b in bars]
        
        print(f"\nJan 27 Summary (from minute bars):")
        print(f"  Open: ${opens[0]:.2f}")
        print(f"  High: ${max(highs):.2f}")
        print(f"  Low: ${min(lows):.2f}")
        print(f"  Close: ${closes[-1]:.2f}")
        print(f"  Volume: {sum(vols):,}")
        print(f"  Change: {((closes[-1] - opens[0]) / opens[0] * 100):+.1f}%")
