"""AST parsing, traversal, and SQL query rewrite engine using sqlglot.

Project ID: F26-176-DriftOps
Module: src/compiler/ast_parser.py
"""

import re
from typing import Any, List, Optional, Tuple
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


def repair_column_drop_or_add(
    sql: str,
    dropped_columns: Optional[List[str]] = None,
    added_columns: Optional[List[str]] = None,
    dialect: str = "postgres",
    remove_dropped: bool = False,
    **kwargs: Any,
) -> Tuple[str, bool]:
    """Repair Failure Class 3 (Column Additions & Drops) via AST transformation.

    Analyzes SQL AST using sqlglot and:
    1. Replaces dropped columns in SELECT projection expressions with `NULL AS column_name`
       (or completely removes them if `remove_dropped=True`) to maintain downstream schema contracts.
       Sets `requires_tier2_override = True` whenever dropped columns are specified.
    2. Appends added columns to SELECT projection lists if detected upstream.

    Args:
        sql: Original SQL query string to repair.
        dropped_columns: List of column names that were dropped upstream.
        added_columns: List of new column names discovered upstream.
        dialect: SQL dialect for AST parsing and generation (default: "postgres").
        remove_dropped: If True, completely removes dropped columns from SELECT list
                        instead of replacing them with NULL placeholders (default: False).
        **kwargs: Optional fallback arguments (e.g. sql_query).

    Returns:
        Tuple[str, bool]: (patched_sql, requires_tier2_override) where:
            - patched_sql: Repaired SQL query string.
            - requires_tier2_override: True if schema drop occurred (forcing Tier 2 review),
                                      False if only non-breaking additions or no-op.

    Raises:
        ParseError: If the SQL string contains invalid syntax.
    """
    raw_sql = kwargs.get("sql_query", sql)
    requires_tier2_override = bool(dropped_columns)

    if not raw_sql or not raw_sql.strip():
        return raw_sql, requires_tier2_override

    try:
        ast = sqlglot.parse_one(raw_sql, read=dialect)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse SQL expression: {exc}") from exc

    dropped_set = {c.strip().lower() for c in (dropped_columns or []) if c.strip()}
    added_list = [c.strip() for c in (added_columns or []) if c.strip()]

    # 1. Process dropped columns in SELECT lists
    if dropped_set:
        for select in ast.find_all(exp.Select):
            for expr in list(select.expressions):
                col_name = None
                alias_name = None

                if isinstance(expr, exp.Column):
                    col_name = expr.name.lower()
                    alias_name = expr.name
                elif isinstance(expr, exp.Alias):
                    alias_name = expr.alias
                    if isinstance(expr.this, exp.Column):
                        col_name = expr.this.name.lower()
                    elif isinstance(expr.this, exp.Cast) and isinstance(expr.this.this, exp.Column):
                        col_name = expr.this.this.name.lower()
                elif isinstance(expr, exp.Cast) and isinstance(expr.this, exp.Column):
                    col_name = expr.this.name.lower()
                    alias_name = expr.this.name

                if col_name and col_name in dropped_set:
                    if remove_dropped:
                        expr.pop()
                    else:
                        null_expr = exp.alias_(exp.null(), alias_name)
                        expr.replace(null_expr)

    # 2. Process added columns into SELECT lists
    if added_list:
        for select in ast.find_all(exp.Select):
            # Avoid appending into subqueries inside WHERE, IN, or EXISTS clauses
            curr = select.parent
            in_predicate = False
            while curr:
                if isinstance(curr, (exp.Where, exp.In, exp.Exists)):
                    in_predicate = True
                    break
                curr = curr.parent
            if in_predicate:
                continue

            existing_names = set()
            for expr in select.expressions:
                if isinstance(expr, exp.Column):
                    existing_names.add(expr.name.lower())
                elif isinstance(expr, exp.Alias):
                    existing_names.add(expr.alias.lower())
                    if hasattr(expr.this, "name") and expr.this.name:
                        existing_names.add(expr.this.name.lower())
                elif hasattr(expr, "alias_or_name") and expr.alias_or_name:
                    existing_names.add(expr.alias_or_name.lower())

            for col in added_list:
                if col.lower() not in existing_names:
                    select.select(exp.column(col), copy=False)
                    existing_names.add(col.lower())

    return ast.sql(dialect=dialect), requires_tier2_override


def repair_nullability_shift(
    sql: str,
    column_name: str,
    default_value: str = "''",
    dialect: str = "postgres",
    **kwargs: Any,
) -> Tuple[str, bool]:
    """Repair Failure Class 4 (Nullability Shifts) via AST transformation.

    Analyzes SQL AST using sqlglot and wraps drifted non-nullable column projections
    in `COALESCE(column_name, <default_value>) AS column_name` (or preserves custom aliases)
    to prevent NULL constraint violations downstream.

    Args:
        sql: Original SQL query to repair.
        column_name: Name of column with nullability shift.
        default_value: SQL literal default value to substitute for NULLs (default: "''").
        dialect: SQL dialect for AST parsing and generation (default: "postgres").
        **kwargs: Optional fallback arguments (e.g. sql_query).

    Returns:
        Tuple[str, bool]: (patched_sql, requires_tier2_override) where:
            - patched_sql: Repaired SQL string containing injected COALESCE.
            - requires_tier2_override: Always True (handling nullability constraints requires human review).

    Raises:
        ParseError: If the SQL string contains invalid syntax.
    """
    raw_sql = kwargs.get("sql_query", sql)
    if not raw_sql or not raw_sql.strip():
        return raw_sql, True

    try:
        ast = sqlglot.parse_one(raw_sql, read=dialect)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse SQL expression: {exc}") from exc

    target_col = column_name.strip().lower()

    # Parse default value expression
    clean_default = default_value.strip()
    try:
        default_expr = sqlglot.parse_one(clean_default, read=dialect)
        if default_expr is None:
            default_expr = exp.Literal.string(clean_default)
    except Exception:
        default_expr = exp.Literal.string(clean_default)

    for select in ast.find_all(exp.Select):
        for expr in list(select.expressions):
            # Case 1: Direct column projection (e.g., SELECT column_name)
            if isinstance(expr, exp.Column) and expr.name.lower() == target_col:
                col_ref = exp.column(expr.name, table=expr.table) if expr.table else exp.column(expr.name)
                coalesce_expr = exp.func("COALESCE", col_ref, default_expr)
                alias_expr = exp.alias_(coalesce_expr, expr.name)
                expr.replace(alias_expr)

            # Case 2: Column with custom alias (e.g., SELECT column_name AS custom_alias)
            elif (
                isinstance(expr, exp.Alias)
                and isinstance(expr.this, exp.Column)
                and expr.this.name.lower() == target_col
            ):
                col = expr.this
                col_ref = exp.column(col.name, table=col.table) if col.table else exp.column(col.name)
                coalesce_expr = exp.func("COALESCE", col_ref, default_expr)
                expr.set("this", coalesce_expr)

            # Case 3: Column already in COALESCE with custom alias
            elif (
                isinstance(expr, exp.Alias)
                and isinstance(expr.this, (exp.Func, exp.Anonymous))
                and expr.this.name.lower() == "coalesce"
                and any(
                    isinstance(arg, exp.Column) and arg.name.lower() == target_col
                    for arg in expr.this.expressions
                )
            ):
                if len(expr.this.expressions) > 1:
                    expr.this.expressions[1] = default_expr
                else:
                    expr.this.expressions.append(default_expr)

            # Case 4: Column already in CAST with custom alias (e.g. SELECT CAST(col AS ...) AS alias)
            elif (
                isinstance(expr, exp.Alias)
                and isinstance(expr.this, exp.Cast)
                and isinstance(expr.this.this, exp.Column)
                and expr.this.this.name.lower() == target_col
            ):
                coalesce_expr = exp.func("COALESCE", expr.this.copy(), default_expr)
                expr.set("this", coalesce_expr)

            # Case 5: Column already in CAST without alias (e.g. SELECT CAST(col AS ...))
            elif (
                isinstance(expr, exp.Cast)
                and isinstance(expr.this, exp.Column)
                and expr.this.name.lower() == target_col
            ):
                coalesce_expr = exp.func("COALESCE", expr.copy(), default_expr)
                alias_expr = exp.alias_(coalesce_expr, expr.this.name)
                expr.replace(alias_expr)

    return ast.sql(dialect=dialect), True


def repair_model_identifier_rename(
    sql: str,
    old_model_name: str,
    new_model_name: str,
    dialect: str = "postgres",
    **kwargs: Any,
) -> Tuple[str, bool]:
    """Repair Failure Class 5 (Model/Table Identifier Drift) via AST transformation.

    Rewrites table/view references in FROM and JOIN clauses as well as `ref('old_model')`
    dbt macro calls to point to `new_model_name`.

    Args:
        sql: Original SQL query to repair.
        old_model_name: Name of drifted model or table identifier.
        new_model_name: Replacement model or table identifier.
        dialect: SQL dialect for AST parsing and generation (default: "postgres").
        **kwargs: Optional fallback arguments (e.g. sql_query).

    Returns:
        Tuple[str, bool]: (patched_sql, requires_tier2_override) where:
            - patched_sql: Repaired SQL query string with updated model identifiers.
            - requires_tier2_override: False (safe identifier mapping).

    Raises:
        ParseError: If the SQL string contains invalid syntax.
    """
    raw_sql = kwargs.get("sql_query", sql)
    if not raw_sql or not raw_sql.strip():
        return raw_sql, False

    old_target = old_model_name.strip()
    new_target = new_model_name.strip()
    old_target_lower = old_target.lower()

    # Pre-process dbt jinja macro patterns if present: {{ ref('old_model') }} or {{ ref("old_model") }}
    escaped_old = re.escape(old_target)
    jinja_pattern = re.compile(
        r"(\{\{\s*ref\s*\(\s*['\"])(" + escaped_old + r")(['\"]\s*\)\s*\}\})",
        re.IGNORECASE,
    )
    patched_jinja_sql = jinja_pattern.sub(r"\g<1>" + new_target + r"\g<3>", raw_sql)

    # Also handle standalone ref('old_model') in non-bracketed macro context
    macro_ref_pattern = re.compile(
        r"(\bref\s*\(\s*['\"])(" + escaped_old + r")(['\"]\s*\))",
        re.IGNORECASE,
    )
    patched_jinja_sql = macro_ref_pattern.sub(r"\g<1>" + new_target + r"\g<3>", patched_jinja_sql)

    try:
        ast = sqlglot.parse_one(patched_jinja_sql, read=dialect)
    except ParseError:
        if "{{" in patched_jinja_sql:
            return patched_jinja_sql, False
        raise
    except Exception as exc:
        if "{{" in patched_jinja_sql:
            return patched_jinja_sql, False
        raise ParseError(f"Failed to parse SQL expression: {exc}") from exc

    # 1. Update exp.Table references in FROM and JOIN clauses
    for table in ast.find_all(exp.Table):
        # Case A: Standard table identifier: FROM old_model or JOIN raw.old_model
        if table.name.lower() == old_target_lower:
            table.set("this", exp.to_identifier(new_target))

        # Case B: Table with func call: FROM ref('old_model')
        elif isinstance(table.this, (exp.Func, exp.Anonymous)) and table.this.name.lower() == "ref":
            for arg in table.this.expressions:
                if isinstance(arg, exp.Literal) and str(arg.this).lower() == old_target_lower:
                    arg.set("this", new_target)
                elif isinstance(arg, exp.Var) and arg.name.lower() == old_target_lower:
                    arg.set("this", new_target)

    # 2. Also check any standalone ref('old_model') function expressions in AST
    for func in ast.find_all(exp.Anonymous, exp.Func):
        if func.name.lower() == "ref":
            for arg in func.expressions:
                if isinstance(arg, exp.Literal) and str(arg.this).lower() == old_target_lower:
                    arg.set("this", new_target)
                elif isinstance(arg, exp.Var) and arg.name.lower() == old_target_lower:
                    arg.set("this", new_target)

    # 3. Update column table qualifiers if table had no explicit alias (e.g. old_model.id)
    for col in ast.find_all(exp.Column):
        if col.table and col.table.lower() == old_target_lower:
            col.set("table", exp.to_identifier(new_target))

    return ast.sql(dialect=dialect), False



