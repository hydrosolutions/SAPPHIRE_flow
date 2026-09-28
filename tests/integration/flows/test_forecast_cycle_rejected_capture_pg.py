"""Plan 404 — end-to-end proof that a QC-rejected forecast is captured
through a REAL run of ``run_forecast_cycle_flow``, against PostgreSQL.

⚠️ Everything else in this repo proves the capture MECHANISM in isolation
(`tests/unit/flows/test_run_forecast_cycle_rejected_capture.py`) or proves
the EXISTING flow/e2e suite is unaffected by the D5/D6 wiring. Neither shows
a real cycle run actually landing a rejection in the table. This file does —
the gap both the code review and the high-risk review flagged as the item
most worth attention (review round, 2026-09-28).

Only the parent rows the ``rejected_forecasts`` foreign keys need (station,
model, artifact) are committed, on a SEPARATE connection from ``db_engine``
— the capture write runs in its OWN transaction, on its OWN connection (T1's
dedicated factory), so it cannot see anything uncommitted on ``db_connection``.
Everything else in the cycle (forecast, alert, pipeline-health, model-state,
observation, NWP, forcing stores) is fake: this file is about the capture
WIRING, not the forecast store's own atomicity (Plan 327/T1's job).

A committed ``rejected_forecasts`` row can never be deleted (T1's append-only
guard) and its parent rows are then held by an ordinary FK (`RESTRICT`), so
the positive-capture test's seed rows are NOT cleaned up afterward — the
session-scoped `db_engine` testcontainer is destroyed at the end of the
pytest session regardless. The lock-conflict test's write never commits, so
its seeds ARE cleaned up.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
import structlog.testing

from sapphire_flow.db.metadata import (
    model_artifacts,
    models,
    stations,
)
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow
from sapphire_flow.store.rejected_forecast_store import (
    PgRejectedForecastStore,
    rejected_capture_transaction_factory,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.domain import ForecastQcRuleParams, ForecastQcRuleSet
from sapphire_flow.types.ids import ModelId, StationId
from tests.fakes.fake_adapters import FakeWeatherForecastSource
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)
from tests.unit.flows.test_run_forecast_cycle import (
    _build_station_and_stores,
    _clock,
    _make_config,
    _SmallFakeModel,
)

# The fake model predicts uniform(1.0, 50.0) — a range excluding that band
# fails every member of every step, so the station's forecast is QC_FAILED
# deterministically (services/run_station_forecast.py:549 onward).
_ALL_REJECT_QC_RULES = ForecastQcRuleSet(
    version="test-all-reject",
    rules=(
        ForecastQcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(hours=1),
            thresholds={"value_min": 1000.0, "value_max": 2000.0},
        ),
    ),
)


def _fakes() -> dict[str, Any]:
    return {
        "station_store": FakeStationStore(),
        "obs_store": FakeObservationStore(),
        "nwp_store": FakeWeatherForecastStore(),
        "forecast_store": FakeForecastStore(),
        "state_store": FakeModelStateStore(),
        "artifact_store": FakeModelArtifactStore(),
        "alert_store": FakeAlertStore(),
        "baseline_store": FakeClimBaselineStore(),
        "basin_store": FakeBasinStore(),
        "forcing_store": FakeHistoricalForcingStore(),
        "pipeline_health_store": FakePipelineHealthStore(),
    }


def _commit_parents(
    engine: sa.Engine,
    fakes: dict[str, Any],
    station_id: StationId,
    model_id: ModelId,
) -> None:
    """Commit exactly the rows `rejected_forecasts`' FKs need, on their OWN
    connection — the capture write's dedicated connection (T1) is a
    completely separate physical connection and cannot see anything
    uncommitted elsewhere."""
    with engine.begin() as conn:
        station = fakes["station_store"].fetch_station(station_id)
        assert station is not None
        from dataclasses import replace

        PgStationStore(conn).store_station(
            replace(station, code=f"P404-{station_id.hex[:8]}")
        )
        conn.execute(
            sa.insert(models).values(
                id=model_id,
                display_name=str(model_id),
                artifact_scope="station",
                description="Plan 404 integration",
                created_at=fakes["station_store"].fetch_station(station_id).created_at,
            )
        )
        for artifact_id, record in fakes["artifact_store"]._records.items():
            conn.execute(
                sa.insert(model_artifacts).values(
                    id=artifact_id,
                    model_id=record.model_id,
                    station_id=record.station_id,
                    group_id=record.group_id,
                    status=record.status.value,
                    artifact_path=record.artifact_path,
                    sha256_hash=record.sha256_hash,
                    training_period_start=record.training_period_start,
                    training_period_end=record.training_period_end,
                    trained_at=record.trained_at,
                    promoted_at=None,
                    promoted_by=None,
                    superseded_at=None,
                    created_at=record.created_at,
                )
            )


def _cleanup_parents(
    engine: sa.Engine, station_id: StationId, model_id: ModelId
) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.delete(model_artifacts).where(model_artifacts.c.model_id == model_id)
        )
        conn.execute(sa.delete(models).where(models.c.id == model_id))
        conn.execute(sa.delete(stations).where(stations.c.id == station_id))


class TestRejectedCaptureAgainstRealFlow:
    def test_a_rejected_forecast_lands_in_the_table_through_a_real_cycle(
        self, db_engine: sa.Engine
    ) -> None:
        """Pre-change (red-first): before Plan 404 T2 wired the four
        collection points and the outermost-`finally` write, this flow
        parameter did not exist, and nothing called `write_batch` — this
        exact test would fail on a `TypeError` for the unexpected keyword.
        With it: the row round-trips through a REAL cycle run, not an
        isolated call to the capture helpers."""
        station_id = StationId(uuid4())
        model_id = ModelId("p404-e2e-reject-model")
        fakes = _fakes()
        _build_station_and_stores(
            station_id,
            model_id,
            fakes["station_store"],
            fakes["obs_store"],
            fakes["nwp_store"],
            fakes["artifact_store"],
            fakes["forcing_store"],
        )
        _commit_parents(db_engine, fakes, station_id, model_id)

        read_conn = db_engine.connect()
        capture_store = PgRejectedForecastStore(
            read_conn,
            transaction_factory=rejected_capture_transaction_factory(db_engine.url),
        )

        attempt_id = uuid4()
        result = run_forecast_cycle_flow(
            station_store=fakes["station_store"],
            obs_store=fakes["obs_store"],
            weather_forecast_store=fakes["nwp_store"],
            forecast_store=fakes["forecast_store"],
            model_state_store=fakes["state_store"],
            artifact_store=fakes["artifact_store"],
            alert_store=fakes["alert_store"],
            baseline_store=fakes["baseline_store"],
            basin_store=fakes["basin_store"],
            forcing_store=fakes["forcing_store"],
            pipeline_health_store=fakes["pipeline_health_store"],
            adapter=FakeWeatherForecastSource(result={}),
            models={model_id: _SmallFakeModel()},
            config=_make_config(),
            qc_rules=_ALL_REJECT_QC_RULES,
            clock=_clock,
            rejected_forecast_store=capture_store,
            id_gen=lambda: attempt_id,
        )

        # A station whose ONLY assigned model is QC-rejected produces zero
        # forecasts and legitimately goes "dark" (pre-existing behaviour,
        # unrelated to Plan 404 — see
        # test_run_forecast_cycle.py::test_station_dark_writes_pipeline_
        # health_and_degrades_cycle). Capture is unaffected either way: it
        # runs in the flow's outermost `finally`, independent of this
        # per-station accounting.
        assert result.stations_failed == 1
        assert any("produced zero forecasts" in err for err in result.errors)
        assert result.forecasts_stored == 0

        try:
            rows, total = capture_store.fetch_rejected_forecasts(
                station_id,
                _clock() - timedelta(days=1),
                _clock() + timedelta(days=1),
            )
            assert total == 1
            assert rows[0].attempt_id == attempt_id
            assert rows[0].station_id == station_id
            assert rows[0].model_id == model_id
            assert rows[0].qc_status.value == "qc_failed"
        finally:
            # A leaked open connection here would block the sibling test's
            # `LOCK TABLE ... ACCESS EXCLUSIVE` indefinitely — caught by
            # running the whole file together (a bounded single-test run
            # never surfaces it), 2026-09-28.
            read_conn.close()


class TestRejectedCaptureLockConflictAgainstRealFlow:
    def test_a_conflicting_lock_fails_capture_without_affecting_the_run(
        self, db_engine: sa.Engine
    ) -> None:
        """The plan's own prescribed case for this file (T2 Verification):
        another session holds a conflicting lock on `rejected_forecasts`
        during a real cycle run — the capture write fails with
        `lock_not_available` and logs `write_failed` once per rejected
        assignment, while the run's own result is unchanged. No row commits
        here, so the parent seeds ARE cleaned up."""
        station_id = StationId(uuid4())
        model_id = ModelId("p404-e2e-lock-model")
        fakes = _fakes()
        _build_station_and_stores(
            station_id,
            model_id,
            fakes["station_store"],
            fakes["obs_store"],
            fakes["nwp_store"],
            fakes["artifact_store"],
            fakes["forcing_store"],
        )
        _commit_parents(db_engine, fakes, station_id, model_id)

        read_conn = db_engine.connect()
        capture_store = PgRejectedForecastStore(
            read_conn,
            transaction_factory=rejected_capture_transaction_factory(db_engine.url),
        )

        blocker = db_engine.connect()
        blocker.execute(sa.text("BEGIN"))
        blocker.execute(
            sa.text("LOCK TABLE rejected_forecasts IN ACCESS EXCLUSIVE MODE")
        )
        try:
            with structlog.testing.capture_logs() as logs:
                result = run_forecast_cycle_flow(
                    station_store=fakes["station_store"],
                    obs_store=fakes["obs_store"],
                    weather_forecast_store=fakes["nwp_store"],
                    forecast_store=fakes["forecast_store"],
                    model_state_store=fakes["state_store"],
                    artifact_store=fakes["artifact_store"],
                    alert_store=fakes["alert_store"],
                    baseline_store=fakes["baseline_store"],
                    basin_store=fakes["basin_store"],
                    forcing_store=fakes["forcing_store"],
                    pipeline_health_store=fakes["pipeline_health_store"],
                    adapter=FakeWeatherForecastSource(result={}),
                    models={model_id: _SmallFakeModel()},
                    config=_make_config(),
                    qc_rules=_ALL_REJECT_QC_RULES,
                    clock=_clock,
                    rejected_forecast_store=capture_store,
                    id_gen=uuid4,
                )
        finally:
            blocker.execute(sa.text("ROLLBACK"))
            blocker.close()

        # The run's own outcome is untouched by the capture failure: the
        # station going "dark" (its sole model is QC-rejected — pre-existing
        # behaviour, see the sibling test above) is the SAME either way,
        # lock conflict or not — the capture failure adds no error of its
        # own to the result.
        assert result.stations_failed == 1
        assert sum("write_failed" in err or "lock" in err for err in result.errors) == 0

        write_failed = [
            e for e in logs if e.get("event") == "rejected_forecast.write_failed"
        ]
        assert len(write_failed) == 1
        assert write_failed[0]["station_id"] == str(station_id)
        assert write_failed[0]["model_id"] == str(model_id)

        try:
            total = capture_store.fetch_rejected_forecasts(
                station_id,
                _clock() - timedelta(days=1),
                _clock() + timedelta(days=1),
            )[1]
            assert total == 0
        finally:
            read_conn.close()

        _cleanup_parents(db_engine, station_id, model_id)
