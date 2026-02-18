#!/usr/bin/env python3
"""
Test Polygon Stock WebSocket Connectivity

Quick test to verify the stock price WebSocket works.
"""

import asyncio
import os
import sys

sys.path.insert(0, '/app')

from firehose.stock_price_monitor import StockPriceMonitor


async def test_websocket():
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        print("ERROR: POLYGON_API_KEY not set")
        sys.exit(1)

    print("=" * 60)
    print("POLYGON STOCK WEBSOCKET TEST")
    print("=" * 60)
    print(f"API Key: {api_key[:8]}...")

    monitor = StockPriceMonitor(api_key)

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

    print("\n1. Connecting to wss://socket.polygon.io/stocks ...")
    started = await monitor.start()

    if not started:
        print("FAILED to connect!")
        sys.exit(1)

    print("   Connected and authenticated!")

    print("\n2. Subscribing to AAPL, SPY, TSLA ...")
    await monitor.subscribe(["AAPL", "SPY", "TSLA"])
    print(f"   Subscribed to: {monitor.subscribed_symbols}")

    print("\n3. Waiting for price updates (15 seconds) ...")
    await asyncio.sleep(15)

    print("\n4. Results:")
    metrics = monitor.get_metrics()
    print(f"   Trades received: {metrics['trades_received']}")
    print(f"   Quotes received: {metrics['quotes_received']}")
    print(f"   Messages/sec: {metrics['messages_per_second']:.2f}")

    print("\n5. Final prices:")
    for sym in ["AAPL", "SPY", "TSLA"]:
        price_state = monitor.get_price(sym)
        if price_state and price_state.price:
            print(f"   {sym}: ${price_state.price:.2f} "
                  f"(bid=${price_state.bid:.2f}, ask=${price_state.ask:.2f})"
                  if price_state.bid else f"   {sym}: ${price_state.price:.2f}")
        else:
            print(f"   {sym}: No data received")

    await monitor.stop()

    print("\n" + "=" * 60)
    if metrics['trades_received'] > 0 or metrics['quotes_received'] > 0:
        print("TEST PASSED - WebSocket is working!")
    else:
        print("TEST INCONCLUSIVE - No data received (market may be closed)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_websocket())
