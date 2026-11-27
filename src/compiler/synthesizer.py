"""Unified Repair Synthesizer Router for DriftOps.

Central dispatcher mapping schema failure classes (Classes 1 through 5)
to their corresponding AST repair synthesis algorithms.

Project ID: F26-176-DriftOps
Module: src/compiler/synthesizer.py
"""

from typing import Any, Dict, Optional, Tuple

from src.compiler.ast_parser import (
    repair_column_drop_or_add,
    repair_column_rename,
    repair_model_identifier_rename,
    repair_nullability_shift,
    repair_type_shift,
)


def synthesize_repair_patch(
    broken_sql: str,
    failure_class: str,
    payload: Optional[Dict[str, Any]] = None,
    dialect: str = "postgres",
) -> Tuple[str, bool]:
    """Dispatch a schema repair request to the appropriate AST repair synthesizer.

    Routes failure taxonomy classes to specialized repair engines:
    - Class 1: Column Rename (`repair_column_rename`) -> Tier 1 safe (requires_tier2=False).
    - Class 2: Type Shifts (`repair_type_shift`) -> Tier 1 if widening, Tier 2 if narrowing.
    - Class 3: Column Additions/Drops (`repair_column_drop_or_add`) -> Tier 2 if dropped columns present.
    - Class 4: Nullability Shifts (`repair_nullability_shift`) -> Tier 2 override (requires_tier2=True).
    - Class 5: Model Identifier Drift (`repair_model_identifier_rename`) -> Tier 1 safe (requires_tier2=False).
    - Classes 6, 7 & Unknown: Semantic / Assertion violations fall back safely (broken_sql, True).

    Args:
        broken_sql: Original or drifted SQL query to repair.
        failure_class: Failure taxonomy classification string.
        payload: Parameters required by the underlying repair synthesizer.
        dialect: SQL dialect for AST parsing and generation (default: "postgres").

    Returns:
        Tuple[str, bool]: (patched_sql, requires_tier2_override) where:
            - patched_sql: Repaired SQL string.
            - requires_tier2_override: True if repair requires human review (Tier 2 Assist),
                                      False if autonomous Tier 1 repair is permitted.
    """
    if payload is None:
        payload = {}

    target_dialect = payload.get("dialect", dialect)
    norm_class = (failure_class or "").strip().lower()

    # --------------------------------------------------------------------------
    # Class 1: Column Rename
    # --------------------------------------------------------------------------
    if norm_class in ("class1_rename", "column_rename", "class_1", "class1", "rename"):
        old_col = (
            payload.get("old_column")
            or payload.get("old_col")
            or payload.get("old_column_name")
            or payload.get("old_name")
            or ""
        )
        new_col = (
            payload.get("new_column")
            or payload.get("new_col")
            or payload.get("new_column_name")
            or payload.get("new_name")
            or ""
        )
        patched_sql = repair_column_rename(
            sql_query=broken_sql,
            old_column=old_col,
            new_column=new_col,
            dialect=target_dialect,
        )
        return patched_sql, False

    # --------------------------------------------------------------------------
    # Class 2: Type Shifts
    # --------------------------------------------------------------------------
    elif norm_class in ("class2_type_shift", "type_shift", "class_2", "class2", "type_narrowing", "type_widening"):
        col = (
            payload.get("column_name")
            or payload.get("column")
            or payload.get("col")
            or ""
        )
        src_type = (
            payload.get("source_type")
            or payload.get("old_type")
            or payload.get("from_type")
            or "VARCHAR"
        )
        tgt_type = (
            payload.get("target_type")
            or payload.get("new_type")
            or payload.get("to_type")
            or "VARCHAR"
        )
        patched_sql, is_narrowing = repair_type_shift(
            sql=broken_sql,
            column_name=col,
            source_type=src_type,
            target_type=tgt_type,
            dialect=target_dialect,
        )
        return patched_sql, is_narrowing

    # --------------------------------------------------------------------------
    # Class 3: Column Additions & Drops
    # --------------------------------------------------------------------------
    elif norm_class in (
        "class3_column_drop_add",
        "column_drop_add",
        "class_3",
        "class3",
        "column_drop",
        "column_add",
        "drop_column",
        "add_column",
    ):
        dropped = payload.get("dropped_columns") or payload.get("dropped_cols") or []
        if isinstance(dropped, str):
            dropped = [dropped]
        added = payload.get("added_columns") or payload.get("added_cols") or []
        if isinstance(added, str):
            added = [added]
        remove_dropped = payload.get("remove_dropped", False)

        patched_sql, req_tier2 = repair_column_drop_or_add(
            sql=broken_sql,
            dropped_columns=dropped,
            added_columns=added,
            dialect=target_dialect,
            remove_dropped=remove_dropped,
        )
        return patched_sql, req_tier2

    # --------------------------------------------------------------------------
    # Class 4: Nullability Shifts
    # --------------------------------------------------------------------------
    elif norm_class in ("class4_nullability", "nullability", "nullability_shift", "class_4", "class4", "nullability_change"):
        col = (
            payload.get("column_name")
            or payload.get("column")
            or payload.get("col")
            or ""
        )
        default_val = payload.get("default_value", "''")
        patched_sql, req_tier2 = repair_nullability_shift(
            sql=broken_sql,
            column_name=col,
            default_value=default_val,
            dialect=target_dialect,
        )
        return patched_sql, req_tier2

    # --------------------------------------------------------------------------
    # Class 5: Model/Table Identifier Drift
    # --------------------------------------------------------------------------
    elif norm_class in (
        "class5_model_rename",
        "model_rename",
        "class_5",
        "class5",
        "model_identifier_rename",
        "table_rename",
    ):
        old_model = (
            payload.get("old_model_name")
            or payload.get("old_model")
            or payload.get("old_table_name")
            or payload.get("old_name")
            or ""
        )
        new_model = (
            payload.get("new_model_name")
            or payload.get("new_model")
            or payload.get("new_table_name")
            or payload.get("new_name")
            or ""
        )
        patched_sql, req_tier2 = repair_model_identifier_rename(
            sql=broken_sql,
            old_model_name=old_model,
            new_model_name=new_model,
            dialect=target_dialect,
        )
        return patched_sql, req_tier2

    # --------------------------------------------------------------------------
    # Unknown / Unsupported Classes (Class 6 Semantic, Class 7 Grain Violations)
    # --------------------------------------------------------------------------
    else:
        # Fall back gracefully, requiring manual Tier 3 or Tier 2 human investigation
        return broken_sql, True
