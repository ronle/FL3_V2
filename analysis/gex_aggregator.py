"""
GEX/DEX Aggregator (Component 2.4)

Aggregates Greeks across option chain to compute:
- Net GEX (Gamma Exposure)
- Net DEX (Delta Exposure)
- Call/Put Walls (max OI strikes)
- Gamma Flip Level (where GEX crosses zero)
- Net VEX (Vanna Exposure)
- Net Charm

Used for phase detection and dealer positioning analysis.
"""

import logging
from dataclasses import dataclass
from typing import Optional
from datetime import date

from .greeks_calculator import calculate_greeks_fast

logger = logging.getLogger(__name__)


@dataclass
class ContractData:
    """Option contract data for GEX calculation."""
    strike: float
    is_call: bool
    open_interest: int
    iv: float
    tte: float  # Time to expiry in years


@dataclass
class GEXMetrics:
    """Aggregated GEX metrics for a symbol."""
    symbol: str
    spot_price: float
    net_gex: float           # Net gamma exposure (positive = dealer long gamma)
    net_dex: float           # Net delta exposure
    call_wall_strike: Optional[float]  # Strike with max call OI
    put_wall_strike: Optional[float]   # Strike with max put OI
    gamma_flip_level: Optional[float]  # Price where GEX = 0
    net_vex: float           # Net Vanna exposure
    net_charm: float         # Net Charm
    contracts_analyzed: int
    total_call_oi: int
    total_put_oi: int


def calculate_gex_per_contract(
    gamma: float,
    open_interest: int,
    spot_price: float,
    is_call: bool
) -> float:
    """
    Calculate GEX contribution for a single contract.

    GEX per contract = Γ × OI × 100 × S² × 0.01

    For dealer positioning:
    - Calls: Dealers are short calls (sold to buyers), so negative gamma
    - Puts: Dealers are short puts (sold to buyers), so positive gamma

    We flip the sign to show NET dealer gamma:
    - Positive net GEX = dealers are long gamma (market stabilizing)
    - Negative net GEX = dealers are short gamma (market destabilizing)
    """
    contract_multiplier = 100  # 100 shares per contract
    scale = 0.01  # Standard GEX scaling

    raw_gex = gamma * open_interest * contract_multiplier * spot_price * spot_price * scale

    # Dealer perspective: short calls = negative gamma, short puts = positive gamma
    if is_call:
        return -raw_gex  # Calls contribute negative GEX (dealers short)
    else:
        return raw_gex   # Puts contribute positive GEX (dealers short puts = long gamma)


def calculate_dex_per_contract(
    delta: float,
    open_interest: int,
    is_call: bool
) -> float:
    """
    Calculate DEX contribution for a single contract.

    DEX = Δ × OI × 100

    Dealer perspective:
    - Short calls: negative delta exposure
    - Short puts: positive delta exposure (puts have negative delta, short = positive)
    """
    contract_multiplier = 100

    raw_dex = delta * open_interest * contract_multiplier

    if is_call:
        return -raw_dex  # Dealers short calls
    else:
        return -raw_dex  # Dealers short puts (put delta is already negative)


def aggregate_gex_metrics(
    symbol: str,
    spot_price: float,
    contracts: list[ContractData],
    rate: float = 0.05
) -> GEXMetrics:
    """
    Aggregate GEX metrics across all contracts.

    Args:
        symbol: Underlying ticker
        spot_price: Current underlying price
        contracts: List of ContractData for all options
        rate: Risk-free rate (default 5%)

    Returns:
        GEXMetrics with all aggregated values
    """
    if not contracts or spot_price <= 0:
        return GEXMetrics(
            symbol=symbol,
            spot_price=spot_price,
            net_gex=0,
            net_dex=0,
            call_wall_strike=None,
            put_wall_strike=None,
            gamma_flip_level=None,
            net_vex=0,
            net_charm=0,
            contracts_analyzed=0,
            total_call_oi=0,
            total_put_oi=0
        )

    net_gex = 0.0
    net_dex = 0.0
    net_vex = 0.0
    net_charm = 0.0
    total_call_oi = 0
    total_put_oi = 0

    # Track OI by strike for wall detection
    call_oi_by_strike = {}
    put_oi_by_strike = {}

    # Track GEX by strike for gamma flip calculation
    gex_by_strike = {}

    for contract in contracts:
        if contract.open_interest <= 0:
            continue

        # Skip contracts with invalid parameters
        if contract.tte <= 0 or contract.iv <= 0:
            continue

        # Calculate Greeks
        greeks = calculate_greeks_fast(
            spot=spot_price,
            strike=contract.strike,
            tte=contract.tte,
            iv=contract.iv,
            is_call=contract.is_call,
            rate=rate
        )

        # GEX contribution
        gex = calculate_gex_per_contract(
            greeks['gamma'], contract.open_interest, spot_price, contract.is_call
        )
        net_gex += gex

        # DEX contribution
        dex = calculate_dex_per_contract(
            greeks['delta'], contract.open_interest, contract.is_call
        )
        net_dex += dex

        # Vanna contribution (same sign treatment as GEX)
        vanna_contrib = greeks['vanna'] * contract.open_interest * 100
        if contract.is_call:
            net_vex -= vanna_contrib
        else:
            net_vex += vanna_contrib

        # Charm contribution
        charm_contrib = greeks['charm'] * contract.open_interest * 100
        net_charm += charm_contrib

        # Track OI by strike
        strike = contract.strike
        if contract.is_call:
            total_call_oi += contract.open_interest
            call_oi_by_strike[strike] = call_oi_by_strike.get(strike, 0) + contract.open_interest
        else:
            total_put_oi += contract.open_interest
            put_oi_by_strike[strike] = put_oi_by_strike.get(strike, 0) + contract.open_interest

        # Track GEX by strike for gamma flip
        gex_by_strike[strike] = gex_by_strike.get(strike, 0) + gex

    # Find call wall (strike with max call OI)
    call_wall = max(call_oi_by_strike.items(), key=lambda x: x[1])[0] if call_oi_by_strike else None

    # Find put wall (strike with max put OI)
    put_wall = max(put_oi_by_strike.items(), key=lambda x: x[1])[0] if put_oi_by_strike else None

    # Find gamma flip level (where cumulative GEX crosses zero)
    gamma_flip = find_gamma_flip(gex_by_strike, spot_price)

    return GEXMetrics(
        symbol=symbol,
        spot_price=spot_price,
        net_gex=net_gex,
        net_dex=net_dex,
        call_wall_strike=call_wall,
        put_wall_strike=put_wall,
        gamma_flip_level=gamma_flip,
        net_vex=net_vex,
        net_charm=net_charm,
        contracts_analyzed=len(contracts),
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi
    )


def find_gamma_flip(gex_by_strike: dict[float, float], spot_price: float) -> Optional[float]:
    """
    Find the price level where GEX flips from positive to negative.

    This is approximated by finding strikes where cumulative GEX changes sign.
    """
    if not gex_by_strike:
        return None

    # Sort strikes
    strikes = sorted(gex_by_strike.keys())

    # Calculate cumulative GEX from lowest strike
    cumulative = 0.0
    last_strike = None
    last_cumulative = 0.0

    for strike in strikes:
        cumulative += gex_by_strike[strike]

        # Check for sign change
        if last_cumulative != 0 and cumulative != 0:
            if (last_cumulative > 0) != (cumulative > 0):
                # Interpolate to find exact flip point
                if last_strike is not None:
                    # Linear interpolation
                    ratio = abs(last_cumulative) / (abs(last_cumulative) + abs(cumulative))
                    flip = last_strike + ratio * (strike - last_strike)
                    return flip

        last_strike = strike
        last_cumulative = cumulative

    # No flip found - return None or spot price
    return None


def interpret_gex(metrics: GEXMetrics) -> dict:
    """
    Interpret GEX metrics for phase detection.

    Returns dict with:
    - dealer_position: 'long_gamma' or 'short_gamma'
    - market_regime: 'stabilizing', 'destabilizing', or 'neutral'
    - support_level: put wall strike
    - resistance_level: call wall strike
    """
    if metrics.net_gex > 0:
        dealer_position = 'long_gamma'
        market_regime = 'stabilizing'  # Dealers buy dips, sell rips
    elif metrics.net_gex < 0:
        dealer_position = 'short_gamma'
        market_regime = 'destabilizing'  # Dealers amplify moves
    else:
        dealer_position = 'neutral'
        market_regime = 'neutral'

    return {
        'dealer_position': dealer_position,
        'market_regime': market_regime,
        'support_level': metrics.put_wall_strike,
        'resistance_level': metrics.call_wall_strike,
        'gamma_flip': metrics.gamma_flip_level,
        'net_gex_millions': metrics.net_gex / 1_000_000,
        'net_dex_shares': int(metrics.net_dex),
    }


if __name__ == "__main__":
    print("GEX Aggregator Tests")
    print("=" * 60)

    # Create sample option chain
    spot = 150.0
    tte = 30 / 365  # 30 days

    # Sample contracts: AAPL at $150 with various strikes
    contracts = [
        # Calls
        ContractData(strike=140, is_call=True, open_interest=5000, iv=0.28, tte=tte),
        ContractData(strike=145, is_call=True, open_interest=8000, iv=0.27, tte=tte),
        ContractData(strike=150, is_call=True, open_interest=15000, iv=0.26, tte=tte),  # ATM
        ContractData(strike=155, is_call=True, open_interest=12000, iv=0.27, tte=tte),
        ContractData(strike=160, is_call=True, open_interest=6000, iv=0.29, tte=tte),
        # Puts
        ContractData(strike=140, is_call=False, open_interest=4000, iv=0.30, tte=tte),
        ContractData(strike=145, is_call=False, open_interest=7000, iv=0.29, tte=tte),
        ContractData(strike=150, is_call=False, open_interest=10000, iv=0.26, tte=tte),  # ATM
        ContractData(strike=155, is_call=False, open_interest=3000, iv=0.25, tte=tte),
        ContractData(strike=160, is_call=False, open_interest=1000, iv=0.24, tte=tte),
    ]

    metrics = aggregate_gex_metrics("AAPL", spot, contracts)

    print(f"\nSymbol: {metrics.symbol}")
    print(f"Spot Price: ${metrics.spot_price}")
    print(f"Contracts Analyzed: {metrics.contracts_analyzed}")
    print(f"\nGEX Metrics:")
    print(f"  Net GEX: {metrics.net_gex:,.0f}")
    print(f"  Net DEX: {metrics.net_dex:,.0f} shares")
    print(f"  Net VEX: {metrics.net_vex:,.0f}")
    print(f"  Net Charm: {metrics.net_charm:,.2f}")
    print(f"\nKey Levels:")
    print(f"  Call Wall: ${metrics.call_wall_strike}")
    print(f"  Put Wall: ${metrics.put_wall_strike}")
    print(f"  Gamma Flip: ${metrics.gamma_flip_level:.2f}" if metrics.gamma_flip_level else "  Gamma Flip: N/A")
    print(f"\nOI Summary:")
    print(f"  Total Call OI: {metrics.total_call_oi:,}")
    print(f"  Total Put OI: {metrics.total_put_oi:,}")
    print(f"  P/C Ratio: {metrics.total_put_oi / metrics.total_call_oi:.2f}" if metrics.total_call_oi > 0 else "")

    interpretation = interpret_gex(metrics)
    print(f"\nInterpretation:")
    print(f"  Dealer Position: {interpretation['dealer_position']}")
    print(f"  Market Regime: {interpretation['market_regime']}")
    print(f"  Net GEX: {interpretation['net_gex_millions']:.2f}M")
