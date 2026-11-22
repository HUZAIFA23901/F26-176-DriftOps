"""Detector Module: Schema drift detection and failure diagnostics.

Responsible for:
- Detecting column renames, deletions, type mutations, and upstream relation drift
- Pre-flight schema metadata snapshot and diff classification (Proactive)
- FastAPI Reactive Webhook Interceptor for runtime dbt failures (Reactive)
"""

from src.detector.preflight import (
    ColumnSchema,
    DriftEvent,
    TableSchema,
    detect_schema_drift,
    fetch_live_pg_schema,
)
from src.detector.webhook_server import (
    WebhookFailurePayload,
    WebhookResponse,
    app,
    parse_dbt_postgres_error,
)

__all__ = [
    "ColumnSchema",
    "TableSchema",
    "DriftEvent",
    "fetch_live_pg_schema",
    "detect_schema_drift",
    "app",
    "parse_dbt_postgres_error",
    "WebhookFailurePayload",
    "WebhookResponse",
]
