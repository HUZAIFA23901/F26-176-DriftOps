"""AST parsing, traversal, and SQL query rewrite engine using sqlglot.

Project ID: F26-176-DriftOps
Module: src/compiler/ast_parser.py
"""

import re
from typing import List, Tuple
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from src.compiler.cast_calculator import TYPE_ALIASES, LOSSLESS_CONVERSIONS


def extract_columns(sql_query: str, dialect: str = "postgres") -> List[str]:
    """Parse SQL into an AST and extract all unique column identifier names.

    Args:
        sql_query: Raw SQL query string to parse.
        dialect: SQL dialect used for parsing (default: "postgres").

    Returns:
        List of distinct column names in order of appearance.

    Raises:
        ParseError: If the SQL string contains invalid syntax.
    """
    if not sql_query or not sql_query.strip():
        return []

    try:
        expression = sqlglot.parse_one(sql_query, read=dialect)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse SQL expression: {exc}") from exc

    seen = set()
    columns: List[str] = []
    for col in expression.find_all(exp.Column):
        col_name = col.name
        if col_name and col_name not in seen:
            seen.add(col_name)
            columns.append(col_name)

    return columns


def repair_column_rename(
    sql_query: str,
    old_column: str,
    new_column: str,
    dialect: str = "postgres",
) -> str:
    """Traverse SQL AST and rewrite references of `old_column` to `new_column`.

    In SELECT projection expressions where old_column is not already aliased,
    it is rewritten as `new_column AS old_column` to preserve the downstream
    schema contract. In existing aliases or predicate clauses, the column reference
    is directly replaced with new_column.

    Args:
        sql_query: Original SQL query to repair.
        old_column: Column name that drifted or failed.
        new_column: Discovered replacement column name.
        dialect: Target SQL dialect (default: "postgres").

    Returns:
        Repaired SQL formatted in the target dialect.

    Raises:
        ParseError: If the input SQL string contains invalid syntax.
    """
    if not sql_query or not sql_query.strip():
        return sql_query

    try:
        ast = sqlglot.parse_one(sql_query, read=dialect)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse SQL expression: {exc}") from exc

    old_target = old_column.strip().lower()

    for col in list(ast.find_all(exp.Column)):
        if col.name.lower() == old_target:
            parent = col.parent
            table = col.table
            replacement_col = exp.column(new_column, table=table) if table else exp.column(new_column)

            # Direct projection item in a Select expression list
            if isinstance(parent, exp.Select) and col in parent.expressions:
                alias = exp.alias_(replacement_col, old_column)
                col.replace(alias)
            # Already an alias (e.g. SELECT old_col AS custom_alias)
            elif isinstance(parent, exp.Alias) and parent.this == col:
                col.replace(replacement_col)
            # Elsewhere (WHERE, JOIN ON, GROUP BY, ORDER BY, functions)
            else:
                col.replace(replacement_col)

    return ast.sql(dialect=dialect)


def _parse_type_and_params(type_str: str) -> Tuple[str, List[int]]:
    """Parse a SQL type string into a normalized base type and integer parameters.

    Examples:
        'VARCHAR(50)' -> ('VARCHAR', [50])
        'NUMERIC(10, 2)' -> ('NUMERIC', [10, 2])
        'INT' -> ('INTEGER', [])
        'FLOAT' -> ('DOUBLE PRECISION', [])

    Args:
        type_str: Raw type string from schema or contract.

    Returns:
        Tuple of (normalized_base_type, list_of_integer_params).
    """
    clean_str = type_str.strip().upper()
    match = re.match(r"^([A-Z\s]+)(?:\((.*?)\))?$", clean_str)
    if not match:
        base = clean_str
        params: List[int] = []
    else:
        base = match.group(1).strip()
        param_str = match.group(2)
        params = []
        if param_str:
            for p in param_str.split(","):
                p_clean = p.strip()
                if p_clean.isdigit():
                    params.append(int(p_clean))

    normalized_base = TYPE_ALIASES.get(base, base)
    return normalized_base, params


def is_narrowing_type_cast(source_type: str, target_type: str) -> bool:
    """Evaluate whether converting from `source_type` to `target_type` is narrowing.

    Widening conversions (e.g. INT -> BIGINT, VARCHAR(50) -> VARCHAR(250), DATE -> TIMESTAMP)
    carry no risk of truncation, precision loss, or format conversion failure.
    Narrowing conversions (e.g. VARCHAR -> INT, FLOAT -> INT, BIGINT -> INT,
    VARCHAR(250) -> VARCHAR(50)) carry data loss or runtime failure risk.

    Args:
        source_type: Original column data type before drift.
        target_type: Candidate or required data type.

    Returns:
        False if the cast is widening/lossless (no narrowing risk).
        True if the cast is narrowing (carries potential data loss/overflow risk).
    """
    src_base, src_params = _parse_type_and_params(source_type)
    tgt_base, tgt_params = _parse_type_and_params(target_type)

    # Identical base types (check length or precision/scale constraints)
    if src_base == tgt_base:
        if src_base in ("VARCHAR", "CHAR"):
            if tgt_params:
                # Unconstrained source to constrained target is narrowing
                if not src_params:
                    return True
                # Narrowing if target capacity is smaller than source
                return tgt_params[0] < src_params[0]
            return False

        if src_base == "NUMERIC":
            if tgt_params:
                if not src_params:
                    return True
                src_prec = src_params[0]
                src_scale = src_params[1] if len(src_params) > 1 else 0
                tgt_prec = tgt_params[0]
                tgt_scale = tgt_params[1] if len(tgt_params) > 1 else 0

                # Integer digits capacity check: (prec - scale)
                if (tgt_prec - tgt_scale) < (src_prec - src_scale) or tgt_scale < src_scale:
                    return True
                return False
            return False

        return False

    # Check lossless implicit widening conversions
    if tgt_base in LOSSLESS_CONVERSIONS.get(src_base, set()):
        if tgt_base == "VARCHAR" and tgt_params:
            if src_params and tgt_params[0] < src_params[0]:
                return True
        return False

    # Incompatible or narrowing conversion
    return True


def repair_type_shift(
    sql: str,
    column_name: str,
    source_type: str,
    target_type: str,
    dialect: str = "postgres",
    **kwargs: str,
) -> Tuple[str, bool]:
    """Repair Failure Class 2 (Type Shift) by injecting AST CAST expressions.

    Analyzes SQL AST using sqlglot and injects explicit CAST expressions into SELECT
    projection lists. Preserves column aliases and existing query structure.

    Args:
        sql: Original SQL query to repair.
        column_name: Name of column requiring type repair.
        source_type: Data type of column before drift.
        target_type: Required target data type.
        dialect: SQL dialect for AST parsing and generation (default: "postgres").
        **kwargs: Optional fallback arguments (e.g. sql_query).

    Returns:
        Tuple[str, bool]: (patched_sql, is_narrowing) where:
            - patched_sql: Repaired SQL string containing injected CAST.
            - is_narrowing: True if cast carries narrowing/loss risk (forcing Tier 2 Assist),
                            False if cast is widening (Tier 1 safe).

    Raises:
        ParseError: If the SQL string contains invalid syntax.
    """
    raw_sql = kwargs.get("sql_query", sql)
    if not raw_sql or not raw_sql.strip():
        return raw_sql, is_narrowing_type_cast(source_type, target_type)

    try:
        ast = sqlglot.parse_one(raw_sql, read=dialect)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse SQL expression: {exc}") from exc

    is_narrowing = is_narrowing_type_cast(source_type, target_type)
    target_col = column_name.strip().lower()

    try:
        to_type = exp.DataType.build(target_type, dialect=dialect)
    except Exception:
        to_type = exp.var(target_type)

    for select in ast.find_all(exp.Select):
        for expr in list(select.expressions):
            # Case 1: Direct column projection (e.g. SELECT col FROM table)
            if isinstance(expr, exp.Column) and expr.name.lower() == target_col:
                col_ref = exp.column(expr.name, table=expr.table) if expr.table else exp.column(expr.name)
                cast_expr = exp.Cast(this=col_ref, to=to_type)
                alias_expr = exp.alias_(cast_expr, expr.name)
                expr.replace(alias_expr)

            # Case 2: Column with custom alias (e.g. SELECT col AS custom_alias FROM table)
            elif (
                isinstance(expr, exp.Alias)
                and isinstance(expr.this, exp.Column)
                and expr.this.name.lower() == target_col
            ):
                col = expr.this
                col_ref = exp.column(col.name, table=col.table) if col.table else exp.column(col.name)
                cast_expr = exp.Cast(this=col_ref, to=to_type)
                expr.set("this", cast_expr)

            # Case 3: Column already in CAST with custom alias (e.g. SELECT CAST(col AS old) AS custom_alias)
            elif (
                isinstance(expr, exp.Alias)
                and isinstance(expr.this, exp.Cast)
                and isinstance(expr.this.this, exp.Column)
                and expr.this.this.name.lower() == target_col
            ):
                expr.this.set("to", to_type)

            # Case 4: Column already in CAST without alias (e.g. SELECT CAST(col AS old))
            elif (
                isinstance(expr, exp.Cast)
                and isinstance(expr.this, exp.Column)
                and expr.this.name.lower() == target_col
            ):
                expr.set("to", to_type)
                alias_expr = exp.alias_(expr.copy(), expr.this.name)
                expr.replace(alias_expr)

    return ast.sql(dialect=dialect), is_narrowing

