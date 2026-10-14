"""Unit test suite for DriftOps Safety Gates and Tier Decision Engine.

Tests:
1. Hard safety gate evaluation (src/gate/hard_gates.py)
2. Class overrides for high-risk drift classes (src/gate/hard_gates.py)
3. DAG blast-radius and risk calculation (src/gate/risk_calculator.py)
4. Execution tier decision routing (src/gate/decision_engine.py)

Project ID: F26-176-DriftOps
Module: tests/test_gate.py
"""

import pytest

from src.gate.hard_gates import evaluate_hard_gates, check_class_override
from src.gate.risk_calculator import calculate_d_impact, calculate_risk_score
from src.gate.decision_engine import (
    ExecutionTier,
    DecisionResult,
    evaluate_patch_tier,
)


# ==============================================================================
# 1. Hard Safety Gate Tests
# ==============================================================================

def test_hard_gates_passing():
    """Verify hard gates pass when F_tests=0.0, C_AST=0.90, rule_valid=True."""
    passed, reasons = evaluate_hard_gates(
        f_tests=0.0,
        c_ast=0.90,
        ast_rule_valid=True,
        c_ast_floor=0.80,
    )
    assert passed is True
    assert reasons == []


def test_hard_gates_failing_f_tests():
    """Verify hard gates fail when shadow tests fail (F_tests > 0.0)."""
    passed, reasons = evaluate_hard_gates(
        f_tests=0.25,
        c_ast=0.95,
        ast_rule_valid=True,
        c_ast_floor=0.80,
    )
    assert passed is False
    assert any("F_tests" in r for r in reasons)


def test_hard_gates_failing_c_ast_floor():
    """Verify hard gates fail when C_AST is below the safety floor (0.80)."""
    passed, reasons = evaluate_hard_gates(
        f_tests=0.0,
        c_ast=0.75,
        ast_rule_valid=True,
        c_ast_floor=0.80,
    )
    assert passed is False
    assert any("C_AST" in r for r in reasons)


def test_hard_gates_failing_ast_rules():
    """Verify hard gates fail when AST safety rules are violated."""
    passed, reasons = evaluate_hard_gates(
        f_tests=0.0,
        c_ast=0.95,
        ast_rule_valid=False,
        c_ast_floor=0.80,
    )
    assert passed is False
    assert any("safety rules violated" in r for r in reasons)


def test_hard_gates_multiple_failures():
    """Verify hard gates collect all failure reasons when multiple invariants fail."""
    passed, reasons = evaluate_hard_gates(
        f_tests=0.50,
        c_ast=0.60,
        ast_rule_valid=False,
        c_ast_floor=0.80,
    )
    assert passed is False
    assert len(reasons) == 3


# ==============================================================================
# 2. Class Override Tests
# ==============================================================================

@pytest.mark.parametrize(
    "failure_class",
    [
        "column_drop",
        "drop_column",
        "column_add",
        "add_column",
        "nullability_shift",
        "type_narrowing",
        "COLUMN_DROP",
        "Nullability_Shift",
    ],
)
def test_check_class_override_positive(failure_class: str):
    """Verify critical drift classes trigger mandatory class override."""
    assert check_class_override(failure_class) is True


@pytest.mark.parametrize(
    "failure_class",
    [
        "column_rename",
        "rename",
        "lossless_type_widen",
        "whitespace_format",
        "",
    ],
)
def test_check_class_override_negative(failure_class: str):
    """Verify safe or non-override classes do not trigger override."""
    assert check_class_override(failure_class) is False


# ==============================================================================
# 3. DAG Impact and Risk Calculator Tests
# ==============================================================================

def test_calculate_d_impact_saturation():
    """Verify D_impact calculation and saturation at 5 and 10 models."""
    assert calculate_d_impact(0) == 0.0
    assert calculate_d_impact(1) == 0.20
    assert calculate_d_impact(2) == 0.40
    assert calculate_d_impact(5) == 1.0
    assert calculate_d_impact(10) == 1.0


def test_calculate_risk_score_bounds():
    """Verify risk score formula R = 0.5 * D_impact + 0.5 * (1 - C_AST)."""
    # Best case: D_impact = 0.0, C_AST = 1.0 -> R = 0.0
    assert calculate_risk_score(d_impact=0.0, c_ast=1.0) == 0.0

    # Max impact, high confidence: D_impact = 1.0, C_AST = 1.0 -> R = 0.5
    assert calculate_risk_score(d_impact=1.0, c_ast=1.0) == 0.50

    # Low impact, floor confidence: D_impact = 0.2 (1 model), C_AST = 0.90
    # R = 0.5 * 0.2 + 0.5 * 0.10 = 0.10 + 0.05 = 0.15
    assert calculate_risk_score(d_impact=0.2, c_ast=0.90) == 0.15

    # High impact, floor confidence: D_impact = 1.0, C_AST = 0.80
    # R = 0.5 * 1.0 + 0.5 * 0.20 = 0.50 + 0.10 = 0.60
    assert calculate_risk_score(d_impact=1.0, c_ast=0.80) == 0.60


# ==============================================================================
# 4. Decision Engine Routing Tests
# ==============================================================================

def test_evaluate_patch_tier_tier_1_auto_fix():
    """Verify route to TIER_1_AUTO_FIX for low-risk, verified repairs."""
    # N=1 (D=0.2), C_AST=0.95 -> R = 0.5*0.2 + 0.5*0.05 = 0.10 + 0.025 = 0.125 < 0.20
    res: DecisionResult = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=0.95,
        ast_rule_valid=True,
        n_downstream=1,
        failure_class="column_rename",
    )
    assert res.tier == ExecutionTier.TIER_1_AUTO_FIX
    assert res.hard_gates_passed is True
    assert res.class_override is False
    assert res.risk_score < 0.20


def test_evaluate_patch_tier_tier_2_assist_by_risk():
    """Verify route to TIER_2_ASSIST for medium-risk repairs (0.20 <= R < 0.60)."""
    # N=3 (D=0.6), C_AST=0.85 -> R = 0.5*0.6 + 0.5*0.15 = 0.30 + 0.075 = 0.375
    res: DecisionResult = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=0.85,
        ast_rule_valid=True,
        n_downstream=3,
        failure_class="column_rename",
    )
    assert res.tier == ExecutionTier.TIER_2_ASSIST
    assert res.hard_gates_passed is True
    assert res.class_override is False
    assert 0.20 <= res.risk_score < 0.60


def test_evaluate_patch_tier_tier_2_assist_by_class_override():
    """Verify class override forces TIER_2_ASSIST even when numeric risk is below 0.20."""
    # N=0 (D=0.0), C_AST=1.0 -> R = 0.0, but failure_class is column_drop
    res: DecisionResult = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=1.0,
        ast_rule_valid=True,
        n_downstream=0,
        failure_class="column_drop",
    )
    assert res.tier == ExecutionTier.TIER_2_ASSIST
    assert res.hard_gates_passed is True
    assert res.class_override is True
    assert res.risk_score == 0.0
    assert any("override" in r.lower() for r in res.reasons)


def test_evaluate_patch_tier_tier_3_by_hard_gate_failure():
    """Verify hard gate failure routes immediately to TIER_3_FALLBACK."""
    # F_tests > 0 routes to Tier 3
    res: DecisionResult = evaluate_patch_tier(
        f_tests=0.10,
        c_ast=0.95,
        ast_rule_valid=True,
        n_downstream=0,
        failure_class="column_rename",
    )
    assert res.tier == ExecutionTier.TIER_3_FALLBACK
    assert res.hard_gates_passed is False
    assert any("Hard Gate" in r for r in res.reasons)


def test_evaluate_patch_tier_tier_3_by_high_risk():
    """Verify high risk score (R >= 0.60) routes to TIER_3_FALLBACK."""
    # N=5 (D=1.0), C_AST=0.80 -> R = 0.5*1.0 + 0.5*0.2 = 0.60 >= 0.60
    res: DecisionResult = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=0.80,
        ast_rule_valid=True,
        n_downstream=5,
        failure_class="column_rename",
    )
    assert res.tier == ExecutionTier.TIER_3_FALLBACK
    assert res.hard_gates_passed is True
    assert res.risk_score >= 0.60


# ==============================================================================
# 5. UC-08 Evaluate Risk Gate: Formulas, Overrides, & Tier Assignment
# ==============================================================================

def test_uc08_evaluate_risk_gate_mathematical_formulas():
    """UC-08: Verify mathematical formulas for D_impact, R, and breakdown telemetry."""
    from src.gate.risk_calculator import explain_risk_breakdown

    # Test D_impact saturation at 5 models:
    assert calculate_d_impact(0) == 0.0
    assert calculate_d_impact(1) == 0.20
    assert calculate_d_impact(3) == 0.60
    assert calculate_d_impact(5) == 1.00
    assert calculate_d_impact(12) == 1.00

    # Test R = 0.5 * D_impact + 0.5 * (1.0 - C_AST):
    # Case 1: D_impact = 0.20 (1 model), C_AST = 0.90 -> R = 0.5*0.20 + 0.5*0.10 = 0.15
    r1 = calculate_risk_score(d_impact=0.20, c_ast=0.90)
    assert r1 == 0.15

    # Case 2: D_impact = 0.60 (3 models), C_AST = 0.85 -> R = 0.5*0.60 + 0.5*0.15 = 0.375
    r2 = calculate_risk_score(d_impact=0.60, c_ast=0.85)
    assert r2 == 0.375

    # Test audit breakdown structure
    breakdown = explain_risk_breakdown(d_impact=0.20, c_ast=0.90)
    assert breakdown["use_case"] == "UC-08: Evaluate Risk Gate"
    assert breakdown["d_impact"] == 0.20
    assert breakdown["structural_uncertainty"] == 0.10
    assert breakdown["risk_score"] == 0.15
    assert "formula" in breakdown


def test_uc08_class_overrides_mandatory_tier2():
    """UC-08: Verify mandatory class overrides route strictly to TIER_2_ASSIST."""
    override_classes = [
        "column_add",
        "add_column",
        "column_drop",
        "drop_column",
        "nullability_shift",
        "type_narrowing",
        "narrowing_cast",
    ]
    for failure_class in override_classes:
        res = evaluate_patch_tier(
            f_tests=0.0,
            c_ast=0.99,
            ast_rule_valid=True,
            n_downstream=0,
            failure_class=failure_class,
        )
        assert res.tier == ExecutionTier.TIER_2_ASSIST
        assert res.class_override is True
        assert res.hard_gates_passed is True


def test_uc08_execution_tier_assignment_boundaries():
    """UC-08: Verify exact boundary threshold conditions for execution tier routing."""
    # Boundary 1: R < 0.20 -> Tier 1 (Auto-Fix)
    res_tier1 = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=0.92,
        ast_rule_valid=True,
        n_downstream=1,  # D_impact = 0.20, 1-C_AST = 0.08 -> R = 0.14 < 0.20
        failure_class="column_rename",
    )
    assert res_tier1.tier == ExecutionTier.TIER_1_AUTO_FIX
    assert res_tier1.risk_score < 0.20

    # Boundary 2: 0.20 <= R < 0.60 -> Tier 2 (Assist)
    res_tier2 = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=0.85,
        ast_rule_valid=True,
        n_downstream=2,  # D_impact = 0.40, 1-C_AST = 0.15 -> R = 0.275
        failure_class="column_rename",
    )
    assert res_tier2.tier == ExecutionTier.TIER_2_ASSIST
    assert 0.20 <= res_tier2.risk_score < 0.60

    # Boundary 3: R >= 0.60 -> Tier 3 (Fallback)
    res_tier3 = evaluate_patch_tier(
        f_tests=0.0,
        c_ast=0.80,
        ast_rule_valid=True,
        n_downstream=5,  # D_impact = 1.0, 1-C_AST = 0.20 -> R = 0.60 >= 0.60
        failure_class="column_rename",
    )
    assert res_tier3.tier == ExecutionTier.TIER_3_FALLBACK
    assert res_tier3.risk_score >= 0.60

