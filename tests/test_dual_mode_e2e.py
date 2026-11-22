"""Dual-Mode End-to-End Integration Test Suite for DriftOps.

Validates the full Dual-Mode integration lifecycle:
1. Proactive Pre-Flight Pipeline (preflight schema diff -> multi-model repair -> tier routing)
   - Class 1: Column Rename -> TIER_1_AUTO_FIX
   - Class 2: Type Shift (Widening -> TIER_1_AUTO_FIX, Narrowing -> TIER_2_ASSIST)
   - Class 3: Column Drop/Add -> TIER_2_ASSIST
   - Class 4: Nullability Shift -> TIER_2_ASSIST
   - Class 5: Model Rename -> TIER_1_AUTO_FIX
   - Compound schema drift across multiple tables
2. Reactive Webhook Interceptor (FastAPI POST /api/v1/webhook/failure -> parser -> dispatcher -> orchestrator)
   - Postgres hint column rename -> TIER_1_AUTO_FIX
   - Missing column without hint -> TIER_2_ASSIST
   - Uniqueness / Grain violation -> TIER_3_FALLBACK
   - Unknown error fallback -> TIER_3_FALLBACK
3. Strict Backward Compatibility of run_drift_repair_pipeline for legacy callers.

Project ID: F26-176-DriftOps
Module: tests/test_dual_mode_e2e.py
"""

import pytest
from fastapi.testclient import TestClient

from src.detector.preflight import ColumnSchema, TableSchema
from src.detector.webhook_server import app
from src.orchestrator.runner import run_drift_repair_pipeline, run_preflight_pipeline


@pytest.fixture
def api_client():
    """Create fresh FastAPI test client for webhook testing."""
    return TestClient(app)


# ==============================================================================
# 1. Proactive Pre-flight Pipeline E2E Tests
# ==============================================================================

def test_proactive_e2e_column_rename_tier_1_auto_fix():
    """Verify pre-flight column rename detection triggers autonomous TIER_1_AUTO_FIX."""
    baseline = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "customer_phone": ColumnSchema("customer_phone", "varchar"),
                "email": ColumnSchema("email", "varchar"),
            },
        )
    }

    live = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "cust_phone": ColumnSchema("cust_phone", "varchar"),
                "email": ColumnSchema("email", "varchar"),
            },
        )
    }

    model_sql_map = {
        "stg_customers": "SELECT id, customer_phone, email FROM raw.raw_customers"
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["table_name"] == "raw.raw_customers"
    assert report["failure_class"] == "column_rename"
    assert report["tier"] == "TIER_1_AUTO_FIX"
    assert report["hard_gates_passed"] is True
    assert "cust_phone AS customer_phone" in report["patched_sql"]
    assert report["risk_score"] < 0.20


def test_proactive_e2e_nullability_shift_tier_2_assist():
    """Verify pre-flight nullability shift enforces COALESCE and routes to TIER_2_ASSIST."""
    baseline = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "email": ColumnSchema("email", "varchar", is_nullable=True),
            },
        )
    }

    live = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "email": ColumnSchema("email", "varchar", is_nullable=False),
            },
        )
    }

    model_sql_map = {
        "stg_customers": "SELECT id, email FROM raw.raw_customers"
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["failure_class"] == "nullability"
    assert report["tier"] == "TIER_2_ASSIST"
    assert report["hard_gates_passed"] is True
    assert "COALESCE(email, '') AS email" in report["patched_sql"]
    assert any("override" in r.lower() for r in report["reasons"])


def test_proactive_e2e_column_drop_tier_2_assist():
    """Verify pre-flight dropped column injects NULL placeholder and routes to TIER_2_ASSIST."""
    baseline = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "fax_number": ColumnSchema("fax_number", "varchar"),
                "email": ColumnSchema("email", "varchar"),
            },
        )
    }

    live = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "email": ColumnSchema("email", "varchar"),
            },
        )
    }

    model_sql_map = {
        "stg_customers": "SELECT id, fax_number, email FROM raw.raw_customers"
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["failure_class"] == "column_drop_add"
    assert report["tier"] == "TIER_2_ASSIST"
    assert report["hard_gates_passed"] is True
    assert "NULL AS fax_number" in report["patched_sql"]


def test_proactive_e2e_type_shift_widening_tier_1():
    """Verify pre-flight type widening cast routes to TIER_1_AUTO_FIX."""
    baseline = {
        "raw.raw_orders": TableSchema(
            table_name="raw.raw_orders",
            columns={
                "order_id": ColumnSchema("order_id", "integer"),
                "amount": ColumnSchema("amount", "integer"),
            },
        )
    }

    live = {
        "raw.raw_orders": TableSchema(
            table_name="raw.raw_orders",
            columns={
                "order_id": ColumnSchema("order_id", "integer"),
                "amount": ColumnSchema("amount", "bigint"),
            },
        )
    }

    model_sql_map = {
        "stg_orders": "SELECT order_id, amount FROM raw.raw_orders"
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["failure_class"] == "type_shift"
    assert report["tier"] == "TIER_1_AUTO_FIX"
    assert "CAST(amount AS BIGINT) AS amount" in report["patched_sql"]


def test_proactive_e2e_type_shift_narrowing_tier_2():
    """Verify pre-flight type narrowing cast requires human review (TIER_2_ASSIST)."""
    baseline = {
        "raw.raw_orders": TableSchema(
            table_name="raw.raw_orders",
            columns={
                "order_id": ColumnSchema("order_id", "integer"),
                "amount": ColumnSchema("amount", "varchar"),
            },
        )
    }

    live = {
        "raw.raw_orders": TableSchema(
            table_name="raw.raw_orders",
            columns={
                "order_id": ColumnSchema("order_id", "integer"),
                "amount": ColumnSchema("amount", "integer"),
            },
        )
    }

    model_sql_map = {
        "stg_orders": "SELECT order_id, amount FROM raw.raw_orders"
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["failure_class"] == "type_shift"
    assert report["tier"] == "TIER_2_ASSIST"
    assert "CAST(amount AS INT) AS amount" in report["patched_sql"]


def test_proactive_e2e_model_rename_tier_1():
    """Verify pre-flight model/table rename routes to TIER_1_AUTO_FIX."""
    baseline = {
        "raw.customers_v1": TableSchema(
            table_name="raw.customers_v1",
            columns={
                "id": ColumnSchema("id", "integer"),
                "email": ColumnSchema("email", "varchar"),
                "status": ColumnSchema("status", "varchar"),
            },
        )
    }

    live = {
        "raw.customers_v2": TableSchema(
            table_name="raw.customers_v2",
            columns={
                "id": ColumnSchema("id", "integer"),
                "email": ColumnSchema("email", "varchar"),
                "status": ColumnSchema("status", "varchar"),
            },
        )
    }

    model_sql_map = {
        "stg_customers": "SELECT id, email, status FROM raw.customers_v1"
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 1
    report = reports[0]
    assert report["failure_class"] == "model_rename"
    assert report["tier"] == "TIER_1_AUTO_FIX"
    assert "raw.customers_v2" in report["patched_sql"]


def test_proactive_e2e_compound_multi_model():
    """Verify pre-flight detection across multiple tables with diverse drift classes."""
    baseline = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "phone": ColumnSchema("phone", "varchar"),
            },
        ),
        "raw.raw_orders": TableSchema(
            table_name="raw.raw_orders",
            columns={
                "order_id": ColumnSchema("order_id", "integer"),
                "note": ColumnSchema("note", "varchar", is_nullable=True),
            },
        ),
    }

    live = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "phone_number": ColumnSchema("phone_number", "varchar"),
            },
        ),
        "raw.raw_orders": TableSchema(
            table_name="raw.raw_orders",
            columns={
                "order_id": ColumnSchema("order_id", "integer"),
                "note": ColumnSchema("note", "varchar", is_nullable=False),
            },
        ),
    }

    model_sql_map = {
        "stg_customers": "SELECT id, phone FROM raw.raw_customers",
        "stg_orders": "SELECT order_id, note FROM raw.raw_orders",
    }

    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    assert len(reports) == 2
    classes = {r["failure_class"]: r["tier"] for r in reports}
    assert classes["column_rename"] == "TIER_1_AUTO_FIX"
    assert classes["nullability"] == "TIER_2_ASSIST"


def test_proactive_e2e_no_drift():
    """Verify pre-flight diff returns empty list when baseline matches live."""
    schema = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={"id": ColumnSchema("id", "integer")},
        )
    }

    reports = run_preflight_pipeline(
        baseline_manifest=schema,
        live_schema=schema,
        model_sql_map={"stg_customers": "SELECT id FROM raw.raw_customers"},
    )
    assert reports == []


# ==============================================================================
# 2. Reactive Webhook Interceptor E2E Tests
# ==============================================================================

def test_reactive_e2e_column_rename_auto_fix(api_client):
    """Verify webhook with Postgres hint dispatches full pipeline to TIER_1_AUTO_FIX."""
    body = {
        "pipeline_id": "dag_prod_customers_20261122",
        "model_name": "stg_customers",
        "failed_sql": "SELECT id, customer_phone FROM raw.raw_customers",
        "error_message": (
            'column "customer_phone" does not exist\n'
            'HINT:  Perhaps you meant to reference the column "contact_number".'
        ),
        "source_table": "raw.raw_customers",
        "downstream_count": 1,
        "metadata": {
            "mock_sandbox_result": {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}
        },
    }

    response = api_client.post("/api/v1/webhook/failure", json=body)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "processed"
    assert data["detected_class"] == "column_rename"
    assert data["extracted_payload"]["old_col"] == "customer_phone"
    assert data["extracted_payload"]["new_col"] == "contact_number"

    summary = data["decision_summary"]
    assert summary["tier"] == "TIER_1_AUTO_FIX"
    assert summary["hard_gates_passed"] is True
    assert summary["risk_score"] < 0.20
    assert "contact_number AS customer_phone" in summary["patched_sql"]


def test_reactive_e2e_column_drop_assist(api_client):
    """Verify webhook with generic missing column dispatches to TIER_2_ASSIST."""
    body = {
        "pipeline_id": "dag_prod_customers_20261122",
        "model_name": "stg_customers",
        "failed_sql": "SELECT id, customer_phone FROM raw.raw_customers",
        "error_message": 'column "customer_phone" does not exist',
        "source_table": "raw.raw_customers",
        "downstream_count": 2,
        "metadata": {
            "mock_sandbox_result": {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}
        },
    }

    response = api_client.post("/api/v1/webhook/failure", json=body)
    assert response.status_code == 200

    data = response.json()
    assert data["detected_class"] == "column_drop_add"
    summary = data["decision_summary"]
    assert summary["tier"] == "TIER_2_ASSIST"
    assert summary["hard_gates_passed"] is True
    assert "NULL AS customer_phone" in summary["patched_sql"]


def test_reactive_e2e_grain_violation_fallback(api_client):
    """Verify webhook uniqueness violation escalates directly to TIER_3_FALLBACK."""
    body = {
        "pipeline_id": "dag_prod_orders_20261122",
        "model_name": "stg_orders",
        "failed_sql": "SELECT order_id, customer_id FROM raw.raw_orders",
        "error_message": 'ERROR: duplicate key value violates unique constraint "orders_pkey"',
        "source_table": "raw.raw_orders",
        "downstream_count": 3,
        "metadata": {},
    }

    response = api_client.post("/api/v1/webhook/failure", json=body)
    assert response.status_code == 200

    data = response.json()
    assert data["detected_class"] == "grain_violation"
    summary = data["decision_summary"]
    assert summary["tier"] == "TIER_3_FALLBACK"
    assert summary["hard_gates_passed"] is False


def test_reactive_e2e_unknown_drift_fallback(api_client):
    """Verify webhook unrecognized fatal error escalates to TIER_3_FALLBACK."""
    body = {
        "pipeline_id": "dag_fatal_20261122",
        "model_name": "stg_customers",
        "failed_sql": "SELECT 1",
        "error_message": "FATAL: terminating connection due to administrator command",
        "source_table": "raw.raw_customers",
        "downstream_count": 1,
        "metadata": {},
    }

    response = api_client.post("/api/v1/webhook/failure", json=body)
    assert response.status_code == 200

    data = response.json()
    assert data["detected_class"] == "unknown_drift"
    summary = data["decision_summary"]
    assert summary["tier"] == "TIER_3_FALLBACK"
    assert summary["hard_gates_passed"] is False


# ==============================================================================
# 3. Backward Compatibility Tests for run_drift_repair_pipeline
# ==============================================================================

def test_pipeline_backward_compatibility_positional_args():
    """Verify legacy positional argument calling convention remains supported."""
    broken_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    result = run_drift_repair_pipeline(
        broken_sql,
        "customer_phone",
        "contact_number",
        "stg_customers",
        1,
        "column_rename",
        mock_sandbox_result=mock_sandbox,
    )

    assert result["tier"] == "TIER_1_AUTO_FIX"
    assert result["old_column"] == "customer_phone"
    assert result["new_column"] == "contact_number"
    assert "contact_number AS customer_phone" in result["patched_sql"]
    assert result["hard_gates_passed"] is True


def test_pipeline_backward_compatibility_keyword_args():
    """Verify legacy keyword argument calling convention remains supported."""
    broken_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    result = run_drift_repair_pipeline(
        broken_sql=broken_sql,
        old_column="customer_phone",
        new_column="contact_number",
        model_name="stg_customers",
        n_downstream=1,
        failure_class="column_rename",
        source_type="VARCHAR",
        target_type="VARCHAR",
        attnum_match=True,
        mock_sandbox_result=mock_sandbox,
    )

    assert result["tier"] == "TIER_1_AUTO_FIX"
    assert result["c_ast"] >= 0.80
    assert result["hard_gates_passed"] is True


def test_pipeline_unified_payload_calling_convention():
    """Verify new dictionary payload calling convention without old_column/new_column."""
    broken_sql = "SELECT id, email FROM raw.raw_customers"
    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0}

    result = run_drift_repair_pipeline(
        broken_sql=broken_sql,
        failure_class="nullability",
        payload={"column_name": "email", "default_value": "''"},
        mock_sandbox_result=mock_sandbox,
    )

    assert result["tier"] == "TIER_2_ASSIST"
    assert result["class_override"] is True
    assert "COALESCE(email, '') AS email" in result["patched_sql"]
    assert result["hard_gates_passed"] is True
