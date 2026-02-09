"""
scripts/backfill_gex.py

Historical GEX backfill CLI.

Reads ORATS ZIP files from a local archive and populates gex_metrics_snapshot
with historical GEX data. Does NOT touch orats_daily.

Usage:
    python -m scripts.backfill_gex --dir "D:\\ORATS_TMP_Files"
    python -m scripts.backfill_gex --dir "D:\\ORATS_TMP_Files" --from 2025-06-01 --to 2025-11-30
    python -m scripts.backfill_gex --file "D:\\ORATS_TMP_Files\\ORATS_SMV_Strikes_20250601.zip"
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sources.orats_ingest import (
    _get_secret,
    _get_db_connection,
    _parse_orats_csv,
    _finalize_gex_metrics,
    _bulk_upsert_gex,
    JsonLogger,
)

logger = JsonLogger("backfill_gex")

# Regex to extract date from filename: ORATS_SMV_Strikes_YYYYMMDD.zip
DATE_PATTERN = re.compile(r"ORATS_SMV_Strikes_(\d{8})\.zip$")


def extract_date_from_filename(filename: str) -> date | None:
    """Extract trade date from ORATS filename."""
    m = DATE_PATTERN.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            return None
    return None


def find_zip_files(
    directory: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Path]:
    """Find and filter ORATS ZIP files in directory."""
    dir_path = Path(directory)
    if not dir_path.exists():
        logger.error("dir_not_found", path=directory)
        return []

    all_zips = sorted(dir_path.glob("ORATS_SMV_Strikes_*.zip"))
    logger.info("files.found", total=len(all_zips))

    if not from_date and not to_date:
        return all_zips

    filtered = []
    for zp in all_zips:
        file_date = extract_date_from_filename(zp.name)
        if file_date is None:
            continue
        if from_date and file_date < from_date:
            continue
        if to_date and file_date > to_date:
            continue
        filtered.append(zp)

    logger.info("files.filtered", count=len(filtered), from_date=str(from_date), to_date=str(to_date))
    return filtered


def backfill_single_file(conn, file_path: str) -> int:
    """
    Process a single ORATS ZIP file and write GEX data.

    Returns number of GEX rows upserted.
    """
    logger.info("processing", file=Path(file_path).name)

    # Parse — we only need GEX data, but _parse_orats_csv returns both
    _records, gex_accum = _parse_orats_csv(file_path)

    if not gex_accum:
        logger.warning("no_gex_data", file=Path(file_path).name)
        return 0

    # Finalize GEX metrics
    gex_rows = _finalize_gex_metrics(gex_accum)

    # Upsert to DB
    count = _bulk_upsert_gex(conn, gex_rows)
    return count


def main():
    parser = argparse.ArgumentParser(description="Backfill GEX data from ORATS archive")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=str, help="Path to a single ORATS ZIP file")
    group.add_argument("--dir", type=str, help="Directory containing ORATS ZIP files")
    parser.add_argument("--from", dest="from_date", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", type=str, help="End date (YYYY-MM-DD)")

    args = parser.parse_args()

    # Parse dates
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else None
    to_date = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else None

    # Build file list
    if args.file:
        files = [Path(args.file)]
        if not files[0].exists():
            logger.error("file_not_found", path=args.file)
            sys.exit(1)
    else:
        files = find_zip_files(args.dir, from_date, to_date)

    if not files:
        logger.error("no_files_to_process")
        sys.exit(1)

    logger.info("backfill.starting", files=len(files))

    # Connect to DB
    dsn = _get_secret("DATABASE_URL")
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.split("postgresql+psycopg://", 1)[1]

    conn = _get_db_connection(dsn)

    total_rows = 0
    processed = 0
    errors = 0
    start_time = time.time()

    try:
        for file_path in files:
            try:
                count = backfill_single_file(conn, str(file_path))
                total_rows += count
                processed += 1

                elapsed = time.time() - start_time
                logger.info(
                    "progress",
                    file=file_path.name,
                    gex_rows=count,
                    processed=processed,
                    total_files=len(files),
                    elapsed_sec=round(elapsed, 1),
                )
            except Exception as e:
                errors += 1
                logger.error("file_failed", file=file_path.name, error=str(e))
                try:
                    conn.rollback()
                except Exception:
                    pass
                continue

    finally:
        conn.close()

    elapsed = time.time() - start_time
    logger.info(
        "backfill.complete",
        files_processed=processed,
        files_errored=errors,
        total_gex_rows=total_rows,
        elapsed_sec=round(elapsed, 1),
    )

    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
