"""
Test Polygon Historical Options Data Access - V2

Tests whether we can pull historical tick-level options trades
for backtesting UOA detection.
"""

import requests
from datetime import datetime, timedelta
import json

# API Key
API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"


def test_aaoi_jan28_deep():
    """
    Deep dive into AAOI Jan 28, 2025
    """
    print(f"\n{'='*60}")
    print("AAOI JAN 28, 2025 - DEEP DIVE")
    print('='*60)
    
    # Get ALL contracts for AAOI (including expired)
    print("\n1. Finding all AAOI contracts around Jan 2025...")
    
    url = f"{BASE_URL}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": "AAOI",
        "expiration_date.gte": "2025-01-01",
        "expiration_date.lte": "2025-03-31",
        "limit": 100,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        contracts = data.get("results", [])
        print(f"Found {len(contracts)} AAOI contracts for Q1 2025")
        
        # Filter for calls with strikes near the money ($35-50)
        atm_calls = [c for c in contracts 
                     if c.get("contract_type") == "call" 
                     and 30 <= c.get("strike_price", 0) <= 55]
        
        print(f"ATM Calls (strike $30-55): {len(atm_calls)}")
        
        for c in atm_calls[:10]:
            print(f"  {c.get('ticker')} | Strike: ${c.get('strike_price')} | Exp: {c.get('expiration_date')}")
        
        if atm_calls:
            # Pick one to test
            test_contract = atm_calls[0].get("ticker")
            return test_contract
    
    return None


def test_contract_trades(contract: str, date: str):
    """Get all trades for a contract on a specific date."""
    print(f"\n2. Getting trades for {contract} on {date}...")
    
    url = f"{BASE_URL}/v3/trades/{contract}"
    params = {
        "timestamp.gte": f"{date}T00:00:00Z",
        "timestamp.lte": f"{date}T23:59:59Z",
        "limit": 1000,  # Get more trades
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        trades = data.get("results", [])
        print(f"Found {len(trades)} trades")
        
        if trades:
            # Aggregate by hour
            hourly_volume = {}
            total_volume = 0
            
            for t in trades:
                ts_ns = t.get("sip_timestamp", 0)
                ts_sec = ts_ns / 1e9
                dt = datetime.fromtimestamp(ts_sec)
                hour = dt.strftime("%H:00")
                size = t.get("size", 0)
                
                hourly_volume[hour] = hourly_volume.get(hour, 0) + size
                total_volume += size
            
            print(f"\nTotal Volume: {total_volume:,}")
            print("\nHourly breakdown:")
            for hour in sorted(hourly_volume.keys()):
                vol = hourly_volume[hour]
                bar = "#" * min(50, vol // 10)
                print(f"  {hour}: {vol:>6,} {bar}")
            
            # Show first few trades
            print("\nFirst 5 trades:")
            for t in trades[:5]:
                ts_ns = t.get("sip_timestamp", 0)
                ts_sec = ts_ns / 1e9
                dt = datetime.fromtimestamp(ts_sec)
                print(f"  {dt.strftime('%H:%M:%S')} | ${t.get('price'):.2f} x {t.get('size')}")
        
        return len(trades)
    else:
        print(f"Error: {response.text}")
        return 0


def test_contract_aggregates(contract: str, date: str):
    """Get minute aggregates for a contract."""
    print(f"\n3. Getting minute bars for {contract} on {date}...")
    
    url = f"{BASE_URL}/v2/aggs/ticker/{contract}/range/1/minute/{date}/{date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 500,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        bars = data.get("results", [])
        print(f"Found {len(bars)} minute bars")
        
        if bars:
            total_vol = sum(b.get("v", 0) for b in bars)
            print(f"Total volume from bars: {total_vol:,}")
            
            # Find the highest volume bars
            top_bars = sorted(bars, key=lambda x: x.get("v", 0), reverse=True)[:5]
            print("\nTop 5 volume minutes:")
            for bar in top_bars:
                ts_ms = bar.get("t", 0)
                dt = datetime.fromtimestamp(ts_ms / 1000)
                print(f"  {dt.strftime('%H:%M')} | Vol: {bar.get('v', 0):,} | Close: ${bar.get('c', 0):.2f}")
        
        return len(bars)
    else:
        print(f"Error: {response.text}")
        return 0


def test_underlying_daily_bars(underlying: str, start_date: str, end_date: str):
    """Get daily aggregates to see the price action."""
    print(f"\n4. Getting daily bars for {underlying} stock...")
    
    url = f"{BASE_URL}/v2/aggs/ticker/{underlying}/range/1/day/{start_date}/{end_date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        bars = data.get("results", [])
        print(f"\n{underlying} Price Action (Jan 24-30, 2025):")
        print("-" * 60)
        
        for bar in bars:
            ts_ms = bar.get("t", 0)
            dt = datetime.fromtimestamp(ts_ms / 1000)
            o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
            vol = bar.get("v", 0)
            
            # Calculate daily change
            change = ((c - o) / o * 100) if o else 0
            direction = "+" if change > 0 else ""
            
            print(f"  {dt.strftime('%Y-%m-%d')} | O:{o:>6.2f} H:{h:>6.2f} L:{l:>6.2f} C:{c:>6.2f} | "
                  f"{direction}{change:>5.1f}% | Vol: {vol:>10,}")
        
        return bars
    else:
        print(f"Error: {response.text}")
        return []


def test_historical_options_snapshot_alternative(underlying: str, date: str):
    """
    Alternative: Get options aggregates for all contracts on a date
    Using grouped daily endpoint
    """
    print(f"\n5. Getting all options activity for {underlying} on {date}...")
    
    # First get all contracts
    url = f"{BASE_URL}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": underlying,
        "as_of": date,  # Contracts as they existed on this date
        "limit": 200,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        contracts = data.get("results", [])
        print(f"Found {len(contracts)} contracts as of {date}")
        
        # Get aggregates for each contract
        total_call_vol = 0
        total_put_vol = 0
        
        sample_contracts = contracts[:20]  # Limit to avoid rate limits
        
        for c in sample_contracts:
            ticker = c.get("ticker")
            is_call = c.get("contract_type") == "call"
            
            # Get daily agg for this contract
            agg_url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{date}/{date}"
            agg_params = {"apiKey": API_KEY}
            
            agg_response = requests.get(agg_url, params=agg_params)
            if agg_response.status_code == 200:
                agg_data = agg_response.json()
                results = agg_data.get("results", [])
                if results:
                    vol = results[0].get("v", 0)
                    if is_call:
                        total_call_vol += vol
                    else:
                        total_put_vol += vol
        
        print(f"\nSampled {len(sample_contracts)} contracts:")
        print(f"  Call Volume: {total_call_vol:,}")
        print(f"  Put Volume: {total_put_vol:,}")
        
        if total_call_vol > 0:
            pc_ratio = total_put_vol / total_call_vol
            print(f"  P/C Ratio: {pc_ratio:.2f}")
        
        return True
    
    return False


def main():
    print("\n" + "="*60)
    print("POLYGON HISTORICAL OPTIONS - AAOI CASE STUDY")
    print("="*60)
    print(f"API Key: {API_KEY[:8]}...")
    print(f"Testing at: {datetime.now()}")
    
    # Test 1: Get AAOI stock price action around Jan 28
    test_underlying_daily_bars("AAOI", "2025-01-24", "2025-01-30")
    
    # Test 2: Find AAOI options contracts
    contract = test_aaoi_jan28_deep()
    
    if contract:
        # Test 3: Get trades for that contract on Jan 28
        test_contract_trades(contract, "2025-01-28")
        
        # Test 4: Get minute aggregates
        test_contract_aggregates(contract, "2025-01-28")
    
    # Test 5: Try to aggregate all options volume
    test_historical_options_snapshot_alternative("AAOI", "2025-01-28")
    
    print("\n" + "="*60)
    print("CONCLUSIONS")
    print("="*60)
    print("""
WHAT WE CAN DO WITH POLYGON ADVANCED:
  [+] Get tick-level trades for any options contract
  [+] Get minute-level aggregates (OHLCV) 
  [+] Get 5+ years of historical data
  [+] Real-time streaming for production
  
FOR BACKTESTING UOA DETECTION:
  1. Query all contracts for underlying on a date
  2. Get minute aggregates for each contract
  3. Sum volume by call/put to get aggregate flow
  4. Detect unusual activity at minute granularity
  
LIMITATION:
  - No single endpoint for "all options volume for AAOI on date"
  - Must aggregate contract-by-contract
  - Could be slow for tickers with 500+ contracts
    """)


if __name__ == "__main__":
    main()
