from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl
import pytest  # noqa: TC002 — used at runtime for monkeypatch type annotation

from sapphire_flow.flows.compute_skills import (
    compute_combined_skills_flow,
    compute_combined_skills_task,
    compute_skills_flow,
    compute_skills_task,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from tests.fakes.fake_stores import (
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationStore,
)

_RNG = random.Random(99)
_EPOCH = ensure_utc(datetime(2025, 1, 15, 0, 0, tzinfo=UTC))


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _populate_stores(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    parameter: str = "discharge",
) -> tuple[
    FakeHindcastStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationStore,
    FakeFlowRegimeConfigStore,
]:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import (
        EnsembleRepresentation,
        ForcingType,
        ObservationSource,
        QcStatus,
    )
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import HindcastForecastId, ObservationId
    from sapphire_flow.types.observation import Observation

    hindcast_store = FakeHindcastStore()
    obs_store = FakeObservationStore()
    skill_store = FakeSkillStore()
    station_store = FakeStationStore()
    flow_regime_store = FakeFlowRegimeConfigStore()

    units = "m³/s" if parameter == "discharge" else "m"
    time_step = timedelta(hours=1)

    for i in range(3):
        step = ensure_utc(datetime(2025, 1, i + 1, tzinfo=UTC))
        vt = ensure_utc(datetime(2025, 1, i + 1, 1, 0, tzinfo=UTC))

        df = pl.DataFrame(
            [{"valid_time": vt, "member_id": m, "value": 10.0 + m} for m in range(3)]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )

        ensemble = ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=step,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
        hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=artifact_id,
            hindcast_step=step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=_uuid(),
            ensemble=ensemble,
            created_at=step,
        )
        hindcast_store.store_hindcast(hc)

        obs = Observation(
            id=ObservationId(_uuid()),
            station_id=station_id,
            timestamp=vt,
            parameter=parameter,
            value=10.5,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=step,
        )
        obs_store.store_observations([obs])

    return hindcast_store, obs_store, skill_store, station_store, flow_regime_store


class TestComputeSkillsTask:
    def test_water_level_parameter_computes_skill(self) -> None:
        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="water_level")
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="water_level",
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=clock,
        )

        assert len(scores) > 0
        assert all(s.parameter == "water_level" for s in scores)
        assert len(diagrams) > 0
        assert all(d.parameter == "water_level" for d in diagrams)

    def test_single_hindcast_still_fetches_observations_and_scores(self) -> None:
        """Plan 228 ALSO FIX #2: bounds derived from `hindcast_step` collapse
        to an empty `[min, max)` range for a single hindcast (min == max) —
        a production single-hindcast caller then fetched no observations at
        all and silently produced zero scores. Bounds must derive from the
        ensemble's own valid_time instead."""
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import (
            EnsembleRepresentation,
            ForcingType,
            ObservationSource,
            QcStatus,
        )
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId, ObservationId
        from sapphire_flow.types.observation import Observation

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        hindcast_store = FakeHindcastStore()
        obs_store = FakeObservationStore()
        skill_store = FakeSkillStore()
        station_store = FakeStationStore()
        flow_regime_store = FakeFlowRegimeConfigStore()

        step = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
        vt = ensure_utc(datetime(2025, 1, 1, 1, 0, tzinfo=UTC))
        time_step = timedelta(hours=1)
        df = pl.DataFrame(
            [{"valid_time": vt, "member_id": m, "value": 10.0 + m} for m in range(3)]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=step,
            parameter="discharge",
            units="m³/s",
            time_step=time_step,
            values=df,
        )
        hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            hindcast_step=step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=_uuid(),
            ensemble=ensemble,
            created_at=step,
        )
        hindcast_store.store_hindcast(hc)

        obs = Observation(
            id=ObservationId(_uuid()),
            station_id=sid,
            timestamp=vt,
            parameter="discharge",
            value=10.5,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=step,
        )
        obs_store.store_observations([obs])

        scores, _diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=clock,
        )

        assert len(scores) > 0

    def test_flow_wrapper_delegates_to_task(self) -> None:
        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_skills_flow(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=clock,
        )

        assert len(scores) > 0
        assert all(s.parameter == "discharge" for s in scores)

    def test_mixed_time_step_history_degrades_gracefully(self) -> None:
        """Review fixer round (major): `compute_skills_task` fetches a
        station/model's ENTIRE unpartitioned hindcast history (no
        `hindcast_run_id` filter, 1970-2100 bounds) and used to hand it
        straight to `observation_fetch_bounds`, which hard-raises
        `ConfigurationError` on any mixed `time_step` within it (Plan 228
        D4). A retraining or a future per-cycle anchoring change can leave
        an hourly-cadence hindcast run sitting alongside a daily-cadence
        one for the SAME station/model — the task must partition into
        homogeneous cohorts and score each, not raise and produce nothing.
        """
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import (
            EnsembleRepresentation,
            ForcingType,
            ObservationSource,
            QcStatus,
        )
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId, ObservationId
        from sapphire_flow.types.observation import Observation

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        # Pre-existing HOURLY-cadence hindcasts for this station/model.
        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        # A DAILY-cadence hindcast for the SAME station/model — a
        # differently configured run coexisting in history. Dated before
        # `_EPOCH` (2025-01-15, this test's `clock()`) so its resampled
        # bucket has actually elapsed by `now` — Plan 228's completed-bucket
        # filter (`_resample_observations_to_forecast_step`) correctly
        # excludes any bucket whose end has not yet elapsed, and a
        # future-dated fixture would be excluded for that reason rather
        # than exercising the cohort-partitioning behavior under test.
        daily_step = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
        daily_vt = ensure_utc(daily_step + timedelta(days=1))
        df = pl.DataFrame(
            [
                {"valid_time": daily_vt, "member_id": m, "value": 20.0 + m}
                for m in range(3)
            ]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        daily_ensemble = ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=daily_step,
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(days=1),
            values=df,
        )
        daily_hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            hindcast_step=daily_step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=_uuid(),
            ensemble=daily_ensemble,
            created_at=daily_step,
        )
        hindcast_store.store_hindcast(daily_hc)
        obs_store.store_observations(
            [
                Observation(
                    id=ObservationId(_uuid()),
                    station_id=sid,
                    timestamp=daily_vt,
                    parameter="discharge",
                    value=20.5,
                    source=ObservationSource.MEASURED,
                    rating_curve_id=None,
                    rating_curve_correction_version=None,
                    qc_status=QcStatus.QC_PASSED,
                    qc_flags=[],
                    qc_rule_version=None,
                    created_at=daily_step,
                )
            ]
        )

        # Buggy code: raises ConfigurationError("mixed time_step") from
        # `observation_fetch_bounds` before either cohort is scored.
        scores, _diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=clock,
        )

        assert len(scores) > 0
        lead_time_hours = {s.lead_time_hours for s in scores}
        # The hourly cohort scores short leads; the daily cohort scores a
        # 24h lead. Both must be present — the fix must not silently drop
        # one cohort in order to avoid the raise.
        assert any(h <= 3 for h in lead_time_hours)
        assert 24 in lead_time_hours


class TestComputeCombinedSkillsTask:
    def test_primary_strategy_returns_empty(self) -> None:
        from sapphire_flow.types.enums import ModelCombinationStrategy

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.PRIMARY,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert scores == []
        assert diagrams == []

    def test_single_model_returns_empty(self) -> None:
        from sapphire_flow.types.enums import ModelCombinationStrategy

        sid = StationId(_uuid())
        mid = ModelId("only-model")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert scores == []
        assert diagrams == []

    def test_two_models_computes_combined_skill(self) -> None:
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import (
            EnsembleRepresentation,
            ForcingType,
            ModelCombinationStrategy,
        )
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId

        sid = StationId(_uuid())
        mid1 = ModelId("model-a")
        mid2 = ModelId("model-b")
        aid1 = ArtifactId(_uuid())
        aid2 = ArtifactId(_uuid())

        stores = _populate_stores(sid, mid1, aid1)
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        # Add hindcasts for a second model at the same steps
        time_step = timedelta(hours=1)
        for i in range(3):
            step = ensure_utc(datetime(2025, 1, i + 1, tzinfo=UTC))
            vt = ensure_utc(datetime(2025, 1, i + 1, 1, 0, tzinfo=UTC))

            df = pl.DataFrame(
                [
                    {"valid_time": vt, "member_id": m, "value": 12.0 + m}
                    for m in range(3)
                ]
            ).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
                pl.col("member_id").cast(pl.Int32),
            )
            ensemble = ForecastEnsemble.from_members(
                station_id=sid,
                issued_at=step,
                parameter="discharge",
                units="m³/s",
                time_step=time_step,
                values=df,
            )
            hc = HindcastForecast(
                id=HindcastForecastId(_uuid()),
                station_id=sid,
                model_id=mid2,
                model_artifact_id=aid2,
                hindcast_step=step,
                forcing_type=ForcingType.REANALYSIS,
                representation=EnsembleRepresentation.MEMBERS,
                hindcast_run_id=_uuid(),
                ensemble=ensemble,
                created_at=step,
            )
            hindcast_store.store_hindcast(hc)

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert len(scores) > 0
        assert len(diagrams) > 0

    def test_bma_strategy_computes_combined_skill(self) -> None:
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import (
            EnsembleRepresentation,
            ForcingType,
            ModelCombinationStrategy,
            ObservationSource,
            QcStatus,
        )
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId, ObservationId
        from sapphire_flow.types.observation import Observation

        sid = StationId(_uuid())
        mid1 = ModelId("bma-model-a")
        mid2 = ModelId("bma-model-b")
        aid1 = ArtifactId(_uuid())
        aid2 = ArtifactId(_uuid())

        hindcast_store = FakeHindcastStore()
        obs_store = FakeObservationStore()
        skill_store = FakeSkillStore()
        station_store = FakeStationStore()
        flow_regime_store = FakeFlowRegimeConfigStore()

        time_step = timedelta(hours=1)
        n_steps = 6
        for i in range(n_steps):
            step = ensure_utc(datetime(2025, 1, i + 1, tzinfo=UTC))
            vt = ensure_utc(datetime(2025, 1, i + 1, 1, 0, tzinfo=UTC))

            df = pl.DataFrame(
                [
                    {"valid_time": vt, "member_id": m, "value": 10.0 + m}
                    for m in range(3)
                ]
            ).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
                pl.col("member_id").cast(pl.Int32),
            )

            for mid, val_offset in [(mid1, 0.0), (mid2, 1.0)]:
                ensemble = ForecastEnsemble.from_members(
                    station_id=sid,
                    issued_at=step,
                    parameter="discharge",
                    units="m³/s",
                    time_step=time_step,
                    values=df.with_columns(
                        (pl.col("value") + val_offset).alias("value")
                    ),
                )
                aid = aid1 if mid == mid1 else aid2
                hc = HindcastForecast(
                    id=HindcastForecastId(_uuid()),
                    station_id=sid,
                    model_id=mid,
                    model_artifact_id=aid,
                    hindcast_step=step,
                    forcing_type=ForcingType.REANALYSIS,
                    representation=EnsembleRepresentation.MEMBERS,
                    hindcast_run_id=_uuid(),
                    ensemble=ensemble,
                    created_at=step,
                )
                hindcast_store.store_hindcast(hc)

            obs = Observation(
                id=ObservationId(_uuid()),
                station_id=sid,
                timestamp=vt,
                parameter="discharge",
                value=10.5,
                source=ObservationSource.MEASURED,
                rating_curve_id=None,
                rating_curve_correction_version=None,
                qc_status=QcStatus.QC_PASSED,
                qc_flags=[],
                qc_rule_version=None,
                created_at=step,
            )
            obs_store.store_observations([obs])

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.BMA,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert len(scores) > 0
        assert len(diagrams) > 0

    def test_mixed_time_step_across_models_degrades_gracefully(self) -> None:
        """Review fixer round (major): `compute_combined_skills_task` is
        the SHARPER case of the `compute_skills_task` finding — it UNIONS
        hindcasts across every combined model before validating, so ONE
        model carrying a differently configured (e.g. daily) hindcast run
        alongside its normal hourly one used to poison the whole
        combination's `observation_fetch_bounds` call, raising
        `ConfigurationError` and producing NO scores for either model. The
        fix must partition per model first and still combine whichever
        cohort has >= 2 models.
        """
        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import (
            EnsembleRepresentation,
            ForcingType,
            ModelCombinationStrategy,
        )
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId

        sid = StationId(_uuid())
        mid1 = ModelId("model-a")
        mid2 = ModelId("model-b")
        aid1 = ArtifactId(_uuid())
        aid2 = ArtifactId(_uuid())

        stores = _populate_stores(sid, mid1, aid1)
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        # model-b gets the SAME hourly-cadence hindcasts as model-a, so the
        # hourly cohort has 2 models and can be combined.
        time_step = timedelta(hours=1)
        for i in range(3):
            step = ensure_utc(datetime(2025, 1, i + 1, tzinfo=UTC))
            vt = ensure_utc(datetime(2025, 1, i + 1, 1, 0, tzinfo=UTC))
            df = pl.DataFrame(
                [
                    {"valid_time": vt, "member_id": m, "value": 12.0 + m}
                    for m in range(3)
                ]
            ).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
                pl.col("member_id").cast(pl.Int32),
            )
            ensemble = ForecastEnsemble.from_members(
                station_id=sid,
                issued_at=step,
                parameter="discharge",
                units="m³/s",
                time_step=time_step,
                values=df,
            )
            hc = HindcastForecast(
                id=HindcastForecastId(_uuid()),
                station_id=sid,
                model_id=mid2,
                model_artifact_id=aid2,
                hindcast_step=step,
                forcing_type=ForcingType.REANALYSIS,
                representation=EnsembleRepresentation.MEMBERS,
                hindcast_run_id=_uuid(),
                ensemble=ensemble,
                created_at=step,
            )
            hindcast_store.store_hindcast(hc)

        # model-a ALSO has a DAILY-cadence hindcast (a differently
        # configured run) that model-b does NOT have — the daily cohort
        # has only 1 model and must be skipped, not crash the whole task.
        daily_step = ensure_utc(datetime(2025, 2, 1, tzinfo=UTC))
        daily_vt = ensure_utc(daily_step + timedelta(days=1))
        daily_df = pl.DataFrame(
            [
                {"valid_time": daily_vt, "member_id": m, "value": 30.0 + m}
                for m in range(3)
            ]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        daily_ensemble = ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=daily_step,
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(days=1),
            values=daily_df,
        )
        daily_hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=sid,
            model_id=mid1,
            model_artifact_id=aid1,
            hindcast_step=daily_step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=_uuid(),
            ensemble=daily_ensemble,
            created_at=daily_step,
        )
        hindcast_store.store_hindcast(daily_hc)

        # Buggy code: `observation_fetch_bounds(all_hindcasts)` raises
        # ConfigurationError("mixed time_step") over the union before
        # either model is scored.
        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert len(scores) > 0
        assert len(diagrams) > 0

    def test_flow_wrapper_delegates_to_task(self) -> None:
        from sapphire_flow.types.enums import ModelCombinationStrategy

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        hindcast_store, obs_store, skill_store, station_store, flow_regime_store = (
            stores
        )

        scores, diagrams = compute_combined_skills_flow(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.PRIMARY,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert scores == []
        assert diagrams == []


class TestBootstrapPath:
    def test_compute_skills_flow_bootstrap_resolves_stores_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        stores_dict = {
            "station_store": MagicMock(),
            "hindcast_store": MagicMock(),
            "obs_store": MagicMock(),
            "skill_store": MagicMock(),
            "flow_regime_store": MagicMock(),
            "model_store": MagicMock(),
            "group_store": MagicMock(),
            "basin_store": MagicMock(),
            "artifact_store": MagicMock(),
        }
        captured: dict[str, object] = {}

        def fake_setup(url: str) -> tuple[object, dict]:
            captured["url"] = url
            return (MagicMock(), stores_dict)

        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores", fake_setup
        )

        with patch(
            "sapphire_flow.flows.compute_skills.compute_skills_task",
            return_value=([], []),
        ) as mock_task:
            scores, diagrams = compute_skills_flow.fn(
                station_id=StationId(_uuid()),
                model_id=ModelId("fake_model"),
                artifact_id=ArtifactId(_uuid()),
                parameter="discharge",
                clock=lambda: _EPOCH,
            )

        assert captured["url"] == "sqlite://"
        assert scores == []
        assert diagrams == []
        assert mock_task.called
        call_kwargs = mock_task.call_args.kwargs
        assert call_kwargs["hindcast_store"] is stores_dict["hindcast_store"]
        assert call_kwargs["obs_store"] is stores_dict["obs_store"]
        assert call_kwargs["skill_store"] is stores_dict["skill_store"]
        assert call_kwargs["station_store"] is stores_dict["station_store"]
        assert call_kwargs["flow_regime_store"] is stores_dict["flow_regime_store"]

    def test_compute_combined_skills_flow_bootstrap_resolves_stores_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        from sapphire_flow.types.enums import ModelCombinationStrategy

        stores_dict = {
            "station_store": MagicMock(),
            "hindcast_store": MagicMock(),
            "obs_store": MagicMock(),
            "skill_store": MagicMock(),
            "flow_regime_store": MagicMock(),
            "model_store": MagicMock(),
            "group_store": MagicMock(),
            "basin_store": MagicMock(),
            "artifact_store": MagicMock(),
        }
        captured: dict[str, object] = {}

        def fake_setup(url: str) -> tuple[object, dict]:
            captured["url"] = url
            return (MagicMock(), stores_dict)

        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores", fake_setup
        )

        with patch(
            "sapphire_flow.flows.compute_skills.compute_combined_skills_task",
            return_value=([], []),
        ) as mock_task:
            scores, diagrams = compute_combined_skills_flow.fn(
                station_id=StationId(_uuid()),
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
                clock=lambda: _EPOCH,
            )

        assert captured["url"] == "sqlite://"
        assert scores == []
        assert diagrams == []
        assert mock_task.called
        call_kwargs = mock_task.call_args.kwargs
        assert call_kwargs["hindcast_store"] is stores_dict["hindcast_store"]
        assert call_kwargs["obs_store"] is stores_dict["obs_store"]
        assert call_kwargs["skill_store"] is stores_dict["skill_store"]
        assert call_kwargs["station_store"] is stores_dict["station_store"]
        assert call_kwargs["flow_regime_store"] is stores_dict["flow_regime_store"]
