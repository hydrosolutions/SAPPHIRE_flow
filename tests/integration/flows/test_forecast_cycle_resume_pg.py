"""Plan 327 T2 — the interrupted-cycle regression, against the REAL store.

⚠️ Everything here runs `run_forecast_cycle_flow` with a live
``PgForecastStore``. A fake cannot carry this regression: before Plan 327 the
fake ACCEPTED a duplicate natural key and afterwards it RESUMES one, so a
cycle test built on it distinguishes one fake from another rather than the new
behaviour from production's. Against Postgres the second run of an interrupted
cycle hit `uq_forecasts_station_model_issued_param`, the station path caught
the `IntegrityError` and appended it to ``errors``, and the group path
re-raised it out of the flow — which is exactly what these tests assert is
gone.

Only the forecast store is real; the rest of the cycle runs on the fakes, with
the rows the `forecasts` foreign keys require (station, model, artifact,
group) mirrored into Postgres.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    forecasts as forecasts_table,
)
from sapphire_flow.db.metadata import (
    model_artifacts,
    models,
    station_groups,
)
from sapphire_flow.exceptions import StoreError
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow
from sapphire_flow.store.forecast_store import PgForecastStore, _build_value_rows
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelCombinationStrategy
from sapphire_flow.types.ids import POOLED_MODEL_ID, ModelId, StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.fakes.fake_adapters import FakeWeatherForecastSource
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)
from tests.integration.store.test_forecast_store import savepoint_factory
from tests.unit.flows.test_run_forecast_cycle import (
    _MODEL_ID,
    _build_station_and_stores,
    _clock,
    _empty_qc_rules,
    _hourly_discharge_qc_rules_covering_the_step,
    _make_config,
    _SmallFakeGroupModel,
    _SmallFakeModel,
    _store_group_run,
)

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ForecastId

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


class _FailForStation:
    """A cycle that dies partway: every forecast for ``target`` fails to store,
    everything else is written normally by the REAL store."""

    def __init__(self, inner: PgForecastStore, target: StationId) -> None:
        self._inner = inner
        self._target = target

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        if forecast.station_id == self._target:
            raise StoreError("simulated mid-cycle store failure")
        return self._inner.store_forecast(forecast)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _FailForCombination:
    """Dies between the contributors and their combination — the resume
    hazard's own shape: the contributors commit, the combination does not."""

    def __init__(self, inner: PgForecastStore) -> None:
        self._inner = inner

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        if forecast.combination_strategy is not None:
            raise StoreError("simulated failure before the combination committed")
        return self._inner.store_forecast(forecast)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _PreCaptureWriter:
    """Writes the header and its values but NO evidence row — a forecast as it
    was written before migration ``0057``. Neither store can produce one now,
    so the historical case has to be seeded through the same write path the
    resume will later classify."""

    def __init__(self, inner: PgForecastStore, conn: sa.Connection) -> None:
        self._inner = inner
        self._conn = conn
        self.pre_capture_ids: list[ForecastId] = []

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        if forecast.combination_strategy is not None:
            raise StoreError("simulated failure before the combination committed")
        self._conn.execute(
            sa.insert(forecasts_table).values(
                id=forecast.id,
                station_id=forecast.station_id,
                model_id=forecast.model_id,
                model_artifact_id=forecast.model_artifact_id,
                issued_at=forecast.issued_at,
                time_step_seconds=int(forecast.ensemble.time_step.total_seconds()),
                nwp_cycle_reference_time=forecast.nwp_cycle_reference_time,
                nwp_cycle_source=forecast.nwp_cycle_source.value,
                representation=forecast.representation.value,
                status=forecast.status.value,
                version=forecast.version,
                parameter=forecast.ensemble.parameter,
                units=forecast.ensemble.units,
                created_at=forecast.created_at,
                updated_at=forecast.updated_at,
                qc_status=forecast.qc_status.value,
                qc_flags=[
                    {
                        "rule_id": flag.rule_id,
                        "rule_version": flag.rule_version,
                        "status": flag.status.value,
                        "detail": flag.detail,
                    }
                    for flag in forecast.qc_flags
                ],
            )
        )
        from sapphire_flow.db.metadata import forecast_values

        rows = _build_value_rows(forecast)
        if rows:
            self._conn.execute(sa.insert(forecast_values), rows)
        self.pre_capture_ids.append(forecast.id)
        return forecast.id

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _fakes() -> dict[str, Any]:
    return {
        "station_store": FakeStationStore(),
        "obs_store": FakeObservationStore(),
        "nwp_store": FakeWeatherForecastStore(),
        "artifact_store": FakeModelArtifactStore(),
        "state_store": FakeModelStateStore(),
        "alert_store": FakeAlertStore(),
        "baseline_store": FakeClimBaselineStore(),
        "basin_store": FakeBasinStore(),
        "forcing_store": FakeHistoricalForcingStore(),
    }


def _mirror_to_postgres(
    conn: sa.Connection,
    fakes: dict[str, Any],
    station_ids: list[StationId],
    model_ids: dict[ModelId, str],
    group_store: FakeStationGroupStore | None = None,
) -> None:
    """Insert the rows `forecasts`' foreign keys require. The fakes remain the
    flow's stores; only `forecast_store` is real."""
    pg_stations = PgStationStore(conn)
    for station_id in station_ids:
        station = fakes["station_store"].fetch_station(station_id)
        assert station is not None
        # `make_station_config` gives every station the same code, which
        # `uq_stations_network_code` rejects. The code plays no part in what
        # these tests exercise — the forecasts FK is on the id — so it is made
        # unique here rather than in the shared fixture.
        pg_stations.store_station(replace(station, code=f"P327-{station_id.hex[:8]}"))

    for model_id, scope in model_ids.items():
        conn.execute(
            sa.insert(models).values(
                id=model_id,
                display_name=str(model_id),
                artifact_scope=scope,
                description="Plan 327 integration",
                created_at=_NOW,
            )
        )

    if group_store is not None:
        for group in group_store._groups.values():
            conn.execute(
                sa.insert(station_groups).values(
                    id=group.id,
                    name=f"{group.name}-{group.id.hex[:8]}",
                    description=group.description,
                    created_at=_NOW,
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )

    # `_records` is the only listing this fake exposes; every artifact the
    # cycle may bind to a forecast needs a matching row or the FK rejects it.
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


def _run(
    fakes: dict[str, Any],
    forecast_store: object,
    *,
    models_by_id: dict[ModelId, object],
    config: object | None = None,
    qc_rules: object | None = None,
    group_store: object | None = None,
    seed: int = 42,
) -> Any:
    return run_forecast_cycle_flow(
        station_store=fakes["station_store"],
        obs_store=fakes["obs_store"],
        weather_forecast_store=fakes["nwp_store"],
        forecast_store=forecast_store,  # type: ignore[arg-type]
        model_state_store=fakes["state_store"],
        artifact_store=fakes["artifact_store"],
        alert_store=fakes["alert_store"],
        baseline_store=fakes["baseline_store"],
        basin_store=fakes["basin_store"],
        group_store=group_store,  # type: ignore[arg-type]
        forcing_store=fakes["forcing_store"],
        adapter=FakeWeatherForecastSource(result={}),
        models=models_by_id,  # type: ignore[arg-type]
        config=config if config is not None else _make_config(),
        qc_rules=qc_rules if qc_rules is not None else _empty_qc_rules(),  # type: ignore[arg-type]
        clock=_clock,
        rng=random.Random(seed),
    )


def _stored_rows(conn: sa.Connection, station_id: StationId) -> list[sa.Row[Any]]:
    return list(
        conn.execute(
            sa.select(
                forecasts_table.c.id,
                forecasts_table.c.model_id,
                forecasts_table.c.created_at,
            ).where(forecasts_table.c.station_id == station_id)
        ).fetchall()
    )


class TestStationPathResumeAgainstPostgres:
    def test_a_cycle_that_died_partway_reruns_with_no_error_recorded(
        self, db_connection: sa.Connection
    ) -> None:
        """🔴 RED before Plan 327 for the reason the defect exists: station A's
        row was already committed, so the re-run's INSERT violated
        `uq_forecasts_station_model_issued_param`; `run_forecast_cycle.py`
        caught that `IntegrityError` and appended it to ``errors``, so the
        cycle reported a failure it should not have. Station B completed
        either way — which is why "the missing station now exists" proves
        nothing and ``errors == []`` is the discriminating assertion."""
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        fakes = _fakes()
        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                fakes["station_store"],
                fakes["obs_store"],
                fakes["nwp_store"],
                fakes["artifact_store"],
                fakes["forcing_store"],
            )
        _mirror_to_postgres(
            db_connection, fakes, [sid_a, sid_b], {_MODEL_ID: "station"}
        )
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        first = _run(
            fakes,
            _FailForStation(store, sid_b),
            models_by_id={_MODEL_ID: _SmallFakeModel()},
        )
        assert first.errors, "the interruption must really have happened"
        before = _stored_rows(db_connection, sid_a)
        assert len(before) == 1
        assert _stored_rows(db_connection, sid_b) == []

        second = _run(fakes, store, models_by_id={_MODEL_ID: _SmallFakeModel()})

        assert list(second.errors) == []
        assert second.forecasts_stored == 2
        after = _stored_rows(db_connection, sid_a)
        assert len(after) == 1
        # Station A is UNCHANGED — same row, not rewritten.
        assert after[0].id == before[0].id
        assert after[0].created_at == before[0].created_at
        assert len(_stored_rows(db_connection, sid_b)) == 1


class TestGroupPathResumeAgainstPostgres:
    def test_an_identical_group_rerun_completes_instead_of_aborting(
        self, db_connection: sa.Connection
    ) -> None:
        """🔴 RED before Plan 327: the GROUP path re-raises ANY store exception,
        so the duplicate-key `IntegrityError` escaped the flow and the whole
        run went to Failed."""
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")
        fakes = _fakes()
        group_store = FakeStationGroupStore()
        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                fakes["station_store"],
                fakes["obs_store"],
                fakes["nwp_store"],
                fakes["artifact_store"],
                fakes["forcing_store"],
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            fakes["artifact_store"],
            group_model_id,
            frozenset({sid_a, sid_b}),
        )
        _mirror_to_postgres(
            db_connection,
            fakes,
            [sid_a, sid_b],
            {group_model_id: "group"},
            group_store=group_store,
        )
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )

        first = _run(
            fakes,
            store,
            models_by_id={group_model_id: _SmallFakeGroupModel()},
            group_store=group_store,
        )
        assert first.forecasts_stored == 2

        # Before Plan 327 this call RAISED out of the flow.
        second = _run(
            fakes,
            store,
            models_by_id={group_model_id: _SmallFakeGroupModel()},
            group_store=group_store,
        )

        assert list(second.errors) == []
        assert second.forecasts_stored == 2
        assert len(_stored_rows(db_connection, sid_a)) == 1
        assert len(_stored_rows(db_connection, sid_b)) == 1


class TestCombinationEvidenceThroughARealResume:
    """🔴 The resume path's own failure mode: a combination builds its evidence
    from the contributors held IN MEMORY, and the store checks those references
    against PERSISTED evidence. Resuming after the contributors committed but
    before their combination did would otherwise write an IMMUTABLE
    `contributor_evidence_not_persisted` record for a contributor that IS
    persisted."""

    def _seed(
        self, conn: sa.Connection
    ) -> tuple[dict[str, Any], StationId, ModelId, ModelId, PgForecastStore]:
        sid = StationId(uuid4())
        model_a = ModelId("fake_model_a")
        model_b = ModelId("fake_model_b")
        fakes = _fakes()
        _build_station_and_stores(
            sid,
            model_a,
            fakes["station_store"],
            fakes["obs_store"],
            fakes["nwp_store"],
            fakes["artifact_store"],
            fakes["forcing_store"],
        )
        from sapphire_flow.types.enums import ModelArtifactStatus, ModelAssignmentStatus
        from sapphire_flow.types.station import ModelAssignment

        fakes["station_store"].store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=model_b,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        fakes["artifact_store"].store_artifact(
            model_id=model_b,
            artifact_bytes=b"fake_artifact_b",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )
        _mirror_to_postgres(
            conn, fakes, [sid], {model_a: "station", model_b: "station"}
        )
        store = PgForecastStore(conn, transaction_factory=savepoint_factory(conn))
        return fakes, sid, model_a, model_b, store

    def _combined_evidence_reason(
        self, conn: sa.Connection, store: PgForecastStore, station_id: StationId
    ) -> str:
        row = conn.execute(
            sa.select(forecasts_table.c.id)
            .where(forecasts_table.c.station_id == station_id)
            .where(forecasts_table.c.model_id == POOLED_MODEL_ID)
        ).scalar_one()
        evidence = store.fetch_evidence(row)
        assert evidence is not None
        return evidence.reason or ""

    def test_resuming_between_contributors_and_combination_records_no_gap(
        self, db_connection: sa.Connection
    ) -> None:
        fakes, sid, model_a, model_b, store = self._seed(db_connection)
        config = _make_config(
            forecast_combination_strategy=ModelCombinationStrategy.POOLED
        )
        models_by_id = {model_a: _SmallFakeModel(), model_b: _SmallFakeModel()}

        first = _run(
            fakes,
            _FailForCombination(store),
            models_by_id=models_by_id,
            config=config,
            qc_rules=_hourly_discharge_qc_rules_covering_the_step(),
        )
        assert first.errors, "the combination must have failed to commit"
        assert (
            db_connection.execute(
                sa.select(sa.func.count())
                .select_from(forecasts_table)
                .where(forecasts_table.c.model_id == POOLED_MODEL_ID)
            ).scalar_one()
            == 0
        )

        second = _run(
            fakes,
            store,
            models_by_id=models_by_id,
            config=config,
            qc_rules=_hourly_discharge_qc_rules_covering_the_step(),
        )

        assert list(second.errors) == []
        reason = self._combined_evidence_reason(db_connection, store, sid)
        # ⛔ NOT "the evidence is COMPLETE" — no COMPLETE capture path exists,
        # so the ordinary incompleteness reasons are expected and permitted.
        # What must be absent are the RETRY-INDUCED ones.
        assert "contributor_evidence_not_persisted" not in reason
        assert "contributor_evidence_mismatch" not in reason
        assert reason, "a real capture always records at least one reason"

    def test_a_pre_capture_contributor_still_reports_honest_absence(
        self, db_connection: sa.Connection
    ) -> None:
        """⛔ Honest historical absence is PERMITTED and must survive the resume:
        a contributor predating evidence capture has no evidence row, so
        `contributor_evidence_not_persisted` is the correct record and the
        rebinding must not suppress it. Forbidding that reason unconditionally
        would make a branch this plan explicitly supports unsatisfiable."""
        fakes, sid, model_a, model_b, store = self._seed(db_connection)
        config = _make_config(
            forecast_combination_strategy=ModelCombinationStrategy.POOLED
        )
        models_by_id = {model_a: _SmallFakeModel(), model_b: _SmallFakeModel()}

        writer = _PreCaptureWriter(store, db_connection)
        first = _run(
            fakes,
            writer,
            models_by_id=models_by_id,
            config=config,
            qc_rules=_hourly_discharge_qc_rules_covering_the_step(),
        )
        assert first.errors  # the combination did not commit
        assert writer.pre_capture_ids, "contributors must have been written"
        assert (
            db_connection.execute(
                sa.select(sa.func.count())
                .select_from(sa.table("forecast_evidence"))
                .where(sa.column("forecast_id").in_(writer.pre_capture_ids))
            ).scalar_one()
            == 0
        )

        second = _run(
            fakes,
            store,
            models_by_id=models_by_id,
            config=config,
            qc_rules=_hourly_discharge_qc_rules_covering_the_step(),
        )

        assert list(second.errors) == []
        reason = self._combined_evidence_reason(db_connection, store, sid)
        assert "contributor_evidence_not_persisted" in reason
