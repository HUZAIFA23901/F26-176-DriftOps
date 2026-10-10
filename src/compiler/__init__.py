"""Compiler Module: SQL parsing, AST representation, and dbt manifest integration.

Responsible for:
- Parsing dialect SQL via sqlglot
- Extracting lineage, column projections, and join graphs
- Comparing original vs candidate AST structures
- Calculating C_AST confidence scores and validating AST safety rules
"""

from src.compiler.ast_parser import extract_columns, repair_column_rename
from src.compiler.cast_calculator import (
    calculate_name_similarity,
    calculate_type_compatibility,
    calculate_catalog_invariant,
    compute_c_ast,
)
from src.compiler.rules import validate_ast_rules

__all__ = [
    "extract_columns",
    "repair_column_rename",
    "calculate_name_similarity",
    "calculate_type_compatibility",
    "calculate_catalog_invariant",
    "compute_c_ast",
    "validate_ast_rules",
]
