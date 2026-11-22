"""AST Rule Validator and hard safety gate for DriftOps.

Validates that automated candidate repairs preserve structural semantics
and do not inject arbitrary joins, altered filters, or unexpected expressions.

Project ID: F26-176-DriftOps
Module: src/compiler/rules.py
"""

from typing import Optional
import sqlglot
from sqlglot import exp


def _normalize_predicate(node: Optional[exp.Expression]) -> Optional[str]:
    """Normalize a WHERE predicate tree by replacing column identifiers and cast wrappers."""
    if node is None:
        return None
    clone = node.copy()
    for col in clone.find_all(exp.Column):
        col.replace(exp.column("__NORM_COL__"))
    for cast in clone.find_all(exp.Cast):
        cast.replace(cast.this)
    return clone.sql()


def _is_valid_projection_modification(
    orig_proj: exp.Expression,
    patch_proj: exp.Expression,
    norm_class: str = "column_rename",
) -> bool:
    """Validate that a projection expression was only modified legitimately per failure class."""
    if orig_proj.sql().strip() == patch_proj.sql().strip():
        return True

    # Unpack aliases
    patch_inner = patch_proj.this if isinstance(patch_proj, exp.Alias) else patch_proj

    # Unpack explicit Casts
    if isinstance(patch_inner, exp.Cast):
        patch_inner = patch_inner.this

    # If nullability shift, allow COALESCE(col, literal)
    if "nullability" in norm_class and isinstance(patch_inner, exp.Coalesce):
        first_arg = patch_inner.this
        if isinstance(first_arg, exp.Column):
            return True
        return False

    # If column drop, allow NULL
    if ("drop" in norm_class or "add" in norm_class) and isinstance(patch_inner, exp.Null):
        return True

    # The underlying target must be a pure column reference (no added literals, math, or subqueries)
    if not isinstance(patch_inner, exp.Column):
        return False

    return True


def validate_ast_rules(
    original_sql: str,
    patched_sql: str,
    dialect: str = "postgres",
    failure_class: str = "column_rename",
) -> bool:
    """Enforce deterministic safety gating on candidate query patches.

    Verifies:
    1. No new JOIN clauses were added.
    2. No WHERE clause filters were injected or altered.
    3. No raw literal values or unexpected columns were added.
    4. Changes are strictly limited to targeted column renames, explicit casts,
       COALESCE nullability handling, or column drop/add projections.

    Args:
        original_sql: The base unpatched SQL query string.
        patched_sql: The candidate repaired SQL query string.
        dialect: SQL dialect for AST parsing (default: "postgres").
        failure_class: Canonical failure class string (default: "column_rename").

    Returns:
        True if the patched SQL satisfies all safety rules, False otherwise.
    """
    if not original_sql or not patched_sql:
        return False

    try:
        orig_ast = sqlglot.parse_one(original_sql, read=dialect)
        patch_ast = sqlglot.parse_one(patched_sql, read=dialect)
    except Exception:
        return False

    # Rule 1: No new JOIN clauses added
    orig_joins = list(orig_ast.find_all(exp.Join))
    patch_joins = list(patch_ast.find_all(exp.Join))
    if len(patch_joins) > len(orig_joins):
        return False

    # Rule 2: No WHERE clause filters injected or altered
    orig_where = orig_ast.find(exp.Where)
    patch_where = patch_ast.find(exp.Where)

    if (orig_where is None and patch_where is not None) or (orig_where is not None and patch_where is None):
        return False

    if orig_where is not None and patch_where is not None:
        if _normalize_predicate(orig_where) != _normalize_predicate(patch_where):
            return False

    norm_class = (failure_class or "column_rename").strip().lower()

    # Rule 3: No new literals added (unless nullability shift which introduces default value in coalesce)
    orig_literals = list(orig_ast.find_all(exp.Literal))
    patch_literals = list(patch_ast.find_all(exp.Literal))
    if "nullability" not in norm_class:
        if len(patch_literals) > len(orig_literals):
            return False
    else:
        coalesce_count = len(list(patch_ast.find_all(exp.Coalesce)))
        if len(patch_literals) > len(orig_literals) + coalesce_count:
            return False

    # Rule 3b: Table check
    orig_tables = {t.name.lower() for t in orig_ast.find_all(exp.Table)}
    patch_tables = {t.name.lower() for t in patch_ast.find_all(exp.Table)}
    if "model" in norm_class or "table" in norm_class:
        if len(patch_tables) != len(orig_tables):
            return False
    else:
        if not patch_tables.issubset(orig_tables):
            return False

    # Rule 3c: Invariant clause counts (Group, Order, Having, Limit, Union)
    for clause_cls in (exp.Group, exp.Order, exp.Having, exp.Limit, exp.Union):
        if len(list(patch_ast.find_all(clause_cls))) != len(list(orig_ast.find_all(clause_cls))):
            return False

    # Rule 4: Strictly limited to targeted column renames, casts, or allowed projections
    orig_selects = orig_ast.selects
    patch_selects = patch_ast.selects

    if "drop" in norm_class or "add" in norm_class:
        for pp in patch_selects:
            patch_inner = pp.this if isinstance(pp, exp.Alias) else pp
            if isinstance(patch_inner, exp.Cast):
                patch_inner = patch_inner.this
            if not isinstance(patch_inner, (exp.Column, exp.Null)):
                return False
        return True

    if len(orig_selects) != len(patch_selects):
        return False

    for op, pp in zip(orig_selects, patch_selects):
        if not _is_valid_projection_modification(op, pp, norm_class=norm_class):
            return False

    return True
