"""Unit and integration test suite for DriftOps Ephemeral Sandbox and dbt Runner.

Project ID: F26-176-DriftOps
Module: tests/test_sandbox.py
"""

import pytest
import psycopg2
import docker

from src.sandbox.container_manager import SandboxContainer
from src.sandbox.dbt_runner import parse_dbt_run_results, run_dbt_tests


def is_docker_available() -> bool:
    """Check if local Docker daemon is available and responsive."""
    try:
        client = docker.from_env()
        return bool(client.ping())
    except Exception:
        return False


# ==============================================================================
# 1. dbt Run Results & F_tests Metric Parser Tests (Pure Unit Tests)
# ==============================================================================

def test_parse_dbt_run_results_zero_failures():
    """Verify F_tests is 0.0 when all tests pass."""
    fake_json = {
        "results": [
            {"status": "pass", "unique_id": "test_1"},
            {"status": "pass", "unique_id": "test_2"},
            {"status": "pass", "unique_id": "test_3"},
            {"status": "pass", "unique_id": "test_4"},
        ]
    }
    metrics = parse_dbt_run_results(run_results_data=fake_json)
    assert metrics["total"] == 4
    assert metrics["passed"] == 4
    assert metrics["failed"] == 0
    assert metrics["f_tests"] == 0.0


def test_parse_dbt_run_results_with_failures():
    """Verify F_tests calculation when failures are present."""
    fake_json = {
        "results": [
            {"status": "pass", "unique_id": "test_1"},
            {"status": "fail", "unique_id": "test_2"},
            {"status": "pass", "unique_id": "test_3"},
            {"status": "error", "unique_id": "test_4"},
        ]
    }
    # 2 failed out of 4 = 0.50
    metrics = parse_dbt_run_results(run_results_data=fake_json)
    assert metrics["total"] == 4
    assert metrics["passed"] == 2
    assert metrics["failed"] == 2
    assert metrics["f_tests"] == 0.50


def test_parse_dbt_run_results_empty():
    """Verify F_tests defaults to 0.0 when test total is zero."""
    metrics = parse_dbt_run_results(run_results_data={"results": []})
    assert metrics["total"] == 0
    assert metrics["passed"] == 0
    assert metrics["failed"] == 0
    assert metrics["f_tests"] == 0.0


def test_parse_dbt_run_results_from_cli_logs():
    """Verify fallback parser extracts counts from CLI log outputs."""
    logs = [
        "12:00:00  1 of 4 START test unique_stg_customers_customer_id ... [RUN]",
        "12:00:01  1 of 4 PASS unique_stg_customers_customer_id ... [PASS in 0.05s]",
        "12:00:02  2 of 4 START test not_null_stg_customers_customer_phone ... [RUN]",
        "12:00:03  2 of 4 FAIL 1 not_null_stg_customers_customer_phone ... [FAIL in 0.06s]",
        "12:00:04  Done. PASS=3 WARN=0 ERROR=1 SKIP=0 TOTAL=4",
    ]
    metrics = parse_dbt_run_results(run_results_data=None, log_lines=logs)
    assert metrics["total"] == 4
    assert metrics["passed"] == 3
    assert metrics["failed"] == 1
    assert metrics["f_tests"] == 0.25


# ==============================================================================
# 2. Ephemeral Sandbox Container Lifecycle & Integration Tests
# ==============================================================================

@pytest.mark.skipif(not is_docker_available(), reason="Docker daemon is not reachable")
def test_sandbox_container_lifecycle():
    """Verify ephemeral container spins up on dynamic port and cleans up cleanly."""
    sandbox = SandboxContainer(image_name="postgres:15-alpine", db_name="test_shadow_db")

    with sandbox as active_sandbox:
        params = active_sandbox.connection_params
        assert params is not None
        assert params["host"] == "localhost"
        assert params["port"] > 1024
        assert params["dbname"] == "test_shadow_db"

        # Verify Postgres connection
        conn = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            dbname=params["dbname"],
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            assert cur.fetchone()[0] == 1
        conn.close()

    # Verify container has been removed after exit
    assert sandbox.container is None
    assert sandbox.connection_params is None


@pytest.mark.skipif(not is_docker_available(), reason="Docker daemon is not reachable")
def test_seed_schema_and_patch():
    """Verify init_sql and candidate patch view are applied in shadow database."""
    patched_sql = "SELECT id AS customer_id, customer_phone, email, created_at FROM raw.raw_customers"

    with SandboxContainer(image_name="postgres:15-alpine", db_name="test_seed_db") as sandbox:
        sandbox.seed_schema_and_patch(
            init_sql_path="testbed/init.sql",
            patched_sql=patched_sql,
            model_name="stg_customers",
        )

        params = sandbox.connection_params
        conn = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            dbname=params["dbname"],
        )
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM analytics.stg_customers;")
            count = cur.fetchone()[0]
            assert count == 10
        conn.close()


@pytest.mark.skipif(not is_docker_available(), reason="Docker daemon is not reachable")
def test_run_dbt_tests_live_verification():
    """Verify run_dbt_tests returns F_tests=0.0 on valid patch and F_tests>0 on assertion failure."""
    valid_patch = "SELECT id AS customer_id, customer_phone, email, created_at FROM raw.raw_customers"
    broken_patch = "SELECT NULL::INT AS customer_id, customer_phone, email, created_at FROM raw.raw_customers"

    with SandboxContainer(image_name="postgres:15-alpine", db_name="test_dbt_run_db") as sandbox:
        # Case 1: Valid patch -> All stg_customers tests pass (unique, not_null) -> F_tests == 0.0
        sandbox.seed_schema_and_patch(
            init_sql_path="testbed/init.sql",
            patched_sql=valid_patch,
            model_name="stg_customers",
        )
        valid_results = run_dbt_tests(
            target_config=sandbox.connection_params,
            project_dir="testbed",
            select="stg_customers",
        )
        assert valid_results["total"] >= 1
        assert valid_results["failed"] == 0
        assert valid_results["f_tests"] == 0.0

        # Case 2: Broken patch -> not_null/unique constraint violation -> F_tests > 0.0
        sandbox.seed_schema_and_patch(
            init_sql_path="testbed/init.sql",
            patched_sql=broken_patch,
            model_name="stg_customers",
        )
        broken_results = run_dbt_tests(
            target_config=sandbox.connection_params,
            project_dir="testbed",
            select="stg_customers",
        )
        assert broken_results["total"] >= 1
        assert broken_results["failed"] > 0
        assert broken_results["f_tests"] > 0.0
