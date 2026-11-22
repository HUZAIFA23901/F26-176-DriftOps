"""Unit Test Suite for Pre-flight Metadata Diff & Drift Detection Engine.

Tests:
1. Clean schema comparison returning empty list [].
2. Class 1: column_rename detection and payload formatting.
3. Class 2: type_shift detection and payload formatting.
4. Class 3: column_drop_add detection and payload formatting.
5. Class 4: nullability shift detection and payload formatting.
6. Class 5: model_rename detection and payload formatting.
7. Compound multi-class drift scenarios.
8. Live PostgreSQL schema extraction mock test.
9. Serialization and dictionary conversion helpers.

Project ID: F26-176-DriftOps
Module: tests/test_preflight_detector.py
"""

from unittest.mock import MagicMock, patch
import pytest

from src.detector import (
    ColumnSchema,
    DriftEvent,
    TableSchema,
    detect_schema_drift,
    fetch_live_pg_schema,
)


# ==============================================================================
# 1. Clean State Test
# ==============================================================================

def test_clean_schema_no_drift():
    """Verify clean identical schemas produce an empty drift events list."""
    baseline = {
        "raw_customers": TableSchema(
            table_name="raw_customers",
            columns={
                "id": ColumnSchema(name="id", data_type="integer", is_nullable=False),
                "customer_phone": ColumnSchema(name="customer_phone", data_type="varchar", is_nullable=True),
                "email": ColumnSchema(name="email", data_type="varchar", is_nullable=True),
            },
        )
    }

    live = {
        "raw_customers": TableSchema(
            table_name="raw_customers",
            columns={
                "id": ColumnSchema(name="id", data_type="integer", is_nullable=False),
                "customer_phone": ColumnSchema(name="customer_phone", data_type="varchar", is_nullable=True),
                "email": ColumnSchema(name="email", data_type="varchar", is_nullable=True),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert events == []


# ==============================================================================
# 2. Class 1: Column Rename Tests
# ==============================================================================

def test_detect_column_rename():
    """Verify Class 1 column_rename is detected when name similarity is high and types compatible."""
    baseline = {
        "raw_customers": TableSchema(
            table_name="raw_customers",
            columns={
                "id": ColumnSchema(name="id", data_type="integer", is_nullable=False),
                "customer_phone": ColumnSchema(name="customer_phone", data_type="varchar", is_nullable=True),
                "email": ColumnSchema(name="email", data_type="varchar", is_nullable=True),
            },
        )
    }

    live = {
        "raw_customers": TableSchema(
            table_name="raw_customers",
            columns={
                "id": ColumnSchema(name="id", data_type="integer", is_nullable=False),
                "cust_phone": ColumnSchema(name="cust_phone", data_type="varchar", is_nullable=True),
                "email": ColumnSchema(name="email", data_type="varchar", is_nullable=True),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert len(events) == 1
    event = events[0]

    assert event.table_name == "raw_customers"
    assert event.failure_class == "column_rename"
    assert event.payload["old_col"] == "customer_phone"
    assert event.payload["new_col"] == "cust_phone"
    assert event.confidence >= 0.70


# ==============================================================================
# 3. Class 2: Type Shift Tests
# ==============================================================================

def test_detect_type_shift():
    """Verify Class 2 type_shift is detected when column data types mutate."""
    baseline = {
        "orders": TableSchema(
            table_name="orders",
            columns={
                "order_id": ColumnSchema(name="order_id", data_type="integer", is_nullable=False),
                "amount": ColumnSchema(name="amount", data_type="integer", is_nullable=True),
            },
        )
    }

    live = {
        "orders": TableSchema(
            table_name="orders",
            columns={
                "order_id": ColumnSchema(name="order_id", data_type="integer", is_nullable=False),
                "amount": ColumnSchema(name="amount", data_type="bigint", is_nullable=True),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert len(events) == 1
    event = events[0]

    assert event.table_name == "orders"
    assert event.failure_class == "type_shift"
    assert event.payload["column_name"] == "amount"
    assert event.payload["source_type"] == "integer"
    assert event.payload["target_type"] == "bigint"
    assert event.confidence == 1.0


# ==============================================================================
# 4. Class 3: Column Drop and Add Tests
# ==============================================================================

def test_detect_column_drop_add():
    """Verify Class 3 column_drop_add is detected for non-rename additions and deletions."""
    baseline = {
        "products": TableSchema(
            table_name="products",
            columns={
                "product_id": ColumnSchema(name="product_id", data_type="integer", is_nullable=False),
                "name": ColumnSchema(name="name", data_type="varchar", is_nullable=False),
                "legacy_notes": ColumnSchema(name="legacy_notes", data_type="text", is_nullable=True),
            },
        )
    }

    live = {
        "products": TableSchema(
            table_name="products",
            columns={
                "product_id": ColumnSchema(name="product_id", data_type="integer", is_nullable=False),
                "name": ColumnSchema(name="name", data_type="varchar", is_nullable=False),
                "sku_code": ColumnSchema(name="sku_code", data_type="varchar", is_nullable=True),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert len(events) == 1
    event = events[0]

    assert event.table_name == "products"
    assert event.failure_class == "column_drop_add"
    assert "legacy_notes" in event.payload["dropped_columns"]
    assert "sku_code" in event.payload["added_columns"]
    assert event.confidence == 1.0


# ==============================================================================
# 5. Class 4: Nullability Shift Tests
# ==============================================================================

def test_detect_nullability_shift():
    """Verify Class 4 nullability is detected when a column becomes non-nullable."""
    baseline = {
        "users": TableSchema(
            table_name="users",
            columns={
                "id": ColumnSchema(name="id", data_type="integer", is_nullable=False),
                "email": ColumnSchema(name="email", data_type="varchar", is_nullable=True),
            },
        )
    }

    live = {
        "users": TableSchema(
            table_name="users",
            columns={
                "id": ColumnSchema(name="id", data_type="integer", is_nullable=False),
                "email": ColumnSchema(name="email", data_type="varchar", is_nullable=False),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert len(events) == 1
    event = events[0]

    assert event.table_name == "users"
    assert event.failure_class == "nullability"
    assert event.payload["column_name"] == "email"
    assert event.confidence == 1.0


def test_nullability_shift_ignored_when_loosened():
    """Verify changing is_nullable from False to True is not flagged as nullability restriction."""
    baseline = {
        "users": TableSchema(
            table_name="users",
            columns={
                "phone": ColumnSchema(name="phone", data_type="varchar", is_nullable=False),
            },
        )
    }

    live = {
        "users": TableSchema(
            table_name="users",
            columns={
                "phone": ColumnSchema(name="phone", data_type="varchar", is_nullable=True),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert events == []


# ==============================================================================
# 6. Class 5: Model / Table Rename Tests
# ==============================================================================

def test_detect_model_rename():
    """Verify Class 5 model_rename is detected when a missing table matches a new table signature."""
    baseline = {
        "raw_orders": TableSchema(
            table_name="raw_orders",
            columns={
                "order_id": ColumnSchema(name="order_id", data_type="integer", is_nullable=False),
                "customer_id": ColumnSchema(name="customer_id", data_type="integer", is_nullable=False),
                "order_date": ColumnSchema(name="order_date", data_type="date", is_nullable=False),
                "status": ColumnSchema(name="status", data_type="varchar", is_nullable=False),
                "total": ColumnSchema(name="total", data_type="numeric", is_nullable=False),
            },
        )
    }

    live = {
        "stg_orders": TableSchema(
            table_name="stg_orders",
            columns={
                "order_id": ColumnSchema(name="order_id", data_type="integer", is_nullable=False),
                "customer_id": ColumnSchema(name="customer_id", data_type="integer", is_nullable=False),
                "order_date": ColumnSchema(name="order_date", data_type="date", is_nullable=False),
                "status": ColumnSchema(name="status", data_type="varchar", is_nullable=False),
                "total": ColumnSchema(name="total", data_type="numeric", is_nullable=False),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    assert len(events) == 1
    event = events[0]

    assert event.table_name == "raw_orders"
    assert event.failure_class == "model_rename"
    assert event.payload["old_model_name"] == "raw_orders"
    assert event.payload["new_model_name"] == "stg_orders"
    assert event.confidence >= 0.80


# ==============================================================================
# 7. Compound & Multi-Class Scenarios
# ==============================================================================

def test_compound_multiple_drifts():
    """Verify detector correctly captures multiple drift classes within the same table."""
    baseline = {
        "accounts": TableSchema(
            table_name="accounts",
            columns={
                "acc_id": ColumnSchema(name="acc_id", data_type="integer", is_nullable=False),
                "balance": ColumnSchema(name="balance", data_type="integer", is_nullable=True),
                "status_code": ColumnSchema(name="status_code", data_type="varchar", is_nullable=True),
                "notes": ColumnSchema(name="notes", data_type="text", is_nullable=True),
            },
        )
    }

    live = {
        "accounts": TableSchema(
            table_name="accounts",
            columns={
                # Type shift integer -> bigint
                "acc_id": ColumnSchema(name="acc_id", data_type="bigint", is_nullable=False),
                # Nullability shift True -> False
                "balance": ColumnSchema(name="balance", data_type="integer", is_nullable=False),
                # Column rename status_code -> status_cd (JW ~0.95)
                "status_cd": ColumnSchema(name="status_cd", data_type="varchar", is_nullable=True),
                # notes dropped, extra_tag added -> column_drop_add
                "extra_tag": ColumnSchema(name="extra_tag", data_type="varchar", is_nullable=True),
            },
        )
    }

    events = detect_schema_drift(baseline, live)
    failure_classes = [e.failure_class for e in events]

    assert "type_shift" in failure_classes
    assert "nullability" in failure_classes
    assert "column_rename" in failure_classes
    assert "column_drop_add" in failure_classes


# ==============================================================================
# 8. fetch_live_pg_schema Mock Test
# ==============================================================================

def test_fetch_live_pg_schema_mock():
    """Verify fetch_live_pg_schema correctly maps database cursor rows to TableSchema dict."""
    mock_cursor_rows = [
        ("raw_customers", "id", "integer", "NO"),
        ("raw_customers", "customer_phone", "character varying", "YES"),
        ("raw_orders", "order_id", "bigint", "NO"),
        ("raw_orders", "total", "numeric", "YES"),
    ]

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = mock_cursor_rows
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn

    with patch("psycopg2.connect", return_value=mock_conn):
        conn_params = {"host": "localhost", "port": 5432, "user": "postgres", "password": "pw", "dbname": "db"}
        schema_dict = fetch_live_pg_schema(conn_params, schema_name="raw")

    assert "raw_customers" in schema_dict
    assert "raw_orders" in schema_dict

    cust_table = schema_dict["raw_customers"]
    assert "id" in cust_table.columns
    assert cust_table.columns["id"].data_type == "integer"
    assert cust_table.columns["id"].is_nullable is False

    assert "customer_phone" in cust_table.columns
    assert cust_table.columns["customer_phone"].data_type == "character varying"
    assert cust_table.columns["customer_phone"].is_nullable is True


# ==============================================================================
# 9. Serialization and Dictionary Helpers
# ==============================================================================

def test_schema_serialization_helpers():
    """Verify to_dict and from_dict methods across ColumnSchema and TableSchema."""
    col = ColumnSchema(name="test_col", data_type="varchar", is_nullable=False)
    col_dict = col.to_dict()
    assert col_dict == {"name": "test_col", "data_type": "varchar", "is_nullable": False}
    rebuilt_col = ColumnSchema.from_dict(col_dict)
    assert rebuilt_col == col

    table = TableSchema(table_name="t1", columns={"test_col": col})
    tbl_dict = table.to_dict()
    assert tbl_dict["table_name"] == "t1"
    assert "test_col" in tbl_dict["columns"]
    rebuilt_table = TableSchema.from_dict(tbl_dict)
    assert rebuilt_table.table_name == "t1"
    assert rebuilt_table.columns["test_col"].name == "test_col"

    event = DriftEvent(table_name="t1", failure_class="type_shift", payload={"a": 1}, confidence=0.99)
    event_dict = event.dict()
    assert event_dict["failure_class"] == "type_shift"
    assert event_dict["confidence"] == 0.99
