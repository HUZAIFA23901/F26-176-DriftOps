"""Unit test suite for Failure Class 3: Column Additions & Drops Repair Synthesizer.

Verifies:
1. Upstream dropped columns are safely replaced with `NULL AS column_name` in the SELECT list.
2. Dropping columns always triggers mandatory human review (`requires_tier2_override == True`).
3. Upstream added columns are cleanly appended into the SELECT projection list.
4. Support for safe column deletion (`remove_dropped=True`).
5. Deduplication and predicate isolation (added columns not injected into WHERE subqueries).
6. AST validity verification with `sqlglot.parse_one`.
7. Clean module re-export from `src.compiler`.

Project ID: F26-176-DriftOps
Module: tests/test_class3_column_add_drop.py
"""

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from src.compiler import repair_column_drop_or_add


# ==============================================================================
# 1. Dropped Column Tests (NULL Injections & Tier 2 Overrides)
# ==============================================================================

def test_dropped_column_replaced_with_null_and_flags_tier2():
    """Verify missing column is replaced with NULL AS <col> and requires_tier2_override == True."""
    original_sql = "SELECT id, customer_phone, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["customer_phone"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    assert "NULL AS customer_phone" in patched_sql

    expected_sql = "SELECT id, NULL AS customer_phone, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    # AST Validity Check
    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    aliases = list(ast.find_all(exp.Alias))
    null_aliases = [a for a in aliases if isinstance(a.this, exp.Null)]
    assert len(null_aliases) == 1
    assert null_aliases[0].alias == "customer_phone"


def test_dropped_column_with_custom_alias():
    """Verify dropped column preserves existing projection alias with NULL expression."""
    original_sql = "SELECT id, customer_phone AS phone, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["customer_phone"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, NULL AS phone, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_dropped_column_with_table_qualification():
    """Verify table qualification is cleanly replaced with NULL AS column_name."""
    original_sql = "SELECT c.id, c.customer_phone, c.email FROM raw.raw_customers AS c"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["customer_phone"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT c.id, NULL AS customer_phone, c.email FROM raw.raw_customers AS c"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_dropped_column_with_existing_cast():
    """Verify dropped column inside existing CAST expression is replaced with NULL AS alias."""
    original_sql = "SELECT id, CAST(customer_phone AS VARCHAR) AS phone, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["customer_phone"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, NULL AS phone, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_dropped_column_safe_remove_option():
    """Verify remove_dropped=True completely removes column from SELECT projection."""
    original_sql = "SELECT id, customer_phone, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["customer_phone"],
        remove_dropped=True,
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    assert "customer_phone" not in patched_sql


def test_multiple_dropped_columns():
    """Verify multiple dropped columns are each replaced with NULL placeholders."""
    original_sql = "SELECT id, phone, fax, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["phone", "fax"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, NULL AS phone, NULL AS fax, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 2. Added Column Tests (Projection Appends)
# ==============================================================================

def test_added_column_appended_to_select():
    """Verify newly detected upstream column is appended to the SELECT projection list."""
    original_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        added_columns=["email"],
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, customer_phone, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    cols = [c.name for c in ast.find_all(exp.Column)]
    assert "email" in cols


def test_multiple_added_columns():
    """Verify multiple added columns are appended in order to the SELECT list."""
    original_sql = "SELECT id FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        added_columns=["first_name", "last_name", "email"],
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, first_name, last_name, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_added_columns_avoids_duplicates():
    """Verify existing projection columns are not re-appended when listed in added_columns."""
    original_sql = "SELECT id, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        added_columns=["email", "status"],
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, email, status FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_added_column_does_not_pollute_subquery_in_where():
    """Verify added columns are appended only to the main query and not scalar/WHERE subqueries."""
    original_sql = "SELECT id FROM raw.raw_customers WHERE id IN (SELECT customer_id FROM orders)"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        added_columns=["status"],
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, status FROM raw.raw_customers WHERE id IN (SELECT customer_id FROM orders)"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 3. Combined & Complex Query Transformations
# ==============================================================================

def test_simultaneous_drop_and_add():
    """Verify simultaneous drop and add transforms query and correctly flags Tier 2 override."""
    original_sql = "SELECT id, old_feature, count FROM raw.metrics"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["old_feature"],
        added_columns=["new_feature"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, NULL AS old_feature, count, new_feature FROM raw.metrics"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_dropped_column_preserves_predicates_and_grouping():
    """Verify dropped column in SELECT list does not rewrite WHERE or GROUP BY clauses."""
    original_sql = (
        "SELECT id, customer_phone FROM raw.raw_customers "
        "WHERE customer_phone IS NOT NULL GROUP BY id, customer_phone"
    )
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=["customer_phone"],
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = (
        "SELECT id, NULL AS customer_phone FROM raw.raw_customers "
        "WHERE customer_phone IS NOT NULL GROUP BY id, customer_phone"
    )
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 4. Error Handling & Edge Cases
# ==============================================================================

def test_invalid_sql_syntax_raises_parse_error():
    """Verify invalid SQL query raises ParseError."""
    with pytest.raises(ParseError):
        repair_column_drop_or_add(
            sql="SELECT FROM WHERE ;",
            dropped_columns=["customer_phone"],
        )


def test_empty_sql_query():
    """Verify empty SQL string returns unchanged with appropriate tier 2 flag."""
    sql, requires_tier2 = repair_column_drop_or_add(
        sql="",
        dropped_columns=["customer_phone"],
    )
    assert sql == ""
    assert requires_tier2 is True

    sql_no_drop, requires_tier2_no_drop = repair_column_drop_or_add(
        sql="",
        added_columns=["customer_phone"],
    )
    assert sql_no_drop == ""
    assert requires_tier2_no_drop is False


def test_no_op_when_lists_empty():
    """Verify original SQL is preserved when no columns dropped or added."""
    original_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_column_drop_or_add(
        sql=original_sql,
        dropped_columns=[],
        added_columns=[],
        dialect="postgres",
    )
    assert patched_sql == original_sql
    assert requires_tier2 is False
