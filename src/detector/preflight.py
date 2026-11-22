"""Pre-flight Schema Metadata Snapshot and Diff Classification Engine.

Inspects live database metadata against a stored baseline schema manifest
to proactively detect and classify schema drift events before running dbt pipelines.

Supported Drift Classes:
- Class 1: column_rename
- Class 2: type_shift
- Class 3: column_drop_add
- Class 4: nullability
- Class 5: model_rename

Project ID: F26-176-DriftOps
Module: src/detector/preflight.py
"""

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, List, Optional, Set

import psycopg2

from src.compiler.cast_calculator import (
    calculate_name_similarity,
    calculate_type_compatibility,
)

# Standardized PostgreSQL data type normalization map
PG_TYPE_NORMALIZATION: Dict[str, str] = {
    "character varying": "VARCHAR",
    "varchar": "VARCHAR",
    "character": "CHAR",
    "char": "CHAR",
    "bpchar": "CHAR",
    "text": "TEXT",
    "integer": "INTEGER",
    "int": "INTEGER",
    "int4": "INTEGER",
    "smallint": "SMALLINT",
    "int2": "SMALLINT",
    "bigint": "BIGINT",
    "int8": "BIGINT",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMPTZ",
    "timestamptz": "TIMESTAMPTZ",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "numeric": "NUMERIC",
    "decimal": "NUMERIC",
    "double precision": "DOUBLE PRECISION",
    "float8": "DOUBLE PRECISION",
    "real": "REAL",
    "float4": "REAL",
    "date": "DATE",
    "json": "JSON",
    "jsonb": "JSONB",
    "uuid": "UUID",
    "bytea": "BYTEA",
}


def normalize_type_name(data_type: str) -> str:
    """Normalize PostgreSQL data type string to uppercase canonical form."""
    cleaned = re.sub(r"\(.*?\)", "", (data_type or "")).strip().lower()
    return PG_TYPE_NORMALIZATION.get(cleaned, cleaned.upper())


@dataclass
class ColumnSchema:
    """Representation of a column's metadata in a table schema."""
    name: str
    data_type: str
    is_nullable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert column schema to a dictionary."""
        return asdict(self)

    def dict(self) -> Dict[str, Any]:
        """Pydantic-style compatibility dict conversion."""
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColumnSchema":
        """Instantiate ColumnSchema from a dictionary."""
        return cls(
            name=data["name"],
            data_type=data["data_type"],
            is_nullable=data.get("is_nullable", True),
        )


@dataclass
class TableSchema:
    """Representation of a table schema containing its column definitions."""
    table_name: str
    columns: Dict[str, ColumnSchema] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert table schema to a dictionary."""
        return {
            "table_name": self.table_name,
            "columns": {col_name: col.to_dict() for col_name, col in self.columns.items()},
        }

    def dict(self) -> Dict[str, Any]:
        """Pydantic-style compatibility dict conversion."""
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TableSchema":
        """Instantiate TableSchema from a dictionary."""
        cols: Dict[str, ColumnSchema] = {}
        for c_name, c_data in data.get("columns", {}).items():
            if isinstance(c_data, ColumnSchema):
                cols[c_name] = c_data
            else:
                cols[c_name] = ColumnSchema.from_dict(c_data)
        return cls(table_name=data["table_name"], columns=cols)


@dataclass
class DriftEvent:
    """Structured representation of a detected schema drift discrepancy."""
    table_name: str
    failure_class: str
    payload: Dict[str, Any]
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert drift event to a dictionary."""
        return asdict(self)

    def dict(self) -> Dict[str, Any]:
        """Pydantic-style compatibility dict conversion."""
        return self.to_dict()


def fetch_live_pg_schema(
    conn_params: Dict[str, Any],
    schema_name: str = "raw",
) -> Dict[str, TableSchema]:
    """Extract schema metadata for tables in schema_name from PostgreSQL.

    Queries information_schema.columns to construct TableSchema instances
    for all tables belonging to the specified schema.

    Args:
        conn_params: PostgreSQL connection parameters dictionary
                     (host, port, user, password, dbname).
        schema_name: PostgreSQL schema name to inspect (default: "raw").

    Returns:
        Dictionary mapping table_name -> TableSchema.
    """
    tables: Dict[str, TableSchema] = {}

    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            query = """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s
                ORDER BY table_name, ordinal_position;
            """
            cur.execute(query, (schema_name,))
            rows = cur.fetchall()

            for tbl_name, col_name, dt, is_null_str in rows:
                if tbl_name not in tables:
                    tables[tbl_name] = TableSchema(table_name=tbl_name, columns={})
                is_nullable = str(is_null_str).strip().upper() == "YES"
                tables[tbl_name].columns[col_name] = ColumnSchema(
                    name=col_name,
                    data_type=str(dt).lower(),
                    is_nullable=is_nullable,
                )

    return tables


def detect_schema_drift(
    baseline_manifest: Dict[str, TableSchema],
    live_schema: Dict[str, TableSchema],
    rename_similarity_threshold: float = 0.70,
    model_rename_overlap_threshold: float = 0.80,
) -> List[DriftEvent]:
    """Compare baseline schema manifest against live schema and classify drift events.

    Detects and classifies discrepancies into 5 standard failure classes:
    1. column_rename: Column disappeared from baseline and appears in live with >=0.70 similarity.
    2. type_shift: Column exists in both schemas but data_type differs.
    3. column_drop_add: Columns dropped without rename match, or newly added columns.
    4. nullability: Column changed from is_nullable=True to is_nullable=False.
    5. model_rename: Table missing in baseline, but live has a table with >=0.80 column overlap.

    Args:
        baseline_manifest: Manifest mapping table_name -> TableSchema (ground truth).
        live_schema: Live snapshot mapping table_name -> TableSchema.
        rename_similarity_threshold: Minimum Jaro-Winkler similarity for rename (default: 0.70).
        model_rename_overlap_threshold: Minimum column signature overlap for table rename (default: 0.80).

    Returns:
        List of structured DriftEvent instances.
    """
    events: List[DriftEvent] = []

    baseline_tables = set(baseline_manifest.keys())
    live_tables = set(live_schema.keys())

    missing_tables = list(baseline_tables - live_tables)
    new_tables = list(live_tables - baseline_tables)

    matched_old_tables: Set[str] = set()
    matched_new_tables: Set[str] = set()

    # --------------------------------------------------------------------------
    # Class 5: Model / Table Rename Detection
    # --------------------------------------------------------------------------
    for old_tbl in sorted(missing_tables):
        b_cols = set(baseline_manifest[old_tbl].columns.keys())
        if not b_cols:
            continue

        best_candidate: Optional[str] = None
        best_overlap: float = 0.0

        for new_tbl in sorted(new_tables):
            if new_tbl in matched_new_tables:
                continue

            l_cols = set(live_schema[new_tbl].columns.keys())
            if not l_cols:
                continue

            # Compute column signature overlap
            overlap = len(b_cols & l_cols) / max(len(b_cols), len(l_cols))
            if overlap >= model_rename_overlap_threshold and overlap > best_overlap:
                best_overlap = overlap
                best_candidate = new_tbl

        if best_candidate is not None:
            matched_old_tables.add(old_tbl)
            matched_new_tables.add(best_candidate)
            events.append(
                DriftEvent(
                    table_name=old_tbl,
                    failure_class="model_rename",
                    payload={
                        "old_model_name": old_tbl,
                        "new_model_name": best_candidate,
                        "old_table_name": old_tbl,
                        "new_table_name": best_candidate,
                    },
                    confidence=round(best_overlap, 4),
                )
            )

    # --------------------------------------------------------------------------
    # Column-level Discrepancy Detection on Common Tables
    # --------------------------------------------------------------------------
    common_tables = sorted(baseline_tables & live_tables)

    for table_name in common_tables:
        b_table = baseline_manifest[table_name]
        l_table = live_schema[table_name]

        b_cols = b_table.columns
        l_cols = l_table.columns

        b_col_names = set(b_cols.keys())
        l_col_names = set(l_cols.keys())

        missing_col_names = [c for c in b_cols if c not in l_col_names]
        new_col_names = [c for c in l_cols if c not in b_col_names]

        renamed_old: Set[str] = set()
        renamed_new: Set[str] = set()

        # ----------------------------------------------------------------------
        # Class 1: Column Rename
        # ----------------------------------------------------------------------
        for old_col in list(missing_col_names):
            best_new: Optional[str] = None
            best_sim: float = 0.0

            for new_col in list(new_col_names):
                if new_col in renamed_new:
                    continue

                sim = calculate_name_similarity(old_col, new_col)

                # Type compatibility check
                norm_src = normalize_type_name(b_cols[old_col].data_type)
                norm_tgt = normalize_type_name(l_cols[new_col].data_type)
                compat = calculate_type_compatibility(norm_src, norm_tgt)

                # Must be compatible type and meet similarity threshold
                if compat > 0.0 and sim >= rename_similarity_threshold:
                    if sim > best_sim:
                        best_sim = sim
                        best_new = new_col

            if best_new is not None:
                renamed_old.add(old_col)
                renamed_new.add(best_new)
                events.append(
                    DriftEvent(
                        table_name=table_name,
                        failure_class="column_rename",
                        payload={
                            "old_col": old_col,
                            "new_col": best_new,
                            "old_column": old_col,
                            "new_column": best_new,
                        },
                        confidence=round(best_sim, 4),
                    )
                )

        # ----------------------------------------------------------------------
        # Class 3: Column Additions and Drops
        # ----------------------------------------------------------------------
        dropped_cols = [c for c in missing_col_names if c not in renamed_old]
        added_cols = [c for c in new_col_names if c not in renamed_new]

        if dropped_cols or added_cols:
            events.append(
                DriftEvent(
                    table_name=table_name,
                    failure_class="column_drop_add",
                    payload={
                        "dropped_columns": dropped_cols,
                        "added_columns": added_cols,
                    },
                    confidence=1.0,
                )
            )

        # ----------------------------------------------------------------------
        # Class 2 & 4: Type Shift and Nullability Shift on Shared Columns
        # ----------------------------------------------------------------------
        shared_cols = [c for c in b_cols if c in l_cols]
        for col_name in shared_cols:
            b_col = b_cols[col_name]
            l_col = l_cols[col_name]

            # Class 2: Type Shift
            norm_b_type = normalize_type_name(b_col.data_type)
            norm_l_type = normalize_type_name(l_col.data_type)
            if norm_b_type != norm_l_type:
                events.append(
                    DriftEvent(
                        table_name=table_name,
                        failure_class="type_shift",
                        payload={
                            "column_name": col_name,
                            "source_type": b_col.data_type,
                            "target_type": l_col.data_type,
                        },
                        confidence=1.0,
                    )
                )

            # Class 4: Nullability (changed from True to False)
            if b_col.is_nullable is True and l_col.is_nullable is False:
                events.append(
                    DriftEvent(
                        table_name=table_name,
                        failure_class="nullability",
                        payload={"column_name": col_name},
                        confidence=1.0,
                    )
                )

    return events
