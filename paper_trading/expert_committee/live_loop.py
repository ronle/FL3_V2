"""
Account E Live Loop — Run expert committee cycles + exit checks during market hours.

- Expert cycle (4 agents + PM + execute): every 60 minutes
- Exit checks (stops/targets/EOD): every 30 seconds
- Market hours only: 9:45 AM - 3:55 PM ET

Usage:
    ALPACA_API_KEY_E=... ALPACA_SECRET_KEY_E=... python -m paper_trading.expert_committee.live_loop
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from paper_trading.expert_committee.runner_v2 import run_cycle
from paper_trading.expert_committee.account_e_executor import AccountEExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).resolve().parent.parent.parent / "temp" / "account_e_live.log",
            mode="a",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

# Timing
CYCLE_INTERVAL_SEC = 3600       # 60 min between expert cycles
EXIT_CHECK_INTERVAL_SEC = 30    # 30 sec between exit checks
MARKET_OPEN = dt_time(9, 45)    # Start scanning at 9:45 AM ET
MARKET_CLOSE = dt_time(15, 58)  # Loop exits after this
LAST_ENTRY = dt_time(15, 30)    # No new entries after 3:30 PM ET


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url.strip()
    return "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"


def _is_market_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _can_enter() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= LAST_ENTRY


def main():
    api_key = os.environ.get("ALPACA_API_KEY_E", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY_E", "")
    signal_only = not (api_key and secret_key)

    db_url = _get_db_url()
    executor = None
    if not signal_only:
        executor = AccountEExecutor(db_url, api_key, secret_key)
    else:
        logger.warning("ALPACA_API_KEY_E / SECRET not set — running in SIGNAL-ONLY mode (no trades)")

    logger.info("=" * 60)
    logger.info("Account E Live Loop starting")
    logger.info(f"  Expert cycle: every {CYCLE_INTERVAL_SEC // 60} min")
    logger.info(f"  Exit checks: every {EXIT_CHECK_INTERVAL_SEC} sec")
    logger.info(f"  Market window: {MARKET_OPEN} - {MARKET_CLOSE} ET")
    logger.info(f"  Last entry: {LAST_ENTRY} ET")
    logger.info(f"  Overnight holds: ENABLED (PM decides all exits)")
    logger.info(f"  Mode: {'LIVE TRADING' if executor else 'SIGNAL-ONLY (no Alpaca creds)'}")
    logger.info("=" * 60)

    last_cycle_time = 0

    while True:
        try:
            now = time.time()
            now_et = datetime.now(ET)

            if not _is_market_hours():
                if now_et.weekday() < 5 and now_et.time() > MARKET_CLOSE:
                    logger.info("[E] Market closed. Exiting loop (positions carry overnight).")
                    break
                time.sleep(30)
                continue

            # --- Exit checks (every 30s during market hours) ---
            if executor:
                exit_results = executor.check_exits()
                if exit_results:
                    for r in exit_results:
                        if r.get("success"):
                            logger.info(f"[E] EXIT: {r['symbol']} ({r['reason']}) P&L ${r.get('pnl', 0):+,.2f}")

            # --- Expert cycle (every 60 min, only if we can still enter) ---
            time_since_cycle = now - last_cycle_time
            if time_since_cycle >= CYCLE_INTERVAL_SEC and _can_enter():
                logger.info("=" * 60)
                logger.info(f"[E] Starting expert cycle at {now_et.strftime('%H:%M ET')}")
                logger.info("=" * 60)

                try:
                    # Run experts + PM
                    cycle_result = run_cycle()
                    logger.info(f"[E] Cycle result: {json.dumps(cycle_result)}")

                    # Execute PM EXIT decisions first (free capital before entries)
                    pm_exits = cycle_result.get("pm_exits", [])
                    if pm_exits and executor:
                        logger.info(f"[E] PM requested {len(pm_exits)} exits: {pm_exits}")
                        exit_results = executor.execute_pm_exits(pm_exits)
                        for r in exit_results:
                            if r.get("success"):
                                logger.info(
                                    f"[E] PM EXIT: {r['symbol']} ({r['reason']}) "
                                    f"P&L ${r.get('pnl', 0):+,.2f}"
                                )
                            else:
                                logger.warning(f"[E] PM exit failed: {r}")

                    # Execute any PM entry decisions
                    if cycle_result.get("pm_decisions", 0) > 0:
                        if executor:
                            exec_results = executor.execute_pending()
                            for r in exec_results:
                                if r.get("success"):
                                    logger.info(
                                        f"[E] TRADE: {r['side'].upper()} {r['shares']} {r['symbol']} "
                                        f"@ ${r['price']:.2f}"
                                    )
                                else:
                                    logger.warning(f"[E] Trade failed: {r}")
                        else:
                            logger.info(f"[E] PM produced {cycle_result['pm_decisions']} decisions (signal-only mode, not executing)")
                    else:
                        logger.info("[E] PM produced 0 entry decisions this cycle")

                except Exception as e:
                    logger.error(f"[E] Expert cycle failed: {e}", exc_info=True)

                last_cycle_time = now

            time.sleep(EXIT_CHECK_INTERVAL_SEC)

        except KeyboardInterrupt:
            logger.info("[E] Interrupted — shutting down")
            break
        except Exception as e:
            logger.error(f"[E] Unexpected error: {e}", exc_info=True)
            time.sleep(60)

    logger.info("[E] Live loop stopped")


if __name__ == "__main__":
    main()
