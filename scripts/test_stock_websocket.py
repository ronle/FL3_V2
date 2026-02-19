#!/usr/bin/env python3
"""
Test Alpaca SIP Stock WebSocket Connectivity

Quick test to verify the Alpaca SIP WebSocket works (real-time trades + quotes).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from firehose.stock_price_monitor import StockPriceMonitor


async def test_websocket():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
        sys.exit(1)

    print("=" * 60)
    print("ALPACA SIP STOCK WEBSOCKET TEST")
    print("=" * 60)
    print(f"API Key: {api_key[:8]}...")

    monitor = StockPriceMonitor(api_key, secret_key)

    # Track updates
    trade_count = 0
    quote_count = 0

    def on_trade(trade):
        nonlocal trade_count
        trade_count += 1
        if trade_count <= 5:  # Only print first 5
            print(f"  TRADE: {trade.symbol} ${trade.price:.2f} x {trade.size}")

    def on_quote(quote):
        nonlocal quote_count
        quote_count += 1
        if quote_count <= 3:  # Only print first 3
            print(f"  QUOTE: {quote.symbol} bid=${quote.bid:.2f} ask=${quote.ask:.2f}")

    monitor.on_trade = on_trade
    monitor.on_quote = on_quote

    print("\n1. Connecting to wss://stream.data.alpaca.markets/v2/sip ...")
    started = await monitor.start()

    if not started:
        print("FAILED to connect!")
        sys.exit(1)

    print("   Connected and authenticated!")

    print("\n2. Subscribing to AAPL, SPY, TSLA ...")
    await monitor.subscribe(["AAPL", "SPY", "TSLA"])
    print(f"   Subscribed to: {monitor.subscribed_symbols}")

    print("\n3. Waiting for initial data (10 seconds) ...")
    await asyncio.sleep(10)

    mid_metrics = monitor.get_metrics()
    print(f"   Trades so far: {mid_metrics['trades_received']}")
    print(f"   Symbols tracked: {sorted(monitor.subscribed_symbols)}")

    # --- Dynamic subscribe ---
    print("\n4. Subscribing to NVDA, MSFT mid-stream ...")
    await monitor.subscribe(["NVDA", "MSFT"])
    print(f"   Subscribed to: {sorted(monitor.subscribed_symbols)}")
    assert "NVDA" in monitor.subscribed_symbols, "NVDA not in subscribed set"
    assert "MSFT" in monitor.subscribed_symbols, "MSFT not in subscribed set"
    assert len(monitor.subscribed_symbols) == 5, f"Expected 5 symbols, got {len(monitor.subscribed_symbols)}"
    print("   OK — 5 symbols active")

    print("\n5. Waiting for data on new symbols (10 seconds) ...")
    await asyncio.sleep(10)

    for sym in ["NVDA", "MSFT"]:
        ps = monitor.get_price(sym)
        if ps and ps.price:
            print(f"   {sym}: ${ps.price:.2f}")
        else:
            print(f"   {sym}: No data yet (normal after hours)")

    # --- Dynamic unsubscribe ---
    print("\n6. Unsubscribing TSLA ...")
    trades_before_unsub = monitor.metrics.trades_received
    await monitor.unsubscribe(["TSLA"])
    print(f"   Subscribed to: {sorted(monitor.subscribed_symbols)}")
    assert "TSLA" not in monitor.subscribed_symbols, "TSLA still in subscribed set"
    assert len(monitor.subscribed_symbols) == 4, f"Expected 4 symbols, got {len(monitor.subscribed_symbols)}"
    assert monitor.get_price("TSLA") is None, "TSLA price state not cleared"
    print("   OK — TSLA removed, price state cleared")

    # --- set_symbols (add + remove in one call) ---
    print("\n7. set_symbols(['SPY', 'AAPL', 'AMZN']) — drops NVDA, MSFT; adds AMZN ...")
    await monitor.set_symbols(["SPY", "AAPL", "AMZN"])
    print(f"   Subscribed to: {sorted(monitor.subscribed_symbols)}")
    assert monitor.subscribed_symbols == {"SPY", "AAPL", "AMZN"}, f"Unexpected set: {monitor.subscribed_symbols}"
    print("   OK — exactly SPY, AAPL, AMZN")

    print("\n8. Waiting for AMZN data (10 seconds) ...")
    await asyncio.sleep(10)

    ps = monitor.get_price("AMZN")
    if ps and ps.price:
        print(f"   AMZN: ${ps.price:.2f}")
    else:
        print(f"   AMZN: No data yet (normal after hours)")

    # --- Final results ---
    print("\n9. Final results:")
    metrics = monitor.get_metrics()
    print(f"   Total trades received: {metrics['trades_received']}")
    print(f"   Total quotes received: {metrics['quotes_received']}")
    print(f"   Messages/sec: {metrics['messages_per_second']:.2f}")
    print(f"   Final symbols: {sorted(monitor.subscribed_symbols)}")

    print("\n10. Final prices:")
    for sym in sorted(monitor.subscribed_symbols):
        price_state = monitor.get_price(sym)
        if price_state and price_state.price:
            parts = f"   {sym}: ${price_state.price:.2f}"
            if price_state.bid:
                parts += f" (bid=${price_state.bid:.2f}, ask=${price_state.ask:.2f})"
            print(parts)
        else:
            print(f"   {sym}: No data received")

    await monitor.stop()

    print("\n" + "=" * 60)
    if metrics['trades_received'] > 0 or metrics['quotes_received'] > 0:
        print("TEST PASSED - WebSocket + dynamic sub/unsub working!")
    else:
        print("TEST INCONCLUSIVE - No data received (market may be closed)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_websocket())
