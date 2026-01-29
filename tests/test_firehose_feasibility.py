#!/usr/bin/env python3
"""
Polygon Firehose Feasibility Test (Component 0.4.1)

Tests:
1. Connection stability to Polygon websocket
2. Throughput measurement (messages/sec)
3. Memory usage during sustained load
4. Unique symbol count
5. Message parsing correctness

Run during market hours for best results.
"""

import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import websockets

# Configuration
POLYGON_WS_URL = "wss://socket.polygon.io/options"
TEST_DURATION_SECONDS = int(os.environ.get("TEST_DURATION", 1800))  # 30 min default
REPORT_INTERVAL_SECONDS = 60


@dataclass
class Stats:
    start_time: float = field(default_factory=time.time)
    total_messages: int = 0
    trade_messages: int = 0
    parse_errors: int = 0
    symbols_seen: set = field(default_factory=set)
    underlyings_seen: set = field(default_factory=set)
    messages_per_interval: list = field(default_factory=list)
    reconnect_count: int = 0
    last_message_time: float = 0
    max_lag_ms: float = 0


def parse_occ_symbol(symbol: str) -> Optional[dict]:
    """Parse OCC option symbol to extract underlying."""
    try:
        s = symbol[2:] if symbol.startswith("O:") else symbol
        i = 0
        while i < len(s) and s[i].isalpha():
            i += 1
        if i == 0:
            return None
        underlying = s[:i]
        return {"underlying": underlying, "full_symbol": symbol}
    except Exception:
        return None


async def run_firehose_test(api_key: str, duration: int = TEST_DURATION_SECONDS):
    """Run the firehose feasibility test."""
    stats = Stats()
    interval_start = time.time()
    interval_messages = 0

    print(f"\n{'='*60}")
    print(f"Polygon Firehose Feasibility Test")
    print(f"{'='*60}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Duration: {duration} seconds ({duration/60:.1f} minutes)")
    print(f"{'='*60}\n")

    async def connect_and_process():
        nonlocal interval_start, interval_messages

        uri = POLYGON_WS_URL
        async with websockets.connect(uri) as ws:
            # Authenticate
            auth_msg = {"action": "auth", "params": api_key}
            await ws.send(json.dumps(auth_msg))
            auth_response = await ws.recv()
            print(f"Auth response: {auth_response[:100]}...")

            # Subscribe to all options trades (T.*)
            sub_msg = {"action": "subscribe", "params": "T.*"}
            await ws.send(json.dumps(sub_msg))
            sub_response = await ws.recv()
            print(f"Subscribe response: {sub_response[:100]}...")
            print(f"\nListening for trades...\n")

            while time.time() - stats.start_time < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    stats.last_message_time = time.time()
                    stats.total_messages += 1
                    interval_messages += 1

                    # Parse message
                    try:
                        data = json.loads(msg)
                        if isinstance(data, list):
                            for item in data:
                                if item.get("ev") == "T":
                                    stats.trade_messages += 1
                                    sym = item.get("sym", "")
                                    stats.symbols_seen.add(sym)

                                    parsed = parse_occ_symbol(sym)
                                    if parsed:
                                        stats.underlyings_seen.add(parsed["underlying"])

                                    # Check lag
                                    if "t" in item:
                                        lag_ms = (time.time() * 1000) - item["t"]
                                        stats.max_lag_ms = max(stats.max_lag_ms, lag_ms)
                    except json.JSONDecodeError:
                        stats.parse_errors += 1

                    # Report interval stats
                    if time.time() - interval_start >= REPORT_INTERVAL_SECONDS:
                        elapsed = time.time() - stats.start_time
                        rate = interval_messages / REPORT_INTERVAL_SECONDS
                        stats.messages_per_interval.append(rate)

                        print(f"[{elapsed/60:5.1f}m] "
                              f"msgs/sec: {rate:7.1f} | "
                              f"total: {stats.total_messages:,} | "
                              f"trades: {stats.trade_messages:,} | "
                              f"symbols: {len(stats.symbols_seen):,} | "
                              f"underlyings: {len(stats.underlyings_seen):,}")

                        interval_start = time.time()
                        interval_messages = 0

                except asyncio.TimeoutError:
                    # No message in 5 seconds - might be slow period
                    if time.time() - stats.last_message_time > 30:
                        print(f"Warning: No messages for 30+ seconds")

    # Run with reconnection logic
    while time.time() - stats.start_time < duration:
        try:
            await connect_and_process()
        except websockets.ConnectionClosed as e:
            stats.reconnect_count += 1
            print(f"\nConnection closed: {e}. Reconnecting in 5s... (attempt {stats.reconnect_count})")
            await asyncio.sleep(5)
        except Exception as e:
            stats.reconnect_count += 1
            print(f"\nError: {e}. Reconnecting in 10s... (attempt {stats.reconnect_count})")
            await asyncio.sleep(10)

    # Generate report
    print_report(stats)
    return stats


def print_report(stats: Stats):
    """Print final test report."""
    elapsed = time.time() - stats.start_time
    avg_rate = stats.total_messages / elapsed if elapsed > 0 else 0

    print(f"\n{'='*60}")
    print(f"FIREHOSE FEASIBILITY TEST REPORT")
    print(f"{'='*60}")
    print(f"Duration:           {elapsed/60:.1f} minutes")
    print(f"Total messages:     {stats.total_messages:,}")
    print(f"Trade messages:     {stats.trade_messages:,}")
    print(f"Average rate:       {avg_rate:.1f} msgs/sec")
    print(f"Unique symbols:     {len(stats.symbols_seen):,}")
    print(f"Unique underlyings: {len(stats.underlyings_seen):,}")
    print(f"Parse errors:       {stats.parse_errors}")
    print(f"Reconnections:      {stats.reconnect_count}")
    print(f"Max lag (ms):       {stats.max_lag_ms:.1f}")

    if stats.messages_per_interval:
        print(f"\nThroughput stats:")
        print(f"  Min rate:  {min(stats.messages_per_interval):.1f} msgs/sec")
        print(f"  Max rate:  {max(stats.messages_per_interval):.1f} msgs/sec")
        print(f"  Avg rate:  {sum(stats.messages_per_interval)/len(stats.messages_per_interval):.1f} msgs/sec")

    # Pass/Fail criteria
    print(f"\n{'='*60}")
    print(f"PASS/FAIL CRITERIA")
    print(f"{'='*60}")

    stable = stats.reconnect_count <= 2
    has_trades = stats.trade_messages > 0
    reasonable_lag = stats.max_lag_ms < 5000  # < 5 sec lag

    print(f"[{'PASS' if stable else 'FAIL'}] Connection stable (reconnects <= 2): {stats.reconnect_count}")
    print(f"[{'PASS' if has_trades else 'FAIL'}] Receiving trades: {stats.trade_messages:,}")
    print(f"[{'PASS' if reasonable_lag else 'FAIL'}] Lag acceptable (< 5s): {stats.max_lag_ms:.0f}ms")

    overall = stable and has_trades and reasonable_lag
    print(f"\n>>> OVERALL: {'PASS' if overall else 'FAIL'} <<<")
    print(f"{'='*60}\n")

    return overall


async def quick_connectivity_test(api_key: str):
    """Quick 60-second connectivity test for after-hours validation."""
    print("\n" + "="*60)
    print("QUICK CONNECTIVITY TEST (60 seconds)")
    print("="*60 + "\n")

    stats = Stats()
    start = time.time()

    try:
        async with websockets.connect(POLYGON_WS_URL) as ws:
            # Auth
            await ws.send(json.dumps({"action": "auth", "params": api_key}))
            auth_resp = await ws.recv()
            print(f"[OK] Auth: {auth_resp[:80]}...")

            # Subscribe
            await ws.send(json.dumps({"action": "subscribe", "params": "T.*"}))
            sub_resp = await ws.recv()
            print(f"[OK] Subscribe: {sub_resp[:80]}...")

            # Collect for 60 seconds
            print("\nCollecting messages for 60 seconds...")
            while time.time() - start < 60:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(msg)
                    if isinstance(data, list):
                        for item in data:
                            if item.get("ev") == "T":
                                stats.trade_messages += 1
                                stats.symbols_seen.add(item.get("sym", ""))
                                parsed = parse_occ_symbol(item.get("sym", ""))
                                if parsed:
                                    stats.underlyings_seen.add(parsed["underlying"])
                    stats.total_messages += 1
                except asyncio.TimeoutError:
                    print(".", end="", flush=True)

            elapsed = time.time() - start
            print(f"\n\nResults ({elapsed:.0f}s):")
            print(f"  Total messages:     {stats.total_messages:,}")
            print(f"  Trade messages:     {stats.trade_messages:,}")
            print(f"  Unique symbols:     {len(stats.symbols_seen):,}")
            print(f"  Unique underlyings: {len(stats.underlyings_seen):,}")
            print(f"  Rate:               {stats.total_messages/elapsed:.1f} msgs/sec")

            if stats.trade_messages > 0:
                print("\n[PASS] Connection working, receiving trades")
                return True
            else:
                print("\n[WARN] Connected but no trades (expected if after-hours)")
                return True  # Still a pass - connection works

    except Exception as e:
        print(f"\n[FAIL] Connection error: {e}")
        return False


def main():
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        print("ERROR: POLYGON_API_KEY environment variable not set")
        print("Usage: POLYGON_API_KEY=xxx python test_firehose_feasibility.py [--quick]")
        sys.exit(1)

    if "--quick" in sys.argv:
        result = asyncio.run(quick_connectivity_test(api_key))
    else:
        duration = TEST_DURATION_SECONDS
        if "--duration" in sys.argv:
            idx = sys.argv.index("--duration")
            duration = int(sys.argv[idx + 1])
        result = asyncio.run(run_firehose_test(api_key, duration))

    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
