from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import forecasts
from sapphire_flow.types.enums import ForecastStatus


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


class TestForecastStatusReachability:
    """Plan 328 T1: ``uq_forecasts_station_model_issued_param`` is PARTIAL on
    ``status <> 'superseded'``, so the predicate names a status value. Until
    that value is a member of ``ForecastStatus`` the domain cannot produce it,
    the predicate excludes nothing, and the index behaves as a full one —
    which is how a supersede-then-reinsert path appeared to exist for months
    without ever being reachable.

    ⛔ Deliberately asserted against the ENUM, not against the deployed index:
    ``db/metadata.py`` carries the same predicate as the deployed index, so
    diffing the two proves nothing. The migrated schema is checked separately
    in ``tests/integration/db/test_migration_0058_superseded_status.py``.
    """

    @staticmethod
    def _predicate_status_values() -> set[str]:
        index = next(
            i
            for i in forecasts.indexes
            if i.name == "uq_forecasts_station_model_issued_param"
        )
        where = index.dialect_options["postgresql"]["where"]
        return {
            element.value
            for element in where.get_children()
            if isinstance(element, sa.BindParameter)
        }

    def test_every_predicate_value_is_a_forecast_status(self) -> None:
        declared = {status.value for status in ForecastStatus}
        unreachable = self._predicate_status_values() - declared
        assert not unreachable, (
            f"the index predicate excludes {sorted(unreachable)}, which "
            f"ForecastStatus cannot produce — the partial index is a full one"
        )

    def test_status_check_admits_every_forecast_status(self) -> None:
        checks = [
            str(c.sqltext)
            for c in forecasts.c.status.constraints
            if isinstance(c, sa.CheckConstraint)
        ]
        assert checks, "expected a CHECK constraint on forecasts.status"
        combined = " ".join(checks)
        missing = [s.value for s in ForecastStatus if f"'{s.value}'" not in combined]
        assert not missing, f"CHECK rejects declared statuses: {missing}"
