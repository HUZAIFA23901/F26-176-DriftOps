"""Detector Module: Schema drift detection and failure diagnostics.

Responsible for:
- Detecting column renames, deletions, type mutations, and upstream relation drift
- Diagnostic classification of dbt compile/run errors
- Candidate patch generation using Jaro-Winkler fuzzy matching and dialect transforms
"""
