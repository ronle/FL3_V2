"""Adapters module for FL3_V2 - external API integrations."""

try:
    from .polygon_snapshot import (
        PolygonSnapshotFetcher,
        SnapshotResult,
        OptionContract,
        fetch_snapshot,
    )
except ImportError:
    pass

from .alpaca_bars_batch import (
    AlpacaBarsFetcher,
    Bar,
    BarData,
)

__all__ = [
    'PolygonSnapshotFetcher',
    'SnapshotResult',
    'OptionContract',
    'fetch_snapshot',
    'AlpacaBarsFetcher',
    'Bar',
    'BarData',
]
