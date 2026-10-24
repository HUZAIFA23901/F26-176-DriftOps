"""Unit test suite for Failure Class 2: Type Shift Repair Synthesizer.

Verifies:
1. Widening cast injection (e.g., INT -> BIGINT, VARCHAR(50) -> VARCHAR(250)) with is_narrowing == False.
2. Narrowing cast injection (e.g., VARCHAR -> INTEGER, FLOAT -> INT) with is_narrowing == True.
3. Preservation of existing column aliases, table qualifiers, and non-projection clauses.
4. Update of existing CAST expressions.
5. Strict AST validity of generated patched SQL using sqlglot.parse_one.
6. Clean module re-export from src.compiler.

Project ID: F26-176-DriftOps
Module: tests/test_class2_type_shift.py
"""

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from src.compiler import is_narrowing_type_cast, repair_type_shift


# ==============================================================================
# 1. Widening Cast Injection Tests (is_narrowing == False)
# ==============================================================================

def test_widening_cast_int_to_bigint():
    """Verify widening cast from INT to BIGINT returns is_narrowing == False and valid AST."""
    original_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="INT",
        target_type="BIGINT",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert "CAST(customer_phone AS BIGINT) AS customer_phone" in patched_sql

    # AST Validity Check
    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    casts = list(ast.find_all(exp.Cast))
    assert len(casts) == 1
    assert casts[0].this.name == "customer_phone"
    assert casts[0].to.this == exp.DataType.Type.BIGINT


def test_widening_varchar_length_increase():
    """Verify widening VARCHAR(50) to VARCHAR(250) returns is_narrowing == False."""
    original_sql = "SELECT id, notes FROM raw.raw_customers"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="notes",
        source_type="VARCHAR(50)",
        target_type="VARCHAR(250)",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert "CAST(notes AS VARCHAR(250)) AS notes" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    casts = list(ast.find_all(exp.Cast))
    assert len(casts) == 1
    assert casts[0].this.name == "notes"


def test_widening_smallint_to_integer():
    """Verify widening SMALLINT to INTEGER returns is_narrowing == False."""
    original_sql = "SELECT id, quantity FROM raw.raw_orders"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="quantity",
        source_type="SMALLINT",
        target_type="INTEGER",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert "CAST(quantity AS INT) AS quantity" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_widening_date_to_timestamp():
    """Verify widening DATE to TIMESTAMP returns is_narrowing == False."""
    original_sql = "SELECT id, created_date FROM raw.raw_orders"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="created_date",
        source_type="DATE",
        target_type="TIMESTAMP",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert "CAST(created_date AS TIMESTAMP) AS created_date" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 2. Narrowing Cast Injection Tests (is_narrowing == True)
# ==============================================================================

def test_narrowing_cast_varchar_to_integer():
    """Verify narrowing cast from VARCHAR to INTEGER returns is_narrowing == True."""
    original_sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="VARCHAR",
        target_type="INTEGER",
        dialect="postgres",
    )

    assert is_narrowing is True
    assert "CAST(customer_phone AS INT) AS customer_phone" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None
    casts = list(ast.find_all(exp.Cast))
    assert len(casts) == 1
    assert casts[0].this.name == "customer_phone"


def test_narrowing_cast_float_to_int():
    """Verify narrowing cast from FLOAT to INT returns is_narrowing == True."""
    original_sql = "SELECT id, score FROM raw.raw_scores"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="score",
        source_type="FLOAT",
        target_type="INT",
        dialect="postgres",
    )

    assert is_narrowing is True
    assert "CAST(score AS INT) AS score" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_narrowing_varchar_length_decrease():
    """Verify narrowing VARCHAR(250) to VARCHAR(50) returns is_narrowing == True."""
    original_sql = "SELECT id, notes FROM raw.raw_customers"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="notes",
        source_type="VARCHAR(250)",
        target_type="VARCHAR(50)",
        dialect="postgres",
    )

    assert is_narrowing is True
    assert "CAST(notes AS VARCHAR(50)) AS notes" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_narrowing_bigint_to_int():
    """Verify narrowing BIGINT to INT returns is_narrowing == True."""
    original_sql = "SELECT id, large_counter FROM raw.metrics"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="large_counter",
        source_type="BIGINT",
        target_type="INT",
        dialect="postgres",
    )

    assert is_narrowing is True
    assert "CAST(large_counter AS INT) AS large_counter" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 3. Projection Formatting & Alias Preservation Tests
# ==============================================================================

def test_type_shift_preserves_custom_alias():
    """Verify repair_type_shift retains custom alias when column is aliased."""
    original_sql = "SELECT id, customer_phone AS phone FROM raw.raw_customers"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="INT",
        target_type="BIGINT",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert patched_sql == "SELECT id, CAST(customer_phone AS BIGINT) AS phone FROM raw.raw_customers"

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_type_shift_with_table_qualification():
    """Verify repair_type_shift preserves table qualifier in projection."""
    original_sql = "SELECT c.id, c.customer_phone FROM raw.raw_customers AS c"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="INT",
        target_type="BIGINT",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert patched_sql == "SELECT c.id, CAST(c.customer_phone AS BIGINT) AS customer_phone FROM raw.raw_customers AS c"

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_type_shift_preserves_where_and_group_by():
    """Verify repair_type_shift only modifies SELECT projection, leaving predicates untouched."""
    original_sql = (
        "SELECT id, customer_phone FROM raw.raw_customers "
        "WHERE customer_phone IS NOT NULL GROUP BY id, customer_phone"
    )
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="INT",
        target_type="BIGINT",
        dialect="postgres",
    )

    assert is_narrowing is False
    expected_sql = (
        "SELECT id, CAST(customer_phone AS BIGINT) AS customer_phone FROM raw.raw_customers "
        "WHERE customer_phone IS NOT NULL GROUP BY id, customer_phone"
    )
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_type_shift_updates_existing_cast():
    """Verify repair_type_shift rewrites an existing CAST target type."""
    original_sql = "SELECT id, CAST(customer_phone AS INT) AS phone FROM raw.raw_customers"
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="INT",
        target_type="BIGINT",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert patched_sql == "SELECT id, CAST(customer_phone AS BIGINT) AS phone FROM raw.raw_customers"

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_type_shift_cte_support():
    """Verify repair_type_shift correctly injects casts into CTE and main query projections."""
    original_sql = (
        "WITH source AS (SELECT id, customer_phone FROM raw.raw_customers) "
        "SELECT id, customer_phone FROM source"
    )
    patched_sql, is_narrowing = repair_type_shift(
        sql=original_sql,
        column_name="customer_phone",
        source_type="INT",
        target_type="BIGINT",
        dialect="postgres",
    )

    assert is_narrowing is False
    assert "CAST(customer_phone AS BIGINT) AS customer_phone" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 4. Error Handling & Edge Cases
# ==============================================================================

def test_type_shift_invalid_sql_raises():
    """Verify that invalid SQL query raises ParseError."""
    with pytest.raises(ParseError):
        repair_type_shift(
            sql="SELECT FROM WHERE ;",
            column_name="customer_phone",
            source_type="INT",
            target_type="BIGINT",
        )


def test_type_shift_empty_sql():
    """Verify empty SQL string returns unchanged with narrowing flag evaluated."""
    sql, is_narrowing = repair_type_shift(
        sql="",
        column_name="col",
        source_type="INT",
        target_type="BIGINT",
    )
    assert sql == ""
    assert is_narrowing is False


def test_is_narrowing_type_cast_matrix():
    """Comprehensive check on type-narrowing classifier matrix."""
    # Lossless / Widening -> False
    assert is_narrowing_type_cast("INT", "BIGINT") is False
    assert is_narrowing_type_cast("SMALLINT", "INTEGER") is False
    assert is_narrowing_type_cast("VARCHAR(50)", "VARCHAR(250)") is False
    assert is_narrowing_type_cast("VARCHAR(50)", "TEXT") is False
    assert is_narrowing_type_cast("DATE", "TIMESTAMP") is False
    assert is_narrowing_type_cast("TIMESTAMP", "TIMESTAMPTZ") is False
    assert is_narrowing_type_cast("NUMERIC(10, 2)", "NUMERIC(12, 2)") is False
    assert is_narrowing_type_cast("INT", "INT") is False

    # Narrowing / Lossy -> True
    assert is_narrowing_type_cast("VARCHAR", "INTEGER") is True
    assert is_narrowing_type_cast("FLOAT", "INT") is True
    assert is_narrowing_type_cast("BIGINT", "INT") is True
    assert is_narrowing_type_cast("VARCHAR(250)", "VARCHAR(50)") is True
    assert is_narrowing_type_cast("TEXT", "VARCHAR(50)") is True
    assert is_narrowing_type_cast("TIMESTAMP", "DATE") is True
    assert is_narrowing_type_cast("NUMERIC(12, 2)", "NUMERIC(10, 2)") is True
    assert is_narrowing_type_cast("NUMERIC", "NUMERIC(10, 2)") is True
