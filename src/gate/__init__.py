"""Gate Module: Verification, AST structural floor checks, and risk scoring.

Responsible for:
- Enforcing Hard Gate AST floor (HARD_GATE_C_AST_FLOOR)
- Multi-tier risk policy evaluation (Tier 1 autonomous vs Tier 2 bounded candidate)
- Deterministic verification before applying any automated dbt fixes
"""

from src.gate.hard_gates import evaluate_hard_gates, check_class_override
from src.gate.risk_calculator import calculate_d_impact, calculate_risk_score, explain_risk_breakdown
from src.gate.decision_engine import ExecutionTier, DecisionResult, evaluate_patch_tier

__all__ = [
    "evaluate_hard_gates",
    "check_class_override",
    "calculate_d_impact",
    "calculate_risk_score",
    "explain_risk_breakdown",
    "ExecutionTier",
    "DecisionResult",
    "evaluate_patch_tier",
]
