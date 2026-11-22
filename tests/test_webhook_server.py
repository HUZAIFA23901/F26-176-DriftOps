"""Unit and API Integration Test Suite for Reactive Webhook Interceptor.

Tests:
1. Health check endpoint (GET /health) returns HTTP 200.
2. Error log parser (parse_dbt_postgres_error) across all 7 taxonomy classes:
   - Pattern 1: Missing column with hint (column_rename)
   - Pattern 2: Missing column without hint (column_drop_add)
   - Pattern 3: Type mismatch (type_shift)
   - Pattern 4: Nullability constraint (nullability)
   - Pattern 5: Missing relation/table (model_rename)
   - Pattern 6: Unique / Grain constraint violation (grain_violation)
   - Pattern 7: Unrecognized error fallback (unknown_drift)
3. POST /api/v1/webhook/failure endpoint with mocked pipeline dispatcher (HTTP 200).

Project ID: F26-176-DriftOps
Module: tests/test_webhook_server.py
"""

from typing import Any, Dict
from fastapi.testclient import TestClient
import pytest

from src.detector.webhook_server import (
    app,
    get_pipeline_dispatcher,
    parse_dbt_postgres_error,
)


@pytest.fixture
def client():
    """Create a FastAPI TestClient instance."""
    return TestClient(app)


# ==============================================================================
# 1. Health Check Endpoint Test
# ==============================================================================

def test_health_check_endpoint(client):
    """Verify GET /health returns HTTP 200 and service metadata."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "DriftOps" in data["service"]


# ==============================================================================
# 2. Error Log Parser (parse_dbt_postgres_error) Tests
# ==============================================================================

def test_parse_column_rename_with_hint():
    """Verify PostgreSQL missing column with suggestion hint maps to column_rename."""
    error = (
        'column "customer_phone" does not exist\n'
        "LINE 3:     customer_phone,\n"
        "            ^\n"
        'HINT:  Perhaps you meant to reference the column "contact_number".'
    )
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "column_rename"
    assert payload["old_col"] == "customer_phone"
    assert payload["new_col"] == "contact_number"


def test_parse_column_drop_without_hint():
    """Verify PostgreSQL missing column without hint maps to column_drop_add."""
    error = 'ERROR: column "deprecated_notes" does not exist at character 38'
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "column_drop_add"
    assert payload["dropped_columns"] == ["deprecated_notes"]
    assert payload["added_columns"] == []


def test_parse_type_mismatch_expression():
    """Verify type mismatch (column is of type X but expression is of type Y) maps to type_shift."""
    error = 'ERROR: column "order_id" is of type bigint but expression is of type integer'
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "type_shift"
    assert payload["column_name"] == "order_id"
    assert payload["source_type"] == "integer"
    assert payload["target_type"] == "bigint"


def test_parse_type_mismatch_cannot_cast():
    """Verify type mismatch (cannot cast type X to Y) maps to type_shift."""
    error = 'ERROR: cannot cast type varchar to integer in column "acc_balance"'
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "type_shift"
    assert payload["source_type"] == "varchar"
    assert payload["target_type"] == "integer"


def test_parse_nullability_constraint():
    """Verify null value in column violating not-null constraint maps to nullability."""
    error = (
        'ERROR: null value in column "email" of relation "raw_customers" '
        "violates not-null constraint\n"
        "DETAIL: Failing row contains (42, null, +1-555-0199)."
    )
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "nullability"
    assert payload["column_name"] == "email"


def test_parse_model_rename_missing_relation():
    """Verify relation does not exist maps to model_rename."""
    error = 'ERROR: relation "raw.raw_customers" does not exist at character 45'
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "model_rename"
    assert payload["old_model_name"] == "raw.raw_customers"


def test_parse_grain_violation_unique_constraint():
    """Verify duplicate key / unique constraint error maps to grain_violation."""
    error = 'ERROR: duplicate key value violates unique constraint "customers_pkey"'
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "grain_violation"
    assert "duplicate key value" in payload["reason"]


def test_parse_grain_violation_dbt_assertion():
    """Verify dbt test assertion failure log maps to grain_violation."""
    error = "Failure in test unique_stg_customers_customer_id (Got 4 results, configured to fail if != 0)"
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "grain_violation"
    assert "Got 4 results" in payload["reason"]


def test_parse_unknown_drift_fallback():
    """Verify unrecognized error strings fall back to unknown_drift."""
    error = 'FATAL: database "shadow_test_db" does not allow connections'
    failure_class, payload = parse_dbt_postgres_error(error)
    assert failure_class == "unknown_drift"
    assert payload["raw_error"] == error


# ==============================================================================
# 3. POST /api/v1/webhook/failure Endpoint Tests
# ==============================================================================

def test_webhook_failure_endpoint_success(client):
    """Verify POST /api/v1/webhook/failure classifies error and returns HTTP 200 with decision."""
    # Mock pipeline dispatcher to isolate testing from live database/Docker
    def mock_dispatcher(payload, detected_class: str, extracted_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tier": "TIER_1_AUTO_FIX",
            "risk_score": 0.18,
            "hard_gates_passed": True,
            "reasons": ["Risk score 0.18 is below Tier 1 ceiling 0.20"],
            "patched_sql": "SELECT id, contact_number AS customer_phone FROM raw.raw_customers",
        }

    app.dependency_overrides[get_pipeline_dispatcher] = lambda: mock_dispatcher

    try:
        request_body = {
            "pipeline_id": "dag_nightly_analytics_20261122",
            "model_name": "stg_customers",
            "failed_sql": "SELECT id, customer_phone FROM raw.raw_customers",
            "error_message": (
                'column "customer_phone" does not exist\n'
                'HINT:  Perhaps you meant to reference the column "contact_number".'
            ),
            "source_table": "raw.raw_customers",
            "downstream_count": 2,
            "metadata": {"orchestrator": "airflow", "run_id": "manual__2026-11-22"},
        }

        response = client.post("/api/v1/webhook/failure", json=request_body)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "processed"
        assert data["pipeline_id"] == "dag_nightly_analytics_20261122"
        assert data["detected_class"] == "column_rename"
        assert data["extracted_payload"]["old_col"] == "customer_phone"
        assert data["extracted_payload"]["new_col"] == "contact_number"
        assert data["decision_summary"]["tier"] == "TIER_1_AUTO_FIX"
        assert data["decision_summary"]["hard_gates_passed"] is True
    finally:
        app.dependency_overrides.clear()


def test_webhook_failure_endpoint_type_shift(client):
    """Verify POST /api/v1/webhook/failure correctly routes type_shift error."""
    def mock_dispatcher(payload, detected_class: str, extracted_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tier": "TIER_2_ASSIST",
            "risk_score": 0.45,
            "hard_gates_passed": True,
            "reasons": ["Tier 2 bounded review required for type shift"],
            "patched_sql": "SELECT CAST(order_id AS bigint) AS order_id FROM raw.orders",
        }

    app.dependency_overrides[get_pipeline_dispatcher] = lambda: mock_dispatcher

    try:
        request_body = {
            "pipeline_id": "dag_orders_sync_456",
            "model_name": "stg_orders",
            "failed_sql": "SELECT order_id FROM raw.orders",
            "error_message": 'ERROR: column "order_id" is of type bigint but expression is of type integer',
            "source_table": "raw.orders",
            "downstream_count": 3,
        }

        response = client.post("/api/v1/webhook/failure", json=request_body)
        assert response.status_code == 200

        data = response.json()
        assert data["detected_class"] == "type_shift"
        assert data["extracted_payload"]["column_name"] == "order_id"
        assert data["extracted_payload"]["source_type"] == "integer"
        assert data["extracted_payload"]["target_type"] == "bigint"
        assert data["decision_summary"]["tier"] == "TIER_2_ASSIST"
    finally:
        app.dependency_overrides.clear()
