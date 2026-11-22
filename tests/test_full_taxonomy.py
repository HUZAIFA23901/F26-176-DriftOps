"""Integration test suite for the Unified Repair Synthesizer Router across the full 7-Class Taxonomy.

Verifies:
1. Unified dispatch for all repairable failure classes (Classes 1 through 5).
2. Proper tier routing propagation:
   - Class 1 (Rename) -> Tier 1 safe (requires_tier2 == False).
   - Class 2 (Type Shift) -> Tier 1 for widening, Tier 2 for narrowing.
   - Class 3 (Add/Drop) -> Tier 2 for drops, Tier 1 for pure additions.
   - Class 4 (Nullability) -> Tier 2 override (requires_tier2 == True).
   - Class 5 (Model Rename) -> Tier 1 safe (requires_tier2 == False).
   - Classes 6 & 7 (Semantic / Assertion) -> Strict Fallback (requires_tier2 == True).
3. AST validity verification with sqlglot.parse_one for all repaired SQL queries.
4. Clean module re-export from src.compiler.

Project ID: F26-176-DriftOps
Module: tests/test_full_taxonomy.py
"""

import pytest
import sqlglot
from sqlglot import exp

from src.compiler import synthesize_repair_patch


# ==============================================================================
# 1. Class 1: Column Rename
# ==============================================================================

def test_synthesize_class1_column_rename():
    """Verify Class 1 dispatch rewrites column identifier and routes to Tier 1."""
    sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class1_rename",
        payload={
            "old_column": "customer_phone",
            "new_column": "contact_number",
        },
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, contact_number AS customer_phone FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_synthesize_class1_alternate_naming():
    """Verify Class 1 dispatch with 'column_rename' synonym."""
    sql = "SELECT id, customer_phone AS phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="column_rename",
        payload={
            "old_name": "customer_phone",
            "new_name": "contact_number",
        },
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, contact_number AS phone FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 2. Class 2: Type Shifts (Widening vs Narrowing)
# ==============================================================================

def test_synthesize_class2_widening_cast():
    """Verify Class 2 widening cast routes to Tier 1 (requires_tier2 == False)."""
    sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class2_type_shift",
        payload={
            "column_name": "customer_phone",
            "source_type": "INT",
            "target_type": "BIGINT",
        },
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, CAST(customer_phone AS BIGINT) AS customer_phone FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_synthesize_class2_narrowing_cast():
    """Verify Class 2 narrowing cast propagates requires_tier2 == True."""
    sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="type_shift",
        payload={
            "column_name": "customer_phone",
            "source_type": "VARCHAR",
            "target_type": "INTEGER",
        },
    )

    assert requires_tier2 is True
    assert "CAST(customer_phone AS INT) AS customer_phone" in patched_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 3. Class 3: Column Additions & Drops
# ==============================================================================

def test_synthesize_class3_dropped_column():
    """Verify Class 3 dropped column injects NULL placeholder and sets requires_tier2 == True."""
    sql = "SELECT id, customer_phone, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class3_column_drop_add",
        payload={
            "dropped_columns": ["customer_phone"],
        },
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, NULL AS customer_phone, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_synthesize_class3_added_column():
    """Verify Class 3 pure column addition appends to projection and stays Tier 1."""
    sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="column_drop_add",
        payload={
            "added_columns": ["email"],
        },
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, customer_phone, email FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_synthesize_class3_combined_drop_and_add():
    """Verify Class 3 combined drop and add sets requires_tier2 == True."""
    sql = "SELECT id, old_col, email FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="column_drop",
        payload={
            "dropped_columns": ["old_col"],
            "added_columns": ["new_col"],
        },
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, NULL AS old_col, email, new_col FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 4. Class 4: Nullability Shifts
# ==============================================================================

def test_synthesize_class4_nullability_default():
    """Verify Class 4 wraps projection in COALESCE and enforces requires_tier2 == True."""
    sql = "SELECT id, customer_phone FROM raw.raw_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class4_nullability",
        payload={
            "column_name": "customer_phone",
            "default_value": "''",
        },
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, COALESCE(customer_phone, '') AS customer_phone FROM raw.raw_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_synthesize_class4_nullability_numeric():
    """Verify Class 4 wraps numeric projection in COALESCE with numeric literal."""
    sql = "SELECT id, total_amount FROM raw.orders"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="nullability_shift",
        payload={
            "column_name": "total_amount",
            "default_value": "0",
        },
    )

    assert requires_tier2 is True
    expected_sql = "SELECT id, COALESCE(total_amount, 0) AS total_amount FROM raw.orders"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


# ==============================================================================
# 5. Class 5: Model/Table Identifier Drift
# ==============================================================================

def test_synthesize_class5_model_rename():
    """Verify Class 5 renames table identifier in FROM clause and routes to Tier 1."""
    sql = "SELECT id, name FROM old_customers"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class5_model_rename",
        payload={
            "old_model_name": "old_customers",
            "new_model_name": "new_customers",
        },
    )

    assert requires_tier2 is False
    expected_sql = "SELECT id, name FROM new_customers"
    assert patched_sql == expected_sql

    ast = sqlglot.parse_one(patched_sql, read="postgres")
    assert ast is not None


def test_synthesize_class5_dbt_jinja_macro():
    """Verify Class 5 rewrites Jinja ref('old_model') call cleanly."""
    sql = "SELECT id, name FROM {{ ref('old_customers') }}"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="model_rename",
        payload={
            "old_model_name": "old_customers",
            "new_model_name": "new_customers",
        },
    )

    assert requires_tier2 is False
    assert "{{ ref('new_customers') }}" in patched_sql
    assert "old_customers" not in patched_sql


# ==============================================================================
# 6. Fallback & Unsupported Classes (Classes 6 & 7)
# ==============================================================================

def test_synthesize_class6_semantic_shift_fallback():
    """Verify Class 6 (Semantic Business Logic Shift) falls back with requires_tier2 == True."""
    sql = "SELECT id, status FROM raw.orders WHERE status = 'COMPLETED'"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class6_semantic_shift",
        payload={},
    )

    assert requires_tier2 is True
    assert patched_sql == sql  # Unchanged fallback


def test_synthesize_class7_grain_violation_fallback():
    """Verify Class 7 (Grain & PK Assertion Violation) falls back with requires_tier2 == True."""
    sql = "SELECT order_id, line_item_id FROM raw.order_items"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="class7_grain_violation",
        payload={},
    )

    assert requires_tier2 is True
    assert patched_sql == sql  # Unchanged fallback


def test_synthesize_unknown_failure_class_fallback():
    """Verify arbitrary unknown failure class falls back safely with requires_tier2 == True."""
    sql = "SELECT 1"
    patched_sql, requires_tier2 = synthesize_repair_patch(
        broken_sql=sql,
        failure_class="unsupported_exotic_drift",
        payload={},
    )

    assert requires_tier2 is True
    assert patched_sql == sql


# ==============================================================================
# 7. Multi-Class Compound Pipeline Invariant
# ==============================================================================

def test_synthesize_multi_class_compound_pipeline():
    """Verify sequentially applying repairs from multiple failure classes produces valid AST."""
    initial_sql = "SELECT id, phone, amount FROM old_orders"

    # Step 1: Model rename (Class 5)
    sql_step1, tier2_step1 = synthesize_repair_patch(
        broken_sql=initial_sql,
        failure_class="model_rename",
        payload={"old_model_name": "old_orders", "new_model_name": "stg_orders"},
    )
    assert tier2_step1 is False

    # Step 2: Column rename (Class 1)
    sql_step2, tier2_step2 = synthesize_repair_patch(
        broken_sql=sql_step1,
        failure_class="column_rename",
        payload={"old_column": "phone", "new_column": "contact_phone"},
    )
    assert tier2_step2 is False

    # Step 3: Type shift (Class 2 widening)
    sql_step3, tier2_step3 = synthesize_repair_patch(
        broken_sql=sql_step2,
        failure_class="type_shift",
        payload={"column_name": "amount", "source_type": "INT", "target_type": "BIGINT"},
    )
    assert tier2_step3 is False

    # Step 4: Nullability shift (Class 4)
    final_sql, tier2_step4 = synthesize_repair_patch(
        broken_sql=sql_step3,
        failure_class="nullability_shift",
        payload={"column_name": "id", "default_value": "0"},
    )
    assert tier2_step4 is True

    # Validate final compound AST
    ast = sqlglot.parse_one(final_sql, read="postgres")
    assert ast is not None
    assert "stg_orders" in final_sql
    assert "contact_phone AS phone" in final_sql
    assert "CAST(amount AS BIGINT) AS amount" in final_sql
    assert "COALESCE(id, 0) AS id" in final_sql
