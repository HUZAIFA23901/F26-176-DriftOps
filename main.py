"""DriftOps CLI Demonstrator and Main Entry Point.

Supports end-to-end demonstration of the full 7-Class Failure Taxonomy,
Dual-Mode Detectors (Proactive Pre-flight & Reactive Webhook), and automated testing.

Project ID: F26-176-DriftOps
Module: main.py
"""

import argparse
import subprocess
import sys
from typing import Any, Dict, List, Optional

# ANSI color escape codes
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def is_docker_available() -> bool:
    """Check if Docker daemon is running and reachable."""
    try:
        import docker
        client = docker.from_env()
        return bool(client.ping())
    except Exception:
        return False


def describe_drift(res: Dict[str, Any]) -> str:
    """Generate human-readable drift description based on failure class and payload."""
    failure_class = res.get("failure_class", "")
    payload = res.get("payload") or {}

    old_col = res.get("old_column") or payload.get("old_col") or payload.get("old_column") or ""
    new_col = res.get("new_column") or payload.get("new_col") or payload.get("new_column") or ""
    col_name = payload.get("column_name") or payload.get("col") or old_col or ""

    norm_class = failure_class.strip().lower() if failure_class else ""

    if norm_class in ("column_rename", "class1_rename", "class_1", "class1", "rename"):
        return f"Class 1 (column_rename): '{old_col}' -> '{new_col}'"
    elif norm_class in ("type_shift", "class2_type_shift", "class_2", "class2", "type_narrowing", "type_widening"):
        src_t = payload.get("source_type") or res.get("source_type", "VARCHAR")
        tgt_t = payload.get("target_type") or res.get("target_type", "VARCHAR")
        is_narrowing = res.get("class_override", False) or "narrow" in norm_class
        direction = "Narrowing" if is_narrowing else "Widening"
        return f"Class 2 (type_shift): '{col_name}' ({src_t} -> {tgt_t}) [{direction}]"
    elif norm_class in (
        "column_drop_add",
        "class3_column_drop_add",
        "class_3",
        "class3",
        "column_drop",
        "column_add",
        "drop_column",
        "add_column",
    ):
        dropped = payload.get("dropped_columns") or []
        added = payload.get("added_columns") or []
        parts = []
        if dropped:
            parts.append(f"Dropped: {dropped}")
        if added:
            parts.append(f"Added: {added}")
        detail = ", ".join(parts) if parts else (f"Dropped '{old_col}'" if old_col else "Column set mutated")
        return f"Class 3 (column_drop_add): {detail}"
    elif norm_class in ("nullability", "nullability_shift", "class4_nullability", "class_4", "class4", "nullability_change"):
        default_val = payload.get("default_value", "''")
        return f"Class 4 (nullability): '{col_name}' changed to NOT NULL (default: {default_val})"
    elif norm_class in ("model_rename", "class5_model_rename", "class_5", "class5", "table_rename"):
        old_m = payload.get("old_model_name") or payload.get("old_table_name") or ""
        new_m = payload.get("new_model_name") or payload.get("new_table_name") or ""
        return f"Class 5 (model_rename): '{old_m}' -> '{new_m}'"
    elif norm_class in ("semantic_shift", "class6_semantic_shift", "class_6", "class6"):
        return f"Class 6 (semantic_shift): Business Logic / WHERE Filter Alteration"
    elif norm_class in ("grain_violation", "class7_grain_violation", "class_7", "class7"):
        reason = payload.get("reason", "Duplicate rows / PK assertion failed")
        short_reason = reason.split("\n")[0][:65]
        return f"Class 7 (grain_violation): {short_reason}"
    else:
        if old_col and new_col:
            return f"Column Rename ('{old_col}' -> '{new_col}')"
        return failure_class or "Unknown Schema Drift Discrepancy"


def format_runlog(res: Dict[str, Any]) -> str:
    """Format structured pipeline results into the visual runlog specification."""
    c_ast = res.get("c_ast", 0.0)
    s_name = res.get("s_name", 0.0)
    t_compat = res.get("t_compat", 0.0)
    i_catalog = res.get("i_catalog", 0.0)

    ast_rules_str = f"{GREEN}PASS{RESET}" if res.get("ast_rule_valid") else f"{RED}FAIL{RESET}"

    port = res.get("sandbox_port") or 5433
    total = res.get("tests_total", 0)
    passed = res.get("tests_passed", 0)
    f_tests = res.get("f_tests", 0.0)
    sandbox_str = f"Port {port} | Tests Total: {total} | Passed: {passed} | F_tests: {f_tests:.2f}"

    if res.get("hard_gates_passed"):
        gates_str = f"{GREEN}ALL PASSED{RESET}"
    else:
        reasons_list = res.get("reasons", ["Failed safety invariant"])
        gates_str = f"{RED}FAILED: {'; '.join(reasons_list)}{RESET}"

    d_impact = res.get("d_impact", 0.20)
    one_minus_c = round(1.0 - c_ast, 4)
    risk_score = res.get("risk_score", 0.0)

    tier = res.get("tier", "TIER_3_FALLBACK")
    if tier == "TIER_1_AUTO_FIX":
        decision_str = f"{GREEN}TIER 1 (AUTO-FIX) -> Auto-Merged to Staging Branch{RESET}"
    elif tier == "TIER_2_ASSIST":
        decision_str = f"{YELLOW}TIER 2 (ASSIST) -> Candidate Patch PR Emitted for Human Review{RESET}"
    else:
        decision_str = f"{RED}TIER 3 (FALLBACK) -> Gated Block: Escalated to On-Call Data Engineer{RESET}"

    drift_desc = describe_drift(res)
    patched_sql = res.get("patched_sql", "").strip()

    runlog = f"""===============================================================
                   DRIFTOPS ENGINE RUNLOG                       
===============================================================
[1] DRIFT DETECTED     : {drift_desc}
[2] AST PATCH GENERATED: {patched_sql}
[3] C_AST SCORE        : {c_ast:.2f} (S_name={s_name:.2f}, T_compat={t_compat:.2f}, I_catalog={i_catalog:.2f})
[4] AST RULES VALID    : {ast_rules_str}
[5] SANDBOX EXECUTION  : {sandbox_str}
[6] HARD SAFETY GATES  : {gates_str}
[7] RISK SCORE (R)     : {risk_score:.2f} (D_impact={d_impact:.2f}, 1-C_AST={one_minus_c:.2f})
[8] SYSTEM DECISION    : {decision_str}
==============================================================="""
    return runlog


def run_class_demo(class_num: int, use_mock_sandbox: bool = False) -> Dict[str, Any]:
    """Execute scenario demonstration for a specific failure class (1 through 7)."""
    from src.orchestrator.runner import run_drift_repair_pipeline

    effective_mock = use_mock_sandbox or not is_docker_available()
    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0, "port": 5433} if effective_mock else None

    if effective_mock and not use_mock_sandbox:
        print(f"{YELLOW}[NOTICE] Docker daemon unreachable. Running demonstration in mock sandbox mode.{RESET}")

    if class_num == 1:
        # Class 1: Column Rename
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 1] Column Rename (customer_phone -> contact_number)...{RESET}\n")
        broken_sql = (
            "SELECT\n"
            "    id AS customer_id,\n"
            "    customer_phone,\n"
            "    email,\n"
            "    created_at\n"
            "FROM raw.raw_customers"
        )
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            old_column="customer_phone",
            new_column="contact_number",
            model_name="stg_customers",
            n_downstream=1,
            failure_class="column_rename",
            payload={"old_col": "customer_phone", "new_col": "contact_number"},
            mock_sandbox_result=mock_sandbox,
        )
    elif class_num == 2:
        # Class 2: Narrowing Type Shift
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 2] Narrowing Type Shift (loyalty_tier: VARCHAR -> INTEGER)...{RESET}\n")
        broken_sql = "SELECT customer_id, loyalty_tier, email FROM raw.raw_customers"
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name="stg_customers",
            n_downstream=1,
            failure_class="type_shift",
            source_type="VARCHAR",
            target_type="INTEGER",
            payload={"column_name": "loyalty_tier", "source_type": "VARCHAR", "target_type": "INTEGER"},
            mock_sandbox_result=mock_sandbox,
        )
    elif class_num == 3:
        # Class 3: Column Drop & Add
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 3] Column Drop (legacy_fax_number dropped upstream)...{RESET}\n")
        broken_sql = "SELECT id AS customer_id, legacy_fax_number, email FROM raw.raw_customers"
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name="stg_customers",
            n_downstream=1,
            failure_class="column_drop_add",
            payload={"dropped_columns": ["legacy_fax_number"], "added_columns": []},
            mock_sandbox_result=mock_sandbox,
        )
    elif class_num == 4:
        # Class 4: Nullability Shift
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 4] Nullability Constraint Shift (email became NOT NULL)...{RESET}\n")
        broken_sql = "SELECT id AS customer_id, email FROM raw.raw_customers"
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name="stg_customers",
            n_downstream=1,
            failure_class="nullability",
            payload={"column_name": "email", "default_value": "''"},
            mock_sandbox_result=mock_sandbox,
        )
    elif class_num == 5:
        # Class 5: Model / Table Identifier Rename
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 5] Model / Table Rename (raw.raw_customers -> raw.customers_v2)...{RESET}\n")
        broken_sql = "SELECT id AS customer_id, email, status FROM raw.raw_customers"
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name="stg_customers",
            n_downstream=1,
            failure_class="model_rename",
            payload={
                "old_model_name": "raw.raw_customers",
                "new_model_name": "raw.customers_v2",
                "old_table_name": "raw_customers",
                "new_table_name": "customers_v2",
            },
            mock_sandbox_result=mock_sandbox,
        )
    elif class_num == 6:
        # Class 6: Semantic / Business Logic Shift
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 6] Semantic Shift (Altered WHERE Clause Filter)...{RESET}\n")
        broken_sql = "SELECT customer_id, email FROM raw.raw_customers WHERE status = 'ACTIVE'"
        # Simulate unauthorized logic alteration
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name="stg_customers",
            n_downstream=1,
            failure_class="semantic_shift",
            candidate_patch_override="SELECT customer_id, email FROM raw.raw_customers WHERE status = 'VERIFIED'",
            mock_sandbox_result=mock_sandbox,
        )
    elif class_num == 7:
        # Class 7: Grain / PK Uniqueness Assertion Violation
        print(f"\n{BOLD}{CYAN}>>> [Scenario: Class 7] Grain Violation (Duplicate Rows / PK Unique Test Failure)...{RESET}\n")
        broken_sql = "SELECT order_id, customer_id FROM raw.raw_orders"
        res = run_drift_repair_pipeline(
            broken_sql=broken_sql,
            model_name="stg_orders",
            n_downstream=2,
            failure_class="grain_violation",
            payload={"reason": "duplicate key value violates unique constraint 'orders_pkey'"},
            mock_sandbox_result={"f_tests": 0.50, "total": 4, "passed": 2, "failed": 2, "port": 5433},
        )
    else:
        raise ValueError(f"Invalid failure class: {class_num}. Supported range is 1 to 7.")

    print(format_runlog(res))
    return res


def run_all_classes_demo(use_mock_sandbox: bool = False) -> int:
    """Execute all 7 failure class scenarios sequentially with summary audit table."""
    print(f"\n{BOLD}{CYAN}===============================================================")
    print(f"        DRIFTOPS 7-CLASS TAXONOMY SEQUENTIAL DEMONSTRATION      ")
    print(f"==============================================================={RESET}\n")

    summary_records: List[Dict[str, Any]] = []

    for cls in range(1, 8):
        res = run_class_demo(cls, use_mock_sandbox=use_mock_sandbox)
        summary_records.append({
            "class": f"Class {cls}",
            "desc": describe_drift(res),
            "tier": res.get("tier", "TIER_3_FALLBACK"),
            "risk": res.get("risk_score", 1.0),
            "gates": "PASS" if res.get("hard_gates_passed") else "FAIL",
        })
        print()

    # Print summary table
    print(f"{BOLD}{CYAN}==============================================================================================")
    print(f"                                7-CLASS TAXONOMY EXECUTION SUMMARY                             ")
    print(f"=============================================================================================={RESET}")
    print(f"{BOLD}{'Class':<10} {'Drift Description':<48} {'Risk (R)':<10} {'Gates':<8} {'Assigned Execution Tier'}{RESET}")
    print("-" * 94)

    for r in summary_records:
        tier_str = r["tier"]
        if tier_str == "TIER_1_AUTO_FIX":
            tier_colored = f"{GREEN}{tier_str}{RESET}"
        elif tier_str == "TIER_2_ASSIST":
            tier_colored = f"{YELLOW}{tier_str}{RESET}"
        else:
            tier_colored = f"{RED}{tier_str}{RESET}"

        gates_colored = f"{GREEN}PASS{RESET}" if r["gates"] == "PASS" else f"{RED}FAIL{RESET}"
        short_desc = (r["desc"][:45] + "...") if len(r["desc"]) > 48 else r["desc"]
        print(f"{r['class']:<10} {short_desc:<48} {r['risk']:<10.2f} {gates_colored:<17} {tier_colored}")

    print(f"{BOLD}{CYAN}=============================================================================================={RESET}\n")
    return 0


def run_preflight_demo(use_mock_sandbox: bool = False) -> int:
    """Execute Proactive Pre-flight Schema Snapshot & Diff Engine demo."""
    from src.detector.preflight import ColumnSchema, TableSchema
    from src.orchestrator.runner import run_preflight_pipeline

    print(f"\n{BOLD}{CYAN}>>> Initiating DriftOps Proactive Pre-Flight Engine Demo...{RESET}")
    print(f"{CYAN}Comparing baseline JSON manifests against live database metadata snapshot...{RESET}\n")

    baseline = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "customer_phone": ColumnSchema("customer_phone", "varchar"),
                "email": ColumnSchema("email", "varchar", is_nullable=True),
            },
        )
    }

    live = {
        "raw.raw_customers": TableSchema(
            table_name="raw.raw_customers",
            columns={
                "id": ColumnSchema("id", "integer"),
                "cust_phone": ColumnSchema("cust_phone", "varchar"),
                "email": ColumnSchema("email", "varchar", is_nullable=False),
            },
        )
    }

    model_sql_map = {
        "stg_customers": (
            "SELECT\n"
            "    id AS customer_id,\n"
            "    customer_phone,\n"
            "    email\n"
            "FROM raw.raw_customers"
        )
    }

    effective_mock = use_mock_sandbox or not is_docker_available()
    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0, "port": 5433} if effective_mock else None

    if effective_mock and not use_mock_sandbox:
        print(f"{YELLOW}[NOTICE] Docker daemon unreachable. Running shadow sandbox verification in mock mode.{RESET}\n")

    reports = run_preflight_pipeline(
        baseline_manifest=baseline,
        live_schema=live,
        model_sql_map=model_sql_map,
        mock_sandbox_result=mock_sandbox,
    )

    print(f"{BOLD}{GREEN}Found {len(reports)} schema drift discrepancies before pipeline execution:{RESET}\n")
    for idx, report in enumerate(reports, 1):
        print(f"{BOLD}--- Discrepancy #{idx}: Table '{report['table_name']}' ({report['failure_class']}) ---{RESET}")
        pipeline_res = report.get("pipeline_result") or {}
        pipeline_res.setdefault("failure_class", report["failure_class"])
        pipeline_res.setdefault("payload", report["payload"])
        print(format_runlog(pipeline_res))
        print()

    return 0


def run_webhook_demo(use_mock_sandbox: bool = False) -> int:
    """Execute Reactive Webhook Interceptor demo."""
    from fastapi.testclient import TestClient
    from src.detector.webhook_server import app

    print(f"\n{BOLD}{CYAN}>>> Initiating DriftOps Reactive Webhook Interceptor Demo...{RESET}")
    print(f"{CYAN}Simulating incoming orchestration failure alert (Airflow/Prefect/CI) on POST /api/v1/webhook/failure...{RESET}\n")

    client = TestClient(app)

    effective_mock = use_mock_sandbox or not is_docker_available()
    mock_sandbox = {"f_tests": 0.0, "total": 4, "passed": 4, "failed": 0, "port": 5433} if effective_mock else None

    if effective_mock and not use_mock_sandbox:
        print(f"{YELLOW}[NOTICE] Docker daemon unreachable. Simulating shadow sandbox verification in mock mode.{RESET}\n")

    # Payload: Column Rename with hint
    req_body = {
        "pipeline_id": "dag_nightly_etl_stg_customers_20261122",
        "model_name": "stg_customers",
        "failed_sql": "SELECT id, customer_phone FROM raw.raw_customers",
        "error_message": (
            'column "customer_phone" does not exist\n'
            'HINT:  Perhaps you meant to reference the column "contact_number".'
        ),
        "source_table": "raw.raw_customers",
        "downstream_count": 1,
        "metadata": {"mock_sandbox_result": mock_sandbox} if mock_sandbox else {},
    }

    print(f"{BOLD}Simulated Error Message Ingested:{RESET}")
    print(f"{YELLOW}{req_body['error_message']}{RESET}\n")

    response = client.post("/api/v1/webhook/failure", json=req_body)
    data = response.json()

    print(f"{BOLD}Webhook Ingestion Status : {GREEN}{data.get('status', 'processed').upper()}{RESET}")
    print(f"{BOLD}Classified Failure Class : {CYAN}{data.get('detected_class')}{RESET}")
    print(f"{BOLD}Extracted Payload Match  : {data.get('extracted_payload')}{RESET}")

    summary = data.get("decision_summary", {})
    tier = summary.get("tier", "TIER_3_FALLBACK")
    if tier == "TIER_1_AUTO_FIX":
        tier_color = GREEN
    elif tier == "TIER_2_ASSIST":
        tier_color = YELLOW
    else:
        tier_color = RED

    print(f"{BOLD}Synthesized Patch SQL    : {CYAN}{summary.get('patched_sql')}{RESET}")
    print(f"{BOLD}Assigned Gating Tier     : {tier_color}{tier}{RESET}")
    print(f"{BOLD}Hard Safety Gates Passed : {GREEN if summary.get('hard_gates_passed') else RED}{summary.get('hard_gates_passed')}{RESET}")
    print(f"{BOLD}Calculated Risk Score (R): {summary.get('risk_score', 0.0):.2f}\n")

    return 0


def run_pytest_suite() -> int:
    """Execute the project pytest test suite."""
    print(f"\n{BOLD}{CYAN}>>> Running Full DriftOps Verification Test Suite (pytest)...{RESET}\n")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-v"])
    return proc.returncode


def main() -> None:
    """Entry point for DriftOps CLI."""
    parser = argparse.ArgumentParser(
        description="DriftOps: Autonomous Schema Drift Repair and Verification Gate for dbt-core Pipelines"
    )
    parser.add_argument(
        "--demo-class1",
        action="store_true",
        help="Execute Class 1 Column Rename (customer_phone -> contact_number) end-to-end repair (backward compatible)",
    )
    parser.add_argument(
        "--demo-class",
        type=int,
        choices=range(1, 8),
        metavar="<1-7>",
        help="Execute scenario demonstration for specific failure class (1 through 7)",
    )
    parser.add_argument(
        "--demo-all",
        action="store_true",
        help="Execute all 7 failure class scenarios sequentially with summary comparison audit table",
    )
    parser.add_argument(
        "--demo-preflight",
        action="store_true",
        help="Execute Proactive Pre-flight Schema Snapshot & Diff Engine demo",
    )
    parser.add_argument(
        "--demo-webhook",
        action="store_true",
        help="Execute Reactive Webhook Interceptor demo on POST /api/v1/webhook/failure",
    )
    parser.add_argument(
        "--mock-sandbox",
        action="store_true",
        help="Simulate shadow sandbox execution without requiring live Docker container daemon",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Execute full pytest test suite across all modules",
    )

    args = parser.parse_args()

    # If no flags passed, default to running Class 1 demo (preserving existing behavior)
    has_flags = any([
        args.demo_class1,
        args.demo_class is not None,
        args.demo_all,
        args.demo_preflight,
        args.demo_webhook,
        args.run_tests,
    ])

    if not has_flags:
        run_class_demo(1, use_mock_sandbox=args.mock_sandbox)
        return

    if args.demo_class1:
        run_class_demo(1, use_mock_sandbox=args.mock_sandbox)

    if args.demo_class is not None:
        run_class_demo(args.demo_class, use_mock_sandbox=args.mock_sandbox)

    if args.demo_all:
        run_all_classes_demo(use_mock_sandbox=args.mock_sandbox)

    if args.demo_preflight:
        run_preflight_demo(use_mock_sandbox=args.mock_sandbox)

    if args.demo_webhook:
        run_webhook_demo(use_mock_sandbox=args.mock_sandbox)

    if args.run_tests:
        exit_code = run_pytest_suite()
        if exit_code != 0:
            sys.exit(exit_code)


if __name__ == "__main__":
    main()
