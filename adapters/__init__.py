"""Adapters module for FL3_V2 - external API integrations."""

from .polygon_snapshot import (
    PolygonSnapshotFetcher,
    SnapshotResult,
    OptionContract,
    fetch_snapshot,
)

__all__ = [
    'PolygonSnapshotFetcher',
    'SnapshotResult',
    'OptionContract',
    'fetch_snapshot',
]
