"""
PROD-2: Prior-Day TA Enrichment

Problem: TA indicators from intraday bars have only 15.8% coverage
         (many signals fire early when insufficient bars exist)

Solution: Use prior trading day's end-of-day indicator values:
- RSI-14: From prior 14 daily closes
- MACD(12,26,9): From prior 35 daily closes
- SMA-20: From prior 20 daily closes
- VWAP: From current day minute bars (up to signal time)

Expected coverage: 15.8% → 90%+
"""

import json
import gzip
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, date, time as dt_time
from pathlib import Path
from typing import Optional, List, Dict
import pandas as pd
import numpy as np

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"


@dataclass
class PriorDayTA:
    """Prior-day TA indicators."""
    rsi_14: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    sma_20: Optional[float] = None
    ema_9: Optional[float] = None
    price_prior_close: Optional[float] = None


@dataclass
class IntradayTA:
    """Current-day intraday indicators."""
    vwap: Optional[float] = None
    price_vs_vwap: Optional[float] = None
    price_at_signal: Optional[float] = None


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Calculate RSI from closing prices."""
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


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """Calculate EMA."""
    if len(prices) < period:
        return None

    ema = sum(prices[:period]) / period
    k = 2 / (period + 1)

    for price in prices[period:]:
        ema = price * k + ema * (1 - k)

    return round(ema, 4)


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """Calculate SMA."""
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 4)


def calculate_macd(closes: List[float]) -> tuple:
    """Calculate MACD(12, 26, 9). Returns (line, signal, histogram)."""
    if len(closes) < 35:
        return None, None, None

    ema_12 = calculate_ema(closes, 12)
    ema_26 = calculate_ema(closes, 26)

    if ema_12 is None or ema_26 is None:
        return None, None, None

    macd_line = ema_12 - ema_26

    # Calculate MACD history for signal line
    macd_values = []
    for i in range(26, len(closes) + 1):
        e12 = calculate_ema(closes[:i], 12)
        e26 = calculate_ema(closes[:i], 26)
        if e12 and e26:
            macd_values.append(e12 - e26)

    if len(macd_values) < 9:
        return round(macd_line, 4), None, None

    signal_line = calculate_ema(macd_values, 9)
    histogram = macd_line - signal_line if signal_line else None

    return (
        round(macd_line, 4),
        round(signal_line, 4) if signal_line else None,
        round(histogram, 4) if histogram else None
    )


class DailyBarsLoader:
    """Load and cache daily OHLC bars from minute data."""

    def __init__(self):
        self.cache: Dict[date, Dict[str, dict]] = {}

    def load_day(self, trade_date: date) -> Dict[str, dict]:
        """Load minute bars and aggregate to daily OHLC."""
        if trade_date in self.cache:
            return self.cache[trade_date]

        file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
        if not file_path.exists():
            self.cache[trade_date] = {}
            return {}

        try:
            df = pd.read_csv(file_path, compression='gzip')

            daily = {}
            for ticker, group in df.groupby('ticker'):
                daily[ticker] = {
                    'o': float(group['open'].iloc[0]),
                    'h': float(group['high'].max()),
                    'l': float(group['low'].min()),
                    'c': float(group['close'].iloc[-1]),
                    'v': int(group['volume'].sum()),
                }

            self.cache[trade_date] = daily
            return daily
        except Exception as e:
            self.cache[trade_date] = {}
            return {}

    def get_prior_daily_bars(self, symbol: str, trade_date: date, n_days: int = 35) -> List[dict]:
        """Get prior N trading days of daily bars."""
        bars = []
        d = trade_date - timedelta(days=1)

        for _ in range(60):  # Look back up to 60 calendar days
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue

            daily = self.load_day(d)
            if symbol in daily:
                bars.append(daily[symbol])
                if len(bars) >= n_days:
                    break

            d -= timedelta(days=1)

        # Reverse to chronological order (oldest first)
        return list(reversed(bars))


class MinuteBarsLoader:
    """Load and cache minute bars."""

    def __init__(self):
        self.cache: Dict[date, Dict[str, pd.DataFrame]] = {}

    def load_day(self, trade_date: date) -> Dict[str, pd.DataFrame]:
        """Load minute bars for a date."""
        if trade_date in self.cache:
            return self.cache[trade_date]

        file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
        if not file_path.exists():
            self.cache[trade_date] = {}
            return {}

        try:
            df = pd.read_csv(file_path, compression='gzip')
            df['timestamp'] = pd.to_datetime(df['window_start'], unit='ns')

            grouped = {ticker: group.sort_values('timestamp').reset_index(drop=True)
                      for ticker, group in df.groupby('ticker')}

            self.cache[trade_date] = grouped
            return grouped
        except Exception as e:
            self.cache[trade_date] = {}
            return {}


def calculate_prior_day_ta(symbol: str, trade_date: date, daily_loader: DailyBarsLoader) -> PriorDayTA:
    """Calculate TA indicators from prior day's data."""
    prior_bars = daily_loader.get_prior_daily_bars(symbol, trade_date, n_days=35)

    if len(prior_bars) < 5:
        return PriorDayTA()

    closes = [b['c'] for b in prior_bars]

    ta = PriorDayTA()
    ta.price_prior_close = closes[-1] if closes else None

    # RSI-14
    if len(closes) >= 15:
        ta.rsi_14 = calculate_rsi(closes, 14)

    # MACD
    if len(closes) >= 35:
        ta.macd_line, ta.macd_signal, ta.macd_histogram = calculate_macd(closes)

    # SMA-20
    if len(closes) >= 20:
        ta.sma_20 = calculate_sma(closes, 20)

    # EMA-9
    if len(closes) >= 9:
        ta.ema_9 = calculate_ema(closes, 9)

    return ta


def calculate_intraday_ta(
    symbol: str,
    signal_time: datetime,
    minute_loader: MinuteBarsLoader
) -> IntradayTA:
    """Calculate intraday TA (VWAP) up to signal time."""
    trade_date = signal_time.date()
    day_bars = minute_loader.load_day(trade_date)

    ta = IntradayTA()

    if symbol not in day_bars:
        return ta

    bars = day_bars[symbol]
    mask = bars['timestamp'] <= signal_time
    bars_before = bars[mask]

    if len(bars_before) == 0:
        return ta

    # Price at signal
    ta.price_at_signal = float(bars_before.iloc[-1]['close'])

    # VWAP
    if bars_before['volume'].sum() > 0:
        typical_price = (bars_before['high'] + bars_before['low'] + bars_before['close']) / 3
        ta.vwap = float((typical_price * bars_before['volume']).sum() / bars_before['volume'].sum())
        ta.price_vs_vwap = round((ta.price_at_signal - ta.vwap) / ta.vwap * 100, 2)

    return ta


def enrich_signals(signals: List[dict]) -> List[dict]:
    """Enrich all signals with prior-day TA."""
    print("Enriching signals with prior-day TA...")

    daily_loader = DailyBarsLoader()
    minute_loader = MinuteBarsLoader()

    # Group by date
    by_date = defaultdict(list)
    for s in signals:
        dt = datetime.fromisoformat(s['detection_time'])
        by_date[dt.date()].append(s)

    enriched = 0
    no_prior_data = 0

    sorted_dates = sorted(by_date.keys())

    for i, trade_date in enumerate(sorted_dates):
        day_signals = by_date[trade_date]

        for sig in day_signals:
            symbol = sig['symbol']
            signal_time = datetime.fromisoformat(sig['detection_time'])

            # Prior-day TA
            prior_ta = calculate_prior_day_ta(symbol, trade_date, daily_loader)

            sig['rsi_14_prior'] = prior_ta.rsi_14
            sig['macd_line_prior'] = prior_ta.macd_line
            sig['macd_signal_prior'] = prior_ta.macd_signal
            sig['macd_hist_prior'] = prior_ta.macd_histogram
            sig['sma_20_prior'] = prior_ta.sma_20
            sig['ema_9_prior'] = prior_ta.ema_9
            sig['price_prior_close'] = prior_ta.price_prior_close

            # Intraday TA (VWAP)
            intraday_ta = calculate_intraday_ta(symbol, signal_time, minute_loader)

            sig['vwap'] = intraday_ta.vwap
            sig['price_vs_vwap'] = intraday_ta.price_vs_vwap
            sig['price_at_signal_ta'] = intraday_ta.price_at_signal

            # Track coverage
            if prior_ta.rsi_14 is not None:
                enriched += 1
            else:
                no_prior_data += 1

        if (i + 1) % 20 == 0:
            print(f"  Processed {i+1}/{len(sorted_dates)} days... ({enriched:,} enriched)")

    print(f"\nEnrichment complete:")
    print(f"  With prior-day TA: {enriched:,} ({enriched/(enriched+no_prior_data)*100:.1f}%)")
    print(f"  No prior data: {no_prior_data:,}")

    return signals


def analyze_coverage(signals: List[dict]):
    """Analyze TA coverage after enrichment."""
    print("\n" + "="*60)
    print("TA COVERAGE ANALYSIS")
    print("="*60)

    total = len(signals)

    # Prior-day coverage
    has_rsi = len([s for s in signals if s.get('rsi_14_prior') is not None])
    has_macd = len([s for s in signals if s.get('macd_hist_prior') is not None])
    has_sma = len([s for s in signals if s.get('sma_20_prior') is not None])

    # Intraday coverage
    has_vwap = len([s for s in signals if s.get('vwap') is not None])

    print(f"\nTotal signals: {total:,}")
    print(f"\nPrior-day indicators:")
    print(f"  RSI-14:  {has_rsi:,} ({has_rsi/total*100:.1f}%)")
    print(f"  MACD:    {has_macd:,} ({has_macd/total*100:.1f}%)")
    print(f"  SMA-20:  {has_sma:,} ({has_sma/total*100:.1f}%)")
    print(f"\nIntraday indicators:")
    print(f"  VWAP:    {has_vwap:,} ({has_vwap/total*100:.1f}%)")

    # Combined coverage
    has_all_prior = len([s for s in signals
                        if s.get('rsi_14_prior') is not None
                        and s.get('macd_hist_prior') is not None])
    print(f"\nCombined (RSI + MACD): {has_all_prior:,} ({has_all_prior/total*100:.1f}%)")

    return {
        'total': total,
        'rsi_coverage': has_rsi / total * 100,
        'macd_coverage': has_macd / total * 100,
        'vwap_coverage': has_vwap / total * 100,
        'combined_coverage': has_all_prior / total * 100,
    }


def save_enriched_signals(signals: List[dict], output_path: Path):
    """Save enriched signals."""
    output = {
        'description': 'Signals enriched with prior-day TA indicators',
        'indicators': ['rsi_14_prior', 'macd_line_prior', 'macd_signal_prior',
                      'macd_hist_prior', 'sma_20_prior', 'ema_9_prior',
                      'vwap', 'price_vs_vwap'],
        'signals': signals,
    }

    with open(output_path, 'w') as f:
        json.dump(output, f)

    print(f"\nSaved to {output_path}")


def main():
    # Load signals
    print("Loading signals...")

    # Try corrected signals first, then fall back to original
    corrected_path = RESULTS_DIR / "signals_trend_corrected.json"
    if corrected_path.exists():
        with open(corrected_path) as f:
            data = json.load(f)
        signals = data['signals']
        print(f"Loaded {len(signals):,} signals (trend-corrected)")
    else:
        with open(RESULTS_DIR / "e2e_backtest_v2_strikes_sweeps_price_scored.json") as f:
            scored = json.load(f)
        with open(RESULTS_DIR / "e2e_backtest_with_outcomes.json") as f:
            outcomes = json.load(f)

        outcomes_lookup = {(s['symbol'], s['detection_time'][:16]): s
                          for s in outcomes['signals']}

        signals = []
        for s in scored['signals']:
            key = (s['symbol'], s['detection_time'][:16])
            if key in outcomes_lookup:
                o = outcomes_lookup[key]
                if o.get('pct_to_close') is not None and not o.get('filtered_out'):
                    signals.append({**s, **o})

        print(f"Loaded {len(signals):,} signals (original)")

    # Enrich with prior-day TA
    signals = enrich_signals(signals)

    # Analyze coverage
    coverage = analyze_coverage(signals)

    # Save
    output_path = RESULTS_DIR / "signals_with_prior_day_ta.json"
    save_enriched_signals(signals, output_path)

    # Summary
    print("\n" + "="*60)
    print("PROD-2 SUMMARY")
    print("="*60)

    target_coverage = 90
    actual_coverage = coverage['rsi_coverage']

    print(f"\nTarget coverage: {target_coverage}%")
    print(f"Actual RSI coverage: {actual_coverage:.1f}%")

    if actual_coverage >= target_coverage:
        print(f"\n[PASS] Coverage target met!")
    else:
        print(f"\n[INFO] Coverage below target, but significantly improved from 15.8%")


if __name__ == "__main__":
    main()
