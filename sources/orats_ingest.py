"""
sources/orats_ingest.py

V2 ORATS Daily Ingest — migrated from V1, extended with GEX computation.

Downloads and ingests daily ORATS options data from FTP, then computes
Gamma Exposure (GEX) metrics from raw per-strike data.

Data Flow:
1. Connect to ORATS FTP server with credentials (Secret Manager)
2. Download latest daily file (ZIP with CSV ~900K rows)
3. Parse per-strike data:
   a. Aggregate to symbol-level → orats_daily (~5,600 rows)
   b. Accumulate GEX data per symbol → gex_metrics_snapshot (~5,600 rows)
4. Run post-processing: IV rank, HV 30-day, EMAs
5. GEX: finalize metrics, bulk upsert

Auth: ORATS_FTP_USER / ORATS_FTP_PASSWORD (via Google Secret Manager)
DSN:  DATABASE_URL (via Google Secret Manager)
"""

from __future__ import annotations

import csv
import ftplib
import gzip
import io
import os
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import psycopg2
from psycopg2 import extras as pg_extras

# Structured logging
import json


class JsonLogger:
    """Simple JSON logger for ORATS ingest."""

    def __init__(self, name: str = "orats"):
        self.name = name

    def _emit(self, level: str, msg: str, **extra):
        rec = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "lvl": level,
            "name": self.name,
            "msg": msg,
        }
        rec.update(extra)
        print(json.dumps(rec, default=str), flush=True)

    def info(self, msg: str, **extra):
        self._emit("INFO", msg, **extra)

    def warning(self, msg: str, **extra):
        self._emit("WARNING", msg, **extra)

    def error(self, msg: str, **extra):
        self._emit("ERROR", msg, **extra)

    def debug(self, msg: str, **extra):
        self._emit("DEBUG", msg, **extra)

    def exception(self, msg: str, **extra):
        import traceback
        self._emit("ERROR", msg, **dict(extra, traceback=traceback.format_exc()))


logger = JsonLogger("orats_ingest")


# ==============================================================================
# Configuration Constants
# ==============================================================================

FTP_HOST = "us4.hostedftp.com"
FILE_PATTERN = "ORATS_SMV_Strikes_"
BATCH_SIZE = 5000
MAX_AGE_DAYS = 7


# ==============================================================================
# Secret Manager Credential Resolution
# ==============================================================================


def _get_secret(secret_name: str) -> str:
    """
    Resolve secret from Google Secret Manager, with env var fallback for local dev.

    Follows the pattern from paper_trading/dashboard.py:99-112.

    Args:
        secret_name: Name of the secret (e.g., "DATABASE_URL", "ORATS_FTP_USER")

    Returns:
        Secret value string

    Raises:
        ValueError: If secret cannot be resolved from any source
    """
    # Env var fallback (for local testing)
    val = os.environ.get(secret_name)
    if val:
        logger.debug("secret.from_env", name=secret_name)
        return val.strip()

    # Secret Manager (production)
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
        name = f"projects/{project}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        logger.info("secret.from_sm", name=secret_name)
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        raise ValueError(
            f"Cannot resolve secret '{secret_name}' from env or Secret Manager: {e}"
        ) from e


def _resolve_credentials() -> Tuple[str, str, str]:
    """
    Resolve ORATS FTP credentials.

    Returns:
        (ftp_host, username, password)
    """
    ftp_host = os.environ.get("ORATS_FTP_HOST", FTP_HOST).strip()

    username = _get_secret("ORATS_FTP_USER")
    password = _get_secret("ORATS_FTP_PASSWORD")

    if not username or not password:
        raise ValueError("Missing ORATS_FTP_USER or ORATS_FTP_PASSWORD")

    logger.info("credentials_resolved", host=ftp_host, user=username[:3] + "***")
    return ftp_host, username, password


def _resolve_dsn() -> str:
    """
    Resolve database DSN.

    Returns:
        PostgreSQL DSN string
    """
    raw = _get_secret("DATABASE_URL")

    # Normalize psycopg+postgresql scheme
    if raw.startswith("postgresql+psycopg://"):
        raw = "postgresql://" + raw.split("postgresql+psycopg://", 1)[1]

    return raw


# ==============================================================================
# FTP Operations
# ==============================================================================


@contextmanager
def _ftp_connection(host: str, username: str, password: str, timeout: int = 30):
    """Context manager for FTP connection with automatic cleanup."""
    ftp = None
    try:
        ftp = ftplib.FTP(timeout=timeout)
        logger.info("ftp.connecting", host=host, user=username[:3] + "***")
        ftp.connect(host)
        ftp.login(username, password)
        logger.info("ftp.connected", welcome=ftp.getwelcome()[:100])
        yield ftp
    except ftplib.error_perm as e:
        logger.error("ftp.auth_failed", error=str(e))
        raise
    except Exception as e:
        logger.error("ftp.connection_failed", error=str(e))
        raise
    finally:
        if ftp:
            try:
                ftp.quit()
                logger.info("ftp.disconnected")
            except Exception:
                pass


def _construct_file_path(target_date: Optional[date] = None) -> str:
    """
    Construct ORATS FTP file path for a date.

    ORATS FTP structure: /YYYY/ORATS_SMV_Strikes_YYYYMMDD.zip
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    date_str = target_date.strftime("%Y%m%d")
    filename = f"{FILE_PATTERN}{date_str}.zip"
    year = target_date.strftime("%Y")
    file_path = f"/{year}/{filename}"

    logger.info("ftp.file_path_constructed", filename=filename, year=year, path=file_path)
    return file_path


def _find_latest_file(ftp: ftplib.FTP, max_age_days: int = MAX_AGE_DAYS) -> Optional[str]:
    """Find the most recent ORATS file by trying dates in reverse chronological order."""
    logger.info("ftp.finding_latest_file", max_age_days=max_age_days)

    for days_back in range(max_age_days):
        target_date = date.today() - timedelta(days=days_back + 1)

        # Skip weekends
        if target_date.weekday() >= 5:
            continue

        file_path = _construct_file_path(target_date)

        try:
            size = ftp.size(file_path)
            if size and size > 0:
                logger.info(
                    "ftp.latest_file_found",
                    date=target_date.isoformat(),
                    path=file_path,
                    size_bytes=size,
                    days_back=days_back + 1,
                )
                return file_path
        except ftplib.error_perm:
            continue
        except Exception as e:
            logger.warning("ftp.file_check_error", path=file_path, error=str(e))
            continue

    logger.warning("ftp.no_files_found", max_age_days=max_age_days)
    return None


def _download_file(ftp: ftplib.FTP, remote_path: str, local_path: str) -> int:
    """Download file from FTP to local path."""
    safe_local_path = Path(local_path).resolve()
    if not safe_local_path.parent.exists():
        safe_local_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("ftp.downloading", remote=remote_path, local=str(safe_local_path))

    bytes_downloaded = 0

    def callback(data):
        nonlocal bytes_downloaded
        bytes_downloaded += len(data)
        if bytes_downloaded % (10 * 1024 * 1024) == 0:
            logger.info("ftp.download_progress", bytes=bytes_downloaded)

    try:
        with open(safe_local_path, "wb") as f:
            ftp.retrbinary(f"RETR {remote_path}", lambda data: (callback(data), f.write(data)))

        logger.info("ftp.download_complete", bytes=bytes_downloaded, path=str(safe_local_path))
        return bytes_downloaded
    except Exception as e:
        logger.error("ftp.download_failed", remote=remote_path, error=str(e))
        raise


# ==============================================================================
# Data Parsing (with GEX accumulation)
# ==============================================================================


def _parse_orats_csv(file_path: str) -> Tuple[List[Dict[str, Any]], Dict]:
    """
    Parse ORATS SMV Strikes CSV file.

    Returns:
        Tuple of:
        - List of aggregated symbol-level records (for orats_daily)
        - GEX accumulator dict keyed by symbol (for gex_metrics_snapshot)
    """
    import zipfile

    logger.info("csv.parsing", path=file_path)

    # Symbol-level aggregation (existing V1 logic)
    aggregated = defaultdict(lambda: {
        "call_volume": 0,
        "put_volume": 0,
        "call_open_interest": 0,
        "put_open_interest": 0,
        "weighted_iv_sum": 0.0,
        "total_volume": 0,
        "stock_price": None,
        "trade_date": None,
    })

    # GEX accumulation (NEW — per-symbol strike-level data)
    gex_accum = defaultdict(lambda: {
        "net_gex": 0.0,
        "net_dex": 0.0,
        "call_oi_by_strike": defaultdict(int),
        "put_oi_by_strike": defaultdict(int),
        "gex_by_strike": defaultdict(float),
        "spot": 0.0,
        "contracts": 0,
        "trade_date": None,
    })

    row_count = 0

    def safe_int(val):
        if not val or str(val).strip().lower() in ("", "null", "na", "n/a"):
            return 0
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return 0

    def safe_float(val):
        if not val or str(val).strip().lower() in ("", "null", "na", "n/a"):
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def process_rows(reader):
        """Process CSV rows: aggregate for orats_daily + accumulate GEX."""
        nonlocal row_count

        for row in reader:
            row_count += 1

            if row_count % 100000 == 0:
                logger.info("csv.progress", rows_parsed=row_count)

            # Extract common fields
            symbol = (row.get("ticker") or "").strip().upper()
            if not symbol or len(symbol) > 10:
                continue

            trade_date_str = (row.get("trade_date") or "").strip()
            if not trade_date_str:
                continue

            # Parse date
            try:
                trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date()
            except ValueError:
                try:
                    trade_date = datetime.strptime(trade_date_str, "%m/%d/%Y").date()
                except ValueError:
                    continue

            # === Symbol-level aggregation (existing V1 logic) ===
            key = (symbol, trade_date)
            agg = aggregated[key]
            agg["trade_date"] = trade_date

            c_vol = safe_int(row.get("cVolu"))
            p_vol = safe_int(row.get("pVolu"))
            c_oi = safe_int(row.get("cOi"))
            p_oi = safe_int(row.get("pOi"))

            agg["call_volume"] += c_vol
            agg["put_volume"] += p_vol
            agg["call_open_interest"] += c_oi
            agg["put_open_interest"] += p_oi

            # Volume-weighted IV
            c_mid_iv = safe_float(row.get("cMidIv"))
            p_mid_iv = safe_float(row.get("pMidIv"))
            strike_volume = c_vol + p_vol

            if strike_volume > 0 and (c_mid_iv > 0 or p_mid_iv > 0):
                avg_iv = (c_mid_iv + p_mid_iv) / 2.0 if (c_mid_iv > 0 and p_mid_iv > 0) else (c_mid_iv or p_mid_iv)
                agg["weighted_iv_sum"] += avg_iv * strike_volume
                agg["total_volume"] += strike_volume

            # Stock price
            spot = safe_float(row.get("stkPx")) or safe_float(row.get("spot_px"))
            if not agg["stock_price"] and spot > 0:
                agg["stock_price"] = spot

            # === GEX accumulation (NEW) ===
            gamma = safe_float(row.get("gamma"))
            delta = safe_float(row.get("delta"))
            strike = safe_float(row.get("strike"))

            if (c_oi > 0 or p_oi > 0) and spot > 0 and strike > 0:
                g = gex_accum[symbol]
                if g["spot"] == 0:
                    g["spot"] = spot
                g["trade_date"] = trade_date

                # GEX: gamma × OI × 100 × spot² × 0.01
                call_gex = gamma * c_oi * 100 * spot ** 2 * 0.01
                put_gex = gamma * p_oi * 100 * spot ** 2 * 0.01 * (-1)

                g["net_gex"] += call_gex + put_gex

                # DEX: delta × OI × 100
                call_dex = delta * c_oi * 100
                put_dex = abs(delta - 1) * p_oi * 100
                g["net_dex"] += call_dex - put_dex

                # Track per-strike for walls and gamma flip
                g["gex_by_strike"][strike] += call_gex + put_gex
                g["call_oi_by_strike"][strike] += c_oi
                g["put_oi_by_strike"][strike] += p_oi
                g["contracts"] += 1

    try:
        if file_path.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(file_path, "r") as zf:
                csv_files = [name for name in zf.namelist() if name.endswith(".csv")]
                if not csv_files:
                    raise ValueError("No CSV files found in ZIP archive")

                csv_filename = csv_files[0]
                logger.info("zip.extracting", csv_file=csv_filename)

                with zf.open(csv_filename) as csv_file:
                    text_file = io.TextIOWrapper(csv_file, encoding="utf-8", errors="replace")
                    reader = csv.DictReader(text_file)

                    if not reader.fieldnames:
                        raise ValueError("CSV file has no headers")

                    logger.info("csv.headers", columns=reader.fieldnames[:10])
                    process_rows(reader)

        elif file_path.endswith(".gz"):
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    raise ValueError("CSV file has no headers")
                logger.info("csv.headers", columns=reader.fieldnames[:10])
                process_rows(reader)

        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    raise ValueError("CSV file has no headers")
                logger.info("csv.headers", columns=reader.fieldnames[:10])
                process_rows(reader)

        logger.info(
            "csv.parse_complete",
            total_rows=row_count,
            aggregated_symbols=len(aggregated),
            gex_symbols=len(gex_accum),
        )

        # Build symbol-level records list
        records = []
        for (symbol, trade_date), agg in aggregated.items():
            iv_30day = None
            if agg["total_volume"] > 0:
                iv_30day = agg["weighted_iv_sum"] / agg["total_volume"]

            records.append({
                "symbol": symbol,
                "asof_date": trade_date,
                "call_volume": agg["call_volume"],
                "put_volume": agg["put_volume"],
                "call_open_interest": agg["call_open_interest"],
                "put_open_interest": agg["put_open_interest"],
                "iv_30day": iv_30day,
                "iv_rank": None,
                "hv_30day": None,
                "avg_daily_volume": agg["call_volume"] + agg["put_volume"],
                "avg_daily_premium": None,
                "stock_price": agg["stock_price"],
            })

        return records, dict(gex_accum)

    except Exception as e:
        logger.error("csv.parse_failed", path=file_path, error=str(e))
        raise


# ==============================================================================
# GEX Computation
# ==============================================================================


def _find_gamma_flip(gex_by_strike: Dict[float, float], spot_price: float = 0) -> Optional[float]:
    """
    Find the price level where cumulative GEX crosses zero, nearest to spot.

    Collects ALL zero crossings, then returns the one closest to spot_price.
    Strikes far from spot (< 20% or > 300% of spot) are excluded to avoid
    noise from deep OTM options with minimal OI.
    """
    if not gex_by_strike:
        return None

    # Filter strikes to reasonable range around spot if spot is known
    if spot_price > 0:
        lo = spot_price * 0.20
        hi = spot_price * 3.00
        strikes = sorted(k for k in gex_by_strike if lo <= k <= hi)
    else:
        strikes = sorted(gex_by_strike.keys())

    if len(strikes) < 2:
        return None

    # Collect all zero crossings
    crossings = []
    cumulative = 0.0
    last_strike = None
    last_cumulative = 0.0

    for strike in strikes:
        cumulative += gex_by_strike[strike]

        if last_cumulative != 0 and cumulative != 0:
            if (last_cumulative > 0) != (cumulative > 0):
                if last_strike is not None:
                    ratio = abs(last_cumulative) / (abs(last_cumulative) + abs(cumulative))
                    flip = last_strike + ratio * (strike - last_strike)
                    crossings.append(flip)

        last_strike = strike
        last_cumulative = cumulative

    if not crossings:
        return None

    # Return the crossing nearest to spot
    if spot_price > 0:
        return min(crossings, key=lambda x: abs(x - spot_price))

    return crossings[0]


def _finalize_gex_metrics(gex_accum: Dict) -> List[Dict[str, Any]]:
    """
    Convert raw GEX accumulator into rows ready for gex_metrics_snapshot.

    For each symbol:
    - Call wall: strike with max call OI
    - Put wall: strike with max put OI
    - Gamma flip: interpolated zero-crossing of cumulative GEX
    - snapshot_ts: trade_date at 16:00 ET (EOD)
    """
    import pytz

    et = pytz.timezone("America/New_York")
    results = []

    for symbol, g in gex_accum.items():
        if g["contracts"] == 0 or g["spot"] == 0:
            continue

        trade_date = g["trade_date"]
        if trade_date is None:
            continue

        # snapshot_ts = trade_date at 4:00 PM ET
        eod = datetime.combine(trade_date, datetime.min.time().replace(hour=16))
        snapshot_ts = et.localize(eod)

        # Call wall: strike with max call OI
        call_wall = None
        if g["call_oi_by_strike"]:
            call_wall = max(g["call_oi_by_strike"].items(), key=lambda x: x[1])[0]

        # Put wall: strike with max put OI
        put_wall = None
        if g["put_oi_by_strike"]:
            put_wall = max(g["put_oi_by_strike"].items(), key=lambda x: x[1])[0]

        # Gamma flip
        gamma_flip = _find_gamma_flip(g["gex_by_strike"], g["spot"])

        results.append({
            "symbol": symbol,
            "snapshot_ts": snapshot_ts,
            "spot_price": g["spot"],
            "net_gex": g["net_gex"],
            "net_dex": g["net_dex"],
            "call_wall_strike": call_wall,
            "put_wall_strike": put_wall,
            "gamma_flip_level": gamma_flip,
            "net_vex": None,  # Not available from ORATS file
            "net_charm": None,  # Not available from ORATS file
            "contracts_analyzed": g["contracts"],
        })

    logger.info("gex.finalized", symbols=len(results))
    return results


def _bulk_upsert_gex(conn, gex_rows: List[Dict[str, Any]]) -> int:
    """
    Bulk upsert GEX metrics to gex_metrics_snapshot.

    Uses ON CONFLICT (symbol, snapshot_ts) DO UPDATE for idempotent writes.
    """
    if not gex_rows:
        logger.warning("gex.upsert_skipped", reason="no_rows")
        return 0

    logger.info("gex.upsert_starting", count=len(gex_rows))

    sql = """
        INSERT INTO gex_metrics_snapshot (
            symbol, snapshot_ts, spot_price, net_gex, net_dex,
            call_wall_strike, put_wall_strike, gamma_flip_level,
            net_vex, net_charm, contracts_analyzed
        ) VALUES %s
        ON CONFLICT (symbol, snapshot_ts) DO UPDATE SET
            spot_price = EXCLUDED.spot_price,
            net_gex = EXCLUDED.net_gex,
            net_dex = EXCLUDED.net_dex,
            call_wall_strike = EXCLUDED.call_wall_strike,
            put_wall_strike = EXCLUDED.put_wall_strike,
            gamma_flip_level = EXCLUDED.gamma_flip_level,
            net_vex = EXCLUDED.net_vex,
            net_charm = EXCLUDED.net_charm,
            contracts_analyzed = EXCLUDED.contracts_analyzed
    """

    values = [
        (
            r["symbol"], r["snapshot_ts"], r["spot_price"],
            r["net_gex"], r["net_dex"],
            r["call_wall_strike"], r["put_wall_strike"], r["gamma_flip_level"],
            r["net_vex"], r["net_charm"], r["contracts_analyzed"],
        )
        for r in gex_rows
    ]

    with conn.cursor() as cur:
        try:
            pg_extras.execute_values(cur, sql, values, page_size=1000)
            rows_affected = cur.rowcount
            conn.commit()
            logger.info("gex.upsert_complete", rows_affected=rows_affected)
            return rows_affected
        except Exception as e:
            conn.rollback()
            logger.error("gex.upsert_failed", error=str(e))
            raise


# ==============================================================================
# Database Operations
# ==============================================================================


def _get_db_connection(dsn: str):
    """Create database connection using psycopg2."""
    try:
        conn = psycopg2.connect(dsn)
        logger.info("db.connected")
        return conn
    except Exception as e:
        logger.error("db.connection_failed", error=str(e))
        raise


def _bulk_upsert_orats(conn, records: List[Dict[str, Any]]) -> int:
    """
    Bulk upsert ORATS records to orats_daily.

    Uses execute_values for efficient batch insert with ON CONFLICT.
    """
    if not records:
        logger.warning("db.upsert_skipped", reason="no_records")
        return 0

    logger.info("db.upsert_starting", count=len(records))

    with conn.cursor() as cur:
        try:
            # Query previous day's OI for delta calculation
            symbol_dates = [(rec["symbol"], rec["asof_date"]) for rec in records]
            prev_volumes = {}

            if symbol_dates:
                cur.execute("""
                    SELECT d.symbol, d.call_open_interest, d.put_open_interest
                    FROM public.orats_daily d
                    INNER JOIN (
                        SELECT unnest(%s::text[]) as symbol,
                               unnest(%s::date[]) - INTERVAL '1 day' as prev_date
                    ) q ON d.symbol = q.symbol AND d.asof_date = q.prev_date::date
                """, (
                    [sd[0] for sd in symbol_dates],
                    [sd[1] for sd in symbol_dates],
                ))
                for row in cur.fetchall():
                    prev_volumes[row[0]] = (row[1], row[2])

            # Prepare values with delta OI
            values = []
            for rec in records:
                symbol = rec["symbol"]
                call_oi = rec.get("call_open_interest")
                put_oi = rec.get("put_open_interest")
                delta_call_oi = None
                delta_put_oi = None

                if symbol in prev_volumes:
                    prev_call_oi, prev_put_oi = prev_volumes[symbol]
                    if call_oi is not None and prev_call_oi is not None:
                        delta_call_oi = call_oi - prev_call_oi
                    if put_oi is not None and prev_put_oi is not None:
                        delta_put_oi = put_oi - prev_put_oi

                values.append((
                    symbol, rec["asof_date"],
                    rec.get("call_volume"), rec.get("put_volume"),
                    call_oi, put_oi,
                    rec.get("iv_30day"), rec.get("iv_rank"), rec.get("hv_30day"),
                    rec.get("avg_daily_volume"), rec.get("avg_daily_premium"),
                    rec.get("stock_price"),
                    delta_call_oi, delta_put_oi,
                ))

            # Batch upsert using execute_values
            sql = """
                INSERT INTO public.orats_daily (
                    symbol, asof_date, call_volume, put_volume,
                    call_open_interest, put_open_interest,
                    iv_30day, iv_rank, hv_30day,
                    avg_daily_volume, avg_daily_premium, stock_price,
                    delta_call_oi, delta_put_oi, updated_at
                ) VALUES %s
                ON CONFLICT (asof_date, symbol) DO UPDATE SET
                    call_volume = EXCLUDED.call_volume,
                    put_volume = EXCLUDED.put_volume,
                    call_open_interest = EXCLUDED.call_open_interest,
                    put_open_interest = EXCLUDED.put_open_interest,
                    iv_30day = EXCLUDED.iv_30day,
                    iv_rank = EXCLUDED.iv_rank,
                    hv_30day = EXCLUDED.hv_30day,
                    avg_daily_volume = EXCLUDED.avg_daily_volume,
                    avg_daily_premium = EXCLUDED.avg_daily_premium,
                    stock_price = EXCLUDED.stock_price,
                    delta_call_oi = EXCLUDED.delta_call_oi,
                    delta_put_oi = EXCLUDED.delta_put_oi,
                    updated_at = NOW()
            """

            # Template includes NOW() for updated_at
            template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
            pg_extras.execute_values(cur, sql, values, template=template, page_size=1000)

            rows_affected = cur.rowcount
            conn.commit()
            logger.info("db.upsert_complete", rows_affected=rows_affected)
            return rows_affected

        except Exception as e:
            conn.rollback()
            logger.error("db.upsert_failed", error=str(e))
            raise


# ==============================================================================
# Data Retention (DISABLED — user wants all historical data)
# ==============================================================================


def _cleanup_old_data(conn, retention_days: int = 365) -> int:
    """Delete ORATS data older than retention_days. DISABLED."""
    # DISABLED: User wants to retain ALL historical data indefinitely
    logger.info("cleanup.disabled")
    return 0


# ==============================================================================
# IV Rank Computation
# ==============================================================================


def _compute_iv_rank_for_symbols(conn, symbols: List[str], asof_date: date = None) -> int:
    """
    Compute IV Rank from iv_30day for given symbols.

    IV Rank = (Current IV - 60-day Min) / (60-day Max - 60-day Min) x 100
    """
    if not symbols:
        logger.info("iv_rank.no_symbols")
        return 0

    logger.info("iv_rank.starting", symbol_count=len(symbols))

    with conn.cursor() as cur:
        if asof_date is None:
            cur.execute("SELECT MAX(asof_date) FROM public.orats_daily WHERE symbol = ANY(%s)", (symbols,))
            result = cur.fetchone()
            asof_date = result[0] if result else None
            if asof_date is None:
                logger.info("iv_rank.no_data")
                return 0

        logger.info("iv_rank.target_date", asof_date=str(asof_date))

        query = """
            WITH iv_history AS (
                SELECT symbol, asof_date, iv_30day
                FROM public.orats_daily
                WHERE symbol = ANY(%s)
                  AND iv_30day IS NOT NULL
                  AND asof_date BETWEEN %s - INTERVAL '59 days' AND %s
            ),
            iv_stats AS (
                SELECT symbol,
                       MIN(iv_30day) AS iv_min_60d,
                       MAX(iv_30day) AS iv_max_60d,
                       COUNT(*) AS iv_count_60d
                FROM iv_history
                GROUP BY symbol
            ),
            current_iv AS (
                SELECT symbol, iv_30day
                FROM public.orats_daily
                WHERE symbol = ANY(%s) AND asof_date = %s AND iv_30day IS NOT NULL
            )
            UPDATE public.orats_daily AS target
            SET
                iv_rank = CASE
                    WHEN s.iv_max_60d > s.iv_min_60d AND s.iv_count_60d >= 36
                    THEN LEAST(100.0, GREATEST(0.0,
                        ((c.iv_30day - s.iv_min_60d) /
                         (s.iv_max_60d - s.iv_min_60d) * 100.0)
                    ))::NUMERIC(5,2)
                    ELSE NULL
                END,
                updated_at = NOW()
            FROM current_iv c
            JOIN iv_stats s ON s.symbol = c.symbol
            WHERE target.symbol = c.symbol
              AND target.asof_date = %s
        """

        cur.execute(query, (symbols, asof_date, asof_date, symbols, asof_date, asof_date))
        rows_updated = cur.rowcount
        conn.commit()

        logger.info("iv_rank.complete", rows_updated=rows_updated, symbol_count=len(symbols))
        return rows_updated


# ==============================================================================
# EMA Computation
# ==============================================================================


def _compute_emas_for_ingested_data(conn, records: List[Dict[str, Any]]) -> None:
    """Compute EMAs and regime indicators for symbols in the ingested data."""
    if not records:
        logger.info("ema.no_records_to_compute")
        return

    symbols = {rec["symbol"] for rec in records if "symbol" in rec}
    if not symbols:
        logger.info("ema.no_symbols_found")
        return

    logger.info("ema.starting", symbol_count=len(symbols))

    for symbol in sorted(symbols):
        try:
            _compute_emas_for_symbol(conn, symbol)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("ema.symbol_failed", symbol=symbol, error=str(e))


def _compute_emas_for_symbol(conn, symbol: str) -> int:
    """
    Compute EMAs, regime indicators, and derived fields for a single symbol.

    Uses PostgreSQL window functions for:
    - 7/30/60-day EMAs for volume and IV rank
    - Z-scores, trend percentages
    - Derived: iv_hv_ratio, hv_slope, iv_30d_zscore, volume_accel, etc.
    """
    logger.debug("ema.compute_symbol", symbol=symbol)

    with conn.cursor() as cur:
        query = """
        WITH base_data AS (
            SELECT
                asof_date, symbol,
                avg_daily_volume, total_volume, iv_rank, iv_30day, hv_30day,
                total_open_interest, call_volume, put_volume,
                call_open_interest, put_open_interest,
                avg_daily_premium, stock_price,

                AVG(avg_daily_volume) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                )::BIGINT as volume_ema_7d,

                AVG(iv_rank) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                )::NUMERIC(5,2) as iv_rank_ema_7d,

                AVG(avg_daily_volume) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
                )::BIGINT as volume_ema_30d,

                AVG(iv_rank) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
                )::NUMERIC(5,2) as iv_rank_ema_30d,

                AVG(avg_daily_volume) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                )::BIGINT as volume_ema_60d,

                AVG(iv_rank) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                )::NUMERIC(5,2) as iv_rank_ema_60d,

                STDDEV(avg_daily_volume) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) as volume_std_60d,

                STDDEV(iv_rank) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) as iv_rank_std_60d,

                AVG(hv_30day) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
                ) as hv_10d_ma,

                AVG(hv_30day) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
                ) as hv_30d_ma,

                AVG(iv_30day) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) as iv_30d_mean_60d,

                STDDEV(iv_30day) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) as iv_30d_std_60d,

                AVG(total_open_interest) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) as oi_mean_60d,

                STDDEV(total_open_interest) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ) as oi_std_60d,

                LAG(total_volume, 1) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                ) as prev_total_volume,

                AVG(stock_price) OVER (
                    PARTITION BY symbol ORDER BY asof_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) as stock_price_20d_sma

            FROM public.orats_daily
            WHERE symbol = %s
        ),
        indicators_computed AS (
            SELECT
                asof_date, symbol,
                volume_ema_7d, iv_rank_ema_7d,
                volume_ema_30d, iv_rank_ema_30d,
                volume_ema_60d, iv_rank_ema_60d,

                CASE
                    WHEN (
                        COUNT(*) OVER (
                            PARTITION BY symbol ORDER BY asof_date
                            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                        ) >= 10
                        AND STDDEV(total_volume) OVER (
                            PARTITION BY symbol ORDER BY asof_date
                            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                        ) > 0
                    )
                    THEN LEAST(3.0, GREATEST(-3.0,
                        (total_volume - AVG(total_volume) OVER (
                            PARTITION BY symbol ORDER BY asof_date
                            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                        )) / NULLIF(STDDEV(total_volume) OVER (
                            PARTITION BY symbol ORDER BY asof_date
                            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                        ), 0)
                    ))::NUMERIC(6,3)
                    ELSE NULL
                END as volume_zscore,

                CASE
                    WHEN iv_rank_std_60d > 0 AND iv_rank_ema_60d IS NOT NULL
                    THEN LEAST(999.999, GREATEST(-999.999,
                        (iv_rank - iv_rank_ema_60d) / NULLIF(iv_rank_std_60d, 0)
                    ))::NUMERIC(6,3)
                    ELSE NULL
                END as iv_rank_zscore,

                CASE
                    WHEN volume_ema_60d > 0
                    THEN LEAST(999.999, GREATEST(-999.999,
                        (avg_daily_volume - volume_ema_60d) * 100.0 / volume_ema_60d
                    ))::NUMERIC(6,3)
                    ELSE NULL
                END as volume_trend_pct,

                CASE
                    WHEN iv_rank_ema_60d > 0
                    THEN LEAST(999.999, GREATEST(-999.999,
                        (iv_rank - iv_rank_ema_60d) * 100.0 / iv_rank_ema_60d
                    ))::NUMERIC(6,3)
                    ELSE NULL
                END as iv_rank_trend_pct,

                CASE
                    WHEN hv_30day > 0
                    THEN (iv_30day / hv_30day)::NUMERIC(8,4)
                    ELSE NULL
                END as iv_hv_ratio,

                (hv_10d_ma - hv_30d_ma)::NUMERIC(8,4) as hv_slope,

                CASE
                    WHEN iv_30d_std_60d > 0
                    THEN LEAST(99.999, GREATEST(-99.999,
                        (iv_30day - iv_30d_mean_60d) / NULLIF(iv_30d_std_60d, 0)
                    ))::NUMERIC(6,3)
                    ELSE NULL
                END as iv_30d_zscore,

                CASE
                    WHEN oi_std_60d > 0
                    THEN LEAST(99.999, GREATEST(-99.999,
                        (total_open_interest - oi_mean_60d) / NULLIF(oi_std_60d, 0)
                    ))::NUMERIC(6,3)
                    ELSE NULL
                END as open_interest_zscore,

                CASE
                    WHEN prev_total_volume > 0
                    THEN LEAST(9999.9999, GREATEST(-9999.9999,
                        (total_volume::NUMERIC / prev_total_volume) - 1
                    ))::NUMERIC(8,4)
                    ELSE NULL
                END as volume_accel,

                (avg_daily_premium * total_volume)::BIGINT as premium_flow,

                CASE
                    WHEN stock_price_20d_sma > 0
                    THEN ((stock_price / stock_price_20d_sma) - 1)::NUMERIC(8,4)
                    ELSE NULL
                END as price_momentum_20d,

                CASE
                    WHEN iv_rank IS NULL THEN NULL
                    WHEN iv_rank <= 0.33 THEN 0
                    WHEN iv_rank <= 0.67 THEN 1
                    ELSE 2
                END::SMALLINT as vol_regime,

                total_volume, stock_price, stock_price_20d_sma,

                CASE
                    WHEN (call_volume + put_volume) > 0
                    THEN ((call_volume - put_volume)::NUMERIC / (call_volume + put_volume))::NUMERIC(6,4)
                    ELSE 0
                END as put_call_volume_skew,

                CASE
                    WHEN (call_open_interest + put_open_interest) > 0
                    THEN ((call_open_interest - put_open_interest)::NUMERIC / (call_open_interest + put_open_interest))::NUMERIC(6,4)
                    ELSE 0
                END as put_call_oi_skew

            FROM base_data
        ),
        final_computed AS (
            SELECT *,
                CASE
                    WHEN volume_zscore IS NOT NULL AND price_momentum_20d IS NOT NULL
                    THEN (volume_zscore * price_momentum_20d)::NUMERIC(8,4)
                    ELSE NULL
                END as volume_price_interaction
            FROM indicators_computed
        )
        UPDATE public.orats_daily AS target
        SET
            volume_ema_7d = src.volume_ema_7d,
            iv_rank_ema_7d = src.iv_rank_ema_7d,
            volume_ema_30d = src.volume_ema_30d,
            iv_rank_ema_30d = src.iv_rank_ema_30d,
            volume_ema_60d = src.volume_ema_60d,
            iv_rank_ema_60d = src.iv_rank_ema_60d,
            volume_zscore = src.volume_zscore,
            iv_rank_zscore = src.iv_rank_zscore,
            volume_trend_pct = src.volume_trend_pct,
            iv_rank_trend_pct = src.iv_rank_trend_pct,
            iv_hv_ratio = src.iv_hv_ratio,
            hv_slope = src.hv_slope,
            iv_30d_zscore = src.iv_30d_zscore,
            open_interest_zscore = src.open_interest_zscore,
            volume_accel = src.volume_accel,
            premium_flow = src.premium_flow,
            price_momentum_20d = src.price_momentum_20d,
            vol_regime = src.vol_regime,
            volume_price_interaction = src.volume_price_interaction,
            put_call_volume_skew = src.put_call_volume_skew,
            put_call_oi_skew = src.put_call_oi_skew,
            updated_at = NOW()
        FROM final_computed AS src
        WHERE target.symbol = src.symbol
          AND target.asof_date = src.asof_date
        """

        cur.execute(query, (symbol,))
        rows_updated = cur.rowcount
        logger.debug("ema.updated", symbol=symbol, rows=rows_updated)
        return rows_updated


# ==============================================================================
# Historical Volatility Computation
# ==============================================================================


def _compute_hv_30day(conn, symbols: List[str] = None, asof_date: date = None) -> int:
    """
    Compute 30-day Historical Volatility from stock_price data.

    HV = STDDEV(daily_log_returns) x SQRT(252) x 100
    """
    logger.info("hv.compute.starting")

    with conn.cursor() as cur:
        if asof_date is None:
            cur.execute("SELECT MAX(asof_date) FROM public.orats_daily")
            result = cur.fetchone()
            asof_date = result[0] if result else None
            if asof_date is None:
                logger.info("hv.compute.no_data")
                return 0

        if symbols is None:
            cur.execute(
                "SELECT DISTINCT symbol FROM public.orats_daily WHERE asof_date = %s AND stock_price > 0",
                (asof_date,),
            )
            symbols = [row[0] for row in cur.fetchall()]

        if not symbols:
            logger.info("hv.compute.no_symbols")
            return 0

        logger.info("hv.compute.target", asof_date=str(asof_date), symbol_count=len(symbols))

        query = """
        WITH price_history AS (
            SELECT symbol, asof_date, stock_price
            FROM public.orats_daily
            WHERE symbol = ANY(%s)
              AND stock_price IS NOT NULL AND stock_price > 0
              AND asof_date BETWEEN %s - INTERVAL '45 days' AND %s
        ),
        daily_returns AS (
            SELECT symbol, asof_date,
                   LN(stock_price / LAG(stock_price) OVER (
                       PARTITION BY symbol ORDER BY asof_date
                   )) as log_return
            FROM price_history
        ),
        hv_calc AS (
            SELECT symbol,
                   STDDEV(log_return) * SQRT(252) * 100 as hv_30day,
                   COUNT(log_return) as return_count
            FROM daily_returns
            WHERE log_return IS NOT NULL
            GROUP BY symbol
            HAVING COUNT(log_return) >= 20
        )
        UPDATE public.orats_daily AS target
        SET
            hv_30day = h.hv_30day::NUMERIC(6,2),
            updated_at = NOW()
        FROM hv_calc h
        WHERE target.symbol = h.symbol
          AND target.asof_date = %s
          AND h.hv_30day IS NOT NULL
        """

        cur.execute(query, (symbols, asof_date, asof_date, asof_date))
        rows_updated = cur.rowcount
        conn.commit()

        logger.info("hv.compute.complete", rows_updated=rows_updated)
        return rows_updated


# ==============================================================================
# Average Daily Premium Computation
# ==============================================================================


def _compute_avg_daily_premium_from_files(conn, zip_files: List[str]) -> int:
    """
    Compute average daily premium by aggregating strike-level data from ZIP files.

    Premium = total dollar value traded in options per ticker per day.
    Formula: SUM[(call_volume x call_value) + (put_volume x put_value)]
    """
    import zipfile

    logger.info("premium.compute.starting", files=len(zip_files))

    total_updated = 0

    for zip_path in zip_files:
        try:
            file_path = Path(zip_path)
            if not file_path.exists():
                logger.warning("premium.file_not_found", path=str(file_path))
                continue

            logger.debug("premium.processing_file", file=file_path.name)
            premium_by_ticker_date = defaultdict(float)

            with zipfile.ZipFile(file_path, "r") as zf:
                csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_files:
                    logger.warning("premium.no_csv_in_zip", file=file_path.name)
                    continue

                csv_name = csv_files[0]
                content = zf.read(csv_name).decode("utf-8")
                reader = csv.DictReader(io.StringIO(content))

                for row in reader:
                    ticker = row.get("ticker", "").strip()
                    trade_date = row.get("trade_date", "").strip()
                    if not ticker or not trade_date:
                        continue

                    try:
                        call_vol = float(row.get("cVolu", 0) or 0)
                        call_val = float(row.get("cValue", 0) or 0)
                        put_vol = float(row.get("pVolu", 0) or 0)
                        put_val = float(row.get("pValue", 0) or 0)

                        strike_premium = (call_vol * call_val) + (put_vol * put_val)
                        premium_by_ticker_date[(ticker, trade_date)] += strike_premium
                    except (ValueError, TypeError):
                        continue

            if premium_by_ticker_date:
                with conn.cursor() as cur:
                    update_query = """
                    UPDATE public.orats_daily
                    SET avg_daily_premium = %s, updated_at = NOW()
                    WHERE symbol = %s AND asof_date = %s
                    """
                    update_data = [
                        (int(premium), ticker, td)
                        for (ticker, td), premium in premium_by_ticker_date.items()
                    ]
                    cur.executemany(update_query, update_data)
                    rows_updated = cur.rowcount
                    conn.commit()

                    total_updated += rows_updated
                    logger.debug("premium.file_complete", file=file_path.name, rows_updated=rows_updated)

        except Exception as e:
            logger.error("premium.file_failed", file=zip_path, error=str(e))
            conn.rollback()
            continue

    logger.info("premium.compute.complete", total_updated=total_updated)
    return total_updated


# ==============================================================================
# Main Ingestion Flow
# ==============================================================================


def ingest_orats_daily(
    batch_size: int = BATCH_SIZE,
) -> int:
    """
    Main ORATS daily ingestion flow.

    Steps:
    1. Find latest daily file via FTP
    2. Download to temp location
    3. Parse CSV (aggregation + GEX accumulation)
    4. Bulk upsert to orats_daily
    5. Compute IV rank, HV, EMAs
    6. Finalize and upsert GEX metrics
    7. Cleanup temp files
    """
    logger.info("ingest.starting")

    # Resolve credentials and DSN
    ftp_host, username, password = _resolve_credentials()
    dsn = _resolve_dsn()

    total_inserted = 0
    temp_file = None
    conn = None

    try:
        # Connect to FTP and find latest file
        with _ftp_connection(ftp_host, username, password) as ftp:
            remote_path = _find_latest_file(ftp)
            if not remote_path:
                logger.warning("ingest.no_file_found")
                return 0

            suffix = ".zip" if remote_path.endswith(".zip") else ".csv.gz"
            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, suffix=suffix
            ) as tf:
                temp_file = tf.name
                _download_file(ftp, remote_path, temp_file)

        # Parse CSV and get both orats records and GEX data
        records, gex_accum = _parse_orats_csv(temp_file)

        # Connect to DB
        conn = _get_db_connection(dsn)

        # Batch upsert orats_daily
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            total_inserted += _bulk_upsert_orats(conn, batch)

        logger.info("ingest.upsert_complete", total_records=total_inserted)

        # Extract unique symbols
        symbols = list({rec["symbol"] for rec in records if "symbol" in rec})

        # Compute IV Rank
        _compute_iv_rank_for_symbols(conn, symbols)
        logger.info("ingest.iv_rank_computation_complete")

        # Compute 30-day Historical Volatility
        _compute_hv_30day(conn, symbols)
        logger.info("ingest.hv_30day_computation_complete")

        # Compute EMAs
        _compute_emas_for_ingested_data(conn, records)
        logger.info("ingest.ema_computation_complete")

        # === GEX PIPELINE (NEW) ===
        try:
            gex_rows = _finalize_gex_metrics(gex_accum)
            gex_count = _bulk_upsert_gex(conn, gex_rows)
            logger.info("ingest.gex_complete", rows=gex_count)
        except Exception as e:
            # GEX failure does NOT fail the overall job
            logger.error("ingest.gex_failed", error=str(e))

        logger.info("ingest.complete", total_records=total_inserted)
        return total_inserted

    except Exception as e:
        logger.exception("ingest.failed", error=str(e))
        raise

    finally:
        if conn:
            try:
                conn.close()
                logger.info("db.disconnected")
            except Exception:
                pass

        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
                logger.info("temp_file.deleted", path=temp_file)
            except Exception as e:
                logger.warning("temp_file.delete_failed", path=temp_file, error=str(e))


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def main():
    """CLI entry point for ORATS ingestion."""
    import sys

    try:
        total = ingest_orats_daily()
        logger.info("main.exit", status="success", total_records=total)
        sys.exit(0)
    except Exception as e:
        logger.exception("main.exit", status="failure", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
