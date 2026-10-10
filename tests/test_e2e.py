"""End-to-End Pipeline Integration Test Suite for DriftOps.

Tests:
1. End-to-end Column Rename repair pipeline routing to TIER_1_AUTO_FIX.
2. Escalation to TIER_3_FALLBACK when AST safety rules are violated.
3. Escalation to TIER_3_FALLBACK when empirical dbt test assertions fail.
4. CLI runlog formatting and output verification.

Project ID: F26-176-DriftOps
Module: tests/test_e2e.py
"""

import pytest
import docker

from src.orchestrator.runner import run_drift_repair_pipeline
from main import format_runlog


def is_docker_available() -> bool:
    """Check if Docker daemon is responsive."""
    try:
        client = docker.from_env()
        return bool(client.ping())
    except Exception:
        return False


# ==============================================================================
# 1. Live & Deterministic End-to-End Pipeline Tests
# ==============================================================================

@pytest.mark.skipif(not is_docker_available(), reason="Docker daemon is not reachable")
def test_e2e_valid_column_rename_tier_1_auto_fix():
    """Verify end-to-end Column Rename pipeline routes to TIER_1_AUTO_FIX."""
    broken_sql = (
        "SELECT\n"
        "    id AS customer_id,\n"
        "    customer_phone,\n"
        "    email,\n"
        "    created_at\n"
        "FROM raw.raw_customers"
    )

    result = run_drift_repair_pipeline(
        broken_sql=broken_sql,
        old_column="customer_phone",
        new_column="contact_number",
        model_name="stg_customers",
        n_downstream=1,
        failure_class="column_rename",
    )

    # 1. Verify AST transform
    assert "contact_number AS customer_phone" in result["patched_sql"]
    assert result["ast_rule_valid"] is True

    # 2. Verify Confidence and Impact Scores
    assert result["c_ast"] >= 0.80
    assert result["d_impact"] == 0.20

    # 3. Verify Sandbox & Test Outcomes
    assert result["f_tests"] == 0.0
    assert result["tests_total"] >= 1
    assert result["tests_failed"] == 0

    # 4. Verify Decision Routing
    assert result["hard_gates_passed"] is True
    assert result["tier"] == "TIER_1_AUTO_FIX"
    assert result["risk_score"] < 0.20


def test_e2e_ast_rules_violation_escalates_to_tier_3():
    """Verify that injected illegal SQL logic violates AST rules and escalates to TIER_3_FALLBACK."""
    broken_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    malicious_patch = (
        "SELECT id, contact_number AS customer_phone "
        "FROM raw.raw_customers "
        "JOIN raw.injected_table ON 1 = 1"
    )

    result = run_drift_repair_pipeline(
        broken_sql=broken_sql,
        old_column="customer_phone",
        new_column="contact_number",
        candidate_patch_override=malicious_patch,
        mock_sandbox_result={"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0},
    )

    assert result["ast_rule_valid"] is False
    assert result["hard_gates_passed"] is False
    assert result["tier"] == "TIER_3_FALLBACK"
    assert any("Hard Gate 3 Violation" in r for r in result["reasons"])


def test_e2e_dbt_test_failure_escalates_to_tier_3():
    """Verify that empirical test failures in shadow sandbox escalate to TIER_3_FALLBACK."""
    broken_sql = "SELECT id, customer_phone FROM raw.raw_customers"

    result = run_drift_repair_pipeline(
        broken_sql=broken_sql,
        old_column="customer_phone",
        new_column="contact_number",
        mock_sandbox_result={
            "f_tests": 0.25,
            "total": 4,
            "passed": 3,
            "failed": 1,
            "logs": ["1 test failed in shadow db"],
        },
    )

    assert result["ast_rule_valid"] is True
    assert result["f_tests"] == 0.25
    assert result["hard_gates_passed"] is False
    assert result["tier"] == "TIER_3_FALLBACK"
    assert any("Hard Gate 1 Violation" in r for r in result["reasons"])


def test_e2e_class_override_routes_to_tier_2():
    """Verify high-impact drift classes (e.g. column_drop) route to TIER_2_ASSIST."""
    broken_sql = "SELECT id, customer_phone FROM raw.raw_customers"

    result = run_drift_repair_pipeline(
        broken_sql=broken_sql,
        old_column="customer_phone",
        new_column="contact_number",
        failure_class="column_drop",
        mock_sandbox_result={"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0},
    )

    assert result["hard_gates_passed"] is True
    assert result["class_override"] is True
    assert result["tier"] == "TIER_2_ASSIST"
    assert any("override" in r.lower() for r in result["reasons"])


def test_e2e_cli_runlog_formatting():
    """Verify visual CLI runlog formatting matches required output specification."""
    fake_result = {
        "broken_sql": "SELECT customer_phone FROM raw.raw_customers",
        "patched_sql": "SELECT contact_number AS customer_phone FROM raw.raw_customers",
        "old_column": "customer_phone",
        "new_column": "contact_number",
        "c_ast": 0.92,
        "s_name": 0.80,
        "t_compat": 1.0,
        "i_catalog": 1.0,
        "ast_rule_valid": True,
        "f_tests": 0.0,
        "tests_total": 4,
        "tests_passed": 4,
        "tests_failed": 0,
        "d_impact": 0.40,
        "risk_score": 0.24,
        "tier": "TIER_1_AUTO_FIX",
        "hard_gates_passed": True,
        "class_override": False,
        "reasons": [],
        "logs": [],
        "sandbox_port": 5433,
    }

    log_output = format_runlog(fake_result)
    assert "[1] DRIFT DETECTED" in log_output
    assert "[2] AST PATCH GENERATED" in log_output
    assert "[3] C_AST SCORE" in log_output
    assert "[4] AST RULES VALID" in log_output
    assert "[5] SANDBOX EXECUTION" in log_output
    assert "[6] HARD SAFETY GATES" in log_output
    assert "[7] RISK SCORE (R)" in log_output
    assert "[8] SYSTEM DECISION" in log_output
    assert "TIER 1 (AUTO-FIX)" in log_output
