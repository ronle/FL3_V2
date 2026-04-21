"""
Account E Executor — Execute PM decisions on Alpaca paper account.

Simple synchronous executor. Reads pending decisions from pm_decisions_e,
submits market orders to Alpaca, records trades in paper_trades_log_e.

Usage:
    # Execute all pending decisions:
    python -m paper_trading.expert_committee.account_e_executor

    # Check stops and EOD exits on open positions:
    python -m paper_trading.expert_committee.account_e_executor --check-exits
"""

import json
import logging
import os
import sys
from datetime import datetime, date, time as dt_time, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import pytz
import requests

from paper_trading.config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

ALPACA_BASE = "https://paper-api.alpaca.markets"

# Defaults — overridden by config if available
INTRADAY_STOP_PCT = -0.03   # -3% hard stop
SWING_STOP_PCT = -0.05      # -5% hard stop
MAX_POSITIONS = DEFAULT_CONFIG.ACCOUNT_E_MAX_POSITIONS
EOD_CLOSE_TIME = dt_time(15, 55)


class AccountEExecutor:
    """Execute PM decisions for Account E via Alpaca REST API."""

    def __init__(self, db_url: str, api_key: str, secret_key: str):
        self._db_url = db_url.strip()
        self._api_key = api_key
        self._secret_key = secret_key
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        self._shortable_cache: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_pending(self) -> list[dict]:
        """Fetch and execute all pending PM decisions. Returns list of results."""
        decisions = self._fetch_pending_decisions()
        if not decisions:
            logger.info("[E] No pending decisions")
            return []

        # Check position count
        open_count = len(self._get_open_positions())
        equity = self._get_equity()

        results = []
        for dec in decisions:
            if open_count >= MAX_POSITIONS:
                logger.warning(f"[E] Max positions ({MAX_POSITIONS}) reached — skipping {dec['symbol']}")
                self._mark_decision_failed(dec["decision_id"], "max_positions_reached")
                continue

            result = self._execute_one(dec, equity)
            results.append(result)
            if result.get("success"):
                open_count += 1

        return results

    def close_all_positions(self) -> list[dict]:
        """Close ALL open positions individually. Used for emergency only.

        Closes each position one-by-one to get accurate fill prices and P&L.
        """
        db_positions = self._get_open_positions()
        if not db_positions:
            logger.info("[E] No open positions to close")
            return []

        results = []
        for pos in db_positions:
            price = self._get_price(pos["symbol"]) or float(pos["entry_price"])
            result = self._close_position(pos, price, "emergency_close")
            results.append(result)

        return results

    def execute_pm_exits(self, symbols: list[str]) -> list[dict]:
        """Close positions by symbol, as directed by PM EXIT decisions."""
        if not symbols:
            return []

        positions = self._get_open_positions()
        pos_by_symbol = {p["symbol"].upper(): p for p in positions}
        results = []

        for sym in symbols:
            sym_upper = sym.upper()
            pos = pos_by_symbol.get(sym_upper)
            if not pos:
                logger.warning(f"[E] PM EXIT {sym_upper}: no open position found — skipping")
                continue

            price = self._get_price(sym_upper)
            if not price:
                logger.error(f"[E] PM EXIT {sym_upper}: could not get price — skipping")
                continue

            result = self._close_position(pos, price, "pm_exit")
            results.append(result)

        return results

    def check_exits(self) -> list[dict]:
        """Check all open positions for stop/target/EOD exits."""
        positions = self._get_open_positions()
        if not positions:
            logger.info("[E] No open positions")
            return []

        now_et = datetime.now(ET)
        results = []

        for pos in positions:
            symbol = pos["symbol"]
            price = self._get_price(symbol)
            if not price:
                continue

            entry_price = float(pos["entry_price"])
            direction = pos["direction"]
            trade_id = pos["id"]

            # Compute unrealized P&L
            if direction == "long":
                pnl_pct = (price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - price) / entry_price

            # Stop check
            stop_pct = INTRADAY_STOP_PCT if pos["holding_period"] == "intraday" else SWING_STOP_PCT
            if pos["stop_price"]:
                # Use explicit stop
                if direction == "long" and price <= float(pos["stop_price"]):
                    results.append(self._close_position(pos, price, "stop"))
                    continue
                elif direction == "short" and price >= float(pos["stop_price"]):
                    results.append(self._close_position(pos, price, "stop"))
                    continue
            elif pnl_pct <= stop_pct:
                results.append(self._close_position(pos, price, "stop"))
                continue

            # Target check
            if pos["target_price"]:
                if direction == "long" and price >= float(pos["target_price"]):
                    results.append(self._close_position(pos, price, "target"))
                    continue
                elif direction == "short" and price <= float(pos["target_price"]):
                    results.append(self._close_position(pos, price, "target"))
                    continue

            # Note: No forced EOD close or D+5 limit.
            # The PM decides holding periods and exit timing via its own decisions.

            logger.info(
                f"[E] {symbol} {direction}: entry ${entry_price:.2f}, "
                f"now ${price:.2f}, P&L {pnl_pct:+.1%} — holding"
            )

        return results

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def _execute_one(self, dec: dict, equity: float) -> dict:
        """Execute a single PM decision."""
        symbol = dec["symbol"]
        direction = dec["direction"]
        decision_id = str(dec["decision_id"])
        size_pct = float(dec["position_size_pct"] or 0.03)

        # Pre-check shortability for SHORT decisions
        if direction == "short" and not self._is_shortable(symbol):
            logger.warning(f"[E] {symbol} is not shortable on Alpaca — skipping SHORT decision")
            self._mark_decision_failed(decision_id, f"{symbol} not shortable on Alpaca")
            return {"symbol": symbol, "success": False, "reason": "not_shortable"}

        # Calculate position size
        size_usd = equity * size_pct
        price = self._get_price(symbol)
        # Fallback to PM's suggested entry
        if not price and dec.get("suggested_entry"):
            price = float(dec["suggested_entry"])
        if not price or price <= 0:
            self._mark_decision_failed(decision_id, f"No price for {symbol}")
            return {"symbol": symbol, "success": False, "reason": "no_price"}

        shares = max(1, int(size_usd / price))

        # Submit order
        side = "sell" if direction == "short" else "buy"
        order = self._submit_order(symbol, shares, side)

        if not order:
            self._mark_decision_failed(decision_id, "Order submission failed")
            return {"symbol": symbol, "success": False, "reason": "order_failed"}

        order_id = order.get("id", "")
        fill_price = self._get_fill_price(order, fallback=price)

        # Compute stop/target
        stop_price = None
        target_price = None
        if dec.get("suggested_stop"):
            stop_price = float(dec["suggested_stop"])
        else:
            pct = INTRADAY_STOP_PCT if dec.get("holding_period") == "intraday" else SWING_STOP_PCT
            if direction == "long":
                stop_price = round(fill_price * (1 + pct), 2)
            else:
                stop_price = round(fill_price * (1 - pct), 2)

        if dec.get("suggested_target"):
            target_price = float(dec["suggested_target"])

        # Record trade in DB
        trade_id = self._record_trade(
            dec, shares, fill_price, order_id, stop_price, target_price
        )

        # Mark decision executed
        if trade_id:
            self._mark_decision_executed(decision_id, trade_id)

        logger.info(
            f"[E] EXECUTED: {side.upper()} {shares} {symbol} @ ${fill_price:.2f} "
            f"(${size_usd:,.0f}, {size_pct:.0%} of equity) "
            f"stop=${stop_price or 0:.2f} "
            f"decision={decision_id[:8]}"
        )

        return {
            "symbol": symbol,
            "success": True,
            "side": side,
            "shares": shares,
            "price": fill_price,
            "trade_id": trade_id,
            "order_id": order_id,
        }

    def _close_position(self, pos: dict, price: float, reason: str) -> dict:
        """Close an open position."""
        symbol = pos["symbol"]
        shares = pos["shares"]
        direction = pos["direction"]
        trade_id = pos["id"]

        # Submit closing order
        close_side = "sell" if direction == "long" else "buy"
        order = self._submit_order(symbol, shares, close_side)

        if not order:
            logger.error(f"[E] Failed to close {symbol}")
            return {"symbol": symbol, "success": False, "reason": "close_failed"}

        # Get fill price — poll order if not immediately available
        fill_price = self._get_fill_price(order, fallback=price)
        entry_price = float(pos["entry_price"])

        if direction == "long":
            pnl = (fill_price - entry_price) * shares
            pnl_pct = (fill_price - entry_price) / entry_price
        else:
            pnl = (entry_price - fill_price) * shares
            pnl_pct = (entry_price - fill_price) / entry_price

        days_held = 0
        if pos.get("entry_date"):
            days_held = (date.today() - pos["entry_date"]).days

        self._update_trade_exit(trade_id, fill_price, pnl, pnl_pct, reason, days_held)

        logger.info(
            f"[E] CLOSED: {symbol} ({reason}), "
            f"P&L ${pnl:+,.2f} ({pnl_pct:+.1%}), held {days_held}d"
        )

        return {
            "symbol": symbol,
            "success": True,
            "reason": reason,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
        }

    # ------------------------------------------------------------------
    # Alpaca API
    # ------------------------------------------------------------------

    def _get_fill_price(self, order: dict, fallback: float) -> float:
        """Get fill price from order, polling Alpaca if not immediately available."""
        import time as _time

        # Check immediate response
        fp = order.get("filled_avg_price")
        if fp:
            return float(fp)

        # Poll order status (market orders fill within seconds on paper)
        order_id = order.get("id")
        if order_id:
            for _ in range(3):
                _time.sleep(1)
                try:
                    r = requests.get(
                        f"{ALPACA_BASE}/v2/orders/{order_id}",
                        headers=self._headers,
                        timeout=5,
                    )
                    if r.ok:
                        fp = r.json().get("filled_avg_price")
                        if fp:
                            return float(fp)
                except Exception:
                    pass

        return fallback

    def _submit_order(self, symbol: str, qty: int, side: str) -> Optional[dict]:
        """Submit a market order to Alpaca. Returns order dict or None."""
        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            r = requests.post(
                f"{ALPACA_BASE}/v2/orders",
                headers=self._headers,
                json=payload,
                timeout=10,
            )
            if r.ok:
                return r.json()
            else:
                logger.error(f"[E] Order failed {side} {qty} {symbol}: {r.status_code} {r.text}")
                return None
        except Exception as e:
            logger.error(f"[E] Order exception {symbol}: {e}")
            return None

    def _get_price(self, symbol: str) -> Optional[float]:
        """Get latest price from Alpaca data API or snapshot."""
        # Try data.alpaca.markets first
        for base in ["https://data.alpaca.markets", "https://paper-api.alpaca.markets"]:
            try:
                r = requests.get(
                    f"{base}/v2/stocks/{symbol}/trades/latest",
                    headers=self._headers,
                    timeout=5,
                )
                if r.ok:
                    trade = r.json().get("trade", {})
                    p = trade.get("p")
                    if p:
                        return float(p)
            except Exception:
                continue

        # Fallback: get from spot_prices table
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT price FROM spot_prices WHERE symbol = %s ORDER BY updated_at DESC LIMIT 1",
                        (symbol,)
                    )
                    row = cur.fetchone()
                    if row:
                        return float(row[0])
            finally:
                conn.close()
        except Exception:
            pass

        # Final fallback: use suggested_entry from PM decision
        return None

    def _get_equity(self) -> float:
        """Get Account E equity from Alpaca."""
        try:
            r = requests.get(
                f"{ALPACA_BASE}/v2/account",
                headers=self._headers,
                timeout=5,
            )
            if r.ok:
                return float(r.json().get("equity", 100000))
            return 100000.0
        except Exception:
            return 100000.0

    def _is_shortable(self, symbol: str) -> bool:
        """Check if an asset is shortable on Alpaca. Cached per-process."""
        if symbol in self._shortable_cache:
            return self._shortable_cache[symbol]
        try:
            r = requests.get(
                f"{ALPACA_BASE}/v2/assets/{symbol}",
                headers=self._headers,
                timeout=5,
            )
            if r.ok:
                data = r.json()
                result = bool(data.get("shortable")) and bool(data.get("tradable"))
                self._shortable_cache[symbol] = result
                return result
            # Asset not found or other error — be conservative, allow submission
            # (preserves previous behavior: let Alpaca reject at order time)
            return True
        except Exception as e:
            logger.warning(f"[E] Shortability check failed for {symbol}: {e}")
            return True

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    def _fetch_pending_decisions(self) -> list[dict]:
        """Fetch unexecuted, non-vetoed PM decisions from last hour."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT decision_id, symbol, direction, instrument,
                               holding_period, weighted_score, position_size_pct,
                               expert_votes, suggested_entry, suggested_stop,
                               suggested_target
                        FROM pm_decisions_e
                        WHERE NOT executed AND NOT vetoed
                          AND decision_ts > NOW() - INTERVAL '1 hour'
                        ORDER BY weighted_score DESC
                    """)
                    return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[E] Failed to fetch decisions: {e}")
            return []

    def _record_trade(self, dec: dict, shares: int, price: float,
                      order_id: str, stop_price: float,
                      target_price: Optional[float]) -> Optional[int]:
        """Insert trade into paper_trades_log_e."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO paper_trades_log_e (
                            symbol, direction, instrument, holding_period,
                            entry_time, entry_date, entry_price, shares,
                            decision_id, weighted_score, expert_votes,
                            stop_price, target_price,
                            limit_order_id, order_submitted_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s::jsonb,
                            %s, %s,
                            %s, %s
                        ) RETURNING id
                    """, (
                        dec["symbol"], dec["direction"],
                        dec.get("instrument", "stock"),
                        dec.get("holding_period", "intraday"),
                        datetime.now(timezone.utc), date.today(), price, shares,
                        str(dec["decision_id"]), dec.get("weighted_score"),
                        psycopg2.extras.Json(dec.get("expert_votes", {})),
                        stop_price, target_price,
                        order_id, datetime.now(timezone.utc),
                    ))
                    trade_id = cur.fetchone()[0]
                conn.commit()
                return trade_id
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[E] Failed to record trade: {e}")
            return None

    def _mark_decision_executed(self, decision_id: str, trade_id: int):
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE pm_decisions_e
                        SET executed = TRUE, executed_at = NOW(), trade_id = %s
                        WHERE decision_id = %s
                    """, (trade_id, decision_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[E] Failed to mark executed: {e}")

    def _mark_decision_failed(self, decision_id: str, reason: str):
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE pm_decisions_e
                        SET executed = TRUE, execution_notes = %s
                        WHERE decision_id = %s
                    """, (f"FAILED: {reason}", decision_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[E] Failed to mark failed: {e}")

    def _update_trade_exit(self, trade_id: int, exit_price: float,
                           pnl: float, pnl_pct: float, reason: str,
                           days_held: int):
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE paper_trades_log_e
                        SET exit_time = NOW(), exit_price = %s, pnl = %s,
                            pnl_pct = %s, exit_reason = %s,
                            actual_holding_days = %s, updated_at = NOW()
                        WHERE id = %s
                    """, (exit_price, pnl, pnl_pct, reason, days_held, trade_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[E] Failed to update exit: {e}")

    def _get_open_positions(self) -> list[dict]:
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, symbol, direction, instrument, holding_period,
                               entry_price, shares, entry_date, stop_price,
                               target_price, expiration_date
                        FROM paper_trades_log_e
                        WHERE exit_time IS NULL
                    """)
                    return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[E] Failed to fetch positions: {e}")
            return []


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def _get_creds():
    """Get DB URL and Alpaca creds."""
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
    ).strip()
    api_key = os.environ.get("ALPACA_API_KEY_E", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY_E", "")
    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY_E and ALPACA_SECRET_KEY_E must be set")
        sys.exit(1)
    return db_url, api_key, secret_key


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import argparse
    parser = argparse.ArgumentParser(description="Account E Executor")
    parser.add_argument("--check-exits", action="store_true",
                        help="Check open positions for stop/target/EOD exits")
    args = parser.parse_args()

    db_url, api_key, secret_key = _get_creds()
    executor = AccountEExecutor(db_url, api_key, secret_key)

    if args.check_exits:
        results = executor.check_exits()
    else:
        results = executor.execute_pending()

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
