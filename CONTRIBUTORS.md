# DriftOps Project Contributors

**Project ID:** `F26-176-DriftOps`  
**Title:** Inline Repair & Verification Gate for dbt-core Pipelines

---

## Core Engineering Team

| Name | Role / Focus Area | Contact / GitHub |
| :--- | :--- | :--- |
| **Huzaifa** | Core Pipeline Orchestrator, Decision Engine, Risk Gates | [@HUZAIFA23901](https://github.com/HUZAIFA23901) |
| **Zeeshan Sajid** | AST Parser, SQLGlot Transforms, End-to-End CLI | [@zeeshansajid361](https://github.com/zeeshansajid361) |
| **Adnan** | CI/CD Automation Pipeline, Multi-Version Testing Matrix & Project Documentation | [adnanfreelancer75500@gmail.com](mailto:adnanfreelancer75500@gmail.com) |

---

## Contribution Breakdown & Milestone Deliverables

### Adnan:
- **CI/CD Automation (`.github/workflows/ci.yml`)**:
  - Implemented multi-version Python matrix testing (`Python 3.10` and `Python 3.11`).
  - Added compile-time syntax validation (`compileall`) across `src/`, `tests/`, and CLI drivers.
  - Added automated smoke test and CLI verification gate (`python main.py --demo-class1`).
  - Configured `workflow_dispatch` for manual on-demand CI triggers in GitHub Actions.
- **Documentation & Verification Architecture (`docs/CI_CD_PIPELINE.md`)**:
  - Documented CI/CD test automation lifecycle, matrix environments, and gate specifications.
  - Established project contributor records and architectural integration reference.
