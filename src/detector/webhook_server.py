"""Reactive Webhook Interceptor for DriftOps.

FastAPI HTTP server listening for dbt execution failures dispatched by
orchestration engines (Airflow, Prefect, GitHub Actions, Dagster).
Parses PostgreSQL and dbt runtime error messages into canonical drift taxonomy
classes and triggers the verification & repair pipeline.

Project ID: F26-176-DriftOps
Module: src/detector/webhook_server.py
"""

from contextlib import asynccontextmanager
import re
from typing import Any, Callable, Dict, Tuple

from fastapi import Depends, FastAPI, status
from pydantic import BaseModel, Field


class WebhookFailurePayload(BaseModel):
    """Payload received from external orchestrators on dbt execution failure."""
    pipeline_id: str
    model_name: str
    failed_sql: str
    error_message: str
    source_table: str = "raw.raw_customers"
    downstream_count: int = 2
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WebhookResponse(BaseModel):
    """Structured response acknowledging error ingestion and pipeline classification."""
    status: str
    pipeline_id: str
    detected_class: str
    extracted_payload: Dict[str, Any]
    decision_summary: Dict[str, Any]


def parse_dbt_postgres_error(error_message: str) -> Tuple[str, Dict[str, Any]]:
    """Parse PostgreSQL or dbt error log string and map to canonical failure taxonomy.

    Supported patterns:
    1. Missing column with Postgres hint:
       column "<old_col>" does not exist ... Perhaps you meant to reference the column "<new_col>"
       -> ("column_rename", {"old_col": old_col, "new_col": new_col})
    2. Missing column without hint:
       column "<col>" does not exist
       -> ("column_drop_add", {"dropped_columns": [col], "added_columns": []})
    3. Type mismatch:
       column "<col>" is of type <target_type> but expression is of type <source_type>
       or cannot cast type <source_type> to <target_type>
       -> ("type_shift", {"column_name": col, "source_type": source_type, "target_type": target_type})
    4. Nullability constraint:
       null value in column "<col>" ... violates not-null constraint
       -> ("nullability", {"column_name": col})
    5. Missing table/model:
       relation "<old_model>" does not exist
       -> ("model_rename", {"old_model_name": old_model, "new_model_name": ""})
    6. Uniqueness / grain violation:
       duplicate key value violates unique constraint or Got \\d+ results, configured to fail if != 0
       -> ("grain_violation", {"reason": error_message})
    7. Fallback for unrecognized errors:
       -> ("unknown_drift", {"raw_error": error_message})

    Args:
        error_message: Raw string of the database error or dbt test failure log.

    Returns:
        Tuple of (failure_class, extracted_payload_dict).
    """
    raw_msg = (error_message or "").strip()

    # Pattern 1: Missing column with Postgres hint (MUST precede generic column missing)
    rename_hint_match = re.search(
        r'column\s+["\'](?P<old_col>[^"\']+)["\']\s+does\s+not\s+exist.*?'
        r'perhaps\s+you\s+meant\s+to\s+reference\s+the\s+column\s+["\'](?P<new_col>[^"\']+)["\']',
        raw_msg,
        re.IGNORECASE | re.DOTALL,
    )
    if rename_hint_match:
        old_col = rename_hint_match.group("old_col")
        new_col = rename_hint_match.group("new_col")
        return "column_rename", {
            "old_col": old_col,
            "new_col": new_col,
            "old_column": old_col,
            "new_column": new_col,
        }

    # Pattern 2: Missing column without hint
    missing_col_match = re.search(
        r'column\s+["\'](?P<col>[^"\']+)["\']\s+does\s+not\s+exist',
        raw_msg,
        re.IGNORECASE,
    )
    if missing_col_match:
        col = missing_col_match.group("col")
        return "column_drop_add", {
            "dropped_columns": [col],
            "added_columns": [],
        }

    # Pattern 3A: Type mismatch (column is of type X but expression is of type Y)
    type_mismatch_match = re.search(
        r'column\s+["\'](?P<col>[^"\']+)["\']\s+is\s+of\s+type\s+(?P<target_type>[^\s,;]+)\s+'
        r'but\s+expression\s+is\s+of\s+type\s+(?P<source_type>[^\s,;]+)',
        raw_msg,
        re.IGNORECASE,
    )
    if type_mismatch_match:
        return "type_shift", {
            "column_name": type_mismatch_match.group("col"),
            "source_type": type_mismatch_match.group("source_type"),
            "target_type": type_mismatch_match.group("target_type"),
        }

    # Pattern 3B: Type mismatch (cannot cast type X to Y)
    cannot_cast_match = re.search(
        r'cannot\s+cast\s+type\s+(?P<source_type>[^\s,;]+)\s+to\s+(?P<target_type>[^\s,;]+)',
        raw_msg,
        re.IGNORECASE,
    )
    if cannot_cast_match:
        # Check if a column name is mentioned in quotes in the message
        col_match = re.search(r'column\s+["\'](?P<col>[^"\']+)["\']', raw_msg, re.IGNORECASE)
        col_name = col_match.group("col") if col_match else ""
        return "type_shift", {
            "column_name": col_name,
            "source_type": cannot_cast_match.group("source_type"),
            "target_type": cannot_cast_match.group("target_type"),
        }

    # Pattern 4: Nullability constraint
    nullability_match = re.search(
        r'null\s+value\s+in\s+column\s+["\'](?P<col>[^"\']+)["\'].*?violates\s+not-null\s+constraint',
        raw_msg,
        re.IGNORECASE | re.DOTALL,
    )
    if nullability_match:
        return "nullability", {
            "column_name": nullability_match.group("col"),
        }

    # Pattern 5: Missing table/relation/model
    relation_match = re.search(
        r'relation\s+["\'](?P<model>[^"\']+)["\']\s+does\s+not\s+exist',
        raw_msg,
        re.IGNORECASE,
    )
    if relation_match:
        old_model = relation_match.group("model")
        return "model_rename", {
            "old_model_name": old_model,
            "new_model_name": "",
            "old_table_name": old_model,
        }

    # Pattern 6: Grain / Uniqueness violation
    grain_match = re.search(
        r'(duplicate\s+key\s+value\s+violates\s+unique\s+constraint|'
        r'Got\s+\d+\s+results,\s*configured\s+to\s+fail\s+if\s*!=\s*0|'
        r'violates\s+unique\s+constraint)',
        raw_msg,
        re.IGNORECASE,
    )
    if grain_match:
        return "grain_violation", {
            "reason": raw_msg,
        }

    # Pattern 7: Fallback for unrecognized error
    return "unknown_drift", {
        "raw_error": raw_msg,
    }


def default_pipeline_dispatcher(
    payload: WebhookFailurePayload,
    detected_class: str,
    extracted_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Default pipeline dispatcher routing failures to src.orchestrator.runner."""
    from src.orchestrator.runner import run_drift_repair_pipeline

    old_col = extracted_payload.get("old_col", "")
    new_col = extracted_payload.get("new_col", "")

    try:
        pipeline_result = run_drift_repair_pipeline(
            broken_sql=payload.failed_sql,
            old_column=old_col,
            new_column=new_col,
            model_name=payload.model_name,
            n_downstream=payload.downstream_count,
            failure_class=detected_class,
        )
        return {
            "tier": pipeline_result.get("tier", "TIER_3_FALLBACK"),
            "risk_score": pipeline_result.get("risk_score", 1.0),
            "hard_gates_passed": pipeline_result.get("hard_gates_passed", False),
            "reasons": pipeline_result.get("reasons", []),
            "patched_sql": pipeline_result.get("patched_sql", ""),
        }
    except Exception as exc:
        return {
            "tier": "TIER_3_FALLBACK",
            "risk_score": 1.0,
            "hard_gates_passed": False,
            "reasons": [f"Pipeline execution error: {exc}"],
            "patched_sql": "",
        }


def get_pipeline_dispatcher() -> Callable[..., Dict[str, Any]]:
    """Dependency provider for pipeline dispatcher."""
    return default_pipeline_dispatcher


# ==============================================================================
# FastAPI Application Initialization
# ==============================================================================

app = FastAPI(
    title="DriftOps Reactive Webhook Interceptor",
    description="Captures production dbt & PostgreSQL failure logs and triggers autonomous repair gates.",
    version="1.0.0",
)


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check() -> Dict[str, str]:
    """Health check endpoint to verify webhook server availability."""
    return {
        "status": "healthy",
        "service": "DriftOps Webhook Interceptor",
    }


@app.post(
    "/api/v1/webhook/failure",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
)
def handle_pipeline_failure_webhook(
    payload: WebhookFailurePayload,
    dispatcher: Callable[..., Dict[str, Any]] = Depends(get_pipeline_dispatcher),
) -> WebhookResponse:
    """Ingest pipeline failure alert, classify error log, and dispatch repair gate."""
    detected_class, extracted_payload = parse_dbt_postgres_error(payload.error_message)

    decision_summary = dispatcher(
        payload=payload,
        detected_class=detected_class,
        extracted_payload=extracted_payload,
    )

    return WebhookResponse(
        status="processed",
        pipeline_id=payload.pipeline_id,
        detected_class=detected_class,
        extracted_payload=extracted_payload,
        decision_summary=decision_summary,
    )
