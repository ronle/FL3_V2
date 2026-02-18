"""
Test Polygon Historical Options Data Access

Tests whether we can pull historical tick-level options trades
for backtesting UOA detection.

Key endpoints to test:
1. Options Trades - tick level trades for a contract
2. Options Aggregates - minute/hour bars for a contract  
3. Options Chain - list all contracts for underlying
"""

import requests
from datetime import datetime, timedelta
import json

# API Key
API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"


def test_options_contracts(underlying: str = "AAOI"):
    """
    Test 1: Get all options contracts for an underlying
    
    This tells us what contracts exist/existed for a symbol.
    """
    print(f"\n{'='*60}")
    print(f"TEST 1: Options Contracts for {underlying}")
    print('='*60)
    
    url = f"{BASE_URL}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": underlying,
        "expired": "true",  # Include expired contracts for historical
        "limit": 10,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        print(f"Found {len(results)} contracts (showing first 10)")
        
        for c in results[:5]:
            print(f"  {c.get('ticker')} | {c.get('contract_type')} | "
                  f"Strike: ${c.get('strike_price')} | Exp: {c.get('expiration_date')}")
        
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_options_trades(contract_symbol: str, date: str):
    """
    Test 2: Get tick-level trades for a specific contract on a date
    
    This is the KEY endpoint for UOA detection backtesting.
    """
    print(f"\n{'='*60}")
    print(f"TEST 2: Options Trades for {contract_symbol} on {date}")
    print('='*60)
    
    # Polygon options trades endpoint
    url = f"{BASE_URL}/v3/trades/{contract_symbol}"
    params = {
        "timestamp.gte": f"{date}T09:30:00Z",  # Market open
        "timestamp.lte": f"{date}T16:00:00Z",  # Market close
        "limit": 50,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        print(f"Found {len(results)} trades")
        
        if results:
            print("\nSample trades:")
            for t in results[:10]:
                # Convert nanosecond timestamp
                ts_ns = t.get("sip_timestamp", 0)
                ts_sec = ts_ns / 1e9
                dt = datetime.fromtimestamp(ts_sec)
                
                print(f"  {dt.strftime('%H:%M:%S.%f')} | "
                      f"Price: ${t.get('price'):.2f} | "
                      f"Size: {t.get('size')} | "
                      f"Exchange: {t.get('exchange')}")
        
        return len(results) > 0
    else:
        print(f"Error: {response.text}")
        return False


def test_options_aggregates(contract_symbol: str, date: str):
    """
    Test 3: Get minute-level aggregates for a contract
    
    Useful for volume analysis without tick-level granularity.
    """
    print(f"\n{'='*60}")
    print(f"TEST 3: Options Minute Aggregates for {contract_symbol}")
    print('='*60)
    
    url = f"{BASE_URL}/v2/aggs/ticker/{contract_symbol}/range/1/minute/{date}/{date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        print(f"Found {len(results)} minute bars")
        
        if results:
            total_volume = sum(r.get("v", 0) for r in results)
            print(f"Total volume: {total_volume:,}")
            
            print("\nFirst 10 bars:")
            for bar in results[:10]:
                ts_ms = bar.get("t", 0)
                dt = datetime.fromtimestamp(ts_ms / 1000)
                
                print(f"  {dt.strftime('%H:%M')} | "
                      f"O:{bar.get('o'):.2f} H:{bar.get('h'):.2f} "
                      f"L:{bar.get('l'):.2f} C:{bar.get('c'):.2f} | "
                      f"Vol: {bar.get('v', 0):,}")
        
        return len(results) > 0
    else:
        print(f"Error: {response.text}")
        return False


def test_aaoi_jan28():
    """
    Test 4: AAOI specific test for Jan 28, 2025
    
    This is the day we want to analyze for the case study.
    """
    print(f"\n{'='*60}")
    print("TEST 4: AAOI Jan 28, 2025 - The Real Test")
    print('='*60)
    
    # First, find what contracts existed for AAOI around Jan 28
    url = f"{BASE_URL}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": "AAOI",
        "expiration_date.gte": "2025-01-28",
        "expiration_date.lte": "2025-02-28",  # Near-term expiries
        "contract_type": "call",
        "limit": 20,
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        contracts = data.get("results", [])
        print(f"Found {len(contracts)} AAOI call contracts expiring Jan-Feb 2025")
        
        if contracts:
            # Pick a likely ATM contract (AAOI was ~$37-45 on Jan 28)
            for c in contracts:
                strike = c.get("strike_price", 0)
                if 35 <= strike <= 50:
                    contract_ticker = c.get("ticker")
                    print(f"\nTesting contract: {contract_ticker}")
                    print(f"  Strike: ${strike}, Exp: {c.get('expiration_date')}")
                    
                    # Get trades for this contract on Jan 28
                    test_options_trades(contract_ticker, "2025-01-28")
                    test_options_aggregates(contract_ticker, "2025-01-28")
                    break
    else:
        print(f"Error finding contracts: {response.text}")


def test_aggregated_underlying_volume(underlying: str, date: str):
    """
    Test 5: Get total options volume for an underlying on a date
    
    This is what we'd use to detect unusual AGGREGATE activity.
    """
    print(f"\n{'='*60}")
    print(f"TEST 5: Total Options Activity for {underlying} on {date}")
    print('='*60)
    
    # Use the snapshot endpoint for a specific date
    # Note: This might only work for current day
    url = f"{BASE_URL}/v3/snapshot/options/{underlying}"
    params = {
        "apiKey": API_KEY
    }
    
    response = requests.get(url, params=params)
    print(f"Snapshot Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        print(f"Found {len(results)} contracts in snapshot")
        
        # Aggregate stats
        total_volume = 0
        total_oi = 0
        call_volume = 0
        put_volume = 0
        
        for r in results:
            day = r.get("day", {})
            details = r.get("details", {})
            vol = day.get("volume", 0)
            oi = r.get("open_interest", 0)
            
            total_volume += vol
            total_oi += oi
            
            if details.get("contract_type") == "call":
                call_volume += vol
            else:
                put_volume += vol
        
        pc_ratio = put_volume / call_volume if call_volume > 0 else 0
        
        print(f"\nAggregate Stats:")
        print(f"  Total Volume: {total_volume:,}")
        print(f"  Total OI: {total_oi:,}")
        print(f"  Call Volume: {call_volume:,}")
        print(f"  Put Volume: {put_volume:,}")
        print(f"  P/C Ratio: {pc_ratio:.2f}")
        
        return True
    else:
        print(f"Error: {response.text}")
        return False


def main():
    print("\n" + "="*60)
    print("POLYGON HISTORICAL OPTIONS DATA TEST")
    print("="*60)
    print(f"API Key: {API_KEY[:8]}...")
    print(f"Testing at: {datetime.now()}")
    
    # Run tests
    results = {}
    
    # Test 1: Basic contract lookup
    results["contracts"] = test_options_contracts("AAOI")
    
    # Test 2: Historical trades (using a known liquid contract)
    # SPY is always liquid, use a recent date
    results["trades_spy"] = test_options_trades("O:SPY250131C00600000", "2025-01-27")
    
    # Test 3: Minute aggregates
    results["aggregates"] = test_options_aggregates("O:SPY250131C00600000", "2025-01-27")
    
    # Test 4: AAOI specific
    test_aaoi_jan28()
    
    # Test 5: Current snapshot (for comparison)
    results["snapshot"] = test_aggregated_underlying_volume("AAOI", "today")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
    for test, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {test}: {status}")
    
    print("\n" + "="*60)
    print("KEY FINDINGS FOR FL3 V2 BACKTEST:")
    print("="*60)
    print("""
If trades endpoint works:
  → Can replay tick-level options flow for any historical date
  → Can detect EXACT time when unusual call buying started
  → Can backtest real-time UOA detection algorithm

If only aggregates work:
  → Can still detect unusual volume at minute granularity
  → Less precise but still useful for validation
    """)


if __name__ == "__main__":
    main()
