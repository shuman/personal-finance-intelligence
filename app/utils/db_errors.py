"""
Normalise raw database / SQLAlchemy exceptions into human-friendly messages.

Usage:
    from app.utils.db_errors import friendly_error
    raise HTTPException(status_code=400, detail=friendly_error(exc))
"""
from __future__ import annotations

import re


# ------------------------------------------------------------------
# asyncpg  (PostgreSQL)
# ------------------------------------------------------------------
_ASYNC_PG_MAP: list[tuple[re.Pattern, str]] = [
    # UniqueViolationError
    (
        re.compile(r"duplicate key value violates unique constraint.*\"(\w+)\"", re.I | re.S),
        "unique_violation",
    ),
    # NotNullViolationError
    (
        re.compile(r"null value in column \"(\w+)\".*violates not-null constraint", re.I | re.S),
        "not_null_violation",
    ),
    # ForeignKeyViolationError
    (
        re.compile(
            r"violates foreign key constraint.*\"(\w+)\".*DETAIL:.*Key \((\w+)\)=\(([^)]+)\)",
            re.I | re.S,
        ),
        "fk_violation",
    ),
]

# ------------------------------------------------------------------
# SQLite
# ------------------------------------------------------------------
_SQLITE_MAP: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"UNIQUE constraint failed: (\w+\.\w+)", re.I),
        "sqlite_unique",
    ),
    (
        re.compile(r"NOT NULL constraint failed: (\w+\.\w+)", re.I),
        "sqlite_not_null",
    ),
]

# ------------------------------------------------------------------
# Friendly constraint-name → message templates
# ------------------------------------------------------------------
_CONSTRAINT_FRIENDLY: dict[str, str] = {
    # Statement constraints
    "ix_statements_filename": "A statement with this filename has already been uploaded.",
    "ix_statements_pdf_hash": "This exact PDF file has already been uploaded.",
    "idx_17008_ix_statements_filename": "A statement with this filename has already been uploaded.",
    # Transaction constraints
    "uq_transaction_duplicate": "Duplicate transactions detected — same date, description, and amount already exist in this statement.",
    # Generic fallback
}


def _extract_detail(raw: str) -> str:
    """Pull the DETAIL line from a PostgreSQL error if present."""
    m = re.search(r"DETAIL:\s*(.+)", raw, re.I)
    return m.group(1).strip() if m else ""


def _friendly_constraint(constraint_name: str) -> str:
    """Return a human-friendly description for a known constraint, or None."""
    return _CONSTRAINT_FRIENDLY.get(constraint_name)


def _friendly_field(field_name: str) -> str:
    """Convert a DB column name to a readable label."""
    return field_name.replace("_", " ").replace("raw", "").strip().title()


def normalize(raw: str) -> str:
    """Convert a raw DB error string into a user-facing message."""

    # --- asyncpg (PostgreSQL) ---
    for pattern, kind in _ASYNC_PG_MAP:
        m = pattern.search(raw)
        if not m:
            continue

        if kind == "unique_violation":
            constraint = m.group(1)
            msg = _friendly_constraint(constraint)
            if msg:
                return msg
            detail = _extract_detail(raw)
            if detail:
                return f"This record already exists ({detail})."
            return "A record with these details already exists."

        if kind == "not_null_violation":
            col = m.group(1)
            return f"Required information is missing: {_friendly_field(col)}. Please check the form and try again."

        if kind == "fk_violation":
            constraint, col, value = m.group(1), m.group(2), m.group(3)
            return f"The {col} referenced ({value}) does not exist. Please refresh the page and try again."

    # --- SQLite ---
    for pattern, kind in _SQLITE_MAP:
        m = pattern.search(raw)
        if not m:
            continue

        if kind == "sqlite_unique":
            table_col = m.group(1)
            if "transactions" in table_col:
                return "Duplicate transactions detected — same date, description, and amount."
            return "This record already exists."

        if kind == "sqlite_not_null":
            table_col = m.group(1)
            col = table_col.split(".")[-1] if "." in table_col else table_col
            return f"Required information is missing: {_friendly_field(col)}."

    # --- Generic / unknown ---
    # Strip class names like <class 'asyncpg.exceptions...'>
    cleaned = re.sub(r"<class '[^']+'>:\s*", "", raw)
    # If it's still very technical, give a safe fallback
    if len(cleaned) > 200 or "constraint" in cleaned.lower():
        return "A data conflict occurred. The record may already exist or required information is missing. Please try again."
    return cleaned


def friendly_error(exc: Exception) -> str:
    """Convenience wrapper: pass any exception, get a friendly string."""
    raw = str(getattr(exc, "orig", exc))
    return normalize(raw)
