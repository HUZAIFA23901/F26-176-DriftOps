"""dbt Execution Harness and Test Log Parser for DriftOps.

Executes dbt test suites against ephemeral sandbox databases and computes
the empirical test failure ratio F_tests = failed_tests / total_tests.

Project ID: F26-176-DriftOps
Module: src/sandbox/dbt_runner.py
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional


def parse_dbt_run_results(
    run_results_data: Optional[Dict[str, Any]] = None,
    log_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Extract test execution metrics and calculate F_tests.

    Args:
        run_results_data: Parsed dictionary from target/run_results.json.
        log_lines: Captured standard output and error lines from dbt run.

    Returns:
        Dictionary with f_tests, total, passed, and failed counts.
    """
    total = 0
    passed = 0
    failed = 0

    # 1. Attempt structured parsing from run_results.json
    if run_results_data and "results" in run_results_data:
        results = run_results_data.get("results", [])
        total = len(results)
        for r in results:
            status = str(r.get("status", "")).lower()
            if status in ("pass", "success"):
                passed += 1
            elif status in ("fail", "error"):
                failed += 1

    # 2. Fallback to parsing CLI log output if JSON was unavailable or empty
    if total == 0 and log_lines:
        for line in log_lines:
            # Match: "Done. PASS=6 WARN=0 ERROR=0 ... TOTAL=6"
            done_match = re.search(r"PASS=(\d+).*?ERROR=(\d+).*?TOTAL=(\d+)", line)
            if done_match:
                passed = int(done_match.group(1))
                failed = int(done_match.group(2))
                total = int(done_match.group(3))
                break

            # Match individual test status lines: "1 of 6 PASS ..." or "1 of 6 FAIL ..."
            if re.search(r"\bPASS\b", line) and re.search(r"\bof\b", line):
                total += 1
                passed += 1
            elif (re.search(r"\bFAIL\b", line) or re.search(r"\bERROR\b", line)) and re.search(r"\bof\b", line):
                total += 1
                failed += 1

    f_tests = round(failed / total, 4) if total > 0 else 0.0

    return {
        "f_tests": f_tests,
        "total": total,
        "passed": passed,
        "failed": failed,
    }


def run_dbt_tests(
    target_config: Dict[str, Any],
    project_dir: str = "testbed",
    select: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute dbt test against an ephemeral sandbox target database.

    Args:
        target_config: Connection dictionary containing host, port, user, password, dbname.
        project_dir: Root directory of target dbt-core project (default: "testbed").
        select: Optional model or tag selection string for dbt test.

    Returns:
        Structured test result dictionary:
        {
            "f_tests": float,
            "total": int,
            "passed": int,
            "failed": int,
            "logs": list[str]
        }
    """
    env = os.environ.copy()
    env["POSTGRES_HOST"] = str(target_config.get("host", "localhost"))
    env["POSTGRES_PORT"] = str(target_config.get("port", 5432))
    env["POSTGRES_USER"] = str(target_config.get("user", "postgres"))
    env["POSTGRES_PASSWORD"] = str(target_config.get("password", "postgres"))
    env["POSTGRES_DB"] = str(target_config.get("dbname", "shadow_db"))

    proj_path = Path(project_dir).resolve()
    cmd = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "test",
        "--project-dir",
        str(proj_path),
        "--profiles-dir",
        str(proj_path),
    ]

    if select:
        cmd.extend(["--select", select])

    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    logs: List[str] = proc.stdout.splitlines() + proc.stderr.splitlines()

    # Parse target/run_results.json
    run_results_path = proj_path / "target" / "run_results.json"
    run_results_json: Optional[Dict[str, Any]] = None

    if run_results_path.exists():
        try:
            with open(run_results_path, "r", encoding="utf-8") as f:
                run_results_json = json.load(f)
        except Exception:
            run_results_json = None

    metrics = parse_dbt_run_results(run_results_data=run_results_json, log_lines=logs)

    return {
        "f_tests": metrics["f_tests"],
        "total": metrics["total"],
        "passed": metrics["passed"],
        "failed": metrics["failed"],
        "logs": logs,
    }
