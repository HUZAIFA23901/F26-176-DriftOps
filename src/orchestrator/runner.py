"""End-to-End Pipeline Orchestrator for DriftOps.

Coordinates the end-to-end verification and repair lifecycle:
1. AST Candidate Patch Synthesis
2. Confidence Score (C_AST) Calculation
3. AST Safety Rule Validation
4. Ephemeral Docker Sandbox Execution & dbt Testing
5. Deterministic Risk Gate Evaluation & Tier Routing

Project ID: F26-176-DriftOps
Module: src/orchestrator/runner.py
"""

import os
from typing import Any, Dict, Optional

import psycopg2

from src.compiler.ast_parser import repair_column_rename
from src.compiler.cast_calculator import (
    calculate_name_similarity,
    calculate_type_compatibility,
    calculate_catalog_invariant,
    compute_c_ast,
)
from src.compiler.rules import validate_ast_rules
from src.sandbox.container_manager import SandboxContainer
from src.sandbox.dbt_runner import run_dbt_tests
from src.gate.decision_engine import evaluate_patch_tier


def run_drift_repair_pipeline(
    broken_sql: str,
    old_column: str,
    new_column: str,
    model_name: str = "stg_customers",
    n_downstream: int = 2,
    failure_class: str = "column_rename",
    source_type: str = "VARCHAR",
    target_type: str = "VARCHAR",
    attnum_match: bool = True,
    project_dir: str = "testbed",
    init_sql_path: str = "testbed/init.sql",
    select: Optional[str] = None,
    mock_sandbox_result: Optional[Dict[str, Any]] = None,
    candidate_patch_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the complete DriftOps inline repair and verification gate pipeline.

    Args:
        broken_sql: Original dbt SQL model query that failed due to schema drift.
        old_column: Column name in the model that drifted or failed.
        new_column: Discovered replacement column in the upstream relation.
        model_name: Model identifier (default: "stg_customers").
        n_downstream: Count of downstream dependent models in the dbt DAG.
        failure_class: Schema failure diagnostic class (default: "column_rename").
        source_type: Original data type in model schema.
        target_type: Discovered upstream data type.
        attnum_match: Whether catalog ordinal match is preserved.
        project_dir: Path to the target dbt project directory.
        init_sql_path: Path to database bootstrap script.
        select: Optional dbt test selection filter.
        mock_sandbox_result: Optional pre-configured test results dictionary for testing.
        candidate_patch_override: Optional explicit candidate SQL to bypass auto-generation for testing.

    Returns:
        Structured result payload containing the repair status, scores, gate evaluation,
        and execution diagnostics.
    """
    # --------------------------------------------------------------------------
    # Step 1: AST Repair Generation
    # --------------------------------------------------------------------------
    patched_sql = candidate_patch_override or repair_column_rename(
        sql_query=broken_sql,
        old_column=old_column,
        new_column=new_column,
        dialect="postgres",
    )

    # --------------------------------------------------------------------------
    # Step 2: AST Confidence Computation (C_AST)
    # --------------------------------------------------------------------------
    s_name = calculate_name_similarity(old_column, new_column)
    t_compat = calculate_type_compatibility(source_type, target_type)
    i_catalog = calculate_catalog_invariant(attnum_match)
    c_ast = compute_c_ast(
        old_name=old_column,
        new_name=new_column,
        source_type=source_type,
        target_type=target_type,
        attnum_match=attnum_match,
    )

    # --------------------------------------------------------------------------
    # Step 3: AST Rule Validation
    # --------------------------------------------------------------------------
    ast_rule_valid = validate_ast_rules(
        original_sql=broken_sql,
        patched_sql=patched_sql,
        dialect="postgres",
    )

    # --------------------------------------------------------------------------
    # Step 4: Ephemeral Sandbox Execution & dbt Testing
    # --------------------------------------------------------------------------
    sandbox_port = 0
    execution_logs: list[str] = []

    if mock_sandbox_result is not None:
        # Used for deterministic unit testing without requiring Docker runtime
        f_tests = float(mock_sandbox_result.get("f_tests", 0.0))
        tests_total = int(mock_sandbox_result.get("total", 4))
        tests_passed = int(mock_sandbox_result.get("passed", 4))
        tests_failed = int(mock_sandbox_result.get("failed", 0))
        sandbox_port = int(mock_sandbox_result.get("port", 5432))
        execution_logs = mock_sandbox_result.get("logs", ["Mock sandbox run completed successfully."])
    else:
        try:
            with SandboxContainer(db_name="shadow_drift_db") as sandbox:
                sandbox_port = sandbox.host_port or 0

                # Seed shadow database and simulate upstream relation drift
                with psycopg2.connect(
                    host=sandbox.connection_params["host"],
                    port=sandbox.connection_params["port"],
                    user=sandbox.connection_params["user"],
                    password=sandbox.connection_params["password"],
                    dbname=sandbox.connection_params["dbname"],
                ) as conn:
                    with conn.cursor() as cur:
                        if init_sql_path and os.path.exists(init_sql_path):
                            with open(init_sql_path, "r", encoding="utf-8") as f:
                                cur.execute(f.read())

                        # Simulate upstream schema drift in source table
                        if old_column and new_column and old_column.lower() != new_column.lower():
                            try:
                                cur.execute(
                                    f"ALTER TABLE raw.raw_customers RENAME COLUMN {old_column} TO {new_column};"
                                )
                            except Exception:
                                pass

                        # Deploy candidate patch view
                        cur.execute("CREATE SCHEMA IF NOT EXISTS analytics;")
                        target_relation = model_name if "." in model_name else f"analytics.{model_name}"
                        cur.execute(f"DROP VIEW IF EXISTS {target_relation} CASCADE;")
                        cur.execute(f"DROP TABLE IF EXISTS {target_relation} CASCADE;")
                        clean_patch = patched_sql.strip().rstrip(";")
                        cur.execute(f"CREATE VIEW {target_relation} AS {clean_patch};")

                        # Build downstream mart table if testing full DAG
                        try:
                            cur.execute(
                                "CREATE TABLE IF NOT EXISTS analytics.customers AS "
                                "SELECT customer_id, customer_phone, email, created_at FROM analytics.stg_customers;"
                            )
                        except Exception:
                            pass

                    conn.commit()

                # Execute dbt tests in shadow database
                dbt_run = run_dbt_tests(
                    target_config=sandbox.connection_params,
                    project_dir=project_dir,
                    select=select or model_name,
                )
                f_tests = dbt_run["f_tests"]
                tests_total = dbt_run["total"]
                tests_passed = dbt_run["passed"]
                tests_failed = dbt_run["failed"]
                execution_logs = dbt_run["logs"]
        except Exception as exc:
            f_tests = 1.0
            tests_total = 0
            tests_passed = 0
            tests_failed = 1
            execution_logs = [f"Ephemeral sandbox execution encountered error: {exc}"]

    # --------------------------------------------------------------------------
    # Step 5: Risk Gating & Tier Decision
    # --------------------------------------------------------------------------
    decision = evaluate_patch_tier(
        f_tests=f_tests,
        c_ast=c_ast,
        ast_rule_valid=ast_rule_valid,
        n_downstream=n_downstream,
        failure_class=failure_class,
    )

    # --------------------------------------------------------------------------
    # Step 6: Return Comprehensive Summary Dictionary
    # --------------------------------------------------------------------------
    tier_str = decision.tier.value if hasattr(decision.tier, "value") else str(decision.tier)

    return {
        "broken_sql": broken_sql,
        "patched_sql": patched_sql,
        "old_column": old_column,
        "new_column": new_column,
        "c_ast": c_ast,
        "s_name": s_name,
        "t_compat": t_compat,
        "i_catalog": i_catalog,
        "ast_rule_valid": ast_rule_valid,
        "f_tests": f_tests,
        "tests_total": tests_total,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "d_impact": decision.d_impact,
        "risk_score": decision.risk_score,
        "tier": tier_str,
        "hard_gates_passed": decision.hard_gates_passed,
        "class_override": decision.class_override,
        "reasons": decision.reasons,
        "logs": execution_logs,
        "sandbox_port": sandbox_port,
    }
