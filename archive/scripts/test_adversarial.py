"""
TEST-1: Adversarial Backtest

Worst-case simulation to validate edge is real:
- Entry = HIGHEST price in 5-min window after signal (worst fill)
- Exit = LOWEST price in 5-min window after stop trigger (worst fill)
- Additional 0.1% slippage on top

If still profitable → edge is real
If break-even or negative → no real edge

Success criteria: Positive return under adversarial conditions
"""

import gzip
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, date, time as dt_time
from pathlib import Path
from typing import Optional, List, Tuple

import pandas as pd
import numpy as np

# Paths
BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"

# Strategy parameters
MIN_SCORE = 10
REQUIRED_TREND = 1

# Adversarial parameters
ENTRY_WINDOW_MINUTES = 5
EXIT_WINDOW_MINUTES = 5
ADDITIONAL_SLIPPAGE = 0.001  # 0.1%

# Trailing stop parameters
TRAIL_PCT = 0.5  # 0.5% trailing stop
HARD_STOP_PCT = 2.0  # -2% hard stop


@dataclass
class AdversarialTrade:
    """Trade with adversarial pricing."""
    symbol: str
    signal_time: datetime
    score: int

    # Prices
    signal_price: float
    entry_price: float  # Adversarial: highest in window
    high_water_mark: float
    exit_price: float  # Adversarial: lowest in window

    # Timing
    entry_time: datetime
    exit_time: datetime
    exit_reason: str  # 'trail', 'hard_stop', 'eod'

    # Returns
    raw_return: float
    adversarial_return: float  # After additional slippage

    def __repr__(self):
        return f"{self.symbol} {self.signal_time.strftime('%Y-%m-%d %H:%M')} | Entry:{self.entry_price:.2f} HWM:{self.high_water_mark:.2f} Exit:{self.exit_price:.2f} | {self.adversarial_return:+.2f}% ({self.exit_reason})"


def load_signals():
    """Load scored signals with outcomes."""
    print("Loading signals...")

    with open(RESULTS_DIR / "e2e_backtest_v2_strikes_sweeps_price_scored.json") as f:
        scored = json.load(f)

    with open(RESULTS_DIR / "e2e_backtest_with_outcomes.json") as f:
        outcomes = json.load(f)

    # Build lookup
    outcomes_lookup = {}
    for s in outcomes['signals']:
        key = (s['symbol'], s['detection_time'][:16])
        outcomes_lookup[key] = s

    signals = []
    for s in scored['signals']:
        key = (s['symbol'], s['detection_time'][:16])
        if key in outcomes_lookup:
            o = outcomes_lookup[key]
            if o.get('pct_to_close') is not None and not o.get('filtered_out'):
                merged = {**s, **o}
                merged['detection_dt'] = datetime.fromisoformat(s['detection_time'])
                signals.append(merged)

    print(f"Loaded {len(signals):,} valid signals")
    return signals


def load_minute_bars(trade_date: date) -> dict:
    """Load minute bars for a date, grouped by ticker."""
    file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
    if not file_path.exists():
        return {}

    try:
        df = pd.read_csv(file_path, compression='gzip')
        df['timestamp'] = pd.to_datetime(df['window_start'], unit='ns')

        grouped = {}
        for ticker, group in df.groupby('ticker'):
            grouped[ticker] = group.sort_values('timestamp').reset_index(drop=True)
        return grouped
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return {}


def get_adversarial_entry_price(bars_df: pd.DataFrame, signal_time: datetime) -> Tuple[Optional[float], Optional[datetime]]:
    """
    Entry = WORST price (highest) in 5-min window after signal.
    Returns (price, time) or (None, None) if no bars.
    """
    window_end = signal_time + timedelta(minutes=ENTRY_WINDOW_MINUTES)

    mask = (bars_df['timestamp'] >= signal_time) & (bars_df['timestamp'] < window_end)
    window_bars = bars_df[mask]

    if len(window_bars) == 0:
        # Fallback: use first bar after signal
        after = bars_df[bars_df['timestamp'] >= signal_time]
        if len(after) == 0:
            return None, None
        first_bar = after.iloc[0]
        return first_bar['high'], first_bar['timestamp']

    # Find bar with highest high
    max_idx = window_bars['high'].idxmax()
    worst_bar = window_bars.loc[max_idx]
    return worst_bar['high'], worst_bar['timestamp']


def get_adversarial_exit_price(bars_df: pd.DataFrame, stop_time: datetime) -> Tuple[Optional[float], Optional[datetime]]:
    """
    Exit = WORST price (lowest) in 5-min window after stop trigger.
    Returns (price, time) or (None, None) if no bars.
    """
    window_end = stop_time + timedelta(minutes=EXIT_WINDOW_MINUTES)

    mask = (bars_df['timestamp'] >= stop_time) & (bars_df['timestamp'] < window_end)
    window_bars = bars_df[mask]

    if len(window_bars) == 0:
        # Fallback: use first bar after stop
        after = bars_df[bars_df['timestamp'] >= stop_time]
        if len(after) == 0:
            return None, None
        first_bar = after.iloc[0]
        return first_bar['low'], first_bar['timestamp']

    # Find bar with lowest low
    min_idx = window_bars['low'].idxmin()
    worst_bar = window_bars.loc[min_idx]
    return worst_bar['low'], worst_bar['timestamp']


def simulate_trailing_stop(
    bars_df: pd.DataFrame,
    entry_time: datetime,
    entry_price: float,
    market_close: datetime
) -> Tuple[Optional[datetime], str, float]:
    """
    Simulate trailing stop from entry to market close.

    Returns: (stop_trigger_time, exit_reason, high_water_mark)
    """
    # Get bars from entry to market close
    mask = (bars_df['timestamp'] > entry_time) & (bars_df['timestamp'] <= market_close)
    holding_bars = bars_df[mask].sort_values('timestamp')

    if len(holding_bars) == 0:
        return None, 'no_bars', entry_price

    hwm = entry_price
    trail_level = entry_price * (1 - TRAIL_PCT / 100)
    hard_stop_level = entry_price * (1 - HARD_STOP_PCT / 100)

    for _, bar in holding_bars.iterrows():
        # Update HWM with bar's high
        if bar['high'] > hwm:
            hwm = bar['high']
            trail_level = hwm * (1 - TRAIL_PCT / 100)

        # Check hard stop (intra-bar)
        if bar['low'] <= hard_stop_level:
            return bar['timestamp'], 'hard_stop', hwm

        # Check trailing stop (intra-bar)
        if hwm > entry_price and bar['low'] <= trail_level:
            return bar['timestamp'], 'trail', hwm

    # Held to close
    return market_close, 'eod', hwm


def run_adversarial_backtest(signals: list) -> List[AdversarialTrade]:
    """Run adversarial backtest on signals."""

    # Filter to target strategy
    filtered = [s for s in signals
                if s.get('trend') == REQUIRED_TREND and s.get('score', 0) >= MIN_SCORE]

    print(f"\nTarget signals (trend={REQUIRED_TREND}, score>={MIN_SCORE}): {len(filtered):,}")

    # Group by date
    by_date = defaultdict(list)
    for s in filtered:
        by_date[s['detection_dt'].date()].append(s)

    trades = []
    processed_days = 0
    skipped_no_bars = 0
    skipped_no_entry = 0

    print(f"Processing {len(by_date)} trading days...\n")

    for trade_date in sorted(by_date.keys()):
        day_bars = load_minute_bars(trade_date)
        day_sigs = by_date[trade_date]

        # Market close time
        market_close = datetime.combine(trade_date, dt_time(15, 55))  # 3:55 PM

        for sig in day_sigs:
            symbol = sig['symbol']
            signal_time = sig['detection_dt']

            if symbol not in day_bars:
                skipped_no_bars += 1
                continue

            symbol_bars = day_bars[symbol]

            # Get adversarial entry price
            entry_price, entry_time = get_adversarial_entry_price(symbol_bars, signal_time)
            if entry_price is None:
                skipped_no_entry += 1
                continue

            # Simulate trailing stop
            stop_time, exit_reason, hwm = simulate_trailing_stop(
                symbol_bars, entry_time, entry_price, market_close
            )

            if stop_time is None:
                skipped_no_entry += 1
                continue

            # Get adversarial exit price
            exit_price, exit_time = get_adversarial_exit_price(symbol_bars, stop_time)
            if exit_price is None:
                # Use last known price
                last_bar = symbol_bars.iloc[-1]
                exit_price = last_bar['low']
                exit_time = last_bar['timestamp']

            # Calculate returns
            raw_return = (exit_price - entry_price) / entry_price * 100
            adversarial_return = raw_return - (ADDITIONAL_SLIPPAGE * 100)

            trade = AdversarialTrade(
                symbol=symbol,
                signal_time=signal_time,
                score=sig.get('score', 0),
                signal_price=sig.get('price_at_signal', entry_price),
                entry_price=entry_price,
                high_water_mark=hwm,
                exit_price=exit_price,
                entry_time=entry_time,
                exit_time=exit_time,
                exit_reason=exit_reason,
                raw_return=raw_return,
                adversarial_return=adversarial_return,
            )
            trades.append(trade)

        processed_days += 1
        if processed_days % 20 == 0:
            print(f"  Processed {processed_days}/{len(by_date)} days... ({len(trades)} trades)")

    print(f"\nCompleted: {len(trades)} trades simulated")
    print(f"Skipped (no bars): {skipped_no_bars}")
    print(f"Skipped (no entry): {skipped_no_entry}")

    return trades


def analyze_results(trades: List[AdversarialTrade]):
    """Analyze adversarial backtest results."""

    if not trades:
        print("No trades to analyze")
        return

    print("\n" + "="*70)
    print("ADVERSARIAL BACKTEST RESULTS")
    print("="*70)

    # Overall stats
    returns = [t.adversarial_return for t in trades]
    raw_returns = [t.raw_return for t in trades]

    win_rate = len([r for r in returns if r > 0]) / len(returns) * 100
    avg_return = sum(returns) / len(returns)
    median_return = sorted(returns)[len(returns)//2]

    raw_win_rate = len([r for r in raw_returns if r > 0]) / len(raw_returns) * 100
    raw_avg_return = sum(raw_returns) / len(raw_returns)

    print(f"\nTotal Trades: {len(trades):,}")
    print(f"Trades/Day: {len(trades)/126:.1f}")

    print(f"\n--- Raw (before additional slippage) ---")
    print(f"Win Rate: {raw_win_rate:.1f}%")
    print(f"Avg Return: {raw_avg_return:+.3f}%")

    print(f"\n--- Adversarial (with {ADDITIONAL_SLIPPAGE*100:.1f}% slippage) ---")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Avg Return: {avg_return:+.3f}%")
    print(f"Median Return: {median_return:+.3f}%")

    # Distribution
    big_win = len([r for r in returns if r > 1]) / len(returns) * 100
    small_win = len([r for r in returns if 0 < r <= 1]) / len(returns) * 100
    small_loss = len([r for r in returns if -1 <= r <= 0]) / len(returns) * 100
    big_loss = len([r for r in returns if r < -1]) / len(returns) * 100

    print(f"\n--- Distribution ---")
    print(f"Big Win (>1%):    {big_win:.1f}%")
    print(f"Small Win (0-1%): {small_win:.1f}%")
    print(f"Small Loss (0-1%): {small_loss:.1f}%")
    print(f"Big Loss (<-1%):  {big_loss:.1f}%")

    # By exit reason
    print(f"\n--- By Exit Reason ---")
    for reason in ['trail', 'hard_stop', 'eod']:
        subset = [t for t in trades if t.exit_reason == reason]
        if subset:
            sub_returns = [t.adversarial_return for t in subset]
            sub_wr = len([r for r in sub_returns if r > 0]) / len(sub_returns) * 100
            sub_avg = sum(sub_returns) / len(sub_returns)
            print(f"  {reason:12}: {len(subset):>4} trades ({len(subset)/len(trades)*100:>5.1f}%) | WR: {sub_wr:.1f}% | Avg: {sub_avg:+.3f}%")

    # Cumulative P&L
    cumulative = 0
    max_dd = 0
    peak = 0

    for r in returns:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    print(f"\n--- Cumulative ---")
    print(f"Total Return: {cumulative:+.2f}%")
    print(f"Max Drawdown: {max_dd:.2f}%")

    # GO/NO-GO Decision
    print("\n" + "="*70)
    print("GO/NO-GO DECISION")
    print("="*70)

    if avg_return > 0.20 and win_rate >= 55:
        print(f"\n✅ PASS: Adversarial avg return {avg_return:+.3f}% > +0.20% and WR {win_rate:.1f}% >= 55%")
        print("   → Edge appears real. Proceed to extended testing.")
    elif avg_return > 0:
        print(f"\n⚠️ MARGINAL: Adversarial avg return {avg_return:+.3f}% is positive but below +0.20% target")
        print("   → Edge may exist but is thin. Consider further optimization.")
    else:
        print(f"\n❌ FAIL: Adversarial avg return {avg_return:+.3f}% is negative or zero")
        print("   → No real edge under worst-case assumptions. Reconsider approach.")

    return {
        'total_trades': len(trades),
        'win_rate': win_rate,
        'avg_return': avg_return,
        'raw_win_rate': raw_win_rate,
        'raw_avg_return': raw_avg_return,
        'total_return': cumulative,
        'max_dd': max_dd,
    }


def compare_to_baseline(trades: List[AdversarialTrade], signals: list):
    """Compare adversarial to original backtest results."""

    print("\n" + "="*70)
    print("COMPARISON: Adversarial vs Original Backtest")
    print("="*70)

    # Original results for same signals
    filtered = [s for s in signals
                if s.get('trend') == REQUIRED_TREND and s.get('score', 0) >= MIN_SCORE]

    orig_returns = [s['pct_to_close'] for s in filtered if s.get('pct_to_close') is not None]
    adv_returns = [t.adversarial_return for t in trades]

    orig_wr = len([r for r in orig_returns if r > 0]) / len(orig_returns) * 100
    orig_avg = sum(orig_returns) / len(orig_returns)

    adv_wr = len([r for r in adv_returns if r > 0]) / len(adv_returns) * 100
    adv_avg = sum(adv_returns) / len(adv_returns)

    print(f"\n{'Metric':<25} {'Original':<15} {'Adversarial':<15} {'Diff':<10}")
    print("-"*65)
    print(f"{'Trades':<25} {len(orig_returns):<15,} {len(adv_returns):<15,} {'-':<10}")
    print(f"{'Win Rate':<25} {orig_wr:<15.1f} {adv_wr:<15.1f} {adv_wr-orig_wr:+.1f}")
    print(f"{'Avg Return':<25} {orig_avg:<+15.3f} {adv_avg:<+15.3f} {adv_avg-orig_avg:+.3f}")

    print(f"\n--- Key Insight ---")
    degradation = orig_avg - adv_avg
    print(f"Return degradation from adversarial pricing: {degradation:.3f}%")
    if adv_avg > 0:
        print(f"Edge retained: {adv_avg/orig_avg*100:.1f}% of original")
    else:
        print(f"Edge completely erased by adversarial assumptions")


if __name__ == "__main__":
    signals = load_signals()
    trades = run_adversarial_backtest(signals)

    if trades:
        results = analyze_results(trades)
        compare_to_baseline(trades, signals)

        # Save results
        output = {
            'parameters': {
                'min_score': MIN_SCORE,
                'required_trend': REQUIRED_TREND,
                'entry_window_min': ENTRY_WINDOW_MINUTES,
                'exit_window_min': EXIT_WINDOW_MINUTES,
                'additional_slippage': ADDITIONAL_SLIPPAGE,
                'trail_pct': TRAIL_PCT,
                'hard_stop_pct': HARD_STOP_PCT,
            },
            'results': results,
            'trades': [
                {
                    'symbol': t.symbol,
                    'signal_time': t.signal_time.isoformat(),
                    'entry_price': t.entry_price,
                    'exit_price': t.exit_price,
                    'hwm': t.high_water_mark,
                    'exit_reason': t.exit_reason,
                    'adversarial_return': t.adversarial_return,
                }
                for t in trades
            ]
        }

        output_path = RESULTS_DIR / "adversarial_backtest_results.json"
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\nResults saved to {output_path}")
