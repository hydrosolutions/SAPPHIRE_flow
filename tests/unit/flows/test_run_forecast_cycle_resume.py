"""Plan 327 T2 — a forecast cycle that died partway must be re-runnable.

⭐ The discriminating shape, which took two attempts to state in the plan: the
re-run must complete with **NO error recorded**. Asserting "the missing station
now exists" passes TODAY — a station-path collision is caught and appended to
``errors`` while the remaining stations proceed regardless. The GROUP path is
the other discriminating shape: there the same collision is fatal and the whole
flow run fails.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
from structlog.testing import capture_logs

from sapphire_flow.exceptions import ForecastRetryConflictError, StoreError
from sapphire_flow.flows.run_forecast_cycle import (
    _persist_forecast,
    run_forecast_cycle_flow,
)
from sapphire_flow.services.forecast_retry import ForecastRetryRow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    AlertSource,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    ModelCombinationStrategy,
)
from sapphire_flow.types.forecast_evidence import EvidenceStatus, ForecastEvidence
from sapphire_flow.types.ids import (
    COMBINED_MODEL_IDS,
    POOLED_MODEL_ID,
    ForecastId,
    ModelId,
    StationId,
)
from sapphire_flow.types.station import ModelAssignment
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
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)
from tests.unit.flows.test_run_forecast_cycle import (
    _MODEL_ID,
    _NOW,
    _build_station_and_stores,
    _clock,
    _empty_qc_rules,
    _hourly_discharge_qc_rules_covering_the_step,
    _make_alerting_config,
    _make_config,
    _make_forecast_threshold,
    _SmallFakeGroupModel,
    _SmallFakeModel,
    _store_group_run,
)

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast


class _FailForStationForecastStore:
    """A cycle that dies partway: every forecast for ``target`` fails to
    store, everything else is written normally. Wraps ONE ``FakeForecastStore``
    so the surviving rows are still there for the re-run."""

    def __init__(self, inner: FakeForecastStore, target: StationId) -> None:
        self._inner = inner
        self._target = target

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        if forecast.station_id == self._target:
            raise StoreError("simulated mid-cycle store failure")
        return self._inner.store_forecast(forecast)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _stores() -> dict[str, object]:
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


def _run(
    stores: dict[str, object],
    forecast_store: object,
    *,
    models: dict[ModelId, object],
    seed: int = 42,
    config: object | None = None,
    group_store: object | None = None,
    pipeline_health_store: object | None = None,
    qc_rules: object | None = None,
):  # type: ignore[no-untyped-def]
    return run_forecast_cycle_flow(
        station_store=stores["station_store"],  # type: ignore[arg-type]
        obs_store=stores["obs_store"],  # type: ignore[arg-type]
        weather_forecast_store=stores["nwp_store"],  # type: ignore[arg-type]
        forecast_store=forecast_store,  # type: ignore[arg-type]
        model_state_store=stores["state_store"],  # type: ignore[arg-type]
        artifact_store=stores["artifact_store"],  # type: ignore[arg-type]
        alert_store=stores["alert_store"],  # type: ignore[arg-type]
        pipeline_health_store=pipeline_health_store,  # type: ignore[arg-type]
        baseline_store=stores["baseline_store"],  # type: ignore[arg-type]
        basin_store=stores["basin_store"],  # type: ignore[arg-type]
        group_store=group_store,  # type: ignore[arg-type]
        forcing_store=stores["forcing_store"],  # type: ignore[arg-type]
        adapter=FakeWeatherForecastSource(result={}),
        models=models,  # type: ignore[arg-type]
        config=config if config is not None else _make_config(),
        qc_rules=qc_rules if qc_rules is not None else _empty_qc_rules(),  # type: ignore[arg-type]
        clock=_clock,
        rng=random.Random(seed),
    )


class TestResumeStationPath:
    def test_a_cycle_that_died_partway_reruns_with_no_error_recorded(self) -> None:
        """🔴 The RED assertion is ``errors == []``.

        Before Plan 327 the re-run's collision on the already-written station
        was caught and appended to ``errors``, so the cycle reported a failure
        it should not have — while the *other* station completed either way,
        which is why "the missing station now exists" proves nothing here.
        """
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        stores = _stores()
        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                stores["station_store"],  # type: ignore[arg-type]
                stores["obs_store"],  # type: ignore[arg-type]
                stores["nwp_store"],  # type: ignore[arg-type]
                stores["artifact_store"],  # type: ignore[arg-type]
                stores["forcing_store"],  # type: ignore[arg-type]
            )
        forecast_store = FakeForecastStore()

        # Run 1 dies on station B.
        first = _run(
            stores,
            _FailForStationForecastStore(forecast_store, sid_b),
            models={_MODEL_ID: _SmallFakeModel()},
        )
        assert first.errors  # the interruption really happened
        stored_a = forecast_store.fetch_latest_forecast(sid_a)
        assert stored_a is not None
        assert forecast_store.fetch_latest_forecast(sid_b) is None

        # Run 2 is an IDENTICAL recomputation of the whole cycle.
        second = _run(stores, forecast_store, models={_MODEL_ID: _SmallFakeModel()})

        assert list(second.errors) == []
        assert second.forecasts_stored == 2
        resumed_a = forecast_store.fetch_latest_forecast(sid_a)
        completed_b = forecast_store.fetch_latest_forecast(sid_b)
        assert completed_b is not None
        # Station A is UNCHANGED — same row, same values, not rewritten.
        assert resumed_a is not None
        assert resumed_a.id == stored_a.id
        assert resumed_a.ensemble.values.equals(stored_a.ensemble.values)

    def test_an_identical_rerun_writes_no_second_row(self) -> None:
        """The constraint's protective half must survive: this plan removes an
        obstacle, it does not remove the guard."""
        sid = StationId(uuid4())
        stores = _stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
        )
        forecast_store = FakeForecastStore()

        first = _run(stores, forecast_store, models={_MODEL_ID: _SmallFakeModel()})
        issued_at = forecast_store.fetch_latest_forecast(sid).issued_at  # type: ignore[union-attr]
        after_first = forecast_store.fetch_forecasts_for_cycle(issued_at)

        second = _run(stores, forecast_store, models={_MODEL_ID: _SmallFakeModel()})

        assert list(first.errors) == []
        assert list(second.errors) == []
        after_second = forecast_store.fetch_forecasts_for_cycle(issued_at)
        assert len(after_second) == len(after_first) == 1
        assert after_second[0].id == after_first[0].id


class TestRefusal:
    def test_a_differing_rerun_is_refused_and_names_what_differed(self) -> None:
        sid = StationId(uuid4())
        stores = _stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
        )
        forecast_store = FakeForecastStore()

        _run(stores, forecast_store, models={_MODEL_ID: _SmallFakeModel()})
        original = forecast_store.fetch_latest_forecast(sid)
        assert original is not None

        # A DIFFERENT seed means a different recomputation: decision-table row 1.
        second = _run(
            stores, forecast_store, models={_MODEL_ID: _SmallFakeModel()}, seed=99
        )

        assert second.forecasts_stored == 0
        assert len(second.errors) == 1
        assert "Retry conflict" in second.errors[0]
        assert "row 1" in second.errors[0]
        # ⛔ Not written, not silently skipped: the stored row still holds run 1.
        kept = forecast_store.fetch_latest_forecast(sid)
        assert kept is not None
        assert kept.id == original.id
        assert kept.ensemble.values.equals(original.ensemble.values)

    def test_a_refused_forecast_is_not_alerted_on(self) -> None:
        """🔴 ``store_forecast``'s return is discarded and alerting consumes the
        in-memory ensemble, so a store-level refusal alone would still leave the
        cycle free to alert on content the store would not keep."""
        sid = StationId(uuid4())
        stores = _stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
        )
        stores["station_store"].store_thresholds([_make_forecast_threshold(sid)])  # type: ignore[union-attr]
        forecast_store = FakeForecastStore()
        alerting = _make_alerting_config()

        first = _run(
            stores,
            forecast_store,
            models={_MODEL_ID: _SmallFakeModel()},
            config=alerting,
        )
        assert first.alerts_checked is True
        raised_first = len(
            stores["alert_store"].fetch_active_alerts(source=AlertSource.FORECAST)  # type: ignore[union-attr]
        )
        assert raised_first > 0

        second = _run(
            stores,
            forecast_store,
            models={_MODEL_ID: _SmallFakeModel()},
            seed=99,
            config=alerting,
        )

        assert any("Retry conflict" in err for err in second.errors)
        assert second.alerts_checked is False
        assert (
            len(
                stores["alert_store"].fetch_active_alerts(source=AlertSource.FORECAST)  # type: ignore[union-attr]
            )
            == raised_first
        )


class TestResumeGroupPath:
    """⚠️ The station path tolerates a store failure; the GROUP path treats it
    as fatal. Before Plan 327 the re-run below raised out of the flow."""

    def _seed(self) -> tuple[dict[str, object], FakeStationGroupStore, ModelId]:
        stores = _stores()
        group_store = FakeStationGroupStore()
        group_model_id = ModelId("fake_group_model")
        station_ids = {StationId(uuid4()), StationId(uuid4())}
        for sid in station_ids:
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                stores["station_store"],  # type: ignore[arg-type]
                stores["obs_store"],  # type: ignore[arg-type]
                stores["nwp_store"],  # type: ignore[arg-type]
                stores["artifact_store"],  # type: ignore[arg-type]
                stores["forcing_store"],  # type: ignore[arg-type]
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            stores["artifact_store"],  # type: ignore[arg-type]
            group_model_id,
            frozenset(station_ids),
        )
        return stores, group_store, group_model_id

    def test_an_identical_group_rerun_completes(self) -> None:
        stores, group_store, group_model_id = self._seed()
        forecast_store = FakeForecastStore()
        health = FakePipelineHealthStore()

        first = _run(
            stores,
            forecast_store,
            models={group_model_id: _SmallFakeGroupModel()},
            group_store=group_store,
            pipeline_health_store=health,
        )
        assert first.forecasts_stored == 2

        second = _run(
            stores,
            forecast_store,
            models={group_model_id: _SmallFakeGroupModel()},
            group_store=group_store,
            pipeline_health_store=health,
        )

        assert list(second.errors) == []
        assert second.forecasts_stored == 2

    def test_a_differing_group_rerun_stays_fatal(self) -> None:
        stores, group_store, group_model_id = self._seed()
        forecast_store = FakeForecastStore()

        _run(
            stores,
            forecast_store,
            models={group_model_id: _SmallFakeGroupModel()},
            group_store=group_store,
        )

        with pytest.raises(ForecastRetryConflictError) as excinfo:
            _run(
                stores,
                forecast_store,
                models={group_model_id: _SmallFakeGroupModel()},
                group_store=group_store,
                seed=99,
            )

        assert excinfo.value.row is ForecastRetryRow.VALUES_DIFFER


class TestPersistForecastEvidenceRebinding:
    """🔴 IDs alone are not enough: a combination's evidence copies each
    contributor's in-memory evidence HASH, which the store checks against the
    PERSISTED evidence."""

    def _stored(self, store: FakeForecastStore, sid: StationId) -> OperationalForecast:
        result = store.fetch_latest_forecast(sid)
        assert result is not None
        return result

    def _seed_one(self) -> tuple[dict[str, object], StationId, FakeForecastStore]:
        sid = StationId(uuid4())
        stores = _stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
        )
        forecast_store = FakeForecastStore()
        _run(stores, forecast_store, models={_MODEL_ID: _SmallFakeModel()})
        return stores, sid, forecast_store

    def test_a_resumed_contributor_carries_the_persisted_id_and_hash(self) -> None:
        stores, sid, forecast_store = self._seed_one()
        persisted = self._stored(forecast_store, sid)
        assert persisted.evidence is not None

        # A re-run recomputes the SAME numbers under a NEW id, with its own
        # freshly captured (and therefore differently hashed) evidence.
        recomputed = replace(
            persisted,
            id=ForecastId(uuid4()),
            evidence=ForecastEvidence(
                status=EvidenceStatus.INCOMPLETE,
                manifest_json="{}",
                snapshot=b"a different snapshot",
                snapshot_sha256="0" * 64,
                artifact=None,
                artifact_sha256=None,
                reason="runtime_image_bytes_unpinned",
            ),
        )

        resolved = _persist_forecast(forecast_store, recomputed)  # type: ignore[arg-type]

        assert resolved.id == persisted.id
        assert resolved.evidence is not None
        assert resolved.evidence.snapshot_sha256 == persisted.evidence.snapshot_sha256
        assert resolved.evidence.reason == persisted.evidence.reason

    def test_honest_historical_absence_is_preserved_not_suppressed(self) -> None:
        """⛔ A contributor predating evidence capture legitimately yields
        ``contributor_evidence_not_persisted``. Only RETRY-INDUCED gaps are
        forbidden — an unconditional "never produces it" would make a branch
        this plan explicitly supports unsatisfiable."""
        sid = StationId(uuid4())
        forecast_store = FakeForecastStore()
        stores, seeded_sid, seeded_store = self._seed_one()
        template = self._stored(seeded_store, seeded_sid)

        pre_capture = replace(
            template,
            id=ForecastId(uuid4()),
            station_id=sid,
            evidence=None,
        )
        # ⚠️ NOT `store_forecast(evidence=None)` — that writes
        # `prediction_capture_unavailable` evidence, exactly as Postgres does.
        # Historical absence has to be seeded explicitly.
        forecast_store.seed_pre_capture_forecast(pre_capture)

        recomputed = replace(pre_capture, id=ForecastId(uuid4()))
        resolved = _persist_forecast(forecast_store, recomputed)  # type: ignore[arg-type]

        assert resolved.id == pre_capture.id
        assert resolved.evidence is not None
        assert resolved.evidence.reason == "pre_capture_forecast"
        # No snapshot digest to agree on — the combination check downstream
        # still (correctly) reports the contributor as not persisted.
        assert resolved.evidence.snapshot_sha256 is None


class _MutateCombinedForecastStore:
    """Stores the COMBINATION with shifted values and everything else verbatim.

    Used for the FIRST run only, so that the second run's honest recomputation
    of the same combination classifies as decision-table row 1 against a real
    stored row — the refusal is produced by the production classifier, not by
    the test.
    """

    def __init__(self, inner: FakeForecastStore) -> None:
        self._inner = inner

    def store_forecast(self, forecast: OperationalForecast) -> ForecastId:
        if forecast.model_id in COMBINED_MODEL_IDS:
            ensemble = forecast.ensemble
            forecast = replace(
                forecast,
                ensemble=ForecastEnsemble.from_members(
                    station_id=ensemble.station_id,
                    issued_at=ensemble.issued_at,
                    parameter=ensemble.parameter,
                    units=ensemble.units,
                    time_step=ensemble.time_step,
                    values=ensemble.values.with_columns(pl.col("value") + 1000.0),
                ),
            )
        return self._inner.store_forecast(forecast)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class TestRefusedCombinationDoesNotReachAlerting:
    """🔴 A combination is NEVER a key in ``all_ensembles`` — that dict holds the
    contributors, and the pooled alert strategy rebuilds the pool from them. So
    dropping ``_pooled`` from it is a no-op, and a refused combination has to
    reach alert STRATEGY selection or the cycle alerts on exactly the pooled
    content the store refused."""

    def _seed_two_models(self) -> tuple[dict[str, object], StationId, ModelId, ModelId]:
        sid = StationId(uuid4())
        model_a = ModelId("fake_model_a")
        model_b = ModelId("fake_model_b")
        stores = _stores()
        _build_station_and_stores(
            sid,
            model_a,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
        )
        stores["station_store"].store_model_assignment(  # type: ignore[union-attr]
            ModelAssignment(
                station_id=sid,
                model_id=model_b,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        stores["artifact_store"].store_artifact(  # type: ignore[union-attr]
            model_id=model_b,
            artifact_bytes=b"fake_artifact_b",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )
        stores["station_store"].store_thresholds([_make_forecast_threshold(sid)])  # type: ignore[union-attr]
        return stores, sid, model_a, model_b

    def test_a_refused_pooled_combination_degrades_alerting_to_primary(self) -> None:
        stores, sid, model_a, model_b = self._seed_two_models()
        forecast_store = FakeForecastStore()
        models = {model_a: _SmallFakeModel(), model_b: _SmallFakeModel()}
        config = _make_config(
            enable_forecast_alerts=True,
            alert_model_strategy=ModelCombinationStrategy.POOLED,
            forecast_combination_strategy=ModelCombinationStrategy.POOLED,
            danger_levels=[
                {
                    "name": "DL1",
                    "level": 1,
                    "color": "#facc15",
                    "trigger_probability": 0.1,
                    "resolve_probability": 0.05,
                }
            ],
        )

        first = _run(
            stores,
            _MutateCombinedForecastStore(forecast_store),
            models=models,
            config=config,
            qc_rules=_hourly_discharge_qc_rules_covering_the_step(),
        )
        assert first.alerts_checked is True
        raised = stores["alert_store"].fetch_active_alerts(source=AlertSource.FORECAST)  # type: ignore[union-attr]
        assert len(raised) == 1
        # The pooled combination WAS the alert basis on the first run.
        assert raised[0].alert_model_strategy is ModelCombinationStrategy.POOLED
        assert set(raised[0].model_ids) == {model_a, model_b}

        with capture_logs() as logs:
            second = _run(
                stores,
                forecast_store,
                models=models,
                config=config,
                qc_rules=_hourly_discharge_qc_rules_covering_the_step(),
            )

        # The members resumed; only the COMBINATION was refused (row 1).
        assert [err for err in second.errors if "Retry conflict" in err] == [
            err for err in second.errors
        ]
        assert any(
            "Retry conflict" in err and str(POOLED_MODEL_ID) in err
            for err in second.errors
        )

        after = stores["alert_store"].fetch_active_alerts(source=AlertSource.FORECAST)  # type: ignore[union-attr]
        assert len(after) == 1
        # ⛔ THE DEFECT: without the fix this alert is still POOLED — the cycle
        # acted on exactly the combined content the store refused to keep.
        assert after[0].alert_model_strategy is ModelCombinationStrategy.PRIMARY
        assert after[0].model_ids == (model_a,)
        assert any(
            entry.get("event") == "alert.strategy_degraded"
            and entry.get("reason") == "combination_refused"
            for entry in logs
        )
