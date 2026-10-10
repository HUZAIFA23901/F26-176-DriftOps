"""Sandbox Module: Isolated Docker container management and ephemeral test execution.

Responsible for:
- Provisioning lightweight ephemeral PostgreSQL sandbox instances (SandboxContainer)
- Applying seed fixtures and candidate SQL patches to shadow schemas
- Running dbt test suites and computing empirical test failure ratios F_tests (run_dbt_tests)
"""

from src.sandbox.container_manager import SandboxContainer
from src.sandbox.dbt_runner import run_dbt_tests, parse_dbt_run_results

__all__ = [
    "SandboxContainer",
    "run_dbt_tests",
    "parse_dbt_run_results",
]
