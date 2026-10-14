# DriftOps Project Contributors

**Project ID:** `F26-176-DriftOps`  
**Title:** Inline Repair & Verification Gate for dbt-core Pipelines

---

## Core Engineering Team

| Name | Role / Focus Area | Contact / GitHub |
| :--- | :--- | :--- |
| **Huzaifa** | Core Pipeline Orchestrator, Ephemeral Sandbox Runner, E2E Integration | [@HUZAIFA23901](https://github.com/HUZAIFA23901) |
| **Zeeshan Sajid** | AST Parser, SQLGlot Transforms, End-to-End CLI Demonstrator | [@zeeshansajid361](https://github.com/zeeshansajid361) |
| **Adnan** | UC-08 Evaluate Risk Gate, Mathematical Formulations, Execution Tiers & CI/CD Automation | [adnanfreelancer75500@gmail.com](mailto:adnanfreelancer75500@gmail.com) |

---

## Contribution Breakdown & Milestone Deliverables

### Adnan:
- **UC-08 Evaluate Risk Gate (`src/gate/risk_calculator.py`, `src/gate/decision_engine.py`, `tests/test_gate.py`)**:
  - Implemented mathematical formulas for deployment risk evaluation:
    - DAG Blast-Radius Impact: $D_{\text{impact}} = \min(1.0, \max(0.0, N_{\text{downstream}} / 5.0))$
    - Composite Risk Score: $R = 0.5 \times D_{\text{impact}} + 0.5 \times (1.0 - C_{\text{AST}})$
  - Implemented structural risk telemetry and audit breakdown (`explain_risk_breakdown`) for real-time diagnostics.
  - Designed mandatory failure class overrides enforcing human-in-the-loop review (`TIER_2_ASSIST`) for column additions/drops, nullability shifts, and narrowing casts.
  - Enforced 3-tier execution routing policies:
    - **Tier 1 (Autonomous Auto-Fix)**: $R < 0.20$
    - **Tier 2 (Bounded PR Assist)**: $0.20 \le R < 0.60$ or active class override
    - **Tier 3 (Block & Fallback)**: $R \ge 0.60$ or hard gate violation
  - Authored dedicated unit test verification suites covering all UC-08 boundary conditions and formulas.

- **CI/CD Automation & Quality Assurance (`.github/workflows/ci.yml`, `docs/CI_CD_PIPELINE.md`)**:
  - Implemented multi-version Python matrix testing (`Python 3.10` and `Python 3.11`).
  - Added compile-time syntax validation (`compileall`) across `src/`, `tests/`, and CLI drivers.
  - Added automated smoke test and CLI verification gate (`python main.py --demo-class1`).
  - Configured `workflow_dispatch` for manual on-demand CI triggers in GitHub Actions.
  - Authored comprehensive CI/CD architectural documentation and test guides.
