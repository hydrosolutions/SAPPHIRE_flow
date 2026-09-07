"""Plan 241 T4 — a forecast's cadence is STORED, not inferred.

RED-FIRST NOTE. The first test here was observed failing against `main`
(a20e38b3) before this change, with the store returning ``1:00:00`` for a
forecast stored as daily — the fabricated hour, not a missing attribute or a
signature error. ⛔ It cannot be run red on this branch: T4's writer/reader are
already present here, so it passes in place. Reproduce the red on a clean
`main` checkout if you need to see it again.

These are INTEGRATION tests on purpose. Anything that stores or reads a forecast
is a claim about the database and cannot be established by a unit run.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import sqlalchemy as sa
import structlog

from sapphire_flow.db.metadata import forecasts
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId

_ISSUED = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _ensemble(
    station_id: StationId, *, n_steps: int, time_step: timedelta
) -> ForecastEnsemble:
    rows = [
        {
            "member_id": m,
            "valid_time": _ISSUED + time_step * (step + 1),
            "value": 10.0 + m + step,
        }
        for step in range(n_steps)
        for m in range(5)
    ]
    return ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=_ISSUED,
        parameter="discharge",
        units="m³/s",
        time_step=time_step,
        values=pl.DataFrame(rows),
    )


class TestStoredCadenceIsAuthoritative:
    def test_one_step_daily_forecast_round_trips_as_daily(
        self, db_connection: sa.Connection
    ) -> None:
        """THE DEFECT. Measured on main: stored 1 day, read back 1:00:00."""
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        ensemble = _ensemble(sid, n_steps=1, time_step=timedelta(days=1))
        assert ensemble.forecast_horizon_steps == 1

        fc = dataclasses.replace(_make_forecast(sid, mid, aid), ensemble=ensemble)
        store.store_forecast(fc)

        fetched = store.fetch_forecast(fc.id)
        assert fetched is not None
        assert fetched.ensemble.time_step == timedelta(days=1)

        # The COLUMN itself must hold it — a round-trip alone would also pass if
        # the reader were still inferring, which is the whole defect.
        stored = db_connection.execute(
            sa.select(forecasts.c.time_step_seconds).where(forecasts.c.id == fc.id)
        ).scalar_one()
        assert stored == 86400

    def test_non_daily_multi_step_forecast_round_trips_unchanged(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        ensemble = _ensemble(sid, n_steps=4, time_step=timedelta(hours=6))

        fc = dataclasses.replace(_make_forecast(sid, mid, aid), ensemble=ensemble)
        store.store_forecast(fc)

        fetched = store.fetch_forecast(fc.id)
        assert fetched is not None
        assert fetched.ensemble.time_step == timedelta(hours=6)


class TestLegacyNullRowsKeepTodaysBehaviour:
    """0053 is nullable-first and backfills nothing, so pre-migration rows read
    through the inference path. It must behave EXACTLY as it did before T4."""

    def _store_then_null_the_cadence(
        self, db_connection: sa.Connection, ensemble: ForecastEnsemble
    ) -> tuple[PgForecastStore, object]:
        sid = ensemble.station_id
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        fc = dataclasses.replace(_make_forecast(sid, mid, aid), ensemble=ensemble)
        store.store_forecast(fc)
        # Simulate a row written before 0053: the column is NULL.
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == fc.id)
            .values(time_step_seconds=None)
        )
        return store, fc.id

    def test_legacy_multi_step_row_still_infers_its_cadence(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        ensemble = _ensemble(sid, n_steps=4, time_step=timedelta(hours=6))
        store, fid = self._store_then_null_the_cadence(db_connection, ensemble)

        fetched = store.fetch_forecast(fid)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.ensemble.time_step == timedelta(hours=6)

    def test_legacy_one_step_row_still_returns_the_fabricated_hour(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ REGRESSION GUARD for the branch T4 deliberately KEEPS.

        Deleting the fallback would raise IndexError on a row that reads today.
        The value is wrong — that is the historical behaviour, preserved on
        purpose — and correcting it needs the live-data measurement the
        deferred tightening owns.
        """
        sid = _seed_station(db_connection)
        ensemble = _ensemble(sid, n_steps=1, time_step=timedelta(days=1))
        store, fid = self._store_then_null_the_cadence(db_connection, ensemble)

        with structlog.testing.capture_logs() as captured:
            fetched = store.fetch_forecast(fid)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.ensemble.time_step == timedelta(hours=1)

        # The fabrication must be VISIBLE, not silent — that is the only thing
        # separating this retained branch from the defect it descends from.
        events = [
            e
            for e in captured
            if e.get("event") == "forecast.legacy_time_step_fabricated"
        ]
        assert len(events) == 1, f"expected one warning, got {captured}"
        assert events[0]["log_level"] == "warning"
        assert events[0]["fabricated_time_step_seconds"] == 3600
        # The plan requires the warning to NAME the row, otherwise it is
        # unactionable: an operator cannot find which forecast was fabricated.
        assert events[0]["forecast_id"] == str(fid)
        assert events[0]["station_id"] == str(sid)
