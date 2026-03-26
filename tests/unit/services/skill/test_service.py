from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.services.skill.service import compute_skill_for_station
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import SeasonDefinition, StationThreshold
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForcingType,
    ObservationSource,
    QcStatus,
    SkillSource,
    ThresholdSource,
)
from sapphire_flow.types.ids import (
    ArtifactId,
    HindcastForecastId,
    ModelId,
    ObservationId,
    StationId,
)
from sapphire_flow.types.observation import Observation

_EPOCH = ensure_utc(datetime(2025, 1, 15, 0, 0, tzinfo=UTC))
_RNG = random.Random(42)


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


def _make_threshold(
    *,
    station_id: StationId,
    danger_level: str = "moderate",
    value: float = 50.0,
) -> StationThreshold:
    return StationThreshold(
        station_id=station_id,
        danger_level=danger_level,
        parameter="discharge",
        value=value,
        source=ThresholdSource.AUTHORITY,
        created_at=_EPOCH,
        updated_at=_EPOCH,
    )


@pytest.fixture
def station_id() -> StationId:
    return StationId(_uuid())


@pytest.fixture
def model_id() -> ModelId:
    return ModelId("test_model")


@pytest.fixture
def artifact_id() -> ArtifactId:
    return ArtifactId(_uuid())


@pytest.fixture
def clock() -> object:
    return lambda: _EPOCH


@pytest.fixture
def uuid_factory() -> object:
    return uuid4


@pytest.fixture
def seasons() -> list[SeasonDefinition]:
    return [
        SeasonDefinition(name="winter", months=frozenset({12, 1, 2})),
        SeasonDefinition(name="summer", months=frozenset({6, 7, 8})),
    ]


class TestComputeSkillBasic:
    def test_compute_skill_basic(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # 5 hindcasts in January, each with 3 lead steps
        hindcasts = []
        observations = []

        for i in range(5):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=3,
                    value=10.0,
                )
            )
            for j in range(1, 4):
                vt = ensure_utc(
                    datetime.fromtimestamp(step.timestamp() + j * 3600, tz=UTC)
                )
                observations.append(
                    _make_observation(station_id=station_id, timestamp=vt, value=10.0)
                )

        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        assert len(scores) > 0
        assert len(diagrams) > 0

        # All scores have the right station/model/artifact
        for s in scores:
            assert s.station_id == station_id
            assert s.model_id == model_id
            assert s.model_artifact_id == artifact_id

        # Should have lead times 1, 2, 3 for the all-seasons/all-regime aggregate
        lead_times = {s.lead_time_hours for s in scores}
        assert {1, 2, 3}.issubset(lead_times)

        # Should have expected metrics
        metrics = {s.metric for s in scores}
        assert "crps" in metrics
        assert "nse" in metrics
        assert "kge" in metrics
        assert "mae" in metrics
        assert "sharpness_p10_p90" in metrics

    def test_scores_have_correct_sample_size(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        n_hindcasts = 4
        hindcasts = []
        observations = []

        for i in range(n_hindcasts):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=10.0)
            )

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        # All-season/all-regime aggregate at lead_time=1 → sample_size = n_hindcasts
        aggregate = [
            s
            for s in scores
            if s.lead_time_hours == 1 and s.season is None and s.flow_regime is None
        ]
        assert len(aggregate) > 0
        assert all(s.sample_size == n_hindcasts for s in aggregate)


class TestNoMatchingObservations:
    def test_no_matching_observations(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        hindcasts = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                hindcast_step=_utc(2025, 1, 1),
            )
        ]
        # Observations at completely different timestamps
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=_utc(2020, 6, 1),
                value=5.0,
            )
        ]

        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=None,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        assert scores == []
        assert diagrams == []

    def test_empty_hindcasts(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        obs = [_make_observation(station_id=station_id, timestamp=_EPOCH, value=5.0)]
        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=[],
            observations=obs,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=None,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )
        assert scores == []
        assert diagrams == []


class TestSeasonStratification:
    def test_season_stratification(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        seasons = [
            SeasonDefinition(name="winter", months=frozenset({12, 1, 2})),
            SeasonDefinition(name="summer", months=frozenset({6, 7, 8})),
        ]

        hindcasts = []
        observations = []

        # January hindcasts (winter)
        for i in range(3):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(_make_observation(station_id=station_id, timestamp=vt))

        # July hindcasts (summer)
        for i in range(3):
            step = _utc(2025, 7, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(_make_observation(station_id=station_id, timestamp=vt))

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        season_names = {s.season for s in scores}
        # Should have both season-specific and all-season aggregate
        assert "winter" in season_names
        assert "summer" in season_names
        assert None in season_names  # all-seasons aggregate

    def test_scores_freshness_is_current(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        from sapphire_flow.types.enums import SkillFreshness

        step = _utc(2025, 1, 1)
        vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
        hindcasts = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                hindcast_step=step,
                n_steps=1,
            )
        ]
        observations = [_make_observation(station_id=station_id, timestamp=vt)]

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        assert all(s.freshness == SkillFreshness.CURRENT for s in scores)
        assert all(s.computation_version == 1 for s in scores)


class TestParameterMismatch:
    def test_mismatched_parameter_raises(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        step = _utc(2025, 1, 1)
        vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
        hindcasts = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                hindcast_step=step,
                n_steps=1,
                parameter="water_level",
            )
        ]
        observations = [_make_observation(station_id=station_id, timestamp=vt)]

        with pytest.raises(ValueError, match="parameters other than"):
            compute_skill_for_station(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                hindcasts=hindcasts,
                observations=observations,
                thresholds=[],
                flow_regime_config=None,
                seasons=seasons,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=ForcingType.REANALYSIS,
                clock=clock,  # type: ignore[arg-type]
                uuid_factory=uuid_factory,  # type: ignore[arg-type]
                parameter="discharge",
            )


class TestParameterFiltering:
    def test_discharge_only_hindcasts_produce_correct_sample_size(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        n_discharge = 3
        hindcasts_all = []
        observations = []

        # 3 discharge hindcasts
        for i in range(n_discharge):
            step = _utc(2025, 1, i + 1)
            hindcasts_all.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    parameter="discharge",
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(
                    station_id=station_id, timestamp=vt, value=10.0, parameter="discharge"
                )
            )

        # 2 water_level hindcasts (should be excluded by pre-filter)
        for i in range(2):
            step = _utc(2025, 2, i + 1)
            hindcasts_all.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    parameter="water_level",
                )
            )

        # Pre-filter to discharge only (as the flow would do)
        discharge_hindcasts = [
            h for h in hindcasts_all if h.ensemble.parameter == "discharge"
        ]
        assert len(discharge_hindcasts) == n_discharge

        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=discharge_hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        assert len(scores) > 0
        assert len(diagrams) > 0

        # All-season/all-regime aggregate at lead_time=1
        aggregate = [
            s
            for s in scores
            if s.lead_time_hours == 1 and s.season is None and s.flow_regime is None
        ]
        assert len(aggregate) > 0
        assert all(s.sample_size == n_discharge for s in aggregate)
