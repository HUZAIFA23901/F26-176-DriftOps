"""Gate Module: Verification, AST structural floor checks, and risk scoring.

Responsible for:
- Enforcing Hard Gate AST floor (HARD_GATE_C_AST_FLOOR)
- Multi-tier risk policy evaluation (Tier 1 autonomous vs Tier 2 bounded candidate)
- Deterministic verification before applying any automated dbt fixes
"""
