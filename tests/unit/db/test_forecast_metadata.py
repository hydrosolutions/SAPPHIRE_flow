from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import forecasts


class TestForecastProvenanceSchema:
    """epic-088 M4: the forecasts table reflects the provenance changes.

    A pure metadata assertion (no DB) that mirrors the intent of migration
    0026: ``nwp_cycle_reference_time`` becomes nullable and the
    ``nwp_cycle_source`` CHECK admits the third value ``'runoff_only'``.
    RED on main (column is NOT NULL, CHECK is the 2-value set).
    """

    def test_reference_time_is_nullable(self) -> None:
        assert forecasts.c.nwp_cycle_reference_time.nullable is True

    def test_source_check_admits_runoff_only(self) -> None:
        # Column-level CHECK constraints live on the Column, not the Table.
        source_checks = [
            str(c.sqltext)
            for c in forecasts.c.nwp_cycle_source.constraints
            if isinstance(c, sa.CheckConstraint)
        ]
        assert source_checks, "expected a CHECK constraint on nwp_cycle_source"
        combined = " ".join(source_checks)
        assert "runoff_only" in combined
        assert "primary" in combined
        assert "fallback" in combined
