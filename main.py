"""DriftOps CLI Demonstrator and Main Entry Point.

Project ID: F26-176-DriftOps
"""

import argparse
import sys
import subprocess
from typing import Any, Dict

# ANSI color escape codes
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def format_runlog(res: Dict[str, Any]) -> str:
    """Format structured pipeline results into the visual runlog specification."""
    c_ast = res["c_ast"]
    s_name = res["s_name"]
    t_compat = res["t_compat"]
    i_catalog = res["i_catalog"]

    ast_rules_str = f"{GREEN}PASS{RESET}" if res["ast_rule_valid"] else f"{RED}FAIL{RESET}"

    port = res["sandbox_port"] or 5433
    total = res["tests_total"]
    passed = res["tests_passed"]
    f_tests = res["f_tests"]
    sandbox_str = f"Port {port} | Tests Total: {total} | Passed: {passed} | F_tests: {f_tests:.2f}"

    if res["hard_gates_passed"]:
        gates_str = f"{GREEN}ALL PASSED{RESET}"
    else:
        gates_str = f"{RED}FAILED: {'; '.join(res['reasons'])}{RESET}"

    d_impact = res["d_impact"]
    one_minus_c = round(1.0 - c_ast, 4)
    risk_score = res["risk_score"]

    tier = res["tier"]
    if tier == "TIER_1_AUTO_FIX":
        decision_str = f"{GREEN}TIER 1 (AUTO-FIX) -> Auto-Merged to Staging Branch{RESET}"
    elif tier == "TIER_2_ASSIST":
        decision_str = f"{YELLOW}TIER 2 (ASSIST) -> Candidate Patch PR Emitted for Human Review{RESET}"
    else:
        decision_str = f"{RED}TIER 3 (FALLBACK) -> Gated Block: Escalated to On-Call Data Engineer{RESET}"

    runlog = f"""===============================================================
                   DRIFTOPS ENGINE RUNLOG                       
===============================================================
[1] DRIFT DETECTED     : Column Rename ('{res["old_column"]}' -> '{res["new_column"]}')
[2] AST PATCH GENERATED: {res["patched_sql"]}
[3] C_AST SCORE        : {c_ast:.2f} (S_name={s_name:.2f}, T_compat={t_compat:.2f}, I_catalog={i_catalog:.2f})
[4] AST RULES VALID    : {ast_rules_str}
[5] SANDBOX EXECUTION  : {sandbox_str}
[6] HARD SAFETY GATES  : {gates_str}
[7] RISK SCORE (R)     : {risk_score:.2f} (D_impact={d_impact:.2f}, 1-C_AST={one_minus_c:.2f})
[8] SYSTEM DECISION    : {decision_str}
==============================================================="""
    return runlog


def run_class_1_demo() -> int:
    """Execute live Class 1 Column Rename scenario."""
    from src.orchestrator.runner import run_drift_repair_pipeline

    print(f"\n{BOLD}{CYAN}>>> Initiating DriftOps Inline Repair Engine (Scenario: Class 1 Column Rename)...{RESET}\n")

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

    print(format_runlog(result))
    return 0


def run_pytest_suite() -> int:
    """Execute the project pytest test suite."""
    print(f"\n{BOLD}{CYAN}>>> Running Full DriftOps Verification Test Suite (pytest)...{RESET}\n")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-v"])
    return proc.returncode


def main() -> None:
    """Entry point for DriftOps CLI."""
    parser = argparse.ArgumentParser(
        description="DriftOps: Inline Repair and Verification Gate for dbt-core Pipelines"
    )
    parser.add_argument(
        "--demo-class1",
        action="store_true",
        help="Execute live Class 1 Column Rename (customer_phone -> contact_number) end-to-end repair",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Execute full pytest test suite across all modules",
    )

    args = parser.parse_args()

    # If no flags passed, default to running the Class 1 demo
    if not args.demo_class1 and not args.run_tests:
        run_class_1_demo()
        return

    if args.demo_class1:
        exit_code = run_class_1_demo()
        if exit_code != 0:
            sys.exit(exit_code)

    if args.run_tests:
        exit_code = run_pytest_suite()
        if exit_code != 0:
            sys.exit(exit_code)


if __name__ == "__main__":
    main()
