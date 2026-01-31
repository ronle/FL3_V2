#!/usr/bin/env python
"""
Paper Trading Startup Script

Quick way to start paper trading with all checks.

Usage:
    python start_paper_trading.py              # Full mode
    python start_paper_trading.py --dry-run    # Dry run (no real trades)
    python start_paper_trading.py --test       # Connectivity test only
    python start_paper_trading.py --premarket  # Generate TA cache only
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import pytz

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent))

ET = pytz.timezone("America/New_York")


def check_environment():
    """Verify all required environment variables are set."""
    required = {
        "POLYGON_API_KEY": "Polygon API key for firehose",
        "ALPACA_API_KEY": "Alpaca API key for trading",
        "ALPACA_SECRET_KEY": "Alpaca secret key for trading",
    }

    missing = []
    for key, desc in required.items():
        if not os.environ.get(key):
            missing.append(f"  {key}: {desc}")

    if missing:
        print("ERROR: Missing required environment variables:")
        for m in missing:
            print(m)
        print("\nSet these in your environment or .env file")
        return False

    return True


def check_market_status():
    """Check if market is open or when it opens."""
    now = datetime.now(ET)
    weekday = now.weekday()

    print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} ET")

    if weekday >= 5:
        print("Market Status: CLOSED (Weekend)")
        if weekday == 5:
            print("  Opens: Monday 9:30 AM ET")
        else:
            print("  Opens: Tomorrow 9:30 AM ET")
        return False

    hour, minute = now.hour, now.minute
    time_mins = hour * 60 + minute

    pre_market = 4 * 60  # 4:00 AM
    market_open = 9 * 60 + 30  # 9:30 AM
    market_close = 16 * 60  # 4:00 PM

    if time_mins < pre_market:
        print("Market Status: CLOSED (Pre-hours)")
        print(f"  Pre-market starts: 4:00 AM ET ({(pre_market - time_mins)//60}h {(pre_market - time_mins)%60}m)")
        return False
    elif time_mins < market_open:
        print("Market Status: PRE-MARKET")
        print(f"  Market opens: 9:30 AM ET ({(market_open - time_mins)//60}h {(market_open - time_mins)%60}m)")
        return "premarket"
    elif time_mins < market_close:
        print("Market Status: OPEN")
        mins_left = market_close - time_mins
        print(f"  Closes in: {mins_left//60}h {mins_left%60}m")
        return True
    else:
        print("Market Status: CLOSED (After-hours)")
        print("  Next open: Tomorrow 9:30 AM ET")
        return False


async def run_premarket():
    """Run pre-market TA cache generation."""
    print("\n" + "=" * 60)
    print("PRE-MARKET TA CACHE GENERATION")
    print("=" * 60)

    from paper_trading.premarket_ta_cache import main as premarket_main
    await premarket_main()


async def run_test():
    """Run connectivity tests."""
    print("\n" + "=" * 60)
    print("CONNECTIVITY TEST")
    print("=" * 60)

    from paper_trading.main import main as paper_main
    sys.argv = ["", "--test"]
    await paper_main()


async def run_trading(dry_run: bool = False):
    """Run the paper trading engine."""
    print("\n" + "=" * 60)
    print("PAPER TRADING ENGINE")
    print("=" * 60)

    from paper_trading.main import main as paper_main

    if dry_run:
        sys.argv = ["", "--dry-run"]
    else:
        sys.argv = [""]

    await paper_main()


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Launcher")
    parser.add_argument("--dry-run", action="store_true",
                       help="Run without executing real trades")
    parser.add_argument("--test", action="store_true",
                       help="Run connectivity test only")
    parser.add_argument("--premarket", action="store_true",
                       help="Generate TA cache only")
    parser.add_argument("--force", action="store_true",
                       help="Run even if market is closed")
    args = parser.parse_args()

    print("=" * 60)
    print("FL3 V2 - Paper Trading System")
    print("=" * 60 + "\n")

    # Check environment
    if not check_environment():
        sys.exit(1)

    print("\nEnvironment: OK\n")

    # Check market status
    market_status = check_market_status()

    if args.test:
        asyncio.run(run_test())
        return

    if args.premarket:
        asyncio.run(run_premarket())
        return

    # Warn if market closed
    if not market_status and not args.force:
        print("\nMarket is closed. Use --force to run anyway (for testing)")
        print("Or use --premarket to generate TA cache")
        print("Or use --test to run connectivity test")
        sys.exit(0)

    # Suggest premarket if needed
    if market_status == "premarket":
        print("\nTip: Run --premarket first to generate TA cache")

    # Run trading
    if args.dry_run:
        print("\n*** DRY RUN MODE - No real trades will be executed ***\n")

    asyncio.run(run_trading(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
