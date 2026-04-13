from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.services.skill.combined_skill import compute_combined_skill
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import SeasonDefinition
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForcingType,
    ModelCombinationStrategy,
    ObservationSource,
    QcStatus,
    SkillSource,
)
from sapphire_flow.types.ids import (
    POOLED_MODEL_ID,
    ArtifactId,
    HindcastForecastId,
    ModelId,
    ObservationId,
    StationId,
)
from sapphire_flow.types.observation import Observation

_EPOCH = ensure_utc(datetime(2025, 1, 15, 0, 0, tzinfo=UTC))
_RNG = random.Random(99)


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _utc(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _make_hindcast(
    *,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    hindcast_step: UtcDatetime,
    n_members: int = 5,
    n_steps: int = 3,
    value: float = 10.0,
    parameter: str = "discharge",
) -> object:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import HindcastForecast

    rows = []
    time_step = timedelta(hours=1)
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(
                hindcast_step.timestamp() + (step + 1) * 3600, tz=UTC
            )
        )
        for m in range(n_members):
            rows.append({"valid_time": vt, "member_id": m, "value": value + m * 0.1})

    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    units = "m3/s" if parameter == "discharge" else "m"
    ensemble = ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=hindcast_step,
        parameter=parameter,
        units=units,
        time_step=time_step,
        values=df,
    )
    return HindcastForecast(
        id=HindcastForecastId(_uuid()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        hindcast_step=hindcast_step,
        forcing_type=ForcingType.REANALYSIS,
        representation=EnsembleRepresentation.MEMBERS,
        hindcast_run_id=_uuid(),
        ensemble=ensemble,
        created_at=_EPOCH,
    )


def _make_observation(
    *,
    station_id: StationId,
    timestamp: UtcDatetime,
    value: float = 10.0,
    parameter: str = "discharge",
) -> Observation:
    return Observation(
        id=ObservationId(_uuid()),
        station_id=station_id,
        timestamp=timestamp,
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.QC_PASSED,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_EPOCH,
    )


@pytest.fixture
def station_id() -> StationId:
    return StationId(_uuid())


@pytest.fixture
def artifact_id_a() -> ArtifactId:
    return ArtifactId(_uuid())


@pytest.fixture
def artifact_id_b() -> ArtifactId:
    return ArtifactId(_uuid())


@pytest.fixture
def model_a() -> ModelId:
    return ModelId("model_a")


@pytest.fixture
def model_b() -> ModelId:
    return ModelId("model_b")


@pytest.fixture
def clock() -> object:
    return lambda: _EPOCH


@pytest.fixture
def seasons() -> list[SeasonDefinition]:
    return [SeasonDefinition(name="winter", months=frozenset({12, 1, 2}))]


class TestTwoModelsFullOverlap:
    def test_combined_skill_computed_on_all_steps(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Both models have hindcasts at same 5 steps
        steps = [_utc(2025, 1, i + 1) for i in range(5)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]

        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in steps
        ]

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        assert len(scores) > 0
        assert len(diagrams) > 0
        # All scores have POOLED_MODEL_ID
        assert all(s.model_id == POOLED_MODEL_ID for s in scores)
        # artifact_id is None for combined scores
        assert all(s.model_artifact_id is None for s in scores)


class TestPartialOverlap:
    def test_combined_skill_on_intersection_only(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Model A: steps 1-5, Model B: steps 3-7 → intersection 3-5
        steps_a = [_utc(2025, 1, i + 1) for i in range(5)]  # days 1-5
        steps_b = [_utc(2025, 1, i + 3) for i in range(5)]  # days 3-7

        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_a
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_b
        ]

        # Create observations for all days 1-7
        all_steps = sorted(set(steps_a) | set(steps_b))
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in all_steps
        ]

        scores, _ = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        # Should get scores (intersection = days 3-5, i.e. 3 steps)
        assert len(scores) > 0
        aggregate = [
            s
            for s in scores
            if s.lead_time_hours == 1 and s.season is None and s.flow_regime is None
        ]
        assert len(aggregate) > 0
        # sample_size should be 3 (intersection of 3 steps)
        assert all(s.sample_size == 3 for s in aggregate)


class TestNoOverlap:
    def test_no_intersection_returns_empty(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Model A: days 1-3, Model B: days 4-6 → no intersection
        steps_a = [_utc(2025, 1, i + 1) for i in range(3)]
        steps_b = [_utc(2025, 1, i + 4) for i in range(3)]

        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_a
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_b
        ]

        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=_utc(2025, 1, 5),
            )
        ]

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        assert scores == []
        assert diagrams == []


class TestSingleModel:
    def test_single_model_still_computes(
        self,
        station_id: StationId,
        model_a: ModelId,
        artifact_id_a: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        steps = [_utc(2025, 1, i + 1) for i in range(4)]
        hindcasts = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in steps
        ]

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        assert len(scores) > 0
        assert all(s.model_id == POOLED_MODEL_ID for s in scores)


class TestCoverageLogging:
    def test_coverage_log_message(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        from structlog.testing import capture_logs

        # Model A: days 1-4, Model B: days 3-6 → intersection days 3-4 (2 steps)
        steps_a = [_utc(2025, 1, i + 1) for i in range(4)]
        steps_b = [_utc(2025, 1, i + 3) for i in range(4)]

        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_a
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_b
        ]

        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in sorted(set(steps_a) | set(steps_b))
        ]

        with capture_logs() as captured:
            compute_combined_skill(
                station_id=station_id,
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
                hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
                observations=observations,
                thresholds=[],
                flow_regime_config=None,
                seasons=seasons,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=ForcingType.REANALYSIS,
                clock=clock,  # type: ignore[arg-type]
                uuid_factory=uuid4,
            )

        coverage_events = [
            e for e in captured if e.get("event") == "combined_skill.coverage"
        ]
        assert len(coverage_events) == 1
        assert coverage_events[0]["intersection_steps"] == 2
