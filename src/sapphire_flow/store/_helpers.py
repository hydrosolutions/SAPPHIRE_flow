from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

if TYPE_CHECKING:
    import sqlalchemy as sa


def utc_from_row(value: Any) -> UtcDatetime:
    return ensure_utc(value)


def utc_or_none(value: Any) -> UtcDatetime | None:
    return ensure_utc(value) if value is not None else None


def require_real_transaction(conn: sa.Connection, *, caller: str) -> None:
    """Shared by every multi-statement write pipeline that does not own its
    own transaction (`store/basin_importer.py::import_basin_package`,
    `store/caravan_import.py::import_caravan_attributes`'s gated exit-gate
    path): refuse to run unless ``conn`` is genuinely inside a real,
    non-AUTOCOMMIT transaction. Verified empirically against a live
    Postgres connection (originally `basin_importer.py`'s own guard): on an
    ``isolation_level="AUTOCOMMIT"`` connection (production's
    ``flows/_db.py::setup_production_stores``), even an EXPLICIT
    ``conn.begin()`` does not make subsequent statements roll back together
    -- a statement executed before a later failure stays committed after
    ``rollback()``. A connection that passes both checks below (no
    AUTOCOMMIT execution option, and an active transaction already open)
    DOES roll back correctly, because ordinary Postgres transactions are
    atomic by construction. ``caller`` names the function in the error
    message so a misuse is traceable to its call site."""
    if conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
        raise RuntimeError(
            f"{caller} refuses to run on an AUTOCOMMIT-isolation connection "
            "-- each statement would commit independently, so a mid-pipeline "
            "failure could leave writes partially applied. Acquire a "
            "connection via engine.connect() (not the shared production "
            "AUTOCOMMIT connection) and wrap the call in conn.begin(), or "
            "use engine.begin() directly."
        )
    if not conn.in_transaction():
        raise RuntimeError(
            f"{caller} requires an already-open transaction on conn (call "
            "conn.begin() -- or use engine.begin() -- before invoking) so a "
            "mid-pipeline failure rolls back all writes instead of leaving "
            "partial state."
        )
