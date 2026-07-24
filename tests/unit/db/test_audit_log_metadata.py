"""Plan 147 Slice B — metadata/migration parity for audit_log CHECK constraints.

Pure ``sa.MetaData`` introspection (no DB) that locks parity with migration
0045: both the column-level ``actor_type`` enum CHECK and the table-level
``actor_id``/``actor_type`` pairing CHECK must be declared in
``sapphire_flow.db.metadata``. Without this, a future
``alembic revision --autogenerate`` would see the DB has the composite CHECK
but metadata doesn't, and could propose silently dropping it. RED against a
metadata definition that omits the composite CHECK.
"""

from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import audit_log


def _check_constraints(constraints: object) -> list[sa.CheckConstraint]:
    return [c for c in constraints if isinstance(c, sa.CheckConstraint)]  # type: ignore[misc]


class TestAuditLogCheckConstraintParity:
    def test_actor_type_enum_check_present(self) -> None:
        # Column-level CHECK constraints live on the Column, not the Table
        # (mirrors migration 0045's inline actor_type CHECK — unnamed in
        # metadata, consistent with every other enum-style CHECK in this
        # file, e.g. stations.status, forecasts.representation).
        checks = _check_constraints(audit_log.c.actor_type.constraints)
        assert checks, "expected a CHECK constraint on audit_log.actor_type"
        combined = " ".join(str(c.sqltext) for c in checks)
        assert "'user'" in combined
        assert "'api_key'" in combined
        assert "'system'" in combined

    def test_actor_id_matches_actor_type_check_present_by_name(self) -> None:
        # Table-level composite CHECK from migration 0045:72-77
        # (ck_audit_log_actor_id_matches_actor_type). Must exist in metadata
        # with the exact name + SQL the migration creates, or a future
        # autogenerate diff could propose dropping it.
        checks = {c.name: c for c in _check_constraints(audit_log.constraints)}
        assert "ck_audit_log_actor_id_matches_actor_type" in checks, (
            "expected audit_log to declare "
            "ck_audit_log_actor_id_matches_actor_type as a table-level CHECK "
            "(migration 0045:72-77) — a future autogenerate could propose "
            "dropping this load-bearing actor pairing backstop"
        )
        sqltext = str(checks["ck_audit_log_actor_id_matches_actor_type"].sqltext)
        assert "actor_type = 'system'" in sqltext
        assert "actor_id IS NULL" in sqltext
        assert "'user', 'api_key'" in sqltext
        assert "actor_id IS NOT NULL" in sqltext
