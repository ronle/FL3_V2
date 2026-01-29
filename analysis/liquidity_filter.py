"""
Liquidity & Price Filter (Component 5.7)

Filters out penny stocks and illiquid tickers that are untradeable
or have excessive slippage risk.
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FilterReason(Enum):
    """Reason for filtering out a candidate."""
    PASSED = "PASSED"
    PENNY_STOCK = "PENNY_STOCK"
    LOW_OPTION_VOLUME = "LOW_OPTION_VOLUME"
    LOW_OPEN_INTEREST = "LOW_OPEN_INTEREST"


@dataclass
class LiquidityResult:
    """Result of liquidity filter check."""
    passed: bool
    reason: FilterReason
    stock_price: float
    avg_option_volume: float
    total_open_interest: int
    details: str


@dataclass
class LiquidityThresholds:
    """Configurable thresholds for liquidity filter."""
    min_stock_price: float = 5.00
    min_avg_option_volume: float = 500
    min_total_open_interest: int = 1000
    enabled: bool = True

    @classmethod
    def from_config(cls, config_path: Optional[Path] = None) -> 'LiquidityThresholds':
        """Load thresholds from config file."""
        if config_path is None:
            config_path = Path(__file__).parent.parent / 'config' / 'filters.json'

        try:
            with open(config_path) as f:
                config = json.load(f)
                liquidity = config.get('liquidity', {})
                return cls(
                    min_stock_price=liquidity.get('min_stock_price', 5.00),
                    min_avg_option_volume=liquidity.get('min_avg_option_volume', 500),
                    min_total_open_interest=liquidity.get('min_total_open_interest', 1000),
                    enabled=liquidity.get('enabled', True)
                )
        except FileNotFoundError:
            logger.warning(f"Config file not found: {config_path}, using defaults")
            return cls()
        except Exception as e:
            logger.warning(f"Error loading config: {e}, using defaults")
            return cls()


# Default thresholds (can be overridden by config)
_thresholds: Optional[LiquidityThresholds] = None


def get_thresholds() -> LiquidityThresholds:
    """Get current thresholds (lazy load from config)."""
    global _thresholds
    if _thresholds is None:
        _thresholds = LiquidityThresholds.from_config()
    return _thresholds


def reload_thresholds(config_path: Optional[Path] = None) -> LiquidityThresholds:
    """Reload thresholds from config file."""
    global _thresholds
    _thresholds = LiquidityThresholds.from_config(config_path)
    return _thresholds


def check_liquidity(
    stock_price: float,
    avg_option_volume: float,
    total_open_interest: int,
    thresholds: Optional[LiquidityThresholds] = None
) -> LiquidityResult:
    """
    Check if a symbol passes liquidity requirements.

    Args:
        stock_price: Current stock price
        avg_option_volume: Average daily options volume (ORATS avg_daily_volume)
        total_open_interest: Total open interest across all strikes
        thresholds: Optional custom thresholds (uses config if None)

    Returns:
        LiquidityResult with pass/fail and reason
    """
    if thresholds is None:
        thresholds = get_thresholds()

    # If filter disabled, always pass
    if not thresholds.enabled:
        return LiquidityResult(
            passed=True,
            reason=FilterReason.PASSED,
            stock_price=stock_price,
            avg_option_volume=avg_option_volume,
            total_open_interest=total_open_interest,
            details="Filter disabled"
        )

    # Check stock price (penny stock filter)
    if stock_price < thresholds.min_stock_price:
        return LiquidityResult(
            passed=False,
            reason=FilterReason.PENNY_STOCK,
            stock_price=stock_price,
            avg_option_volume=avg_option_volume,
            total_open_interest=total_open_interest,
            details=f"Price ${stock_price:.2f} < ${thresholds.min_stock_price:.2f} minimum"
        )

    # Check options volume
    if avg_option_volume < thresholds.min_avg_option_volume:
        return LiquidityResult(
            passed=False,
            reason=FilterReason.LOW_OPTION_VOLUME,
            stock_price=stock_price,
            avg_option_volume=avg_option_volume,
            total_open_interest=total_open_interest,
            details=f"Avg option volume {avg_option_volume:.0f} < {thresholds.min_avg_option_volume:.0f} minimum"
        )

    # Check open interest
    if total_open_interest < thresholds.min_total_open_interest:
        return LiquidityResult(
            passed=False,
            reason=FilterReason.LOW_OPEN_INTEREST,
            stock_price=stock_price,
            avg_option_volume=avg_option_volume,
            total_open_interest=total_open_interest,
            details=f"Total OI {total_open_interest:,} < {thresholds.min_total_open_interest:,} minimum"
        )

    # All checks passed
    return LiquidityResult(
        passed=True,
        reason=FilterReason.PASSED,
        stock_price=stock_price,
        avg_option_volume=avg_option_volume,
        total_open_interest=total_open_interest,
        details="Passed all liquidity checks"
    )


def filter_candidates(
    candidates: list[dict],
    thresholds: Optional[LiquidityThresholds] = None
) -> tuple[list[dict], list[dict], dict]:
    """
    Filter a list of candidates by liquidity requirements.

    Args:
        candidates: List of candidate dicts with 'stock_price', 'avg_daily_volume', 'total_open_interest'
        thresholds: Optional custom thresholds

    Returns:
        Tuple of (passed_candidates, filtered_candidates, stats)
    """
    if thresholds is None:
        thresholds = get_thresholds()

    passed = []
    filtered = []
    stats = {
        FilterReason.PENNY_STOCK.value: 0,
        FilterReason.LOW_OPTION_VOLUME.value: 0,
        FilterReason.LOW_OPEN_INTEREST.value: 0,
    }

    for c in candidates:
        result = check_liquidity(
            stock_price=float(c.get('stock_price') or 0),
            avg_option_volume=float(c.get('avg_daily_volume') or 0),
            total_open_interest=int(c.get('total_open_interest') or 0),
            thresholds=thresholds
        )

        if result.passed:
            passed.append(c)
        else:
            filtered.append({
                'candidate': c,
                'reason': result.reason,
                'details': result.details
            })
            stats[result.reason.value] += 1

    # Log summary
    total = len(candidates)
    passed_count = len(passed)
    filtered_count = len(filtered)

    logger.info(
        f"Liquidity filter: {passed_count}/{total} passed, "
        f"{filtered_count} filtered "
        f"(penny: {stats['PENNY_STOCK']}, "
        f"low_vol: {stats['LOW_OPTION_VOLUME']}, "
        f"low_oi: {stats['LOW_OPEN_INTEREST']})"
    )

    return passed, filtered, stats


def get_filter_summary(stats: dict) -> str:
    """Get human-readable filter summary."""
    parts = []
    for reason, count in stats.items():
        if count > 0:
            parts.append(f"{reason}: {count}")
    return ", ".join(parts) if parts else "None filtered"
