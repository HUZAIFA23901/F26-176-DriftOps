"""End-to-End Pipeline Orchestrator for DriftOps.

Coordinates the end-to-end verification and repair lifecycle across all drift classes:
1. AST Candidate Patch Synthesis (Class 1-5 via Unified Synthesizer)
2. Confidence Score (C_AST) Calculation
3. AST Safety Rule Validation
4. Ephemeral Docker Sandbox Execution & dbt Testing
5. Deterministic Risk Gate Evaluation & Tier Routing
6. Proactive Pre-flight Schema Drift Detection & Multi-Model Repair

Project ID: F26-176-DriftOps
Module: src/orchestrator/runner.py
"""

import os
from typing import Any, Dict, List, Optional

import psycopg2

from src.compiler.ast_parser import repair_column_rename
from src.compiler.synthesizer import synthesize_repair_patch
from src.compiler.cast_calculator import (
    calculate_name_similarity,
    calculate_type_compatibility,
    calculate_catalog_invariant,
    compute_c_ast,
)
from src.compiler.rules import validate_ast_rules
from src.sandbox.container_manager import SandboxContainer
from src.sandbox.dbt_runner import run_dbt_tests
from src.gate.decision_engine import evaluate_patch_tier, ExecutionTier
from src.gate.hard_gates import check_class_override
from src.gate.risk_calculator import calculate_d_impact
from src.detector.preflight import detect_schema_drift, TableSchema, DriftEvent


def run_drift_repair_pipeline(
    broken_sql: str,
    old_column: Optional[str] = None,
    new_column: Optional[str] = None,
    model_name: str = "stg_customers",
    n_downstream: int = 1,
    failure_class: str = "column_rename",
    payload: Optional[Dict[str, Any]] = None,
    source_type: str = "VARCHAR",
    target_type: str = "VARCHAR",
    attnum_match: bool = True,
    project_dir: str = "testbed",
    init_sql_path: str = "testbed/init.sql",
    select: Optional[str] = None,
    mock_sandbox_result: Optional[Dict[str, Any]] = None,
    candidate_patch_override: Optional[str] = None,
    dialect: str = "postgres",
) -> Dict[str, Any]:
    """Execute the complete DriftOps inline repair and verification gate pipeline.

    Args:
        broken_sql: Original dbt SQL model query that failed due to schema drift.
        old_column: Column name in the model that drifted or failed.
        new_column: Discovered replacement column in the upstream relation.
        model_name: Model identifier (default: "stg_customers").
        n_downstream: Count of downstream dependent models in the dbt DAG.
        failure_class: Schema failure diagnostic class (default: "column_rename").
        payload: Parameters required by the underlying repair synthesizer.
        source_type: Original data type in model schema.
        target_type: Discovered upstream data type.
        attnum_match: Whether catalog ordinal match is preserved.
        project_dir: Path to the target dbt project directory.
        init_sql_path: Path to database bootstrap script.
        select: Optional dbt test selection filter.
        mock_sandbox_result: Optional pre-configured test results dictionary for testing.
        candidate_patch_override: Optional explicit candidate SQL to bypass auto-generation for testing.
        dialect: SQL dialect for AST parsing and generation (default: "postgres").

    Returns:
        Structured result payload containing the repair status, scores, gate evaluation,
        and execution diagnostics.
    """
    norm_class = (failure_class or "column_rename").strip().lower()

    # --------------------------------------------------------------------------
    # Fast-Path for Unrepairable / Non-AST Classes (Class 6 & 7 & Unknown)
    # --------------------------------------------------------------------------
    unrepairable_classes = (
        "grain_violation",
        "class7_grain_violation",
        "semantic_shift",
        "class6_semantic_shift",
        "unknown_drift",
    )
    if norm_class in unrepairable_classes:
        d_impact = calculate_d_impact(n_downstream)
        return {
            "broken_sql": broken_sql,
            "patched_sql": broken_sql,
            "failure_class": failure_class,
            "payload": payload or {},
            "old_column": old_column or "",
            "new_column": new_column or "",
            "c_ast": 0.0,
            "s_name": 0.0,
            "t_compat": 0.0,
            "i_catalog": 0.0,
            "ast_rule_valid": False,
            "f_tests": 1.0,
            "tests_total": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "d_impact": d_impact,
            "risk_score": 1.0,
            "tier": ExecutionTier.TIER_3_FALLBACK.value,
            "hard_gates_passed": False,
            "class_override": False,
            "reasons": [
                f"Deterministic AST repair cannot resolve failure class '{failure_class}'; human engineering investigation required (Tier 3 Fallback)."
            ],
            "logs": [f"Unrepairable schema drift class encountered: {failure_class}"],
            "sandbox_port": 0,
        }

    # --------------------------------------------------------------------------
    # Step 1: Normalization & Payload Assembly
    # --------------------------------------------------------------------------
    effective_payload = dict(payload) if payload else {}

    if old_column:
        effective_payload.setdefault("old_column", old_column)
        effective_payload.setdefault("old_col", old_column)
    elif "old_col" in effective_payload:
        effective_payload.setdefault("old_column", effective_payload["old_col"])
    elif "old_column" in effective_payload:
        effective_payload.setdefault("old_col", effective_payload["old_column"])

    if new_column:
        effective_payload.setdefault("new_column", new_column)
        effective_payload.setdefault("new_col", new_column)
    elif "new_col" in effective_payload:
        effective_payload.setdefault("new_column", effective_payload["new_col"])
    elif "new_column" in effective_payload:
        effective_payload.setdefault("new_col", effective_payload["new_column"])

    if "source_type" not in effective_payload:
        effective_payload["source_type"] = source_type
    if "target_type" not in effective_payload:
        effective_payload["target_type"] = target_type

    # Extract normalized identifiers
    old_col_val = (
        effective_payload.get("old_column")
        or effective_payload.get("old_col")
        or effective_payload.get("column_name")
        or old_column
        or ""
    )
    new_col_val = (
        effective_payload.get("new_column")
        or effective_payload.get("new_col")
        or new_column
        or ""
    )
    src_t = effective_payload.get("source_type", source_type)
    tgt_t = effective_payload.get("target_type", target_type)

    # --------------------------------------------------------------------------
    # Step 2: AST Candidate Patch Synthesis
    # --------------------------------------------------------------------------
    if candidate_patch_override is not None:
        patched_sql = candidate_patch_override
        requires_tier2_override = check_class_override(failure_class)
    else:
        patched_sql, requires_tier2_override = synthesize_repair_patch(
            broken_sql=broken_sql,
            failure_class=failure_class,
            payload=effective_payload,
            dialect=dialect,
        )

    # --------------------------------------------------------------------------
    # Step 3: AST Confidence Computation (C_AST)
    # --------------------------------------------------------------------------
    if norm_class in ("column_rename", "class1_rename", "class_1", "rename"):
        s_name = calculate_name_similarity(old_col_val, new_col_val)
        t_compat = calculate_type_compatibility(src_t, tgt_t)
        i_catalog = calculate_catalog_invariant(attnum_match)
        c_ast = compute_c_ast(
            old_name=old_col_val,
            new_name=new_col_val,
            source_type=src_t,
            target_type=tgt_t,
            attnum_match=attnum_match,
        )
    elif norm_class in ("type_shift", "class2_type_shift", "type_narrowing", "type_widening"):
        s_name = 1.0
        t_compat = calculate_type_compatibility(src_t, tgt_t)
        i_catalog = calculate_catalog_invariant(attnum_match)
        c_ast = round(0.4 * s_name + 0.4 * t_compat + 0.2 * i_catalog, 4)
    elif norm_class in ("nullability", "nullability_shift", "class4_nullability"):
        s_name = 1.0
        t_compat = 1.0
        i_catalog = 1.0
        c_ast = float(effective_payload.get("confidence", 1.0))
    elif norm_class in ("column_drop_add", "class3_column_drop_add", "column_drop", "column_add"):
        s_name = 1.0
        t_compat = 1.0
        i_catalog = 1.0
        c_ast = float(effective_payload.get("confidence", 1.0))
    elif norm_class in ("model_rename", "class5_model_rename"):
        old_m = effective_payload.get("old_model_name") or effective_payload.get("old_table_name") or ""
        new_m = effective_payload.get("new_model_name") or effective_payload.get("new_table_name") or ""
        s_name = calculate_name_similarity(old_m, new_m) if old_m and new_m else 1.0
        t_compat = 1.0
        i_catalog = 1.0
        c_ast = float(effective_payload.get("confidence", round(0.4 * s_name + 0.6, 4)))
    else:
        s_name = 1.0
        t_compat = 1.0
        i_catalog = 1.0
        c_ast = float(effective_payload.get("confidence", 0.85))

    # --------------------------------------------------------------------------
    # Step 4: AST Rule Validation
    # --------------------------------------------------------------------------
    ast_rule_valid = validate_ast_rules(
        original_sql=broken_sql,
        patched_sql=patched_sql,
        dialect=dialect,
        failure_class=failure_class,
    )

    # --------------------------------------------------------------------------
    # Step 5: Ephemeral Sandbox Execution & dbt Testing
    # --------------------------------------------------------------------------
    sandbox_port = 0
    execution_logs: List[str] = []

    if mock_sandbox_result is not None:
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

                        # Simulate upstream schema drift if column rename
                        if old_col_val and new_col_val and old_col_val.lower() != new_col_val.lower():
                            try:
                                cur.execute(
                                    f"ALTER TABLE raw.raw_customers RENAME COLUMN {old_col_val} TO {new_col_val};"
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

                        # Build downstream mart table if testing DAG
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
    # Step 6: Risk Gating & Tier Decision
    # --------------------------------------------------------------------------
    decision = evaluate_patch_tier(
        f_tests=f_tests,
        c_ast=c_ast,
        ast_rule_valid=ast_rule_valid,
        n_downstream=n_downstream,
        failure_class=failure_class,
    )

    class_override_active = decision.class_override or requires_tier2_override
    tier_str = decision.tier.value if hasattr(decision.tier, "value") else str(decision.tier)
    reasons = list(decision.reasons)

    # If hard gates passed and tier2 override is triggered, enforce TIER_2_ASSIST
    if decision.hard_gates_passed and class_override_active:
        tier_str = ExecutionTier.TIER_2_ASSIST.value
        if not decision.class_override:
            reasons.append(
                f"Mandatory Tier 2 Assist override triggered by failure class '{failure_class}' structural constraints"
            )

    return {
        "broken_sql": broken_sql,
        "patched_sql": patched_sql,
        "failure_class": failure_class,
        "payload": effective_payload,
        "old_column": old_col_val,
        "new_column": new_col_val,
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
        "class_override": class_override_active,
        "reasons": reasons,
        "logs": execution_logs,
        "sandbox_port": sandbox_port,
    }


def run_preflight_pipeline(
    baseline_manifest: Dict[str, Any],
    live_schema: Dict[str, Any],
    model_sql_map: Dict[str, str],
    n_downstream: int = 1,
    mock_sandbox_result: Optional[Dict[str, Any]] = None,
    project_dir: str = "testbed",
    init_sql_path: str = "testbed/init.sql",
) -> List[Dict[str, Any]]:
    """Proactive pre-flight drift detection and repair pipeline.

    Compares baseline manifest against live database schema snapshot,
    detects drift events across all supported classes, maps them to dbt models,
    and executes the repair and verification pipeline.

    Args:
        baseline_manifest: Stored baseline TableSchema manifest dict.
        live_schema: Live snapshot TableSchema dict.
        model_sql_map: Mapping of model names (or table identifiers) to broken/unrepaired SQL queries.
        n_downstream: Impacted downstream count for risk score calculation.
        mock_sandbox_result: Optional mock sandbox test results for deterministic testing.
        project_dir: Path to dbt project directory.
        init_sql_path: Path to database bootstrap script.

    Returns:
        List of repair report dictionaries for all detected drift events.
    """
    # Normalize baseline manifest to TableSchema objects
    norm_baseline: Dict[str, TableSchema] = {}
    for tbl, data in baseline_manifest.items():
        if isinstance(data, TableSchema):
            norm_baseline[tbl] = data
        elif isinstance(data, dict):
            norm_baseline[tbl] = TableSchema.from_dict(data)

    # Normalize live schema to TableSchema objects
    norm_live: Dict[str, TableSchema] = {}
    for tbl, data in live_schema.items():
        if isinstance(data, TableSchema):
            norm_live[tbl] = data
        elif isinstance(data, dict):
            norm_live[tbl] = TableSchema.from_dict(data)

    # Detect schema drift discrepancies
    drift_events: List[DriftEvent] = detect_schema_drift(
        baseline_manifest=norm_baseline,
        live_schema=norm_live,
    )

    reports: List[Dict[str, Any]] = []

    for event in drift_events:
        matched_model_name: Optional[str] = None
        broken_sql: Optional[str] = None

        # Strategy 1: Exact match on event.table_name
        if event.table_name in model_sql_map:
            matched_model_name = event.table_name
            broken_sql = model_sql_map[event.table_name]
        else:
            # Strategy 2: Simple table name without schema prefix
            simple_table = event.table_name.split(".")[-1]
            if simple_table in model_sql_map:
                matched_model_name = simple_table
                broken_sql = model_sql_map[simple_table]
            else:
                # Strategy 3: Check if model name or query references the table
                for m_name, sql in model_sql_map.items():
                    if (
                        simple_table in m_name
                        or m_name in event.table_name
                        or simple_table in sql
                    ):
                        matched_model_name = m_name
                        broken_sql = sql
                        break

        # Fallback to single model if only one model is registered
        if broken_sql is None and len(model_sql_map) == 1:
            matched_model_name = next(iter(model_sql_map))
            broken_sql = model_sql_map[matched_model_name]

        if broken_sql is None:
            reports.append({
                "table_name": event.table_name,
                "failure_class": event.failure_class,
                "confidence": event.confidence,
                "payload": event.payload,
                "model_name": matched_model_name or event.table_name,
                "tier": ExecutionTier.TIER_3_FALLBACK.value,
                "risk_score": 1.0,
                "hard_gates_passed": False,
                "reasons": [f"No matching dbt model SQL found for drifted table '{event.table_name}'"],
                "pipeline_result": {},
            })
            continue

        # Execute repair pipeline
        pipeline_result = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name=matched_model_name or "stg_model",
            n_downstream=n_downstream,
            failure_class=event.failure_class,
            payload=event.payload,
            mock_sandbox_result=mock_sandbox_result,
            project_dir=project_dir,
            init_sql_path=init_sql_path,
        )

        report_entry = {
            "table_name": event.table_name,
            "failure_class": event.failure_class,
            "confidence": event.confidence,
            "payload": event.payload,
            "model_name": matched_model_name or "stg_model",
            "pipeline_result": pipeline_result,
            "tier": pipeline_result.get("tier"),
            "patched_sql": pipeline_result.get("patched_sql"),
            "risk_score": pipeline_result.get("risk_score"),
            "hard_gates_passed": pipeline_result.get("hard_gates_passed"),
            "reasons": pipeline_result.get("reasons", []),
        }
        reports.append(report_entry)

    return reports
