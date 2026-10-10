"""AST parsing, traversal, and SQL query rewrite engine using sqlglot.

Project ID: F26-176-DriftOps
Module: src/compiler/ast_parser.py
"""

from typing import List
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


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
