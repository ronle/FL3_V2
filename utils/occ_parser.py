"""
OCC Symbol Parser (Component 2.1)

Parses OCC-format option symbols to extract underlying, expiry, strike, and right.
Format: O:{UNDERLYING}{YYMMDD}{C/P}{STRIKE}
Example: O:AAPL250117C00150000 -> AAPL, 2025-01-17, Call, $150.00

Performance target: >100K symbols/sec
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Pre-compiled regex for performance
# Matches: optional "O:" prefix, letters (underlying), 6 digits (date), C/P, 8 digits (strike)
OCC_PATTERN = re.compile(r'^(?:O:)?([A-Z]+)(\d{6})([CP])(\d{8})$')


@dataclass(frozen=True)
class ParsedOption:
    """Parsed OCC option symbol."""
    underlying: str
    expiry: date
    right: str  # 'call' or 'put'
    strike: float
    raw_symbol: str

    @property
    def is_call(self) -> bool:
        return self.right == 'call'

    @property
    def is_put(self) -> bool:
        return self.right == 'put'

    @property
    def days_to_expiry(self) -> int:
        """Days until expiration from today."""
        return (self.expiry - date.today()).days


def parse_occ_symbol(symbol: str) -> Optional[ParsedOption]:
    """
    Parse an OCC option symbol.

    Args:
        symbol: OCC format symbol (e.g., "O:AAPL250117C00150000")

    Returns:
        ParsedOption if valid, None if invalid

    Examples:
        >>> parse_occ_symbol("O:AAPL250117C00150000")
        ParsedOption(underlying='AAPL', expiry=date(2025, 1, 17), right='call', strike=150.0, ...)

        >>> parse_occ_symbol("O:A260620P00025000")  # Single-letter underlying
        ParsedOption(underlying='A', expiry=date(2026, 6, 20), right='put', strike=25.0, ...)

        >>> parse_occ_symbol("O:BRKB250321C00450000")  # 4-letter underlying
        ParsedOption(underlying='BRKB', expiry=date(2025, 3, 21), right='call', strike=450.0, ...)
    """
    if not symbol:
        return None

    match = OCC_PATTERN.match(symbol.upper())
    if not match:
        return None

    underlying, date_str, right_char, strike_str = match.groups()

    try:
        # Parse date (YYMMDD)
        year = 2000 + int(date_str[:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        expiry = date(year, month, day)

        # Parse strike (8 digits, implied 3 decimal places)
        strike = int(strike_str) / 1000.0

        # Parse right
        right = 'call' if right_char == 'C' else 'put'

        return ParsedOption(
            underlying=underlying,
            expiry=expiry,
            right=right,
            strike=strike,
            raw_symbol=symbol
        )

    except (ValueError, OverflowError):
        return None


def parse_occ_symbol_fast(symbol: str) -> Optional[dict]:
    """
    Fast parsing without dataclass overhead.
    Use when processing high-volume streams.

    Returns dict with keys: underlying, expiry, right, strike
    """
    if not symbol:
        return None

    # Remove O: prefix
    s = symbol[2:] if symbol.startswith("O:") else symbol
    s = s.upper()

    # Find where date starts (first digit after letters)
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1

    if i == 0 or i + 15 > len(s):  # Need at least 1 letter + 6 date + 1 right + 8 strike
        return None

    try:
        underlying = s[:i]
        date_str = s[i:i+6]
        right_char = s[i+6]
        strike_str = s[i+7:i+15]

        if right_char not in ('C', 'P'):
            return None

        return {
            'underlying': underlying,
            'expiry': f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}",
            'right': 'call' if right_char == 'C' else 'put',
            'strike': int(strike_str) / 1000.0
        }

    except (ValueError, IndexError):
        return None


def extract_underlying(symbol: str) -> Optional[str]:
    """
    Extract just the underlying symbol (fastest path).
    Use when you only need the underlying ticker.
    """
    if not symbol:
        return None

    s = symbol[2:] if symbol.startswith("O:") else symbol

    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1

    return s[:i].upper() if i > 0 else None


# Validation helpers
def is_valid_occ_symbol(symbol: str) -> bool:
    """Check if symbol is valid OCC format."""
    return parse_occ_symbol(symbol) is not None


def get_expiry_date(symbol: str) -> Optional[date]:
    """Extract just the expiry date from OCC symbol."""
    parsed = parse_occ_symbol(symbol)
    return parsed.expiry if parsed else None


# Batch processing
def parse_symbols_batch(symbols: list[str]) -> dict[str, ParsedOption]:
    """
    Parse multiple symbols, returning dict of successful parses.

    Args:
        symbols: List of OCC symbols

    Returns:
        Dict mapping original symbol to ParsedOption (only valid symbols)
    """
    results = {}
    for sym in symbols:
        parsed = parse_occ_symbol(sym)
        if parsed:
            results[sym] = parsed
    return results


def group_by_underlying(symbols: list[str]) -> dict[str, list[str]]:
    """
    Group symbols by their underlying ticker.

    Args:
        symbols: List of OCC symbols

    Returns:
        Dict mapping underlying to list of option symbols
    """
    groups = {}
    for sym in symbols:
        underlying = extract_underlying(sym)
        if underlying:
            if underlying not in groups:
                groups[underlying] = []
            groups[underlying].append(sym)
    return groups


if __name__ == "__main__":
    # Quick tests
    test_symbols = [
        "O:AAPL250117C00150000",
        "O:A260620P00025000",
        "O:BRKB250321C00450000",
        "O:TSLA260115C00250000",
        "O:SPY250221P00400000",
        "INVALID",
        "",
        None,
    ]

    print("OCC Parser Tests")
    print("=" * 60)

    for sym in test_symbols:
        result = parse_occ_symbol(sym)
        if result:
            print(f"{sym} -> {result.underlying} {result.expiry} {result.right} ${result.strike}")
        else:
            print(f"{sym} -> INVALID")

    # Performance test
    import time
    test_sym = "O:AAPL250117C00150000"
    iterations = 100_000

    start = time.perf_counter()
    for _ in range(iterations):
        parse_occ_symbol_fast(test_sym)
    elapsed = time.perf_counter() - start

    rate = iterations / elapsed
    print(f"\nPerformance: {rate:,.0f} parses/sec")
    print(f"Target: >100,000/sec - {'PASS' if rate > 100_000 else 'FAIL'}")
