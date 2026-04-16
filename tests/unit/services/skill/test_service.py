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
    FlowRegime,
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
from sapphire_flow.types.skill import FlowRegimeConfig

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
        # 5 hindcasts in January, each with 3 lead steps.
        # Observations VARY so NSE/KGE denominators are non-zero.
        # Ensemble members match observation exactly → perfect forecast.
        hindcasts = []
        observations = []

        for i in range(5):
            step = _utc(2025, 1, i + 1)
            obs_val = float(8 + i)  # varies: 8, 9, 10, 11, 12
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=3,
                    value=obs_val,
                )
            )
            for j in range(1, 4):
                vt = ensure_utc(
                    datetime.fromtimestamp(step.timestamp() + j * 3600, tz=UTC)
                )
                observations.append(
                    _make_observation(
                        station_id=station_id, timestamp=vt, value=obs_val
                    )
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

        # Value assertions for a near-perfect forecast (members cluster at obs value)
        all_season_all_regime = [
            s
            for s in scores
            if s.season is None and s.flow_regime is None and s.lead_time_hours == 1
        ]
        by_metric = {s.metric: s.score for s in all_season_all_regime}
        # Members are at obs + m*0.1 (small spread) → near-perfect but not exact zero error
        assert by_metric["crps"] < 0.15  # small, near-zero
        assert by_metric["nse"] > 0.97  # high but not exactly 1.0 due to tiny spread
        assert by_metric["kge"] > 0.97  # high but not exactly 1.0 due to tiny spread
        assert by_metric["mae"] < 0.25  # small absolute error
        assert by_metric["pbias"] == pytest.approx(0.0, abs=2.0)  # <2% bias

    def test_imperfect_forecast_metrics(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Observations vary; members have spread around observation value
        obs_values = [10.0, 12.0, 8.0, 15.0, 9.0]
        hindcasts = []
        observations = []

        for i, obs_val in enumerate(obs_values):
            step = _utc(2025, 1, i + 1)
            # Forecast is biased high: members at obs + 3.0
            forecast_val = obs_val + 3.0
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=forecast_val,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=obs_val)
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

        by_metric = {
            s.metric: s.score
            for s in scores
            if s.season is None and s.flow_regime is None and s.lead_time_hours == 1
        }
        # Biased forecast → CRPS > 0 and NSE < 1
        assert by_metric["crps"] > 0.0
        assert by_metric["nse"] < 1.0

    def test_worse_than_climatology_nse_negative(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        import math

        # Sinusoidal observations — must vary so SS_tot > 0
        n = 10
        obs_values = [10.0 + 5.0 * math.sin(i * math.pi / 5) for i in range(n)]
        hindcasts = []
        observations = []

        for i, obs_val in enumerate(obs_values):
            step = _utc(2025, 1, i + 1)
            # Forecast is random noise far from observation (anti-correlated)
            noise_val = 30.0 - obs_val  # inverted signal
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=noise_val,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=obs_val)
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

        nse_scores = [
            s
            for s in scores
            if s.metric == "nse"
            and s.season is None
            and s.flow_regime is None
            and s.lead_time_hours == 1
        ]
        assert len(nse_scores) > 0
        assert nse_scores[0].score < 0.0

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
                    station_id=station_id,
                    timestamp=vt,
                    value=10.0,
                    parameter="discharge",
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


def _make_flow_regime_config(
    station_id: StationId,
    p50: float = 150.0,
    p90: float = 350.0,
) -> FlowRegimeConfig:
    return FlowRegimeConfig(
        id=_uuid(),
        station_id=station_id,
        parameter="discharge",
        p50=p50,
        p90=p90,
        computed_at=_EPOCH,
        observation_count=1000,
        version=1,
        created_at=_EPOCH,
    )


def _make_hindcast_quantiles(
    *,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId | None,
    hindcast_step: UtcDatetime,
    n_steps: int = 3,
    value: float = 100.0,
    parameter: str = "discharge",
) -> object:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import HindcastForecast

    quantile_levels = [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]
    rows = []
    time_step = timedelta(hours=1)
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(
                hindcast_step.timestamp() + (step + 1) * 3600, tz=UTC
            )
        )
        for q in quantile_levels:
            rows.append({"valid_time": vt, "quantile": q, "value": value + q * 10.0})

    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("quantile").cast(pl.Float64),
    )
    units = "m3/s" if parameter == "discharge" else "m"
    ensemble = ForecastEnsemble.from_quantiles(
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
        representation=EnsembleRepresentation.QUANTILES,
        hindcast_run_id=_uuid(),
        ensemble=ensemble,
        created_at=_EPOCH,
    )


class TestFlowRegimeStratification:
    def test_flow_regime_keys_present(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        # Aare at Bern: LOW (<150), HIGH (150–350), FLOOD (>350)
        regime_config = _make_flow_regime_config(station_id, p50=150.0, p90=350.0)
        hindcasts = []
        observations = []

        # Low-regime hindcasts: value = 80 m3/s (< p50=150)
        for i in range(3):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=80.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=80.0)
            )

        # High-regime hindcasts: value = 200 m3/s (p50 < 200 < p90)
        for i in range(3):
            step = _utc(2025, 2, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=200.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=200.0)
            )

        # Flood-regime hindcasts: value = 500 m3/s (> p90=350)
        for i in range(3):
            step = _utc(2025, 3, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=500.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=500.0)
            )

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=regime_config,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        regimes = {s.flow_regime for s in scores}
        assert FlowRegime.LOW in regimes
        assert FlowRegime.HIGH in regimes
        assert FlowRegime.FLOOD in regimes
        assert None in regimes  # all-regimes aggregate

    def test_flow_regime_config_id_stamped(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        regime_config = _make_flow_regime_config(station_id, p50=150.0, p90=350.0)
        step = _utc(2025, 1, 1)
        vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
        hindcasts = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_id,
                artifact_id=artifact_id,
                hindcast_step=step,
                n_steps=1,
                value=80.0,
            )
        ]
        observations = [
            _make_observation(station_id=station_id, timestamp=vt, value=80.0)
        ]

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=regime_config,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        regime_scores = [s for s in scores if s.flow_regime is not None]
        assert len(regime_scores) > 0
        assert all(s.flow_regime_config_id == regime_config.id for s in regime_scores)

    def test_crps_produced_per_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        regime_config = _make_flow_regime_config(station_id, p50=150.0, p90=350.0)
        hindcasts = []
        observations = []

        for i in range(3):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=80.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=80.0)
            )

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=regime_config,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        crps_regimes = {
            s.flow_regime
            for s in scores
            if s.metric == "crps" and s.flow_regime is not None
        }
        assert FlowRegime.LOW in crps_regimes


class TestThresholdMetrics:
    def test_bss_pod_far_csi_present(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        thresholds = [
            _make_threshold(
                station_id=station_id, danger_level="moderate", value=150.0
            ),
            _make_threshold(station_id=station_id, danger_level="high", value=300.0),
        ]
        hindcasts = []
        observations = []

        for i in range(6):
            step = _utc(2025, 1, i + 1)
            obs_val = 200.0 if i % 2 == 0 else 80.0
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=obs_val,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=obs_val)
            )

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=thresholds,
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        metrics = {s.metric for s in scores}
        assert "bss_danger_moderate" in metrics
        assert "bss_danger_high" in metrics
        assert "pod_danger_moderate" in metrics
        assert "pod_danger_high" in metrics
        assert "far_danger_moderate" in metrics
        assert "far_danger_high" in metrics
        assert "csi_danger_moderate" in metrics
        assert "csi_danger_high" in metrics

    def test_reliability_and_roc_diagrams_produced(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        thresholds = [
            _make_threshold(
                station_id=station_id, danger_level="moderate", value=150.0
            ),
            _make_threshold(station_id=station_id, danger_level="high", value=300.0),
        ]
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
                    n_steps=1,
                    value=100.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=100.0)
            )

        _, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=thresholds,
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        diagram_types = {d.diagram_type for d in diagrams}
        assert "reliability" in diagram_types
        assert "roc" in diagram_types

        threshold_levels = {
            d.threshold_level for d in diagrams if d.threshold_level is not None
        }
        assert "moderate" in threshold_levels
        assert "high" in threshold_levels


class TestRankHistogramAndSharpness:
    def test_rank_histogram_produced_for_members(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
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
                    n_steps=1,
                    n_members=10,
                    value=100.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=100.0)
            )

        _, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        rank_hist = [d for d in diagrams if d.diagram_type == "rank_histogram"]
        assert len(rank_hist) > 0

    def test_sharpness_metrics_present(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
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
                    n_steps=1,
                    n_members=10,
                    value=100.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=100.0)
            )

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        metrics = {s.metric for s in scores}
        assert "sharpness_p10_p90" in metrics
        assert "sharpness_p25_p75" in metrics


class TestQuantilesRepresentation:
    def test_quantile_hindcasts_produce_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        artifact_id: ArtifactId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        hindcasts = []
        observations = []

        for i in range(5):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast_quantiles(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_step=step,
                    n_steps=1,
                    value=100.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=100.0)
            )

        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        assert len(scores) > 0
        assert len(diagrams) > 0

        metrics = {s.metric for s in scores}
        assert "crps" in metrics
        assert "nse" in metrics


class TestCombinedModelPath:
    def test_artifact_id_none_produces_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        clock: object,
        uuid_factory: object,
    ) -> None:
        hindcasts = []
        observations = []

        for i in range(5):
            step = _utc(2025, 1, i + 1)
            hindcasts.append(
                _make_hindcast(
                    station_id=station_id,
                    model_id=model_id,
                    artifact_id=ArtifactId(_uuid()),
                    hindcast_step=step,
                    n_steps=1,
                    value=100.0,
                )
            )
            vt = ensure_utc(datetime.fromtimestamp(step.timestamp() + 3600, tz=UTC))
            observations.append(
                _make_observation(station_id=station_id, timestamp=vt, value=100.0)
            )

        # Override model_artifact_id to None to simulate combined-model path
        from sapphire_flow.types.forecast import HindcastForecast

        hindcasts_none_artifact = [
            HindcastForecast(
                id=hc.id,
                station_id=hc.station_id,
                model_id=hc.model_id,
                model_artifact_id=None,
                hindcast_step=hc.hindcast_step,
                forcing_type=hc.forcing_type,
                representation=hc.representation,
                hindcast_run_id=hc.hindcast_run_id,
                ensemble=hc.ensemble,
                created_at=hc.created_at,
            )
            for hc in hindcasts
        ]

        scores, _ = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=None,
            hindcasts=hindcasts_none_artifact,
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid_factory,  # type: ignore[arg-type]
            parameter="discharge",
        )

        assert len(scores) > 0
        assert all(s.model_artifact_id is None for s in scores)


class TestParameterStamping:
    def test_parameter_stamped_on_scores(
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
        observations = [
            _make_observation(
                station_id=station_id, timestamp=vt, parameter="water_level"
            )
        ]

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
            parameter="water_level",
        )

        assert len(scores) > 0
        assert all(s.parameter == "water_level" for s in scores)

    def test_parameter_stamped_on_diagrams(
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
                n_members=5,
                parameter="water_level",
            )
        ]
        observations = [
            _make_observation(
                station_id=station_id, timestamp=vt, parameter="water_level"
            )
        ]

        _, diagrams = compute_skill_for_station(
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
            parameter="water_level",
        )

        assert len(diagrams) > 0
        assert all(d.parameter == "water_level" for d in diagrams)
