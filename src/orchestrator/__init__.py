"""Orchestrator Module: End-to-end workflow execution, API, and CI/CD integration.

Responsible for:
- FastAPI webhook endpoints for CI/CD events
- Coordinating detection -> patch synthesis -> sandbox execution -> risk gate evaluation
- GitHub integration (check runs, commit statuses, inline PR review suggestions)
"""

from src.orchestrator.runner import run_drift_repair_pipeline

__all__ = [
    "run_drift_repair_pipeline",
]
