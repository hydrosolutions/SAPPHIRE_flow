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
from tests.conftest import make_deployment_config
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
    UUID,
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
    # Plan 235 T3: one shared run id for every step this "hindcast run"
    # produces — mirrors production (`flows/train_models.py` mints ONE
    # `hindcast_run_id` per onboarding/training unit, reused across its
    # whole `run_station_hindcast` call), and is what a caller now passes to
    # `compute_skills_task`'s (no longer optional) `hindcast_run_id`.
    run_id = _uuid()

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
            hindcast_run_id=run_id,
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

    return (
        hindcast_store,
        obs_store,
        skill_store,
        station_store,
        flow_regime_store,
        run_id,
    )


def _add_daily_cohort(
    hindcast_store: FakeHindcastStore,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    hindcast_run_id: UUID,
) -> None:
    """Adds a second, DAILY-cadence cohort (same run id, same station/
    model) to a `hindcast_store` already seeded by `_populate_stores`'
    hourly cohort — giving `compute_skills_task`'s cohort loop TWO cohorts
    to iterate instead of one."""
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import HindcastForecastId

    daily_step = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
    daily_vt = ensure_utc(daily_step + timedelta(days=1))
    df = pl.DataFrame(
        [{"valid_time": daily_vt, "member_id": m, "value": 20.0 + m} for m in range(3)]
    ).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    daily_ensemble = ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=daily_step,
        parameter="discharge",
        units="m³/s",
        time_step=timedelta(days=1),
        values=df,
    )
    hindcast_store.store_hindcast(
        HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=artifact_id,
            hindcast_step=daily_step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=hindcast_run_id,
            ensemble=daily_ensemble,
            created_at=daily_step,
        )
    )


class TestComputeSkillsTask:
    def test_water_level_parameter_computes_skill(self) -> None:
        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="water_level")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        scores, diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="water_level",
            hindcast_run_id=hindcast_run_id,
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
        hindcast_run_id = _uuid()
        hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            hindcast_step=step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=hindcast_run_id,
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
            hindcast_run_id=hindcast_run_id,
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        scores, diagrams = compute_skills_flow(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_run_id=hindcast_run_id,
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

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
            # Plan 235 T3: SAME run id as the hourly cohort above — with
            # `hindcast_run_id` now required and scoping the fetch, a
            # "mixed cohorts within one call" scenario can only arise from
            # ONE run legitimately producing mixed time_steps (the case
            # this test exercises), never from an unscoped fetch spanning
            # multiple runs.
            hindcast_run_id=hindcast_run_id,
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
            hindcast_run_id=hindcast_run_id,
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


class TestCompletenessGate:
    """Plan 235 fixer round (blocker): the completeness gate must span the
    WHOLE task's cohort loop, not each cohort in isolation — if a LATER
    cohort fails after an EARLIER one already produced results, nothing
    from either cohort may become visible: no store write, no generation
    publication. `test_partial_generation_leaves_previous_publication_
    intact` (store-level) only proves the store correctly hides an
    already-unpublished generation; it never exercises the task's own
    cohort loop, which is what decides whether store/publish get called at
    all. This drives a REAL two-cohort fan-out through `compute_skills_
    task.fn` and fails the second cohort's compute.
    """

    def test_later_cohort_raising_stores_and_publishes_nothing(self) -> None:
        from unittest.mock import MagicMock, patch

        from sapphire_flow.flows import compute_skills as compute_skills_module
        from sapphire_flow.services.skill.service import (
            compute_skill_for_station as real_compute_skill_for_station,
        )

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        # `_populate_stores` seeds one HOURLY cohort (3 steps); add a
        # second, DAILY cohort under the SAME run id so the task's loop
        # has two cohorts to iterate.
        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores
        _add_daily_cohort(hindcast_store, sid, mid, aid, hindcast_run_id)

        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        call_count = {"n": 0}

        def _fail_on_second_cohort(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("simulated failure scoring the second cohort")
            return real_compute_skill_for_station(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.object(
                compute_skills_module,
                "compute_skill_for_station",
                side_effect=_fail_on_second_cohort,
            ),
            pytest.raises(RuntimeError, match="simulated failure"),
        ):
            compute_skills_task.fn(
                station_id=sid,
                model_id=mid,
                artifact_id=aid,
                parameter="discharge",
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                clock=clock,
            )

        assert call_count["n"] == 2, "expected exactly 2 cohorts to be attempted"
        assert skill_store.fetch_latest_scores(sid, mid) == [], (
            "the first cohort's scores must never be stored once a later "
            "cohort in the SAME task invocation fails"
        )
        skill_store.publish_generation.assert_not_called()


class TestSilentCohortGapBlocksPublication:
    """Plan 235 fixer round (blocker, D3 completeness): a cohort that
    returns EMPTY results (no exception — a malformed hindcast, zero
    overlapping observations, any other silent per-cohort failure) must
    ALSO block publication, not just a cohort that RAISES
    (`TestCompletenessGate` above). Otherwise a degraded run silently
    publishes a PARTIAL generation that supersedes-and-hides a previously
    COMPLETE one.
    """

    def test_one_empty_cohort_among_others_blocks_publication(self) -> None:
        from unittest.mock import MagicMock, patch

        from sapphire_flow.flows import compute_skills as compute_skills_module
        from sapphire_flow.services.skill.service import (
            compute_skill_for_station as real_compute_skill_for_station,
        )

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores
        _add_daily_cohort(hindcast_store, sid, mid, aid, hindcast_run_id)

        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        call_count = {"n": 0}

        def _empty_on_second_cohort(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return [], []
            return real_compute_skill_for_station(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(
            compute_skills_module,
            "compute_skill_for_station",
            side_effect=_empty_on_second_cohort,
        ):
            scores, _diagrams = compute_skills_task.fn(
                station_id=sid,
                model_id=mid,
                artifact_id=aid,
                parameter="discharge",
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                deployment_config=make_deployment_config(enable_skill_generations=True),
                clock=clock,
            )

        assert call_count["n"] == 2, "expected exactly 2 cohorts to be attempted"
        assert scores, "the first (successful) cohort's scores are still returned"
        skill_store.publish_generation.assert_not_called()
        assert skill_store.fetch_latest_scores(sid, mid) == [], (
            "an unpublished generation's scores must never become visible "
            "to readers, even though they were physically stored"
        )


class TestComputeCombinedSkillsTask:
    def test_primary_strategy_returns_empty(self) -> None:
        from sapphire_flow.types.enums import ModelCombinationStrategy

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.PRIMARY,
            hindcast_run_ids={mid: hindcast_run_id},
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcast_run_ids={mid: hindcast_run_id},
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        # Add hindcasts for a second model at the same steps, all under ONE
        # shared run id (Plan 235 T3 — one hindcast run's own multi-step
        # history shares one `hindcast_run_id`).
        hindcast_run_id_2 = _uuid()
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
                hindcast_run_id=hindcast_run_id_2,
                ensemble=ensemble,
                created_at=step,
            )
            hindcast_store.store_hindcast(hc)

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcast_run_ids={mid1: hindcast_run_id, mid2: hindcast_run_id_2},
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
        # Plan 235 T3: one shared run id PER model, reused across all its
        # steps below.
        run_ids = {mid1: _uuid(), mid2: _uuid()}
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
                    hindcast_run_id=run_ids[mid],
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
            hindcast_run_ids=run_ids,
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        # model-b gets the SAME hourly-cadence hindcasts as model-a, so the
        # hourly cohort has 2 models and can be combined. Plan 235 T3: its
        # own shared run id, distinct from model-a's.
        hindcast_run_id_2 = _uuid()
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
                hindcast_run_id=hindcast_run_id_2,
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
            # Plan 235 T3: SAME run id as model-a's hourly cohort — see
            # `test_mixed_time_step_history_degrades_gracefully`'s identical
            # note.
            hindcast_run_id=hindcast_run_id,
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
            hindcast_run_ids={mid1: hindcast_run_id, mid2: hindcast_run_id_2},
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        scores, diagrams = compute_combined_skills_flow(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.PRIMARY,
            hindcast_run_ids={mid: hindcast_run_id},
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert scores == []
        assert diagrams == []


class TestMissingRequestedModelBlocksPublication:
    """Plan 235 fixer round (blocker, D3 completeness):
    `fetch_hindcasts_by_station` OMITS a requested model's key entirely
    when it has no matching hindcast rows — it never returns an empty
    list for it. A caller that requested N models but got fewer back must
    not silently combine and publish those as if the combination were
    complete.
    """

    def test_missing_requested_model_skips_publish(self) -> None:
        from unittest.mock import MagicMock

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
        mid3 = ModelId("model-c")  # requested, but NEVER seeded any hindcasts
        aid1 = ArtifactId(_uuid())
        aid2 = ArtifactId(_uuid())

        stores = _populate_stores(sid, mid1, aid1)
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        hindcast_run_id_2 = _uuid()
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
                hindcast_run_id=hindcast_run_id_2,
                ensemble=ensemble,
                created_at=step,
            )
            hindcast_store.store_hindcast(hc)

        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        # mid3 is REQUESTED with a run id nothing was ever stored under —
        # `fetch_hindcasts_by_station` simply omits it from the result.
        scores, _diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcast_run_ids={
                mid1: hindcast_run_id,
                mid2: hindcast_run_id_2,
                mid3: _uuid(),
            },
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        # Plan 235 per-run scope (blocker #2): a cohort must carry the
        # FULL requested model set to combine — mid3 has zero hindcasts
        # ANYWHERE, so no cohort can ever satisfy {mid1, mid2, mid3}, and
        # nothing should combine or score, not merely "not publish".
        assert scores == [], (
            "3 models were requested but mid3 has ZERO hindcasts anywhere "
            "— no cohort can ever satisfy the full requested set, so "
            "nothing should combine, let alone publish"
        )
        skill_store.publish_generation.assert_not_called()

        from sapphire_flow.types.ids import POOLED_MODEL_ID

        assert skill_store.fetch_latest_scores(sid, POOLED_MODEL_ID) == [], (
            "a combination missing a REQUESTED model must never publish, "
            "even though 2 of the 3 requested models were available"
        )


def _make_incrementing_clock():  # type: ignore[no-untyped-def]
    """A clock whose successive calls return strictly increasing
    timestamps — lets a test distinguish "the publication instant" from
    "some earlier cohort's `computed_at`" by ordering alone."""
    counter = {"n": 0}

    def _clock():  # type: ignore[no-untyped-def]
        counter["n"] += 1
        return ensure_utc(
            datetime(2025, 1, 15, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=counter["n"])
        )

    return _clock


class TestPublicationInstant:
    """Plan 235 fixer round (major): `published_at` must be the ACTUAL
    publication instant — the last `clock()` call, immediately before the
    `publish_generation` insert — not a cohort's `computed_at` captured
    while a LATER cohort in the same task was still being scored. D2b#1
    precedence (`computation_version`, then `published_at`) depends on
    that being the true publication order.
    """

    def test_published_at_is_after_every_cohorts_computed_at(self) -> None:
        from unittest.mock import MagicMock

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores
        # Two cohorts means at least two internal `clock()` calls happen
        # (one while scoring each cohort) BEFORE the publish-time call this
        # test is trying to isolate.
        _add_daily_cohort(hindcast_store, sid, mid, aid, hindcast_run_id)

        clock = _make_incrementing_clock()
        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_run_id=hindcast_run_id,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            deployment_config=make_deployment_config(enable_skill_generations=True),
            clock=clock,
        )

        skill_store.publish_generation.assert_called_once()
        published_at = skill_store.publish_generation.call_args.kwargs["published_at"]
        stored_scores = skill_store.fetch_latest_scores(sid, mid)
        assert stored_scores, "expected both cohorts to have scored something"
        max_computed_at = max(s.computed_at for s in stored_scores)

        # The buggy code used `all_scores[0].computed_at` — the FIRST
        # cohort's compute time — as `published_at`. With a strictly
        # increasing clock and 2 cohorts, that value can never be >= the
        # LAST cohort's `computed_at`, so this assertion is exactly what
        # the bug violates.
        assert published_at > max_computed_at, (
            "published_at must be captured AFTER every cohort finished "
            "scoring, not reused from the first cohort's computed_at"
        )


class TestStoreCountMismatchSkipsPublish:
    """Plan 235 fixer round (major/blocker): `ON CONFLICT DO NOTHING` can
    silently drop rows whose natural key collides. A REAL mismatch (fewer
    rows persisted for `generation_id` than this attempt's own scores call
    for, per `count_generation_rows`) is a genuine bug and must RAISE
    `SkillGenerationIncompleteError` — never `publish_generation` — rather
    than silently logging an error and returning normally, which would
    leave Prefect believing the task run SUCCEEDED (fixer round, blocker:
    D1 retry stability's "raise on a real mismatch").
    """

    def test_score_count_mismatch_raises_and_skips_publish(self) -> None:
        from unittest.mock import MagicMock

        from sapphire_flow.exceptions import SkillGenerationIncompleteError

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        # Simulate a natural-key collision: the store reports it inserted
        # ONE fewer score row than it was asked to, and never actually
        # persists it (so `count_generation_rows` sees the real gap too —
        # not just a benign retry where the row already exists from an
        # earlier attempt).
        skill_store.store_skill_scores = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda scores: max(len(scores) - 1, 0)
        )
        skill_store.publish_generation = MagicMock()  # type: ignore[method-assign]

        with pytest.raises(SkillGenerationIncompleteError):
            compute_skills_task.fn(
                station_id=sid,
                model_id=mid,
                artifact_id=aid,
                parameter="discharge",
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                deployment_config=make_deployment_config(enable_skill_generations=True),
                clock=lambda: _EPOCH,
            )

        skill_store.publish_generation.assert_not_called()


class TestRetryStablePublicationSucceeds:
    """Plan 235 fixer round (blocker, D1 retry stability): a Prefect retry
    of `compute_skills_task` replays with the SAME (retry-stable)
    `generation_id` as an earlier attempt that crashed AFTER storing scores
    but BEFORE publishing. The retry's rows collide against the earlier
    attempt's identical natural key + generation_id, so `store_skill_
    scores` reports 0 newly inserted THIS call — that must NOT be read as
    "still incomplete forever"; `count_generation_rows`'s TOTAL lets the
    retry reconcile correctly and publish.

    Plan 235 per-run scope (blocker #3): the argument bound at both call
    sites below is only the retry-stable INVOCATION id — the row's actual
    `generation_id` is now derived from that id plus a content fingerprint
    of what was computed (`resolve_generation_id`), so this test captures
    the id ACTUALLY used (via a spy on `store_skill_scores`) rather than
    asserting it equals the bound argument verbatim. With genuinely
    UNCHANGED inputs across both calls (this test's whole point), the two
    attempts must still derive the SAME final id.
    """

    def test_retry_with_orphaned_rows_from_earlier_crash_still_publishes(
        self,
    ) -> None:
        from unittest.mock import MagicMock

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        fixed_generation_id = _uuid()

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        captured_generation_ids: list[UUID] = []
        real_store_skill_scores = skill_store.store_skill_scores

        def _spy_store_skill_scores(scores: list) -> int:  # type: ignore[type-arg]
            if scores:
                captured_generation_ids.append(scores[0].generation_id)
            return real_store_skill_scores(scores)

        skill_store.store_skill_scores = _spy_store_skill_scores  # type: ignore[method-assign]

        # Attempt 1 "crashes" — its rows land, but its publish never runs.
        real_publish_generation = skill_store.publish_generation
        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("simulated crash before publish")
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
            compute_skills_task.fn(
                station_id=sid,
                model_id=mid,
                artifact_id=aid,
                parameter="discharge",
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                deployment_config=make_deployment_config(enable_skill_generations=True),
                clock=lambda: _EPOCH,
                generation_id=fixed_generation_id,
            )

        assert skill_store.fetch_latest_scores(sid, mid) == [], (
            "attempt 1's crash-before-publish must leave nothing visible"
        )
        assert len(captured_generation_ids) == 1, (
            "attempt 1 must have stored scores despite the crash"
        )
        attempt_1_generation_id = captured_generation_ids[0]
        orphaned_scores, _ = skill_store.count_generation_rows(attempt_1_generation_id)
        assert orphaned_scores > 0, (
            "attempt 1 must have persisted its scores despite the crash"
        )

        # The retry — Prefect replays the SAME task run with the IDENTICAL
        # (invocation) generation_id and, since nothing about the inputs
        # changed, must derive the SAME final generation_id as attempt 1.
        skill_store.publish_generation = real_publish_generation
        scores, _diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_run_id=hindcast_run_id,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            deployment_config=make_deployment_config(enable_skill_generations=True),
            clock=lambda: _EPOCH,
            generation_id=fixed_generation_id,
        )

        assert scores, "the retry must still compute its scores"
        assert scores[0].generation_id == attempt_1_generation_id, (
            "same invocation id + unchanged inputs must derive the SAME "
            "final generation id as attempt 1, so the retry's rows land "
            "on top of, not beside, the orphaned ones"
        )
        assert skill_store.fetch_latest_scores(sid, mid), (
            "the retry must publish successfully — an earlier crash's "
            "orphaned rows under the SAME generation_id must not make this "
            "generation look incomplete forever"
        )


class TestEmptyHindcastRunIdsMapping:
    """Plan 235 fixer round (blocker): `hindcast_run_ids` is required (no
    default) on `compute_combined_skills_task` — but an explicitly EMPTY
    mapping named zero models and used to fall through to
    `PgHindcastStore`'s unscoped-fetch fallback (`if hindcast_run_ids:`
    is falsy for both `None` and `{}`). Reject it at the task boundary too
    (defense in depth) rather than relying solely on the store.
    """

    def test_empty_mapping_raises_configuration_error(self) -> None:
        from sapphire_flow.exceptions import ConfigurationError
        from sapphire_flow.types.enums import ModelCombinationStrategy

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            _hindcast_run_id,
        ) = stores

        with pytest.raises(ConfigurationError, match="hindcast_run_ids"):
            compute_combined_skills_task.fn(
                station_id=sid,
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
                hindcast_run_ids={},
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                clock=lambda: _EPOCH,
            )


class TestGenerationIdRetryStability:
    """Plan 235 fixer round (major, D1): `generation_id` must be minted
    OUTSIDE the retrying task so a Prefect retry (which re-runs the task's
    function body with the SAME bound arguments) replays with the
    identical id, instead of re-entering a `None`-default branch and
    minting a new uuid4() on every retry. The flow wrappers now mint it
    before calling the task.
    """

    def test_compute_skills_flow_mints_a_concrete_id_before_calling_the_task(
        self,
    ) -> None:
        from unittest.mock import patch

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        with patch(
            "sapphire_flow.flows.compute_skills.compute_skills_task",
            return_value=([], []),
        ) as mock_task:
            compute_skills_flow(
                station_id=sid,
                model_id=mid,
                artifact_id=aid,
                parameter="discharge",
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                clock=lambda: _EPOCH,
            )

        mock_task.assert_called_once()
        bound_generation_id = mock_task.call_args.kwargs["generation_id"]
        assert bound_generation_id is not None, (
            "the flow must mint a concrete generation_id BEFORE calling "
            "the task — a retry of the task replays whatever was bound "
            "here, so leaving it None here defeats retry stability"
        )

    def test_compute_combined_skills_flow_mints_a_concrete_id_before_calling_the_task(
        self,
    ) -> None:
        from unittest.mock import patch

        from sapphire_flow.types.enums import ModelCombinationStrategy

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        stores = _populate_stores(sid, mid, aid)
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        with patch(
            "sapphire_flow.flows.compute_skills.compute_combined_skills_task",
            return_value=([], []),
        ) as mock_task:
            compute_combined_skills_flow(
                station_id=sid,
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
                hindcast_run_ids={mid: hindcast_run_id},
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                clock=lambda: _EPOCH,
            )

        mock_task.assert_called_once()
        assert mock_task.call_args.kwargs["generation_id"] is not None


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
                hindcast_run_id=_uuid(),
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
                hindcast_run_ids={},
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


class TestRunScopingIsRequired:
    """Plan 235 T3 exit gate: a recompute omitting the run id cannot
    publish two cohorts (an unanchored one and a Plan-226-anchored one, in
    the trap this closes) into one generation — because it cannot omit the
    run id at all any more. `hindcast_run_id`/`hindcast_run_ids` have no
    default; calling without them is a `TypeError` at the call site, not a
    silent 1970-2100 unscoped fetch.
    """

    def test_compute_skills_task_has_no_hindcast_run_id_default(self) -> None:
        import inspect

        sig = inspect.signature(compute_skills_task.fn)
        assert sig.parameters["hindcast_run_id"].default is inspect.Parameter.empty

    def test_compute_skills_task_raises_typeerror_when_run_id_omitted(self) -> None:
        with pytest.raises(TypeError):
            compute_skills_task.fn(
                station_id=StationId(_uuid()),
                model_id=ModelId("test"),
                artifact_id=ArtifactId(_uuid()),
                parameter="discharge",
            )

    def test_compute_combined_skills_task_has_no_hindcast_run_ids_default(
        self,
    ) -> None:
        import inspect

        sig = inspect.signature(compute_combined_skills_task.fn)
        assert sig.parameters["hindcast_run_ids"].default is inspect.Parameter.empty

    def test_compute_combined_skills_task_raises_typeerror_when_run_ids_omitted(
        self,
    ) -> None:
        from sapphire_flow.types.enums import ModelCombinationStrategy

        with pytest.raises(TypeError):
            compute_combined_skills_task.fn(
                station_id=StationId(_uuid()),
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
            )


class TestRejectedHindcastBlocksPublication:
    """Plan 235 per-run scope (blocker #1): `partition_by_time_step_and_
    phase` silently drops a hindcast whose OWN `valid_time`s internally mix
    phase — it never reaches a cohort at all, so the OLD `cohorts_missing`
    counter (which only ever saw cohorts that survived partitioning) never
    noticed. A run that silently dropped one malformed INPUT still
    reported `cohorts_complete` and published a generation that was a
    valid SUBSET of what should have been scored.
    """

    def test_internally_mixed_phase_hindcast_blocks_publication(self) -> None:
        from unittest.mock import MagicMock

        from sapphire_flow.types.ensemble import ForecastEnsemble
        from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType
        from sapphire_flow.types.forecast import HindcastForecast
        from sapphire_flow.types.ids import HindcastForecastId

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        # A SINGLE additional hindcast, same run id, whose own two leads
        # land at DIFFERENT phases of its 1-hour time_step (:00 and :30) —
        # `_valid_time_phase_us` raises `ConfigurationError` for it, and
        # `partition_by_time_step_and_phase` drops it (logs + `continue`)
        # rather than ever placing it in a cohort.
        bad_step = ensure_utc(datetime(2025, 1, 10, tzinfo=UTC))
        bad_vts = [
            ensure_utc(datetime(2025, 1, 10, 1, 0, tzinfo=UTC)),
            ensure_utc(datetime(2025, 1, 10, 1, 30, tzinfo=UTC)),
        ]
        df = pl.DataFrame(
            [
                {"valid_time": vt, "member_id": m, "value": 10.0 + m}
                for vt in bad_vts
                for m in range(3)
            ]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        bad_ensemble = ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=bad_step,
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(hours=1),
            values=df,
        )
        hindcast_store.store_hindcast(
            HindcastForecast(
                id=HindcastForecastId(_uuid()),
                station_id=sid,
                model_id=mid,
                model_artifact_id=aid,
                hindcast_step=bad_step,
                forcing_type=ForcingType.REANALYSIS,
                representation=EnsembleRepresentation.MEMBERS,
                hindcast_run_id=hindcast_run_id,
                ensemble=bad_ensemble,
                created_at=bad_step,
            )
        )

        scores, _diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_run_id=hindcast_run_id,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            deployment_config=make_deployment_config(enable_skill_generations=True),
            clock=clock,
        )

        assert scores, "the 3 well-formed hindcasts still score"
        skill_store.publish_generation.assert_not_called()
        assert skill_store.fetch_latest_scores(sid, mid) == [], (
            "a run that silently dropped one malformed hindcast during "
            "partitioning must never publish — it is a SUBSET, not the "
            "whole generation, even though every cohort it DID see "
            "scored successfully"
        )


class TestCombinedGateCountsOutputsNotAttempts:
    """Plan 235 per-run scope (blocker #2): `cohorts_combined` used to
    increment BEFORE computation and accept any cohort with >= 2 models
    even when MORE were requested. Both defects let a combination that
    should be incomplete look complete.
    """

    def test_cohort_missing_one_of_three_requested_models_does_not_combine(
        self,
    ) -> None:
        """A cohort carrying only 2 of 3 REQUESTED models must not combine
        — 'any cohort with two models where three were requested' is
        exactly the defect this closes, distinct from a model having ZERO
        hindcasts anywhere (`TestMissingRequestedModelBlocksPublication`)."""
        from unittest.mock import MagicMock

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
        mid3 = ModelId("model-c")
        aid1 = ArtifactId(_uuid())
        aid2 = ArtifactId(_uuid())

        stores = _populate_stores(sid, mid1, aid1)
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        # model-b and model-c each get their OWN hindcasts at the SAME
        # steps as model-a, so all three genuinely HAVE hindcasts — but
        # model-c's run is never added below, so the only candidate cohort
        # carries just {model-a, model-b} against a 3-model request.
        run_id_2 = _uuid()
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
            hindcast_store.store_hindcast(
                HindcastForecast(
                    id=HindcastForecastId(_uuid()),
                    station_id=sid,
                    model_id=mid2,
                    model_artifact_id=aid2,
                    hindcast_step=step,
                    forcing_type=ForcingType.REANALYSIS,
                    representation=EnsembleRepresentation.MEMBERS,
                    hindcast_run_id=run_id_2,
                    ensemble=ensemble,
                    created_at=step,
                )
            )

        run_id_3 = _uuid()
        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        scores, diagrams = compute_combined_skills_task.fn(
            station_id=sid,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            # model-c is requested with a run id that legitimately has NO
            # hindcasts (distinct from `TestMissingRequestedModelBlocksPub
            # lication`, where a model is entirely absent from the store —
            # here `missing_requested_models` is populated too, but the
            # POINT under test is the per-cohort model-set check itself).
            hindcast_run_ids={
                mid1: hindcast_run_id,
                mid2: run_id_2,
                mid3: run_id_3,
            },
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            clock=lambda: _EPOCH,
        )

        assert scores == [], (
            "the only candidate cohort carries {model-a, model-b} against "
            "a 3-model request — it must NOT combine merely because it "
            "has >= 2 models"
        )
        assert diagrams == []
        skill_store.publish_generation.assert_not_called()

    def test_empty_combined_output_does_not_count_as_combined(self) -> None:
        """Two candidate cohorts (hourly + daily), BOTH carrying the full
        2-model requested set. `compute_combined_skill` is patched to
        return real output for the first call and `([], [])` for the
        second — a gate that counts ATTEMPTS would see 2 candidates / 2
        "combined" (looks complete) and publish only the first cohort's
        scores as if that were the whole combination. The fix must see 2
        candidates / 1 actually-combined and refuse to publish."""
        from unittest.mock import MagicMock, patch

        from sapphire_flow.flows import compute_skills as compute_skills_module
        from sapphire_flow.services.skill.combined_skill import (
            compute_combined_skill as real_compute_combined_skill,
        )
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
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        # model-b: the SAME hourly cohort as model-a (full set #1) PLUS a
        # SEPARATE daily cohort also shared with model-a (full set #2).
        run_id_2 = _uuid()
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
            hindcast_store.store_hindcast(
                HindcastForecast(
                    id=HindcastForecastId(_uuid()),
                    station_id=sid,
                    model_id=mid2,
                    model_artifact_id=aid2,
                    hindcast_step=step,
                    forcing_type=ForcingType.REANALYSIS,
                    representation=EnsembleRepresentation.MEMBERS,
                    hindcast_run_id=run_id_2,
                    ensemble=ensemble,
                    created_at=step,
                )
            )
        _add_daily_cohort(hindcast_store, sid, mid1, aid1, hindcast_run_id)
        _add_daily_cohort(hindcast_store, sid, mid2, aid2, run_id_2)

        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        call_count = {"n": 0}

        def _empty_on_second_cohort(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return [], []
            return real_compute_combined_skill(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(
            compute_skills_module,
            "compute_combined_skill",
            side_effect=_empty_on_second_cohort,
        ):
            scores, diagrams = compute_combined_skills_task.fn(
                station_id=sid,
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
                hindcast_run_ids={mid1: hindcast_run_id, mid2: run_id_2},
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                deployment_config=make_deployment_config(enable_skill_generations=True),
                clock=lambda: _EPOCH,
            )

        assert call_count["n"] == 2, "expected exactly 2 candidate cohorts"
        assert scores, "the first (successful) cohort's scores are still returned"
        skill_store.publish_generation.assert_not_called()
        from sapphire_flow.types.ids import POOLED_MODEL_ID

        assert skill_store.fetch_latest_scores(sid, POOLED_MODEL_ID) == [], (
            "one cohort producing empty output must block publication of "
            "the OTHER cohort's real scores — a gate that counts attempts "
            "would see 2/2 'combined' and wrongly publish a partial result"
        )


class TestRetryWithChangedInputsDoesNotCollide:
    """Plan 235 per-run scope (blocker #3): a Prefect retry whose
    RECOMPUTED values differ from an earlier, orphaned attempt (e.g. an
    observation was corrected between the crash and the retry) must not
    silently lose to `ON CONFLICT DO NOTHING` against the earlier attempt's
    stale row under the SAME generation_id — this is D2b's input-identity
    requirement: stable across an UNCHANGED retry, different when inputs
    differ.
    """

    def test_corrected_observation_between_attempts_is_not_shadowed(self) -> None:
        from unittest.mock import MagicMock

        from sapphire_flow.types.enums import ObservationSource, QcStatus
        from sapphire_flow.types.ids import ObservationId
        from sapphire_flow.types.observation import Observation

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        fixed_generation_id = _uuid()

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        captured_generation_ids: list[UUID] = []
        real_store_skill_scores = skill_store.store_skill_scores

        def _spy_store_skill_scores(scores: list) -> int:  # type: ignore[type-arg]
            if scores:
                captured_generation_ids.append(scores[0].generation_id)
            return real_store_skill_scores(scores)

        skill_store.store_skill_scores = _spy_store_skill_scores  # type: ignore[method-assign]

        # Attempt 1 "crashes" before publish — its (later-to-be-stale)
        # scores land under whatever id it derives.
        real_publish_generation = skill_store.publish_generation
        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("simulated crash before publish")
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
            compute_skills_task.fn(
                station_id=sid,
                model_id=mid,
                artifact_id=aid,
                parameter="discharge",
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=flow_regime_store,
                deployment_config=make_deployment_config(enable_skill_generations=True),
                clock=lambda: _EPOCH,
                generation_id=fixed_generation_id,
            )
        assert len(captured_generation_ids) == 1, (
            "attempt 1 must have persisted its (stale) scores"
        )
        attempt_1_generation_id = captured_generation_ids[0]

        # A correction lands: one observation's value changes materially
        # before the retry runs.
        corrected = Observation(
            id=ObservationId(_uuid()),
            station_id=sid,
            timestamp=ensure_utc(datetime(2025, 1, 1, 1, 0, tzinfo=UTC)),
            parameter="discharge",
            value=999.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
        )
        obs_store.store_observations([corrected])

        # The retry — Prefect replays with the SAME (invocation) id, now
        # un-mocked so it can actually publish.
        skill_store.publish_generation = real_publish_generation
        scores, _diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_run_id=hindcast_run_id,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            deployment_config=make_deployment_config(enable_skill_generations=True),
            clock=lambda: _EPOCH,
            generation_id=fixed_generation_id,
        )

        assert scores, "the retry must still compute its scores"
        retry_generation_ids = {s.generation_id for s in scores}
        assert attempt_1_generation_id not in retry_generation_ids, (
            "changed inputs between attempt 1 and the retry must derive a "
            "DIFFERENT final generation id — colliding with attempt 1's "
            "generation_id would risk the retry's corrected rows losing "
            "to ON CONFLICT DO NOTHING against attempt 1's stale ones"
        )
        current = skill_store.fetch_latest_scores(sid, mid)
        assert current, "the retry must publish successfully"
        assert {s.generation_id for s in current} == retry_generation_ids, (
            "readers must see the retry's (corrected) generation, not "
            "attempt 1's stale, orphaned one"
        )


class TestSkillGenerationsCanBeDisabledForRollout:
    """Plan 235 per-run scope (blocker #4): the two-release rollout —
    `DeploymentConfig.enable_skill_generations=False` must keep
    `compute_skills_task` on the pre-235 shape (baseline rows, no
    `skill_generations` publish), so a rollback mid-rollout reads exactly
    what it always has.
    """

    def test_disabled_writes_baseline_rows_and_does_not_publish(self) -> None:
        from unittest.mock import MagicMock

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        clock = lambda: _EPOCH  # noqa: E731

        stores = _populate_stores(sid, mid, aid, parameter="discharge")
        (
            hindcast_store,
            obs_store,
            skill_store,
            station_store,
            flow_regime_store,
            hindcast_run_id,
        ) = stores

        skill_store.publish_generation = MagicMock(  # type: ignore[method-assign]
            wraps=skill_store.publish_generation
        )

        deployment_config = make_deployment_config(enable_skill_generations=False)

        scores, _diagrams = compute_skills_task.fn(
            station_id=sid,
            model_id=mid,
            artifact_id=aid,
            parameter="discharge",
            hindcast_run_id=hindcast_run_id,
            hindcast_store=hindcast_store,
            obs_store=obs_store,
            skill_store=skill_store,
            station_store=station_store,
            flow_regime_store=flow_regime_store,
            deployment_config=deployment_config,
            clock=clock,
        )

        assert scores, "scores must still be computed and stored"
        assert all(s.generation_id is None for s in scores), (
            "with generations disabled, rows must stay baseline "
            "(generation_id=NULL) — the pre-235 shape"
        )
        skill_store.publish_generation.assert_not_called()
        assert skill_store.fetch_latest_scores(sid, mid), (
            "baseline rows with no competing generation must still read as current"
        )


class TestDiagramForcingTypeScopeDoesNotCollapse:
    """Plan 235 per-run scope (major): a `SkillDiagram` carries no
    `forcing_type` column of its own, but the GENERATION that produced it
    does. Two generations differing ONLY by forcing_type must not compete
    for the same diagram scope — each must independently stay "current".
    """

    def test_two_forcing_types_both_stay_current(self) -> None:
        from sapphire_flow.types.enums import ForcingType, SkillSource
        from sapphire_flow.types.skill import SkillDiagram

        sid = StationId(_uuid())
        mid = ModelId("test")
        aid = ArtifactId(_uuid())
        skill_store = FakeSkillStore()

        def _diagram(generation_id: UUID) -> SkillDiagram:
            return SkillDiagram(
                id=_uuid(),
                station_id=sid,
                model_id=mid,
                parameter="discharge",
                model_artifact_id=aid,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                computation_version=2,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                diagram_type="rank_histogram",
                threshold_level=None,
                data={"ranks": [0, 1], "counts": [1, 1]},
                eval_period_start=_EPOCH,
                eval_period_end=_EPOCH,
                created_at=_EPOCH,
                generation_id=generation_id,
            )

        gen_reanalysis = _uuid()
        gen_nwp = _uuid()
        skill_store.store_skill_diagrams([_diagram(gen_reanalysis)])
        skill_store.store_skill_diagrams([_diagram(gen_nwp)])
        skill_store.publish_generation(
            generation_id=gen_reanalysis,
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            parameter="discharge",
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            computation_version=2,
            published_at=_EPOCH,
            score_count=0,
            diagram_count=1,
        )
        skill_store.publish_generation(
            generation_id=gen_nwp,
            station_id=sid,
            model_id=mid,
            model_artifact_id=aid,
            parameter="discharge",
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.NWP_ARCHIVE,
            computation_version=2,
            published_at=_EPOCH,
            score_count=0,
            diagram_count=1,
        )

        current = skill_store.fetch_latest_diagrams(sid, mid)
        assert {d.generation_id for d in current} == {gen_reanalysis, gen_nwp}, (
            "two generations differing ONLY by forcing_type must not "
            "compete for the same diagram scope — both must stay current"
        )
