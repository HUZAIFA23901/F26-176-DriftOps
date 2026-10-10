"""Risk Score Calculator for DriftOps.

Calculates DAG blast radius D_impact and composite deployment risk score:
R = 0.5 * D_impact + 0.5 * (1.0 - C_AST)

Project ID: F26-176-DriftOps
Module: src/gate/risk_calculator.py
"""


def calculate_d_impact(n_downstream: int) -> float:
    """Compute the DAG blast-radius score using absolute saturation at 5 models.

    Formula:
        D_impact = min(1.0, max(0.0, n_downstream / 5.0))

    Args:
        n_downstream: Number of direct and indirect downstream dbt models impacted.

    Returns:
        Float score in [0.0, 1.0], saturating at 1.0 for n_downstream >= 5.
    """
    if n_downstream <= 0:
        return 0.0

    raw_impact = n_downstream / 5.0
    return round(min(1.0, max(0.0, raw_impact)), 4)


def calculate_risk_score(d_impact: float, c_ast: float) -> float:
    """Compute normalized deployment risk score R in [0.0, 1.0].

    Formula:
        R = 0.5 * D_impact + 0.5 * (1.0 - C_AST)

    Balances the structural uncertainty (1 - C_AST) against the potential blast radius
    in the DAG (D_impact).

    Args:
        d_impact: Blast radius impact score (0.0 to 1.0).
        c_ast: Structural AST confidence score (0.0 to 1.0).

    Returns:
        Risk score clamped strictly between [0.0, 1.0].
    """
    clamped_d = max(0.0, min(1.0, d_impact))
    clamped_c = max(0.0, min(1.0, c_ast))

    risk = (0.5 * clamped_d) + (0.5 * (1.0 - clamped_c))
    return round(max(0.0, min(1.0, risk)), 4)
