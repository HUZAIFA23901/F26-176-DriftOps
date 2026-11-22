"""Detector Module: Schema drift detection and failure diagnostics.

Responsible for:
- Detecting column renames, deletions, type mutations, and upstream relation drift
- Pre-flight schema metadata snapshot and diff classification
"""

from src.detector.preflight import (
    ColumnSchema,
    DriftEvent,
    TableSchema,
    detect_schema_drift,
    fetch_live_pg_schema,
)

__all__ = [
    "ColumnSchema",
    "TableSchema",
    "DriftEvent",
    "fetch_live_pg_schema",
    "detect_schema_drift",
]
