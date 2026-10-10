"""Deterministic Hard Safety Gates and Class Overrides for DriftOps.

Enforces zero-tolerance invariant preconditions before allowing automated
pipeline modifications or tier evaluations.

Project ID: F26-176-DriftOps
Module: src/gate/hard_gates.py
"""

from typing import List, Tuple

# Set of failure classes requiring mandatory human-in-the-loop (Tier 2 Assist) review
MANDATORY_OVERRIDE_CLASSES = {
    "column_add",
    "add_column",
    "column_drop",
    "drop_column",
    "nullability_shift",
    "nullability_change",
    "type_narrowing",
    "narrowing_cast",
}


def evaluate_hard_gates(
    f_tests: float,
    c_ast: float,
    ast_rule_valid: bool,
    c_ast_floor: float = 0.80,
) -> Tuple[bool, List[str]]:
    """Evaluate mandatory invariant conditions for candidate patches.

    Conditions:
    1. F_tests == 0.0 (Zero failed dbt assertions in the shadow sandbox).
    2. C_AST >= c_ast_floor (Structural AST confidence meets or exceeds floor).
    3. ast_rule_valid == True (No illegal joins, altered where clauses, or extra columns).

    Args:
        f_tests: Test failure ratio from shadow database execution (0.0 to 1.0).
        c_ast: Composite AST confidence score (0.0 to 1.0).
        ast_rule_valid: Boolean indicating whether AST safety validator passed.
        c_ast_floor: Minimum required C_AST score (default: 0.80).

    Returns:
        Tuple of (passed: bool, reasons: list[str]).
        If passed is True, reasons is empty [].
        If passed is False, reasons contains specific failure explanations.
    """
    reasons: List[str] = []

    # Invariant 1: Zero failed tests in shadow sandbox
    if f_tests > 0.0:
        reasons.append(
            f"Hard Gate 1 Violation: Shadow sandbox test failure ratio F_tests={f_tests:.4f} > 0.0"
        )

    # Invariant 2: Structural confidence must meet or exceed floor
    if c_ast < c_ast_floor:
        reasons.append(
            f"Hard Gate 2 Violation: Structural confidence C_AST={c_ast:.4f} below floor {c_ast_floor:.4f}"
        )

    # Invariant 3: AST syntax and structural safety rules must pass
    if not ast_rule_valid:
        reasons.append(
            "Hard Gate 3 Violation: AST safety rules violated (unauthorized joins, altered filters, or unexpected expressions)"
        )

    passed = len(reasons) == 0
    return (passed, reasons)


def check_class_override(failure_class: str) -> bool:
    """Check if the failure class requires a mandatory Tier 2 (Assist) override.

    Certain high-consequence drift classes (e.g. column deletions, additions,
    nullability changes, or narrowing type casts) must not be autonomously merged
    regardless of low numeric risk scores.

    Args:
        failure_class: Schema failure classification string (e.g. "column_rename", "column_drop").

    Returns:
        True if mandatory Tier 2 override applies, False otherwise.
    """
    if not failure_class:
        return False

    normalized_class = failure_class.strip().lower()
    return normalized_class in MANDATORY_OVERRIDE_CLASSES
