"""Unit test suite for DriftOps AST compiler components.

Tests:
1. AST parsing and column extraction (src/compiler/ast_parser.py)
2. Safe column renaming via AST transforms (src/compiler/ast_parser.py)
3. Structural confidence calculation C_AST (src/compiler/cast_calculator.py)
4. AST safety rule validation (src/compiler/rules.py)

Project ID: F26-176-DriftOps
"""

import pytest
from sqlglot.errors import ParseError

from src.compiler.ast_parser import extract_columns, repair_column_rename
from src.compiler.cast_calculator import (
    calculate_name_similarity,
    calculate_type_compatibility,
    calculate_catalog_invariant,
    compute_c_ast,
)
from src.compiler.rules import validate_ast_rules


# ==============================================================================
# 1. AST Parser & Column Extraction Tests
# ==============================================================================

def test_extract_columns_basic():
    """Verify that extract_columns correctly extracts column names."""
    sql = "SELECT id, customer_phone, email, created_at FROM raw.raw_customers"
    cols = extract_columns(sql)
    assert "customer_phone" in cols
    assert "email" in cols
    assert "id" in cols
    assert "created_at" in cols


def test_extract_columns_with_aliases():
    """Verify column extraction handles aliases correctly."""
    sql = "SELECT id AS customer_id, customer_phone AS phone FROM raw.raw_customers"
    cols = extract_columns(sql)
    assert "id" in cols
    assert "customer_phone" in cols


def test_extract_columns_invalid_syntax():
    """Verify that invalid SQL syntax raises ParseError."""
    with pytest.raises(ParseError):
        extract_columns("SELECT FROM WHERE ;")


# ==============================================================================
# 2. Repair Column Rename Tests
# ==============================================================================

def test_repair_column_rename_basic():
    """Verify repair_column_rename rewrites SELECT customer_phone to contact_number AS customer_phone."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    expected_sql = "SELECT contact_number AS customer_phone FROM raw.raw_customers"

    repaired_sql = repair_column_rename(
        sql_query=original_sql,
        old_column="customer_phone",
        new_column="contact_number",
        dialect="postgres",
    )
    assert repaired_sql == expected_sql


def test_repair_column_rename_with_existing_alias():
    """Verify repair_column_rename preserves custom alias if already present."""
    original_sql = "SELECT customer_phone AS phone FROM raw.raw_customers"
    expected_sql = "SELECT contact_number AS phone FROM raw.raw_customers"

    repaired_sql = repair_column_rename(
        sql_query=original_sql,
        old_column="customer_phone",
        new_column="contact_number",
        dialect="postgres",
    )
    assert repaired_sql == expected_sql


def test_repair_column_rename_in_where_clause():
    """Verify repair_column_rename replaces column inside WHERE clause."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers WHERE customer_phone IS NOT NULL"
    expected_sql = "SELECT contact_number AS customer_phone FROM raw.raw_customers WHERE contact_number IS NOT NULL"

    repaired_sql = repair_column_rename(
        sql_query=original_sql,
        old_column="customer_phone",
        new_column="contact_number",
        dialect="postgres",
    )
    assert repaired_sql == expected_sql


# ==============================================================================
# 3. AST Confidence Calculator (C_AST) Tests
# ==============================================================================

def test_calculate_name_similarity_identical():
    """Verify identical names yield 1.0 similarity."""
    assert calculate_name_similarity("customer_phone", "customer_phone") == 1.0


def test_calculate_name_similarity_partial():
    """Verify partial string matches yield expected Jaro-Winkler range."""
    score = calculate_name_similarity("customer_phone", "contact_number")
    assert 0.0 < score < 1.0


def test_calculate_name_similarity_empty():
    """Verify empty inputs yield 0.0."""
    assert calculate_name_similarity("", "customer_phone") == 0.0
    assert calculate_name_similarity("customer_phone", "") == 0.0


def test_calculate_type_compatibility():
    """Verify type compatibility evaluation."""
    # Lossless / identical
    assert calculate_type_compatibility("VARCHAR", "VARCHAR") == 1.0
    assert calculate_type_compatibility("INT", "BIGINT") == 1.0

    # Explicit cast (e.g. VARCHAR to BIGINT)
    assert calculate_type_compatibility("VARCHAR", "BIGINT") == 0.5
    assert calculate_type_compatibility("BIGINT", "VARCHAR") == 0.5

    # Incompatible
    assert calculate_type_compatibility("BYTEA", "TIMESTAMP") == 0.0
    assert calculate_type_compatibility("UUID", "INTEGER") == 0.0


def test_calculate_catalog_invariant():
    """Verify catalog invariant returns 1.0 when matched, 0.0 otherwise."""
    assert calculate_catalog_invariant(True) == 1.0
    assert calculate_catalog_invariant(False) == 0.0


def test_compute_c_ast_identical_yields_one():
    """Verify compute_c_ast returns 1.0 for identical name, type, and catalog match."""
    score = compute_c_ast(
        old_name="customer_phone",
        new_name="customer_phone",
        source_type="VARCHAR",
        target_type="VARCHAR",
        attnum_match=True,
    )
    assert score == 1.0


def test_compute_c_ast_weighted_calculation():
    """Verify exact weighted sum: 0.4 * S_name + 0.4 * T_compat + 0.2 * I_catalog."""
    # When names match (1.0), types require explicit cast (0.5), catalog does not match (0.0):
    # C_AST = 0.4 * 1.0 + 0.4 * 0.5 + 0.2 * 0.0 = 0.4 + 0.2 = 0.6
    score = compute_c_ast(
        old_name="customer_phone",
        new_name="customer_phone",
        source_type="VARCHAR",
        target_type="BIGINT",
        attnum_match=False,
    )
    assert score == 0.60


# ==============================================================================
# 4. AST Rule Validator Tests
# ==============================================================================

def test_validate_ast_rules_valid_rename():
    """Verify validate_ast_rules returns True for valid column rename with alias."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    patched_sql = "SELECT contact_number AS customer_phone FROM raw.raw_customers"
    assert validate_ast_rules(original_sql, patched_sql) is True


def test_validate_ast_rules_valid_cast():
    """Verify validate_ast_rules returns True for valid explicit cast."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    patched_sql = "SELECT CAST(contact_number AS VARCHAR) AS customer_phone FROM raw.raw_customers"
    assert validate_ast_rules(original_sql, patched_sql) is True


def test_validate_ast_rules_rejects_injected_join():
    """Verify validate_ast_rules rejects patches that add JOIN clauses."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    patched_sql = "SELECT contact_number AS customer_phone FROM raw.raw_customers JOIN raw.other ON 1 = 1"
    assert validate_ast_rules(original_sql, patched_sql) is False


def test_validate_ast_rules_rejects_injected_where():
    """Verify validate_ast_rules rejects patches that inject a WHERE clause."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    patched_sql = "SELECT contact_number AS customer_phone FROM raw.raw_customers WHERE active = TRUE"
    assert validate_ast_rules(original_sql, patched_sql) is False


def test_validate_ast_rules_rejects_altered_where():
    """Verify validate_ast_rules rejects patches that alter WHERE clause logic."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers WHERE id > 10"
    patched_sql = "SELECT contact_number AS customer_phone FROM raw.raw_customers WHERE id > 10 AND status = 'A'"
    assert validate_ast_rules(original_sql, patched_sql) is False


def test_validate_ast_rules_rejects_injected_columns():
    """Verify validate_ast_rules rejects patches that add extra projection columns."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    patched_sql = "SELECT contact_number AS customer_phone, 42 AS secret FROM raw.raw_customers"
    assert validate_ast_rules(original_sql, patched_sql) is False


def test_validate_ast_rules_rejects_injected_operations():
    """Verify validate_ast_rules rejects arbitrary arithmetic expressions."""
    original_sql = "SELECT customer_phone FROM raw.raw_customers"
    patched_sql = "SELECT contact_number + 1 AS customer_phone FROM raw.raw_customers"
    assert validate_ast_rules(original_sql, patched_sql) is False
