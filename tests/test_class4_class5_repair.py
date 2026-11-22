"""Unit test suite for Failure Class 4 (Nullability Shifts) and Failure Class 5 (Model/Table Identifier Drift).

Verifies:
1. Class 4: When NOT NULL shifts are detected, wraps column in `COALESCE(column_name, <default_value>) AS column_name`.
2. Class 4: Always flags `requires_tier2_override == True` due to nullability constraint impact.
3. Class 5: Rewrites table/view references in FROM and JOIN clauses to point to the new model name.
4. Class 5: Rewrites `ref('old_model')` and Jinja `{{ ref('old_model') }}` macro calls.
5. Class 5: Preserves schema qualifications (e.g. `raw.old_model` -> `raw.new_model`) and table aliases.
6. AST validity verification using `sqlglot.parse_one`.

Project ID: F26-176-DriftOps
Module: tests/test_class4_class5_repair.py
"""

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from src.compiler import repair_model_identifier_rename, repair_nullability_shift


# ==============================================================================
# 1. Failure Class 4: Nullability Shifts (COALESCE Injections & Tier 2 Overrides)
# ==============================================================================

def test_nullability_shift_basic():
    """Verify missing NOT NULL constraint wraps projection in COALESCE with default value."""
    original_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_nullability_shift(
        sql=original_sql,
        column_name="customer_phone",
        default_value="''",
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, COALESCE(customer_phone, '') AS customer_phone FROM raw.raw_customers"
    assert patched_sql == expected_sql

    # AST Validity Check
    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    coalesces = list(ast.find_all(exp.Coalesce))
    assert len(coalesces) == 1
    assert coalesces[0].this.name == "customer_phone"


def test_nullability_shift_numeric_default():
    """Verify numeric default value in COALESCE wrapping."""
    original_sql = "SELECT id, score FROM raw.raw_scores"
    patched_sql, requires_tier2 = repair_nullability_shift(
        sql=original_sql,
        column_name="score",
        default_value="0",
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, COALESCE(score, 0) AS score FROM raw.raw_scores"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_nullability_shift_custom_string_default():
    """Verify custom string literal default value in COALESCE wrapping."""
    original_sql = "SELECT id, status FROM raw.raw_orders"
    patched_sql, requires_tier2 = repair_nullability_shift(
        sql=original_sql,
        column_name="status",
        default_value="'UNKNOWN'",
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, COALESCE(status, 'UNKNOWN') AS status FROM raw.raw_orders"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_nullability_shift_with_custom_alias():
    """Verify COALESCE preserves user-specified projection alias."""
    original_sql = "SELECT id, customer_phone AS phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = repair_nullability_shift(
        sql=original_sql,
        column_name="customer_phone",
        default_value="''",
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, COALESCE(customer_phone, '') AS phone FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_nullability_shift_with_table_prefix():
    """Verify COALESCE preserves table prefix in projection reference."""
    original_sql = "SELECT c.id, c.customer_phone FROM raw.raw_customers AS c"
    patched_sql, requires_tier2 = repair_nullability_shift(
        sql=original_sql,
        column_name="customer_phone",
        default_value="''",
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = "SELECT c.id, COALESCE(c.customer_phone, '') AS customer_phone FROM raw.raw_customers AS c"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_nullability_shift_preserves_predicates():
    """Verify COALESCE injection only targets SELECT projection list and not WHERE clauses."""
    original_sql = "SELECT id, customer_phone FROM raw.raw_customers WHERE customer_phone IS NOT NULL"
    patched_sql, requires_tier2 = repair_nullability_shift(
        sql=original_sql,
        column_name="customer_phone",
        default_value="''",
        dialect="postgres",
    )

    assert requires_tier2 is True
    expected_sql = (
        "SELECT id, COALESCE(customer_phone, '') AS customer_phone "
        "FROM raw.raw_customers WHERE customer_phone IS NOT NULL"
    )
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_nullability_shift_invalid_sql_raises():
    """Verify invalid SQL raises ParseError."""
    with pytest.raises(ParseError):
        repair_nullability_shift(
            sql="SELECT FROM WHERE ;",
            column_name="customer_phone",
        )


def test_nullability_shift_empty_sql():
    """Verify empty query returns unchanged with True tier 2 flag."""
    sql, requires_tier2 = repair_nullability_shift(sql="", column_name="col")
    assert sql == ""
    assert requires_tier2 is True


# ==============================================================================
# 2. Failure Class 5: Model/Table Identifier Drift
# ==============================================================================

def test_model_identifier_rename_from_clause():
    """Verify FROM clause target is renamed cleanly."""
    original_sql = "SELECT id, name FROM old_customers"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, name FROM new_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    tables = [t.name for t in ast.find_all(exp.Table)]
    assert "new_customers" in tables
    assert "old_customers" not in tables


def test_model_identifier_rename_with_schema_qualification():
    """Verify schema qualification (e.g. raw.old_customers) is preserved upon rename."""
    original_sql = "SELECT id, name FROM raw.old_customers"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, name FROM raw.new_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_model_identifier_rename_with_table_alias():
    """Verify table alias (e.g. old_customers AS o) is preserved upon rename."""
    original_sql = "SELECT o.id, o.name FROM old_customers AS o"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT o.id, o.name FROM new_customers AS o"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_model_identifier_rename_in_join_clause():
    """Verify JOIN target table is rewritten cleanly to the new model name."""
    original_sql = "SELECT a.id, b.status FROM orders AS a JOIN old_customers AS b ON a.cust_id = b.id"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT a.id, b.status FROM orders AS a JOIN new_customers AS b ON a.cust_id = b.id"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_model_identifier_rename_table_prefixed_columns():
    """Verify columns qualified with table name without alias are updated."""
    original_sql = "SELECT old_customers.id, old_customers.name FROM old_customers"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "SELECT new_customers.id, new_customers.name FROM new_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_model_identifier_rename_dbt_ref_call():
    """Verify standard ref('old_model') call is updated."""
    original_sql = "SELECT * FROM ref('old_customers')"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    assert "new_customers" in patched_sql
    assert "old_customers" not in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_model_identifier_rename_dbt_jinja_macro():
    """Verify Jinja macro {{ ref('old_model') }} is rewritten cleanly."""
    original_sql = "SELECT id, name FROM {{ ref('old_customers') }}"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    assert "{{ ref('new_customers') }}" in patched_sql
    assert "old_customers" not in patched_sql


def test_model_identifier_rename_cte_query():
    """Verify CTE source table reference is updated cleanly."""
    original_sql = "WITH source AS (SELECT * FROM old_customers) SELECT * FROM source"
    patched_sql, requires_tier2 = repair_model_identifier_rename(
        sql=original_sql,
        old_model_name="old_customers",
        new_model_name="new_customers",
        dialect="postgres",
    )

    assert requires_tier2 is False
    expected_sql = "WITH source AS (SELECT * FROM new_customers) SELECT * FROM source"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_model_identifier_rename_invalid_sql_raises():
    """Verify invalid non-Jinja SQL syntax raises ParseError."""
    with pytest.raises(ParseError):
        repair_model_identifier_rename(
            sql="SELECT FROM WHERE ;",
            old_model_name="old_customers",
            new_model_name="new_customers",
        )


def test_model_identifier_rename_empty_sql():
    """Verify empty query returns unchanged with False flag."""
    sql, requires_tier2 = repair_model_identifier_rename(
        sql="",
        old_model_name="old_customers",
        new_model_name="new_customers",
    )
    assert sql == ""
    assert requires_tier2 is False
