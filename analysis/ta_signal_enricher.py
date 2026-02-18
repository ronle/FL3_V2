"""
TA Signal Enricher

Enriches backtest signals with technical indicators at signal time:
- RSI-14
- MACD(12,26,9) with signal line and histogram
- Price vs VWAP (above/below)

Analyzes whether TA-confirmed signals have better outcomes.
"""

import gzip
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# Force unbuffered output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Ensure stdout is unbuffered
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)


@dataclass
class TAIndicators:
    """Technical indicators at signal time."""
    rsi_14: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    vwap: Optional[float] = None
    price_vs_vwap: Optional[float] = None  # Positive = above VWAP
    ema_9: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None


def calculate_ema(prices: list[float], period: int) -> Optional[float]:
    """Calculate EMA for a series."""
    if len(prices) < period:
        return None

    # Start with SMA
    ema = sum(prices[:period]) / period
    k = 2 / (period + 1)

    for price in prices[period:]:
        ema = price * k + ema * (1 - k)

    return ema


def calculate_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Calculate RSI."""
    if len(closes) < period + 1:
        return None

    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent = changes[-period:]

    gains = [c if c > 0 else 0 for c in recent]
    losses = [-c if c < 0 else 0 for c in recent]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_macd(closes: list[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Calculate MACD(12, 26, 9).

    Returns: (macd_line, signal_line, histogram)
    """
    if len(closes) < 35:  # Need 26 + 9 for signal line
        return None, None, None

    # Calculate EMAs
    ema_12 = calculate_ema(closes, 12)
    ema_26 = calculate_ema(closes, 26)

    if ema_12 is None or ema_26 is None:
        return None, None, None

    macd_line = ema_12 - ema_26

    # For signal line, we need MACD history
    # Calculate MACD values for last 9+ periods
    macd_values = []
    for i in range(26, len(closes) + 1):
        subset = closes[:i]
        e12 = calculate_ema(subset, 12)
        e26 = calculate_ema(subset, 26)
        if e12 and e26:
            macd_values.append(e12 - e26)

    if len(macd_values) < 9:
        return macd_line, None, None

    signal_line = calculate_ema(macd_values, 9)
    histogram = macd_line - signal_line if signal_line else None

    return round(macd_line, 4), round(signal_line, 4) if signal_line else None, round(histogram, 4) if histogram else None


def calculate_vwap(df: pd.DataFrame) -> Optional[float]:
    """Calculate VWAP from bars DataFrame."""
    if df.empty or df['volume'].sum() == 0:
        return None

    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['volume']).sum() / df['volume'].sum()
    return round(vwap, 4)


def load_day_bars_grouped(date: str, stocks_dir: Path) -> Optional[dict]:
    """
    Load stock minute bars for a date, pre-grouped by ticker.

    Returns dict: {ticker: DataFrame} for O(1) lookups.
    """
    file_path = stocks_dir / f"{date}.csv.gz"

    if not file_path.exists():
        return None

    try:
        df = pd.read_csv(file_path, compression='gzip')
        # Convert nanoseconds to datetime
        df['timestamp'] = pd.to_datetime(df['window_start'], unit='ns')

        # Pre-group by ticker and sort by time
        grouped = {}
        for ticker, group in df.groupby('ticker'):
            grouped[ticker] = group.sort_values('timestamp').reset_index(drop=True)

        return grouped
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return None


def get_bars_until_time(grouped_bars: dict, symbol: str, signal_time: str) -> pd.DataFrame:
    """Get bars for a symbol up to and including signal time."""
    if symbol not in grouped_bars:
        return pd.DataFrame()

    symbol_df = grouped_bars[symbol]
    signal_dt = pd.Timestamp(signal_time)

    # Filter to bars before signal time
    mask = symbol_df['timestamp'] <= signal_dt
    return symbol_df[mask]


def calculate_ta_at_signal(bars_df: pd.DataFrame) -> TAIndicators:
    """Calculate TA indicators from bars up to signal time."""
    if bars_df.empty or len(bars_df) < 15:
        return TAIndicators()

    closes = bars_df['close'].tolist()

    rsi = calculate_rsi(closes, 14)
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    vwap = calculate_vwap(bars_df)

    current_price = closes[-1]
    price_vs_vwap = round((current_price / vwap - 1) * 100, 2) if vwap else None

    return TAIndicators(
        rsi_14=rsi,
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_histogram=macd_hist,
        vwap=vwap,
        price_vs_vwap=price_vs_vwap,
    )


def enrich_signals(
    signals_path: Path,
    stocks_dir: Path,
    output_path: Path,
    sample_size: Optional[int] = None
) -> pd.DataFrame:
    """
    Enrich signals with TA indicators.

    Args:
        signals_path: Path to e2e_backtest_with_outcomes.json
        stocks_dir: Directory with date.csv.gz files
        output_path: Where to save enriched results
        sample_size: Optional limit for testing

    Returns:
        DataFrame with enriched signals
    """
    logger.info(f"Loading signals from {signals_path}")
    with open(signals_path) as f:
        data = json.load(f)

    signals = data['signals']
    logger.info(f"Loaded {len(signals):,} signals")

    if sample_size:
        signals = signals[:sample_size]
        logger.info(f"Using sample of {sample_size:,} signals")

    # Group signals by date for efficient loading
    by_date = defaultdict(list)
    for i, sig in enumerate(signals):
        date = sig['detection_time'].split('T')[0]
        by_date[date].append((i, sig))

    logger.info(f"Signals span {len(by_date)} trading days")

    # Process each date
    enriched = []
    processed = 0
    missing_bars = 0
    days_processed = 0

    sorted_dates = sorted(by_date.keys())
    total_days = len(sorted_dates)

    for date in sorted_dates:
        day_signals = by_date[date]
        grouped_bars = load_day_bars_grouped(date, stocks_dir)

        if grouped_bars is None:
            missing_bars += len(day_signals)
            for idx, sig in day_signals:
                enriched.append({**sig, 'ta_error': 'no_bars_file'})
            days_processed += 1
            continue

        for idx, sig in day_signals:
            symbol = sig['symbol']
            signal_time = sig['detection_time']

            bars = get_bars_until_time(grouped_bars, symbol, signal_time)
            ta = calculate_ta_at_signal(bars)

            enriched_sig = {
                **sig,
                'rsi_14': ta.rsi_14,
                'macd_line': ta.macd_line,
                'macd_signal': ta.macd_signal,
                'macd_histogram': ta.macd_histogram,
                'vwap': ta.vwap,
                'price_vs_vwap': ta.price_vs_vwap,
                'bars_count': len(bars),
            }
            enriched.append(enriched_sig)

        processed += len(day_signals)
        days_processed += 1
        logger.info(f"Day {days_processed}/{total_days} ({date}): {len(day_signals):,} signals, {processed:,} total")

    logger.info(f"Enrichment complete. {missing_bars:,} signals missing bar data")

    # Convert to DataFrame
    df = pd.DataFrame(enriched)

    # Save
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved enriched signals to {output_path}")

    return df


def analyze_ta_confirmation(df: pd.DataFrame) -> dict:
    """
    Analyze whether TA-confirmed signals have better outcomes.

    TA Confirmation criteria:
    - RSI between 30-70 (not overbought/oversold)
    - MACD histogram positive (bullish momentum)
    - Price above VWAP (bullish)

    For calls (call_pct > 0.5):
    - RSI < 70, MACD positive, above VWAP

    For puts (call_pct <= 0.5):
    - RSI > 30, MACD negative, below VWAP
    """
    # Only signals with outcomes
    df_with_outcome = df[df['pct_to_close'].notna()].copy()
    logger.info(f"Signals with outcomes: {len(df_with_outcome):,}")

    # Only signals with TA data
    df_ta = df_with_outcome[df_with_outcome['rsi_14'].notna() & df_with_outcome['macd_histogram'].notna()].copy()
    logger.info(f"Signals with TA data: {len(df_ta):,}")

    # Win = positive return to close
    df_ta['win'] = df_ta['pct_to_close'] > 0

    # Define confirmation criteria
    # For calls: RSI not overbought, MACD bullish, above VWAP
    df_ta['is_call'] = df_ta['call_pct'] > 0.5

    # Call confirmation: RSI < 70, MACD histogram > 0, price > VWAP
    df_ta['call_ta_confirmed'] = (
        (df_ta['rsi_14'] < 70) &
        (df_ta['macd_histogram'] > 0) &
        (df_ta['price_vs_vwap'] > 0)
    )

    # Put confirmation: RSI > 30, MACD histogram < 0, price < VWAP
    df_ta['put_ta_confirmed'] = (
        (df_ta['rsi_14'] > 30) &
        (df_ta['macd_histogram'] < 0) &
        (df_ta['price_vs_vwap'] < 0)
    )

    # Overall TA confirmed
    df_ta['ta_confirmed'] = (
        (df_ta['is_call'] & df_ta['call_ta_confirmed']) |
        (~df_ta['is_call'] & df_ta['put_ta_confirmed'])
    )

    results = {
        'total_with_ta': len(df_ta),
        'overall': {
            'count': len(df_ta),
            'win_rate': df_ta['win'].mean() * 100,
            'avg_return': df_ta['pct_to_close'].mean(),
        },
        'ta_confirmed': {
            'count': df_ta['ta_confirmed'].sum(),
            'win_rate': df_ta[df_ta['ta_confirmed']]['win'].mean() * 100 if df_ta['ta_confirmed'].sum() > 0 else 0,
            'avg_return': df_ta[df_ta['ta_confirmed']]['pct_to_close'].mean() if df_ta['ta_confirmed'].sum() > 0 else 0,
        },
        'not_confirmed': {
            'count': (~df_ta['ta_confirmed']).sum(),
            'win_rate': df_ta[~df_ta['ta_confirmed']]['win'].mean() * 100,
            'avg_return': df_ta[~df_ta['ta_confirmed']]['pct_to_close'].mean(),
        },
    }

    # Breakdowns by RSI zones
    rsi_zones = [
        ('oversold', 0, 30),
        ('neutral_low', 30, 50),
        ('neutral_high', 50, 70),
        ('overbought', 70, 100),
    ]

    results['by_rsi_zone'] = {}
    for name, low, high in rsi_zones:
        mask = (df_ta['rsi_14'] >= low) & (df_ta['rsi_14'] < high)
        zone_df = df_ta[mask]
        if len(zone_df) > 0:
            results['by_rsi_zone'][name] = {
                'count': len(zone_df),
                'win_rate': zone_df['win'].mean() * 100,
                'avg_return': zone_df['pct_to_close'].mean(),
            }

    # MACD histogram analysis
    results['by_macd'] = {
        'positive': {
            'count': (df_ta['macd_histogram'] > 0).sum(),
            'win_rate': df_ta[df_ta['macd_histogram'] > 0]['win'].mean() * 100,
            'avg_return': df_ta[df_ta['macd_histogram'] > 0]['pct_to_close'].mean(),
        },
        'negative': {
            'count': (df_ta['macd_histogram'] <= 0).sum(),
            'win_rate': df_ta[df_ta['macd_histogram'] <= 0]['win'].mean() * 100,
            'avg_return': df_ta[df_ta['macd_histogram'] <= 0]['pct_to_close'].mean(),
        },
    }

    # VWAP analysis
    results['by_vwap'] = {
        'above': {
            'count': (df_ta['price_vs_vwap'] > 0).sum(),
            'win_rate': df_ta[df_ta['price_vs_vwap'] > 0]['win'].mean() * 100,
            'avg_return': df_ta[df_ta['price_vs_vwap'] > 0]['pct_to_close'].mean(),
        },
        'below': {
            'count': (df_ta['price_vs_vwap'] <= 0).sum(),
            'win_rate': df_ta[df_ta['price_vs_vwap'] <= 0]['win'].mean() * 100,
            'avg_return': df_ta[df_ta['price_vs_vwap'] <= 0]['pct_to_close'].mean(),
        },
    }

    # Combination analysis - all bullish indicators aligned for calls
    calls = df_ta[df_ta['is_call']]
    puts = df_ta[~df_ta['is_call']]

    results['calls'] = {
        'total': len(calls),
        'win_rate': calls['win'].mean() * 100 if len(calls) > 0 else 0,
        'ta_confirmed_count': calls['call_ta_confirmed'].sum(),
        'ta_confirmed_win_rate': calls[calls['call_ta_confirmed']]['win'].mean() * 100 if calls['call_ta_confirmed'].sum() > 0 else 0,
    }

    results['puts'] = {
        'total': len(puts),
        'win_rate': puts['win'].mean() * 100 if len(puts) > 0 else 0,
        'ta_confirmed_count': puts['put_ta_confirmed'].sum(),
        'ta_confirmed_win_rate': puts[puts['put_ta_confirmed']]['win'].mean() * 100 if puts['put_ta_confirmed'].sum() > 0 else 0,
    }

    return results


def print_analysis(results: dict):
    """Print analysis results."""
    print("\n" + "=" * 70)
    print("TA CONFIRMATION ANALYSIS")
    print("=" * 70)

    print(f"\nTotal signals with TA data: {results['total_with_ta']:,}")

    print("\n--- OVERALL ---")
    print(f"Baseline win rate: {results['overall']['win_rate']:.2f}%")
    print(f"Baseline avg return: {results['overall']['avg_return']:.2f}%")

    print("\n--- TA CONFIRMED vs NOT ---")
    ta = results['ta_confirmed']
    no_ta = results['not_confirmed']
    print(f"TA Confirmed:     {ta['count']:>6,} signals | Win: {ta['win_rate']:.2f}% | Avg: {ta['avg_return']:.2f}%")
    print(f"Not Confirmed:    {no_ta['count']:>6,} signals | Win: {no_ta['win_rate']:.2f}% | Avg: {no_ta['avg_return']:.2f}%")

    delta = ta['win_rate'] - no_ta['win_rate']
    print(f"\nDelta: {delta:+.2f}% win rate improvement")

    print("\n--- BY RSI ZONE ---")
    for zone, data in results['by_rsi_zone'].items():
        print(f"{zone:>15}: {data['count']:>6,} signals | Win: {data['win_rate']:.2f}% | Avg: {data['avg_return']:.2f}%")

    print("\n--- BY MACD ---")
    for direction, data in results['by_macd'].items():
        print(f"{direction:>15}: {data['count']:>6,} signals | Win: {data['win_rate']:.2f}% | Avg: {data['avg_return']:.2f}%")

    print("\n--- BY VWAP ---")
    for pos, data in results['by_vwap'].items():
        print(f"{pos:>15}: {data['count']:>6,} signals | Win: {data['win_rate']:.2f}% | Avg: {data['avg_return']:.2f}%")

    print("\n--- CALLS vs PUTS ---")
    c = results['calls']
    p = results['puts']
    print(f"Calls: {c['total']:,} total | Base win: {c['win_rate']:.2f}% | TA confirmed: {c['ta_confirmed_count']:,} @ {c['ta_confirmed_win_rate']:.2f}%")
    print(f"Puts:  {p['total']:,} total | Base win: {p['win_rate']:.2f}% | TA confirmed: {p['ta_confirmed_count']:,} @ {p['ta_confirmed_win_rate']:.2f}%")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    import sys

    base_dir = Path("C:/Users/levir/Documents/FL3_V2")
    signals_path = base_dir / "polygon_data/backtest_results/e2e_backtest_with_outcomes.json"
    stocks_dir = base_dir / "polygon_data/stocks"
    output_path = base_dir / "polygon_data/backtest_results/signals_with_ta.parquet"

    # Check if enriched file exists
    if output_path.exists() and "--force" not in sys.argv:
        logger.info(f"Loading existing enriched data from {output_path}")
        df = pd.read_parquet(output_path)
    else:
        # Sample size for testing (None for full run)
        sample = None
        if "--sample" in sys.argv:
            idx = sys.argv.index("--sample")
            sample = int(sys.argv[idx + 1])

        df = enrich_signals(signals_path, stocks_dir, output_path, sample_size=sample)

    # Run analysis
    results = analyze_ta_confirmation(df)
    print_analysis(results)

    # Save analysis results
    analysis_path = base_dir / "polygon_data/backtest_results/ta_analysis_results.json"
    with open(analysis_path, 'w') as f:
        json.dump(results, f, indent=2, default=float)
    logger.info(f"Saved analysis to {analysis_path}")
