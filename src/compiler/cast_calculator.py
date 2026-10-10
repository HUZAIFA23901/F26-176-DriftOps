"""Structural Confidence Calculator (C_AST) for DriftOps.

Calculates composite AST repair confidence combining lexical Jaro-Winkler distance,
data type compatibility, and catalog ordinal invariants:
C_AST = 0.4 * S_name + 0.4 * T_compat + 0.2 * I_catalog

Project ID: F26-176-DriftOps
Module: src/compiler/cast_calculator.py
"""

import re
from typing import Dict, Set

try:
    import jaro
    _jaro_winkler_fn = jaro.jaro_winkler_metric
except (ImportError, AttributeError):
    try:
        import jaro_winkler  # type: ignore
        _jaro_winkler_fn = jaro_winkler.jaro_winkler_similarity  # type: ignore
    except (ImportError, AttributeError):
        # Fallback pure-python implementation if needed
        _jaro_winkler_fn = None

# Normalized type mappings for PostgreSQL
TYPE_ALIASES: Dict[str, str] = {
    "INT": "INTEGER",
    "INT4": "INTEGER",
    "SMALLINT": "SMALLINT",
    "INT2": "SMALLINT",
    "BIGINT": "BIGINT",
    "INT8": "BIGINT",
    "FLOAT": "DOUBLE PRECISION",
    "FLOAT8": "DOUBLE PRECISION",
    "DOUBLE": "DOUBLE PRECISION",
    "REAL": "REAL",
    "FLOAT4": "REAL",
    "DECIMAL": "NUMERIC",
    "NUMERIC": "NUMERIC",
    "CHAR": "CHAR",
    "CHARACTER": "CHAR",
    "VARCHAR": "VARCHAR",
    "TEXT": "TEXT",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "DATE": "DATE",
    "JSON": "JSON",
    "JSONB": "JSONB",
    "UUID": "UUID",
    "BYTEA": "BYTEA",
}

# Lossless implicit widening conversions
LOSSLESS_CONVERSIONS: Dict[str, Set[str]] = {
    "SMALLINT": {"INTEGER", "BIGINT", "NUMERIC", "DOUBLE PRECISION"},
    "INTEGER": {"BIGINT", "NUMERIC", "DOUBLE PRECISION"},
    "BIGINT": {"NUMERIC"},
    "REAL": {"DOUBLE PRECISION", "NUMERIC"},
    "CHAR": {"VARCHAR", "TEXT"},
    "VARCHAR": {"TEXT"},
    "DATE": {"TIMESTAMP", "TIMESTAMPTZ"},
    "TIMESTAMP": {"TIMESTAMPTZ"},
}

# Explicitly castable types
VALID_EXPLICIT_CASTS: Dict[str, Set[str]] = {
    "VARCHAR": {"BIGINT", "INTEGER", "SMALLINT", "NUMERIC", "DOUBLE PRECISION", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMPTZ", "UUID", "JSON", "JSONB"},
    "TEXT": {"BIGINT", "INTEGER", "SMALLINT", "NUMERIC", "DOUBLE PRECISION", "BOOLEAN", "DATE", "TIMESTAMP", "TIMESTAMPTZ", "UUID", "JSON", "JSONB"},
    "BIGINT": {"VARCHAR", "TEXT", "INTEGER", "SMALLINT"},
    "INTEGER": {"VARCHAR", "TEXT", "SMALLINT"},
    "NUMERIC": {"VARCHAR", "TEXT", "BIGINT", "INTEGER"},
    "DOUBLE PRECISION": {"VARCHAR", "TEXT", "INTEGER", "BIGINT", "REAL"},
    "BOOLEAN": {"VARCHAR", "TEXT", "INTEGER"},
    "DATE": {"VARCHAR", "TEXT"},
    "TIMESTAMP": {"VARCHAR", "TEXT", "DATE"},
    "TIMESTAMPTZ": {"VARCHAR", "TEXT", "DATE", "TIMESTAMP"},
    "UUID": {"VARCHAR", "TEXT"},
    "JSON": {"VARCHAR", "TEXT", "JSONB"},
    "JSONB": {"VARCHAR", "TEXT", "JSON"},
}


def _normalize_type(type_str: str) -> str:
    """Strip length/precision parameters and normalize type identifier."""
    base = re.sub(r"\(.*?\)", "", type_str).strip().upper()
    return TYPE_ALIASES.get(base, base)


def calculate_name_similarity(old_name: str, new_name: str) -> float:
    """Compute normalized Jaro-Winkler string similarity S_name in [0.0, 1.0].

    Args:
        old_name: Original column or identifier name.
        new_name: Candidate column or identifier name.

    Returns:
        Float score in [0.0, 1.0].
    """
    if not old_name or not new_name:
        return 0.0

    s1 = old_name.strip().lower()
    s2 = new_name.strip().lower()

    if s1 == s2:
        return 1.0

    if _jaro_winkler_fn is not None:
        score = float(_jaro_winkler_fn(s1, s2))
        return max(0.0, min(1.0, score))

    # Basic character overlap fallback
    common = len(set(s1) & set(s2))
    total = max(len(s1), len(s2))
    return common / total if total > 0 else 0.0


def calculate_type_compatibility(source_type: str, target_type: str) -> float:
    """Evaluate SQL data type compatibility T_compat.

    Returns:
        1.0 for identical or lossless implicit widening conversions.
        0.5 for valid explicit casts (e.g. VARCHAR to BIGINT).
        0.0 for incompatible or narrowing conversions without safe coercion.
    """
    src = _normalize_type(source_type)
    tgt = _normalize_type(target_type)

    if src == tgt:
        return 1.0

    if tgt in LOSSLESS_CONVERSIONS.get(src, set()):
        return 1.0

    if tgt in VALID_EXPLICIT_CASTS.get(src, set()) or src in VALID_EXPLICIT_CASTS.get(tgt, set()):
        return 0.5

    return 0.0


def calculate_catalog_invariant(attnum_match: bool) -> float:
    """Evaluate PostgreSQL catalog ordinal match invariant I_catalog.

    Args:
        attnum_match: Whether pg_attribute.attnum / column ordinal position matches.

    Returns:
        1.0 if catalog position is preserved, else 0.0.
    """
    return 1.0 if attnum_match else 0.0


def compute_c_ast(
    old_name: str,
    new_name: str,
    source_type: str = "VARCHAR",
    target_type: str = "VARCHAR",
    attnum_match: bool = True,
) -> float:
    """Compute composite AST structural confidence score C_AST.

    Formula:
        C_AST = 0.4 * S_name + 0.4 * T_compat + 0.2 * I_catalog

    Args:
        old_name: Original column identifier name.
        new_name: Discovered candidate column identifier name.
        source_type: Original data type.
        target_type: Candidate data type.
        attnum_match: Whether catalog position ordinal matched.

    Returns:
        Confidence score C_AST clamped to [0.0, 1.0].
    """
    alpha = 0.4
    beta = 0.4
    gamma = 0.2

    s_name = calculate_name_similarity(old_name, new_name)
    t_compat = calculate_type_compatibility(source_type, target_type)
    i_catalog = calculate_catalog_invariant(attnum_match)

    c_ast = (alpha * s_name) + (beta * t_compat) + (gamma * i_catalog)
    return round(max(0.0, min(1.0, c_ast)), 4)
