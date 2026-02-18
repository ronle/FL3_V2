"""
Dashboard Module for Paper Trading

Provides real-time dashboard via Google Sheets showing:
- Active signals (only passed signals)
- Current positions
- Closed trades for the day
"""

import logging
import os
from datetime import datetime
from typing import Optional

import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

# Safety: only allow known trade log tables in dynamic SQL
ALLOWED_TABLES = {"paper_trades_log", "paper_trades_log_b"}

# Google Sheets Configuration
SHEET_ID = os.environ.get("DASHBOARD_SHEET_ID", "")
CREDENTIALS_SECRET = "dashboard-credentials"  # Secret Manager key


class Dashboard:
    """
    Google Sheets dashboard for real-time paper trading visibility.

    Tabs:
    - Active Signals: Signals that passed all filters
    - Positions: Currently held positions
    - Closed Today: Positions closed during the session
    """

    def __init__(self, sheet_id: Optional[str] = None, credentials_json: Optional[str] = None, tab_prefix: str = ""):
        self.sheet_id = sheet_id or SHEET_ID
        self.tab_prefix = tab_prefix
        self._client = None
        self._sheet = None
        self._signals_tab = None
        self._positions_tab = None
        self._closed_tab = None
        self._enabled = False

        if not self.sheet_id:
            logger.warning("Dashboard disabled: DASHBOARD_SHEET_ID not set")
            return

        try:
            self._init_client(credentials_json)
            self._enabled = True
            logger.info(f"Dashboard initialized: {self.sheet_id} (prefix='{self.tab_prefix}')")
        except Exception as e:
            import traceback
            logger.warning(f"Dashboard initialization failed: {type(e).__name__}: {e}")
            logger.warning(f"Traceback: {traceback.format_exc()}")

    def _init_client(self, credentials_json: Optional[str] = None):
        """Initialize Google Sheets client."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as e:
            raise ImportError(f"gspread and google-auth required for dashboard: {e}")

        scopes = ['https://www.googleapis.com/auth/spreadsheets']

        # Try to get credentials from Secret Manager if not provided
        if not credentials_json:
            logger.info("Fetching dashboard credentials from Secret Manager...")
            credentials_json = self._get_credentials_from_secret()

        if credentials_json:
            import json
            logger.info("Got credentials from Secret Manager, initializing gspread...")
            creds_dict = json.loads(credentials_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        else:
            # Fall back to default credentials (for local testing)
            creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if creds_file:
                logger.info(f"Using credentials file: {creds_file}")
                creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
            else:
                raise ValueError("No credentials available - Secret Manager returned None and no GOOGLE_APPLICATION_CREDENTIALS set")

        logger.info("Authorizing gspread client...")
        self._client = gspread.authorize(creds)
        logger.info(f"Opening sheet by key: {self.sheet_id}")
        self._sheet = self._client.open_by_key(self.sheet_id)
        logger.info(f"Sheet opened: {self._sheet.title}")

        # Get or create tabs (Account B uses prefixed tab names)
        logger.info("Getting/creating worksheets...")
        if self.tab_prefix:
            self._signals_tab = self._get_or_create_worksheet(f"{self.tab_prefix}Signals")
            self._positions_tab = self._get_or_create_worksheet(f"{self.tab_prefix}Positions")
            self._closed_tab = self._get_or_create_worksheet(f"{self.tab_prefix}Closed")
        else:
            self._signals_tab = self._get_or_create_worksheet("Active Signals")
            self._positions_tab = self._get_or_create_worksheet("Positions")
            self._closed_tab = self._get_or_create_worksheet("Closed Today")
        logger.info("All worksheets ready")

    def _get_credentials_from_secret(self) -> Optional[str]:
        """Try to fetch credentials from Secret Manager."""
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            project = os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
            name = f"projects/{project}/secrets/{CREDENTIALS_SECRET}/versions/latest"
            logger.info(f"Accessing secret: {name}")
            response = client.access_secret_version(request={"name": name})
            logger.info("Successfully retrieved credentials from Secret Manager")
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            logger.warning(f"Could not fetch credentials from Secret Manager: {e}")
            return None

    def _get_or_create_worksheet(self, title: str):
        """Get existing worksheet or create new one."""
        import gspread
        try:
            ws = self._sheet.worksheet(title)
            logger.info(f"Found existing worksheet: {title}")
            return ws
        except gspread.exceptions.WorksheetNotFound:
            # Create new worksheet
            logger.info(f"Creating new worksheet: {title}")
            ws = self._sheet.add_worksheet(title=title, rows=1000, cols=10)
            return ws

    def log_signal(
        self,
        symbol: str,
        score: int,
        rsi: float,
        ratio: float,
        notional: float,
        price: float,
        timestamp: Optional[datetime] = None,
        volume_ratio: Optional[float] = None,
        engulfing_strength: Optional[float] = None,
    ):
        """
        Log a signal that passed all filters to Active Signals tab.

        Account B uses engulfing_strength + volume_ratio columns instead of RSI + Ratio.
        """
        if not self._enabled:
            return

        try:
            ts = timestamp or datetime.now(ET)
            time_str = ts.strftime("%Y-%m-%d %H:%M:%S")

            if self.tab_prefix:
                # Account B layout: Date/Time, Symbol, Score, Engulfing, Notional, Price, VolR, Action
                vol_str = f"{volume_ratio:.1f}x" if volume_ratio is not None else "—"
                eng_str = str(engulfing_strength) if engulfing_strength is not None else "—"
                row = [
                    time_str,
                    symbol,
                    score,
                    eng_str,
                    f"${notional:,.0f}",
                    f"${price:.2f}",
                    vol_str,
                    "BUY",
                ]
            else:
                # Account A layout: Date/Time, Symbol, Score, RSI, Ratio, Notional, Price, Action
                row = [
                    time_str,
                    symbol,
                    score,
                    f"{rsi:.1f}",
                    f"{ratio:.1f}x",
                    f"${notional:,.0f}",
                    f"${price:.2f}",
                    "BUY",
                ]
            self._signals_tab.append_row(row, value_input_option='USER_ENTERED')
            logger.debug(f"Dashboard: logged signal {symbol}")
        except Exception as e:
            logger.warning(f"Dashboard log_signal failed: {e}")

    def update_position(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        status: str = "HOLDING",
        score: int = 0,
    ):
        """
        Update or add a position to Positions tab.
        """
        if not self._enabled:
            return

        try:
            pnl = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            row = [
                symbol,
                score,
                f"${entry_price:.2f}",
                f"${current_price:.2f}",
                f"{pnl:+.2f}%",
                status
            ]

            # Try to update existing row
            try:
                cell = self._positions_tab.find(symbol)
                self._positions_tab.update(f'A{cell.row}:F{cell.row}', [row])
            except Exception:
                # Not found, append new row
                self._positions_tab.append_row(row, value_input_option='USER_ENTERED')

            logger.debug(f"Dashboard: updated position {symbol} @ ${current_price:.2f}")
        except Exception as e:
            logger.warning(f"Dashboard update_position failed: {e}")

    def rewrite_positions(self, positions_data: list):
        """
        Clear and rewrite the entire Positions tab.

        Takes a list of [symbol, score, entry, current, pnl%, status] rows.
        Self-healing: removes stale entries that shouldn't be there.
        """
        if not self._enabled:
            return

        try:
            self._positions_tab.clear()
            header = ['Symbol', 'Score', 'Entry', 'Current', 'P/L %', 'Status']
            if positions_data:
                self._positions_tab.update('A1', [header] + positions_data, value_input_option='USER_ENTERED')
            else:
                self._positions_tab.update('A1', [header], value_input_option='USER_ENTERED')
        except Exception as e:
            logger.warning(f"Dashboard rewrite_positions failed: {e}")

    def close_position(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        exit_time: Optional[datetime] = None,
        shares: int = 0,
        pnl_dollars: Optional[float] = None,
        score: int = 0,
    ):
        """
        Move position from Positions to Closed Today tab.
        """
        if not self._enabled:
            return

        try:
            ts = exit_time or datetime.now(ET)
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            if pnl_dollars is None:
                pnl_dollars = (exit_price - entry_price) * shares
            result = "WIN" if pnl_pct > 0 else "LOSS" if pnl_pct < 0 else "FLAT"

            # Add to Closed tab
            row = [
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                score,
                shares,
                f"${entry_price:.2f}",
                f"${exit_price:.2f}",
                f"{pnl_pct:+.2f}%",
                f"${pnl_dollars:+,.2f}",
                result
            ]
            self._closed_tab.append_row(row, value_input_option='USER_ENTERED')

            # Remove from Positions tab
            try:
                cell = self._positions_tab.find(symbol)
                self._positions_tab.delete_rows(cell.row)
            except Exception:
                pass  # Not found, already removed

            logger.debug(f"Dashboard: closed position {symbol} @ ${exit_price:.2f} ({pnl_pct:+.2f}%)")
        except Exception as e:
            logger.warning(f"Dashboard close_position failed: {e}")

    def clear_daily(self):
        """
        Clear all tabs at start of trading day and add headers.
        """
        if not self._enabled:
            return

        try:
            # Clear all tabs
            self._signals_tab.clear()
            self._positions_tab.clear()
            self._closed_tab.clear()

            # Add headers (Account B uses different signal columns)
            if self.tab_prefix:
                self._signals_tab.append_row(
                    ['Date/Time', 'Symbol', 'Score', 'Engulfing', 'Notional', 'Price', 'VolR', 'Action'],
                    value_input_option='USER_ENTERED'
                )
            else:
                self._signals_tab.append_row(
                    ['Date/Time', 'Symbol', 'Score', 'RSI', 'Ratio', 'Notional', 'Price', 'Action'],
                    value_input_option='USER_ENTERED'
                )
            self._positions_tab.append_row(
                ['Symbol', 'Score', 'Entry', 'Current', 'P/L %', 'Status'],
                value_input_option='USER_ENTERED'
            )
            self._closed_tab.append_row(
                ['Date/Time', 'Symbol', 'Score', 'Shares', 'Entry', 'Exit', 'P/L %', '$ P/L', 'Result'],
                value_input_option='USER_ENTERED'
            )

            logger.info(f"Dashboard: cleared for new trading day (prefix='{self.tab_prefix}')")
        except Exception as e:
            logger.warning(f"Dashboard clear_daily failed: {e}")

    @property
    def enabled(self) -> bool:
        """Check if dashboard is enabled and connected."""
        return self._enabled


# Singleton instance
_dashboard: Optional[Dashboard] = None


def get_dashboard() -> Dashboard:
    """Get or create the dashboard singleton."""
    global _dashboard
    if _dashboard is None:
        _dashboard = Dashboard()
    return _dashboard


# Database logging for active signals
def log_active_signal_to_db(
    db_url: str,
    symbol: str,
    detected_at: datetime,
    notional: float,
    ratio: float,
    call_pct: float,
    sweep_pct: float,
    num_strikes: int,
    contracts: int,
    rsi: float,
    trend: int,
    price: float,
    score: int,
    volume_ratio: Optional[float] = None,
):
    """
    Log a passed signal to the active_signals database table.
    """
    if not db_url:
        return

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO active_signals (
                detected_at, symbol, notional, ratio, call_pct, sweep_pct,
                num_strikes, contracts, rsi_14, trend, price_at_signal,
                score, action, volume_ratio
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (detected_at, symbol) DO NOTHING
        """, (
            detected_at, symbol, notional, ratio, call_pct, sweep_pct,
            num_strikes, contracts, rsi, trend, price, score, 'BUY', volume_ratio
        ))

        conn.commit()
        cur.close()
        conn.close()
        logger.debug(f"Logged active signal to DB: {symbol}")
    except Exception as e:
        logger.warning(f"Failed to log active signal to DB: {e}")


def update_signal_trade_placed(db_url: str, symbol: str, entry_price: float):
    """Update active_signal when trade is placed."""
    if not db_url:
        return

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        cur.execute("""
            UPDATE active_signals
            SET trade_placed = TRUE, entry_price = %s, action = 'HOLDING'
            WHERE id = (
                SELECT id FROM active_signals
                WHERE symbol = %s AND trade_placed = FALSE AND action = 'BUY'
                ORDER BY detected_at DESC
                LIMIT 1
            )
        """, (entry_price, symbol))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to update signal trade placed: {e}")


def close_signal_in_db(db_url: str, symbol: str, exit_price: float, pnl_pct: float):
    """Update active_signal when position is closed."""
    if not db_url:
        return

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        cur.execute("""
            UPDATE active_signals
            SET exit_price = %s, exit_time = NOW(), pnl_pct = %s, action = 'CLOSED'
            WHERE id = (
                SELECT id FROM active_signals
                WHERE symbol = %s AND action = 'HOLDING'
                ORDER BY detected_at DESC
                LIMIT 1
            )
        """, (exit_price, pnl_pct, symbol))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to close signal in DB: {e}")


def log_trade_open(
    db_url: str,
    symbol: str,
    entry_time,
    entry_price: float,
    shares: int,
    signal_score: int,
    signal_rsi: float,
    signal_notional: float,
    table_name: str = "paper_trades_log",
    volume_ratio: Optional[float] = None,
):
    """
    Log a new trade to paper_trades_log (or paper_trades_log_b) when a position is opened.

    Returns the row id on success, None on failure.
    """
    assert table_name in ALLOWED_TABLES, f"Invalid table: {table_name}"
    if not db_url:
        return None

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        cur.execute(f"""
            INSERT INTO {table_name}
            (symbol, entry_time, entry_price, shares, signal_score, signal_rsi, signal_notional, volume_ratio)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (symbol, entry_time, entry_price, shares, signal_score, signal_rsi, signal_notional, volume_ratio))

        row = cur.fetchone()
        trade_id = row[0] if row else None
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Logged trade open to {table_name}: {symbol} (id={trade_id})")
        return trade_id
    except Exception as e:
        logger.warning(f"Failed to log trade open to DB: {e}")
        return None


def log_trade_close(
    db_url: str,
    trade_db_id,
    symbol: str,
    exit_time,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
    table_name: str = "paper_trades_log",
):
    """
    Update paper_trades_log (or paper_trades_log_b) when a position is closed.

    Uses trade_db_id (primary key) for precise targeting.
    Falls back to symbol + exit_time IS NULL if id is not available.
    """
    assert table_name in ALLOWED_TABLES, f"Invalid table: {table_name}"
    if not db_url:
        return

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        if trade_db_id:
            cur.execute(f"""
                UPDATE {table_name}
                SET exit_time = %s, exit_price = %s, pnl = %s, pnl_pct = %s, exit_reason = %s
                WHERE id = %s
            """, (exit_time, exit_price, pnl, pnl_pct, exit_reason, trade_db_id))
        else:
            cur.execute(f"""
                UPDATE {table_name}
                SET exit_time = %s, exit_price = %s, pnl = %s, pnl_pct = %s, exit_reason = %s
                WHERE id = (
                    SELECT id FROM {table_name}
                    WHERE symbol = %s AND exit_time IS NULL
                    ORDER BY entry_time DESC
                    LIMIT 1
                )
            """, (exit_time, exit_price, pnl, pnl_pct, exit_reason, symbol))

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Logged trade close to {table_name}: {symbol} (id={trade_db_id})")
    except Exception as e:
        logger.warning(f"Failed to log trade close to DB: {e}")


def load_open_trades_from_db(db_url: str, table_name: str = "paper_trades_log") -> list:
    """
    Load open trades from paper_trades_log (or paper_trades_log_b) for startup recovery.

    Returns list of dicts with trade data where exit_time IS NULL.
    Only looks at trades from the last 7 days to avoid stale data.
    """
    assert table_name in ALLOWED_TABLES, f"Invalid table: {table_name}"
    if not db_url:
        return []

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()

        cur.execute(f"""
            SELECT id, symbol, entry_time, entry_price, shares,
                   signal_score, signal_rsi, signal_notional
            FROM {table_name}
            WHERE exit_time IS NULL
            AND created_at > CURRENT_DATE - 7
            ORDER BY entry_time DESC
        """)

        trades = []
        for row in cur.fetchall():
            trades.append({
                "db_id": row[0],
                "symbol": row[1],
                "entry_time": row[2],
                "entry_price": float(row[3]) if row[3] else 0,
                "shares": row[4] or 0,
                "signal_score": row[5] or 0,
                "signal_rsi": float(row[6]) if row[6] else 0,
                "signal_notional": float(row[7]) if row[7] else 0,
            })

        cur.close()
        conn.close()
        logger.info(f"Loaded {len(trades)} open trades from {table_name}")
        return trades
    except Exception as e:
        logger.warning(f"Failed to load open trades from DB: {e}")
        return []
