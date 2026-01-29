"""
Greeks Calculator (Component 2.3)

Black-Scholes Greeks calculations:
- First-order: Delta, Gamma, Theta, Vega
- Second-order: Vanna, Charm

Used for GEX/DEX aggregation and phase detection.
"""

import math
from dataclasses import dataclass
from typing import Optional
from functools import lru_cache

# Standard normal distribution functions
def _norm_cdf(x: float) -> float:
    """Cumulative distribution function of standard normal."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    """Probability density function of standard normal."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


@dataclass
class Greeks:
    """Option Greeks for a single contract."""
    delta: float
    gamma: float
    theta: float
    vega: float
    vanna: float
    charm: float
    d1: float
    d2: float


@dataclass
class OptionParams:
    """Input parameters for Greeks calculation."""
    spot: float          # Underlying price
    strike: float        # Strike price
    tte: float           # Time to expiry in years
    iv: float            # Implied volatility (annualized, e.g., 0.30 for 30%)
    rate: float = 0.05   # Risk-free rate (default 5%)
    div_yield: float = 0 # Dividend yield (default 0%)
    is_call: bool = True # Call or put


def calc_d1_d2(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float,
    div_yield: float
) -> tuple[float, float]:
    """
    Calculate d1 and d2 for Black-Scholes model.

    d1 = [ln(S/K) + (r - q + σ²/2)T] / (σ√T)
    d2 = d1 - σ√T
    """
    if tte <= 0 or iv <= 0:
        return 0.0, 0.0

    sqrt_t = math.sqrt(tte)
    iv_sqrt_t = iv * sqrt_t

    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * iv * iv) * tte) / iv_sqrt_t
    d2 = d1 - iv_sqrt_t

    return d1, d2


def calculate_delta(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float = 0.05,
    div_yield: float = 0,
    is_call: bool = True
) -> float:
    """
    Calculate option Delta.

    Delta (call) = e^(-qT) × N(d1)
    Delta (put)  = e^(-qT) × (N(d1) - 1)
    """
    if tte <= 0:
        # At expiry
        if is_call:
            return 1.0 if spot > strike else 0.0
        else:
            return -1.0 if spot < strike else 0.0

    d1, _ = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)

    if is_call:
        return exp_div * _norm_cdf(d1)
    else:
        return exp_div * (_norm_cdf(d1) - 1)


def calculate_gamma(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float = 0.05,
    div_yield: float = 0
) -> float:
    """
    Calculate option Gamma (same for calls and puts).

    Gamma = e^(-qT) × n(d1) / (S × σ × √T)
    """
    if tte <= 0 or iv <= 0 or spot <= 0:
        return 0.0

    d1, _ = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)
    sqrt_t = math.sqrt(tte)

    return exp_div * _norm_pdf(d1) / (spot * iv * sqrt_t)


def calculate_theta(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float = 0.05,
    div_yield: float = 0,
    is_call: bool = True
) -> float:
    """
    Calculate option Theta (per day).

    Complex formula - returns negative value (time decay).
    """
    if tte <= 0 or iv <= 0:
        return 0.0

    d1, d2 = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)
    exp_rate = math.exp(-rate * tte)
    sqrt_t = math.sqrt(tte)

    # First term (shared)
    term1 = -(spot * iv * exp_div * _norm_pdf(d1)) / (2 * sqrt_t)

    if is_call:
        term2 = div_yield * spot * exp_div * _norm_cdf(d1)
        term3 = -rate * strike * exp_rate * _norm_cdf(d2)
    else:
        term2 = -div_yield * spot * exp_div * _norm_cdf(-d1)
        term3 = rate * strike * exp_rate * _norm_cdf(-d2)

    # Return per-day theta (divide annual by 365)
    return (term1 + term2 + term3) / 365


def calculate_vega(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float = 0.05,
    div_yield: float = 0
) -> float:
    """
    Calculate option Vega (same for calls and puts).
    Returns change in option price for 1% change in IV.

    Vega = S × e^(-qT) × √T × n(d1)
    """
    if tte <= 0:
        return 0.0

    d1, _ = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)
    sqrt_t = math.sqrt(tte)

    # Per 1% IV change (0.01)
    return spot * exp_div * sqrt_t * _norm_pdf(d1) * 0.01


def calculate_vanna(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float = 0.05,
    div_yield: float = 0
) -> float:
    """
    Calculate Vanna (dDelta/dIV or dVega/dSpot).

    Vanna = -e^(-qT) × n(d1) × (d2/σ)
    """
    if tte <= 0 or iv <= 0:
        return 0.0

    d1, d2 = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)

    return -exp_div * _norm_pdf(d1) * (d2 / iv)


def calculate_charm(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    rate: float = 0.05,
    div_yield: float = 0,
    is_call: bool = True
) -> float:
    """
    Calculate Charm (dDelta/dTime, delta decay).

    Charm = -e^(-qT) × n(d1) × [q + (d2×σ)/(2T√T)]
    """
    if tte <= 0 or iv <= 0:
        return 0.0

    d1, d2 = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)
    sqrt_t = math.sqrt(tte)

    # Charm formula
    term1 = div_yield
    term2 = (d2 * iv) / (2 * tte * sqrt_t) if tte > 0 else 0

    charm = -exp_div * _norm_pdf(d1) * (term1 + term2)

    # Adjust sign for puts
    if not is_call:
        charm = -charm

    return charm


def calculate_greeks(params: OptionParams) -> Greeks:
    """
    Calculate all Greeks for an option.

    Args:
        params: OptionParams with all inputs

    Returns:
        Greeks dataclass with all calculated values
    """
    d1, d2 = calc_d1_d2(
        params.spot, params.strike, params.tte,
        params.iv, params.rate, params.div_yield
    )

    delta = calculate_delta(
        params.spot, params.strike, params.tte, params.iv,
        params.rate, params.div_yield, params.is_call
    )

    gamma = calculate_gamma(
        params.spot, params.strike, params.tte, params.iv,
        params.rate, params.div_yield
    )

    theta = calculate_theta(
        params.spot, params.strike, params.tte, params.iv,
        params.rate, params.div_yield, params.is_call
    )

    vega = calculate_vega(
        params.spot, params.strike, params.tte, params.iv,
        params.rate, params.div_yield
    )

    vanna = calculate_vanna(
        params.spot, params.strike, params.tte, params.iv,
        params.rate, params.div_yield
    )

    charm = calculate_charm(
        params.spot, params.strike, params.tte, params.iv,
        params.rate, params.div_yield, params.is_call
    )

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        vanna=vanna,
        charm=charm,
        d1=d1,
        d2=d2
    )


def calculate_greeks_fast(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    is_call: bool,
    rate: float = 0.05,
    div_yield: float = 0
) -> dict:
    """
    Fast Greeks calculation returning dict.
    Use for high-volume processing.
    """
    if tte <= 0 or iv <= 0 or spot <= 0:
        return {
            'delta': 1.0 if is_call and spot > strike else (-1.0 if not is_call and spot < strike else 0.0),
            'gamma': 0.0,
            'vanna': 0.0,
            'charm': 0.0
        }

    d1, d2 = calc_d1_d2(spot, strike, tte, iv, rate, div_yield)
    exp_div = math.exp(-div_yield * tte)
    sqrt_t = math.sqrt(tte)
    n_d1 = _norm_pdf(d1)
    N_d1 = _norm_cdf(d1)

    # Delta
    if is_call:
        delta = exp_div * N_d1
    else:
        delta = exp_div * (N_d1 - 1)

    # Gamma
    gamma = exp_div * n_d1 / (spot * iv * sqrt_t)

    # Vanna
    vanna = -exp_div * n_d1 * (d2 / iv)

    # Charm
    charm_term = div_yield + (d2 * iv) / (2 * tte * sqrt_t)
    charm = -exp_div * n_d1 * charm_term
    if not is_call:
        charm = -charm

    return {
        'delta': delta,
        'gamma': gamma,
        'vanna': vanna,
        'charm': charm
    }


if __name__ == "__main__":
    print("Greeks Calculator Tests")
    print("=" * 60)

    # Test case: AAPL $150 call, 30 days to expiry, 30% IV
    params = OptionParams(
        spot=150.0,
        strike=150.0,  # ATM
        tte=30/365,    # 30 days
        iv=0.30,       # 30% IV
        is_call=True
    )

    greeks = calculate_greeks(params)

    print(f"\nATM Call (S={params.spot}, K={params.strike}, T=30d, IV=30%)")
    print(f"  Delta:  {greeks.delta:.4f}")
    print(f"  Gamma:  {greeks.gamma:.6f}")
    print(f"  Theta:  {greeks.theta:.4f} (per day)")
    print(f"  Vega:   {greeks.vega:.4f} (per 1% IV)")
    print(f"  Vanna:  {greeks.vanna:.6f}")
    print(f"  Charm:  {greeks.charm:.6f}")

    # Test put
    params.is_call = False
    greeks_put = calculate_greeks(params)
    print(f"\nATM Put:")
    print(f"  Delta:  {greeks_put.delta:.4f}")
    print(f"  Gamma:  {greeks_put.gamma:.6f}")

    # OTM call
    params.strike = 160.0
    params.is_call = True
    greeks_otm = calculate_greeks(params)
    print(f"\nOTM Call (K=160):")
    print(f"  Delta:  {greeks_otm.delta:.4f}")
    print(f"  Gamma:  {greeks_otm.gamma:.6f}")

    # ITM call
    params.strike = 140.0
    greeks_itm = calculate_greeks(params)
    print(f"\nITM Call (K=140):")
    print(f"  Delta:  {greeks_itm.delta:.4f}")
    print(f"  Gamma:  {greeks_itm.gamma:.6f}")

    # Performance test
    import time
    iterations = 100_000
    start = time.perf_counter()
    for _ in range(iterations):
        calculate_greeks_fast(150.0, 150.0, 30/365, 0.30, True)
    elapsed = time.perf_counter() - start
    rate = iterations / elapsed
    print(f"\nPerformance: {rate:,.0f} Greeks calculations/sec")

    # Validate against known values (ATM call delta should be ~0.5)
    print("\nValidation:")
    atm_delta = calculate_delta(150, 150, 30/365, 0.30, 0.05, 0, True)
    print(f"  ATM Call Delta: {atm_delta:.4f} (expected ~0.52)")
    print(f"  Status: {'PASS' if 0.50 < atm_delta < 0.55 else 'FAIL'}")
