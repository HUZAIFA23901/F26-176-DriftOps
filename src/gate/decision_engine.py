"""Execution Tier Decision Engine for DriftOps.

Synthesizes hard gate evaluations, class overrides, DAG blast radius, and
structural risk scores to classify candidate repairs into actionable execution tiers:
- Tier 1: Autonomous auto-fix
- Tier 2: Bounded PR assist / human review
- Tier 3: Hard block / manual fallback

Project ID: F26-176-DriftOps
Module: src/gate/decision_engine.py
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from src.gate.hard_gates import evaluate_hard_gates, check_class_override
from src.gate.risk_calculator import calculate_d_impact, calculate_risk_score


class ExecutionTier(str, Enum):
    """Execution tiers for automated patch deployment."""
    TIER_1_AUTO_FIX = "TIER_1_AUTO_FIX"
    TIER_2_ASSIST = "TIER_2_ASSIST"
    TIER_3_FALLBACK = "TIER_3_FALLBACK"


@dataclass
class DecisionResult:
    """Detailed outcome from the risk gate tier evaluation."""
    tier: ExecutionTier
    risk_score: float
    d_impact: float
    c_ast: float
    f_tests: float
    hard_gates_passed: bool
    class_override: bool
    reasons: List[str] = field(default_factory=list)


def evaluate_patch_tier(
    f_tests: float,
    c_ast: float,
    ast_rule_valid: bool,
    n_downstream: int,
    failure_class: str = "column_rename",
    tier_1_max_risk: float = 0.20,
    tier_2_max_risk: float = 0.60,
    c_ast_floor: float = 0.80,
) -> DecisionResult:
    """Evaluate candidate patch against the full DriftOps decision matrix.

    Decision Workflow:
    1. Evaluate hard safety gates. If ANY fail -> TIER_3_FALLBACK immediately.
    2. Compute D_impact and composite risk score R.
    3. Check for mandatory failure class overrides. If active -> TIER_2_ASSIST.
    4. If R < tier_1_max_risk (0.20) -> TIER_1_AUTO_FIX.
    5. If tier_1_max_risk <= R < tier_2_max_risk (0.60) -> TIER_2_ASSIST.
    6. Else (R >= tier_2_max_risk) -> TIER_3_FALLBACK.

    Args:
        f_tests: Test failure ratio from shadow execution (0.0 to 1.0).
        c_ast: Structural AST confidence score (0.0 to 1.0).
        ast_rule_valid: Whether AST safety rules were satisfied.
        n_downstream: Count of impacted downstream DAG models.
        failure_class: Diagnostic class of schema drift (default: "column_rename").
        tier_1_max_risk: Maximum risk ceiling for Tier 1 autonomous repairs (default: 0.20).
        tier_2_max_risk: Maximum risk ceiling for Tier 2 bounded reviews (default: 0.60).
        c_ast_floor: Minimum structural confidence floor (default: 0.80).

    Returns:
        DecisionResult containing assigned tier, scores, gate statuses, and reasons.
    """
    # Step 1: Evaluate hard safety gates
    hard_passed, hard_reasons = evaluate_hard_gates(
        f_tests=f_tests,
        c_ast=c_ast,
        ast_rule_valid=ast_rule_valid,
        c_ast_floor=c_ast_floor,
    )

    d_impact = calculate_d_impact(n_downstream)
    risk_score = calculate_risk_score(d_impact=d_impact, c_ast=c_ast)
    is_class_override = check_class_override(failure_class)

    # If hard gates failed, route immediately to Tier 3 Fallback
    if not hard_passed:
        return DecisionResult(
            tier=ExecutionTier.TIER_3_FALLBACK,
            risk_score=risk_score,
            d_impact=d_impact,
            c_ast=c_ast,
            f_tests=f_tests,
            hard_gates_passed=False,
            class_override=is_class_override,
            reasons=hard_reasons,
        )

    # Step 2: Check for mandatory class overrides (e.g. column deletion, narrowing cast)
    if is_class_override:
        return DecisionResult(
            tier=ExecutionTier.TIER_2_ASSIST,
            risk_score=risk_score,
            d_impact=d_impact,
            c_ast=c_ast,
            f_tests=f_tests,
            hard_gates_passed=True,
            class_override=True,
            reasons=[
                f"Mandatory Tier 2 Assist override triggered by high-impact drift class '{failure_class}'"
            ],
        )

    # Step 3: Evaluate risk score boundaries
    if risk_score < tier_1_max_risk:
        assigned_tier = ExecutionTier.TIER_1_AUTO_FIX
        reasons = [
            f"Risk score {risk_score:.4f} is below Tier 1 ceiling {tier_1_max_risk:.2f}; safe for autonomous inline patch"
        ]
    elif risk_score < tier_2_max_risk:
        assigned_tier = ExecutionTier.TIER_2_ASSIST
        reasons = [
            f"Risk score {risk_score:.4f} within bounded range [{tier_1_max_risk:.2f}, {tier_2_max_risk:.2f}); PR review required"
        ]
    else:
        assigned_tier = ExecutionTier.TIER_3_FALLBACK
        reasons = [
            f"Risk score {risk_score:.4f} exceeds Tier 2 maximum ceiling {tier_2_max_risk:.2f}; patch rejected"
        ]

    return DecisionResult(
        tier=assigned_tier,
        risk_score=risk_score,
        d_impact=d_impact,
        c_ast=c_ast,
        f_tests=f_tests,
        hard_gates_passed=True,
        class_override=False,
        reasons=reasons,
    )
