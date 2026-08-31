from __future__ import annotations

import builtins
import dataclasses
import enum
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import numpy as np
import polars as pl
import pytest
import sqlalchemy.exc as sa_exc
import xarray as xr

from sapphire_flow.adapters.recap_gateway import (
    GatewayResolutionError,
    RecapAuthError,
    RecapConfigurationError,
    RecapPayloadIntegrityError,
    RecapTransientError,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import (
    AdapterError,
    BudgetExceededError,
    ConfigurationError,
    ExtractionError,
    StoreError,
)
from sapphire_flow.flows.run_forecast_cycle import (
    ForecastCycleResult,
    _bind_rating_curve,
    _check_nwp_grid_staleness,
    _fetch_nwp_task,
    _forecast_cycle_health,
    _load_weather_forecast_adapter_config,
    _NwpFetchOutcome,
    run_forecast_cycle_flow,
)
from sapphire_flow.models.climatology_fallback import (
    ClimatologyArtifact,
    ClimatologyFallbackModel,
)
from sapphire_flow.services.forecast_combination import build_combined_forecasts
from sapphire_flow.services.run_station_forecast import (
    run_all_station_forecasts,
    run_all_station_forecasts_per_track,
)
from sapphire_flow.services.track_assembly import assemble_assignment_inputs
from sapphire_flow.services.track_resolution import commit_track, resolve_candidate
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet, StationThreshold
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    AlertEligibility,
    AlertSource,
    AlertStatus,
    ArtifactScope,
    EnsembleMode,
    EnsembleRepresentation,
    ForecastCycleHealth,
    ForecastStatus,
    InterpolationMethod,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    ModelCombinationStrategy,
    NwpCycleSource,
    PipelineCheckType,
    PipelineHealthStatus,
    SpatialRepresentation,
    StationKind,
    StationStatus,
    ThresholdSource,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_track import (
    FeatureName,
    ForcingTrackKey,
    RawFetchOutcome,
    RawFetchStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    NWP_REGRESSION_MODEL_ID,
    ArtifactId,
    BasinId,
    ForecastId,
    ModelId,
    RatingCurveId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.station import (
    GroupModelAssignment,
    ModelAssignment,
    StationGroup,
    StationWeatherSource,
)
from sapphire_flow.types.weather import (
    BasinAverageForecast,
    ElevationBandForecast,
    GriddedForecast,
    PointForecast,
    WeatherForecastRecord,
)
from tests.conftest import (
    make_forecast_ensemble,
    make_observations,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeGridExtractor, FakeWeatherForecastSource
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeNwpGridStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeRatingCurveStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.pipeline import PipelineHealthRecord

_NOW = ensure_utc(datetime(2026, 4, 1, 6, 0, tzinfo=UTC))
_NWP_SOURCE = "icon_ch2_eps"
_MODEL_ID = ModelId("fake_station_model")


@pytest.fixture(autouse=True)
def _block_real_httpx_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_handle_request = httpx.HTTPTransport.handle_request
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}

    def _blocked_httpx_transport(
        self: httpx.HTTPTransport,
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.host in loopback_hosts:
            return original_handle_request(self, request)
        raise AssertionError(
            f"Unexpected real HTTP via httpx in forecast-cycle tests: "
            f"{request.method} {request.url}"
        )

    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        _blocked_httpx_transport,
    )


def _clock() -> UtcDatetime:
    return _NOW


def _make_config(**overrides: object) -> DeploymentConfig:
    defaults: dict[str, object] = {"max_retention_days": 3650}
    defaults.update(overrides)
    return DeploymentConfig(**defaults)  # type: ignore[arg-type]


def _make_alerting_config() -> DeploymentConfig:
    return _make_config(
        enable_forecast_alerts=True,
        alert_model_strategy=ModelCombinationStrategy.POOLED,
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


def _empty_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0", rules=())


def _write_forecast_cycle_config(
    path: Path,
    weather_forecast_section: str = "",
    *,
    max_retention_days: int = 3650,
) -> Path:
    path.write_text(
        f"""
max_retention_days = {max_retention_days}

{weather_forecast_section}
""".strip()
        + "\n"
    )
    return path


def _make_forecast_threshold(station_id: StationId) -> StationThreshold:
    return StationThreshold(
        station_id=station_id,
        danger_level="DL1",
        parameter="discharge",
        value=0.0,
        source=ThresholdSource.AUTHORITY,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _serialized_climatology_artifact(model: ClimatologyFallbackModel) -> bytes:
    rows = [
        {
            "day_of_year": valid_time.timetuple().tm_yday,
            "quantile": quantile,
            "value": 25.0 + float(step),
            "parameter": "discharge",
        }
        for step in range(1, 6)
        for valid_time in [_NOW + step * timedelta(hours=24)]
        for quantile in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
    ]
    return model.serialize_artifact(ClimatologyArtifact(quantiles=pl.DataFrame(rows)))


def _make_nwp_records(
    station_id: StationId,
    n_steps: int = 120,
    n_members: int = 3,
) -> list[WeatherForecastRecord]:
    cycle_time = _NOW
    records = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for param in ["precipitation", "temperature"]:
            for m in range(n_members):
                records.append(
                    WeatherForecastRecord(
                        id=uuid4(),
                        station_id=station_id,
                        nwp_source=_NWP_SOURCE,
                        cycle_time=cycle_time,
                        valid_time=vt,
                        parameter=param,
                        spatial_type=SpatialRepresentation.POINT,
                        band_id=None,
                        member_id=m,
                        value=float(step + m),
                        created_at=_NOW,
                    )
                )
    return records


def _make_gridded_forecast(
    cycle_time: UtcDatetime | None = None,
    nwp_source: str = _NWP_SOURCE,
) -> GriddedForecast:
    ct = cycle_time or _NOW
    ds = xr.Dataset(
        {
            "precipitation": (
                ["member", "valid_time", "latitude", "longitude"],
                np.random.rand(3, 5, 4, 4),
            ),
            "temperature": (
                ["member", "valid_time", "latitude", "longitude"],
                np.random.rand(3, 5, 4, 4),
            ),
        },
        coords={
            "member": [0, 1, 2],
            "valid_time": [
                ensure_utc(datetime.fromtimestamp(ct.timestamp() + i * 3600, tz=UTC))
                for i in range(5)
            ],
            "latitude": [46.0, 46.5, 47.0, 47.5],
            "longitude": [7.0, 7.5, 8.0, 8.5],
        },
    )
    return GriddedForecast(nwp_source=nwp_source, cycle_time=ct, values=ds)


def _make_basin_avg_result(
    station_ids: list[StationId],
    n_steps: int = 10,
    n_members: int = 3,
    cycle_time: UtcDatetime | None = None,
) -> dict[StationId, BasinAverageForecast]:
    ct = cycle_time if cycle_time is not None else _NOW
    result = {}
    for sid in station_ids:
        rows = []
        for step in range(n_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(_NOW.timestamp() + (step + 1) * 3600, tz=UTC)
            )
            for param in ["precipitation", "temperature"]:
                for m in range(n_members):
                    rows.append(
                        {
                            "valid_time": vt,
                            "parameter": param,
                            "member_id": m,
                            "value": float(step + m),
                        }
                    )
        df = pl.DataFrame(rows)
        result[sid] = BasinAverageForecast(
            nwp_source=_NWP_SOURCE,
            cycle_time=ct,
            values=df,
        )
    return result


class _CycleReflectingGridExtractor:
    """Grid extractor mirroring the REAL extractor contract.

    Like ``MeshBasinExtractor``, it tags each output ``BasinAverageForecast``
    with the ``cycle_time`` it is CALLED with, while emitting future-dated
    valid_times so a forecast is still produced. Unlike ``FakeGridExtractor``
    (static result, ignores the arg) this lets a test observe whether records
    are stored under the nominal request or the adapter-resolved cycle.
    """

    def __init__(self, station_ids: list[StationId]) -> None:
        self._station_ids = station_ids
        self.seen_cycle_times: list[UtcDatetime] = []

    def extract(
        self,
        grid: xr.Dataset,
        configs: list[StationWeatherSource],
        basins: dict[StationId, Basin],
        cycle_time: UtcDatetime,
        nwp_source: str,
    ) -> dict[StationId, BasinAverageForecast]:
        self.seen_cycle_times.append(cycle_time)
        out: dict[StationId, BasinAverageForecast] = {}
        for sid in self._station_ids:
            rows = []
            for step in range(10):
                vt = ensure_utc(
                    datetime.fromtimestamp(_NOW.timestamp() + (step + 1) * 3600, tz=UTC)
                )
                for param in ("precipitation", "temperature"):
                    for m in range(3):
                        rows.append(
                            {
                                "valid_time": vt,
                                "parameter": param,
                                "member_id": m,
                                "value": float(step + m),
                            }
                        )
            out[sid] = BasinAverageForecast(
                nwp_source=nwp_source,
                cycle_time=cycle_time,
                values=pl.DataFrame(rows),
            )
        return out


def _build_station_and_stores(
    station_id: StationId,
    model_id: ModelId,
    station_store: FakeStationStore,
    obs_store: FakeObservationStore,
    nwp_store: FakeWeatherForecastStore,
    artifact_store: FakeModelArtifactStore,
    forcing_store: FakeHistoricalForcingStore,
    *,
    n_obs: int = 30,
    seed_nwp: bool = True,
    extraction_type: SpatialRepresentation = SpatialRepresentation.POINT,
    basin_store: FakeBasinStore | None = None,
    station_status: StationStatus = StationStatus.OPERATIONAL,
    seed_model_assignment: bool = True,
    seed_artifact: bool = True,
) -> None:
    basin_id: BasinId | None = None
    if extraction_type == SpatialRepresentation.BASIN_AVERAGE:
        if basin_store is None:
            raise ValueError("basin_store required for BASIN_AVERAGE extraction_type")
        basin_id = BasinId(uuid4())
        basin = Basin(
            id=basin_id,
            code=f"basin_{basin_id}",
            name="Test Basin",
            geometry=None,
            area_km2=100.0,
            attributes=None,
            band_geometries=None,
            created_at=_NOW,
            network="test",
        )
        basin_store.store_basin(basin)
        seed_nwp = False

    station = make_station_config(
        station_id=station_id,
        station_kind=StationKind.RIVER,
        station_status=station_status,
        measured_parameters=frozenset({"discharge"}),
        forecast_targets=frozenset({"discharge"}),
        basin_id=basin_id,
    )
    station_store.store_station(station)

    if seed_model_assignment:
        assignment = ModelAssignment(
            station_id=station_id,
            model_id=model_id,
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        station_store.store_model_assignment(assignment)

    source = StationWeatherSource(
        station_id=station_id,
        nwp_source=_NWP_SOURCE,
        extraction_type=extraction_type,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.FORECAST,
    )
    station_store.store_weather_source(source)

    # Observations for staleness check
    obs_start = ensure_utc(
        datetime.fromtimestamp(_NOW.timestamp() - n_obs * 3600, tz=UTC)
    )
    observations = make_observations(
        n=n_obs,
        station_id=station_id,
        parameter="discharge",
        start=obs_start,
        interval=timedelta(hours=1),
        rng=random.Random(str(station_id)),
    )
    obs_store.store_observations(observations)

    # NWP records in the store (so assemble_station_operational_inputs can fetch them)
    if seed_nwp:
        records = _make_nwp_records(station_id)
        nwp_store.store_weather_forecasts(records)

    # Historical forcing (past_dynamic via StoreBackedReanalysisSource)
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

    forcing_start = ensure_utc(
        datetime.fromtimestamp(_NOW.timestamp() - 30 * 3600, tz=UTC)
    )
    raw_forcing = []
    for i in range(30):
        ts = ensure_utc(
            datetime.fromtimestamp(forcing_start.timestamp() + i * 3600, tz=UTC)
        )
        for param in ["precipitation", "temperature"]:
            raw_forcing.append(
                RawHistoricalForcing(
                    station_id=station_id,
                    source=_NWP_SOURCE,
                    version="1.0",
                    valid_time=ts,
                    parameter=param,
                    spatial_type=SpatialRepresentation.POINT,
                    band_id=None,
                    member_id=None,
                    value=float(i % 10),
                )
            )
    forcing_store.store_forcing(raw_forcing)

    # Active artifact
    if seed_artifact:
        artifact_store.store_artifact(
            model_id=model_id,
            artifact_bytes=b"fake_artifact",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=station_id,
            status=ModelArtifactStatus.ACTIVE,
        )


class _SmallFakeModel(FakeStationForecastModel):
    """Fake model with small lookback so tests don't need years of data."""

    from sapphire_flow.types.model import ModelDataRequirements

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class _PrecipOnlyFakeModel(FakeStationForecastModel):
    """Plan 151 T8b fixer round 2. Same shape as ``_SmallFakeModel`` but a
    NARROWER future feature set, so it projects onto a DISTINCT
    ``ForcingTrackKey`` and therefore its own track. This is the only
    legitimate way one station ends up with two tracks: a station has exactly
    ONE forecast binding by design, which is why the previous version of the
    cross-cycle golden -- which added a second FORECAST-role weather source --
    could never reach the code it claimed to test."""

    from sapphire_flow.types.model import ModelDataRequirements

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation"}),
        future_dynamic_features=frozenset({"precipitation"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class _MultiTimeStepFakeModel(FakeStationForecastModel):
    """Plan 151 T8b fixer round 2. Declares TWO supported time steps, which
    the authoritative contract permits. Owner ruling 2026-08-28: such a model
    is SKIPPED for that station and the run continues -- it must never take
    the cycle down."""

    from sapphire_flow.types.model import ModelDataRequirements

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1), timedelta(days=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class _RaisingForModelArtifactStore:
    """Wraps a ``FakeModelArtifactStore``, raising an unanticipated exception
    from ``fetch_active_artifact_for_station`` for one ``model_id`` — a call
    OUTSIDE ``_run_single_model``'s guarded regions. Used by the Plan 150 T2
    flow-level backstop regression to prove an unexpected exception in a
    lower-priority assignment no longer darkens the whole station (D3/D6).
    """

    def __init__(self, inner: FakeModelArtifactStore, raise_for: ModelId) -> None:
        self._inner = inner
        self._raise_for = raise_for
        self.raised_for_target = False

    def fetch_active_artifact_for_station(
        self, station_id: StationId, model_id: ModelId
    ) -> tuple[ArtifactId, bytes] | None:
        if model_id == self._raise_for:
            self.raised_for_target = True
            raise RuntimeError("unexpected artifact-store failure")
        return self._inner.fetch_active_artifact_for_station(station_id, model_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _NativeFakeModel(FakeStationForecastModel):
    """Native model declaring NO future features (persistence/climatology-like)."""

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


class _RecordingNwpFakeModel(FakeStationForecastModel):
    """NWP model that records the future_dynamic frame it is handed at predict."""

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.SINGLE,
    )

    def __init__(self) -> None:
        self.seen_future_dynamic: pl.DataFrame | None = None

    def predict(
        self,
        artifact: object,
        inputs: object,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> object:
        self.seen_future_dynamic = inputs.data.future_dynamic  # type: ignore[attr-defined]
        return super().predict(artifact, inputs, rng, prior_state)  # type: ignore[arg-type]


class _SnowFedFakeModel(FakeStationForecastModel):
    """Model whose ENTIRE future requirement is the snow channel ('swe') —
    records whether ``predict`` was ever reached, to prove the per-model
    ``assess_future_coverage`` gate suppressed it rather than it running on an
    empty/absent future frame (Plan 145 review fold-in, full-cycle acceptance)."""

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"swe"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.SINGLE,
    )

    def __init__(self) -> None:
        self.predict_called = False

    def predict(
        self,
        artifact: object,
        inputs: object,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> object:
        self.predict_called = True
        return super().predict(artifact, inputs, rng, prior_state)  # type: ignore[arg-type]


class TestWeatherForecastAdapterConfig:
    def test_enabled_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "/tmp/test-nwp"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.enabled is True

    def test_explicit_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.enabled is False

    def test_absent_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(tmp_path / "config.toml")
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.enabled is False

    def test_require_nwp_env_is_parsed_without_sapphire_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SAPPHIRE_REQUIRE_NWP", "1")

        config = _load_weather_forecast_adapter_config()

        assert config.require_nwp is True
        assert config.enabled is False

    def test_require_nwp_invalid_env_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SAPPHIRE_REQUIRE_NWP", "sometimes")

        with pytest.raises(ConfigurationError, match="SAPPHIRE_REQUIRE_NWP"):
            _load_weather_forecast_adapter_config()

    def test_expected_delivery_offset_parses_from_monitoring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false

[adapters.weather_forecast.monitoring]
expected_delivery_offset_hours = 2.5
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.expected_delivery_offset_hours == 2.5

    def test_absent_enabled_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "/tmp/test-nwp"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.enabled is False

    def test_configured_stac_and_scratch_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://custom.example/stac"
stac_collection = "custom-collection"
scratch_path = "/tmp/custom-scratch"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.stac_base_url == "https://custom.example/stac"
        assert config.stac_collection == "custom-collection"
        assert config.scratch_path == Path("/tmp/custom-scratch")

    def test_overlay_scalar_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "/tmp/test-nwp"
""",
        )
        overlay_path = tmp_path / "overlay.toml"
        overlay_path.write_text(
            """
[adapters.weather_forecast]
enabled = false
""".strip()
            + "\n"
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(base_path))
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay_path))

        config = _load_weather_forecast_adapter_config()

        assert config.enabled is False
        assert config.stac_base_url == "https://example.test/stac"

    def test_non_bool_enabled_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = "false"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with pytest.raises(ConfigurationError, match="TOML boolean"):
            _load_weather_forecast_adapter_config()

    def test_enabled_true_missing_meteoswiss_field_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
scratch_path = "/tmp/test-nwp"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with pytest.raises(ConfigurationError, match="stac_collection"):
            _load_weather_forecast_adapter_config()

    def test_grid_extractor_defaults_to_mesh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        assert _load_weather_forecast_adapter_config().grid_extractor == "mesh"

    def test_grid_extractor_explicit_exactextract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
grid_extractor = "exactextract"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        assert _load_weather_forecast_adapter_config().grid_extractor == "exactextract"

    def test_grid_extractor_unknown_value_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
grid_extractor = "regrid"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with pytest.raises(ConfigurationError, match="grid_extractor"):
            _load_weather_forecast_adapter_config()

    def test_disk_guard_thresholds_thread_from_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TOML disk_guard_*_gb values must reach the constructed adapter via
        _WeatherForecastAdapterConfig (Plan 105 config-threading gate)."""
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
disk_guard_scratch_soft_gb = 3.0
disk_guard_scratch_hard_gb = 1.0
disk_guard_archive_soft_gb = 12.0
disk_guard_archive_hard_gb = 6.0
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.disk_guard_scratch_soft_gb == 3.0
        assert config.disk_guard_scratch_hard_gb == 1.0
        assert config.disk_guard_archive_soft_gb == 12.0
        assert config.disk_guard_archive_hard_gb == 6.0

    def test_disk_guard_thresholds_use_defaults_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import (
            DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB,
            DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB,
            DEFAULT_DISK_GUARD_SCRATCH_HARD_GB,
            DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB,
        )

        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.disk_guard_scratch_soft_gb == DEFAULT_DISK_GUARD_SCRATCH_SOFT_GB
        assert config.disk_guard_scratch_hard_gb == DEFAULT_DISK_GUARD_SCRATCH_HARD_GB
        assert config.disk_guard_archive_soft_gb == DEFAULT_DISK_GUARD_ARCHIVE_SOFT_GB
        assert config.disk_guard_archive_hard_gb == DEFAULT_DISK_GUARD_ARCHIVE_HARD_GB

    def test_disk_guard_hard_gte_soft_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """hard_gb >= soft_gb must raise ConfigurationError."""
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
disk_guard_scratch_soft_gb = 1.0
disk_guard_scratch_hard_gb = 1.0
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with pytest.raises(ConfigurationError, match="disk_guard_scratch_hard_gb"):
            _load_weather_forecast_adapter_config()

    def test_type_defaults_to_meteoswiss_nwp_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        assert _load_weather_forecast_adapter_config().type == "meteoswiss_nwp"


class TestWeatherForecastConfigTypeBranch:
    """Plan 082 Task 2C: the `type` selector, not `enabled` alone, decides
    which adapter's required-field set is validated."""

    def test_recap_gateway_type_skips_meteoswiss_field_requirement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
type = "recap_gateway"

[adapters.recap_gateway]
base_url = "https://recap.example.org"
timeout_s = 300
verify_tls = true
staleness_threshold_hours = 6.0
hru_metadata_source = "manual_gpkg_upload"
max_retries = 3
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        config = _load_weather_forecast_adapter_config()

        assert config.type == "recap_gateway"
        assert config.enabled is True

    def test_meteoswiss_nwp_type_still_requires_meteoswiss_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
type = "meteoswiss_nwp"
stac_base_url = "https://example.test/stac"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with pytest.raises(ConfigurationError, match="stac_collection"):
            _load_weather_forecast_adapter_config()

    def test_recap_gateway_type_missing_recap_section_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
type = "recap_gateway"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with pytest.raises(ConfigurationError, match="recap_gateway"):
            _load_weather_forecast_adapter_config()


class _FakeRecapEcmwf:
    def ifs_forecast(self, **kwargs: object) -> object:
        raise AssertionError("dispatch test must not actually fetch")

    def era5_land_reanalysis(self, **kwargs: object) -> object:
        raise AssertionError("dispatch test must not actually fetch")


class _FakeRecapClient:
    def __init__(self) -> None:
        self.ecmwf = _FakeRecapEcmwf()


class _FakeGatewayPolygonBindingStore:
    def fetch_bindings_for_station(self, station_id: object) -> list[object]:
        return []


class TestRecapForecastDispatch:
    """Plan 082 Task 2D: Flow-1 dispatch builds RecapGatewayForecastAdapter,
    never MeteoSwissNwpAdapter, when type=recap_gateway."""

    def test_builds_recap_gateway_forecast_adapter_not_meteoswiss(self) -> None:
        from sapphire_flow.adapters.recap_gateway import RecapGatewayForecastAdapter
        from sapphire_flow.flows.run_forecast_cycle import (
            _build_recap_forecast_adapter,
        )

        adapter, _policy = _build_recap_forecast_adapter(
            config_path=None,
            gateway_polygon_store=_FakeGatewayPolygonBindingStore(),
            recap_client=_FakeRecapClient(),
        )

        assert isinstance(adapter, RecapGatewayForecastAdapter)
        assert adapter.NWP_SOURCE == "ifs_ecmwf"

    def test_raises_when_gateway_polygon_store_unavailable(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import (
            _build_recap_forecast_adapter,
        )

        with pytest.raises(ConfigurationError, match="gateway_polygon_store"):
            _build_recap_forecast_adapter(
                config_path=None,
                gateway_polygon_store=None,
                recap_client=_FakeRecapClient(),
            )


class TestForcingResolutionPolicyConstruction:
    """Plan 151 T8a red-first (D7, construction paths 1/2): the frozen pair
    ``_build_recap_forecast_adapter`` returns supplies ``ForcingResolutionPolicy``
    on both the production and injected-client construction branches."""

    def test_injected_client_config_path_none_yields_named_default(self) -> None:
        # Path 2, config_path=None -- the same branch
        # test_builds_recap_gateway_forecast_adapter_not_meteoswiss drives,
        # now additionally proving it still constructs AND yields the ONE
        # named default policy constant, field-by-field, plus the
        # construction log event (D7 -- never a silent per-field fallback).
        import structlog.testing

        from sapphire_flow.flows.run_forecast_cycle import (
            _build_recap_forecast_adapter,
        )

        default_policy = _flow_attr("_DEFAULT_FORCING_RESOLUTION_POLICY")

        with structlog.testing.capture_logs() as captured:
            adapter, policy = _build_recap_forecast_adapter(
                config_path=None,
                gateway_polygon_store=_FakeGatewayPolygonBindingStore(),
                recap_client=_FakeRecapClient(),
            )

        assert adapter is not None
        assert policy == default_policy
        assert policy.cycle_cadence_hours == default_policy.cycle_cadence_hours
        assert policy.max_cycle_age_hours == default_policy.max_cycle_age_hours
        assert policy.max_retries == default_policy.max_retries
        assert any(
            event.get("event") == "nwp.forcing_resolution_policy_default_used"
            and event.get("construction_path") == "injected_client"
            for event in captured
        )

    def test_injected_client_config_path_set_yields_config_derived_policy(
        self, tmp_path: Path
    ) -> None:
        # Path 2, config_path SET -- today this branch loads no config at
        # all; T8a changes that so a config-bearing injected-client caller
        # gets the SAME config-derived policy as the production branch.
        from sapphire_flow.flows.run_forecast_cycle import (
            _build_recap_forecast_adapter,
        )

        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
type = "recap_gateway"

[adapters.recap_gateway]
base_url = "https://recap.example.org"
timeout_s = 300
verify_tls = true
staleness_threshold_hours = 6.0
hru_metadata_source = "manual_gpkg_upload"
max_retries = 5
max_cycle_age_hours = 30.0
cycle_cadence_hours = 12.0
""",
        )

        adapter, policy = _build_recap_forecast_adapter(
            config_path=str(config_path),
            gateway_polygon_store=_FakeGatewayPolygonBindingStore(),
            recap_client=_FakeRecapClient(),
        )

        assert adapter is not None
        assert policy.cycle_cadence_hours == 12.0
        assert policy.max_cycle_age_hours == 30.0
        assert policy.max_retries == 5

    def test_production_path_yields_config_derived_policy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Path 1, recap_client=None -- the real-client production branch.
        # Non-default cadence/age/retries all round-trip into the policy
        # from the SAME parsed RecapGatewayConfig the adapter is built from.
        from sapphire_flow.flows.run_forecast_cycle import (
            _build_recap_forecast_adapter,
        )

        monkeypatch.setenv("RECAP_API_KEY", "test-key")
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
type = "recap_gateway"

[adapters.recap_gateway]
base_url = "https://recap.example.org"
timeout_s = 300
verify_tls = true
staleness_threshold_hours = 6.0
hru_metadata_source = "manual_gpkg_upload"
max_retries = 7
max_cycle_age_hours = 24.0
cycle_cadence_hours = 3.0
""",
        )

        adapter, policy = _build_recap_forecast_adapter(
            config_path=str(config_path),
            gateway_polygon_store=_FakeGatewayPolygonBindingStore(),
            recap_client=None,
        )

        assert adapter is not None
        assert policy.cycle_cadence_hours == 3.0
        assert policy.max_cycle_age_hours == 24.0
        assert policy.max_retries == 7

    def test_injected_adapter_helper_defaults_when_none(self) -> None:
        # Path 3 (D7): `_default_injected_adapter_forcing_policy` fills the
        # None case with the named default constant and logs its use.
        import structlog.testing

        default_policy = _flow_attr("_DEFAULT_FORCING_RESOLUTION_POLICY")
        default_injected_adapter_forcing_policy = _flow_attr(
            "_default_injected_adapter_forcing_policy"
        )

        with structlog.testing.capture_logs() as captured:
            resolved = default_injected_adapter_forcing_policy(None)

        assert resolved == default_policy
        assert any(
            event.get("event") == "nwp.forcing_resolution_policy_default_used"
            and event.get("construction_path") == "injected_adapter"
            for event in captured
        )

    def test_injected_adapter_helper_explicit_policy_wins(self) -> None:
        # Path 3 (D7 ruling 4): an explicit policy wins outright -- the
        # helper takes NO `adapter` parameter at all, so it structurally
        # cannot read anything off one (the negative criterion holds by
        # construction, not by a runtime trap).
        default_injected_adapter_forcing_policy = _flow_attr(
            "_default_injected_adapter_forcing_policy"
        )
        forcing_resolution_policy_cls = _forcing_track_type_attr(
            "ForcingResolutionPolicy"
        )

        explicit_policy = forcing_resolution_policy_cls(
            cycle_cadence_hours=1.0, max_cycle_age_hours=2.0, max_retries=9
        )

        assert (
            default_injected_adapter_forcing_policy(explicit_policy) is explicit_policy
        )

    def test_injected_adapter_flow_level_accepts_new_parameter(self) -> None:
        # End-to-end smoke test: the flow accepts an injected adapter AND an
        # explicit forcing_resolution_policy without raising. The parameter
        # is not yet threaded into any resolver call in this run (T8a is
        # dormant), so there is no further downstream effect to assert.
        forcing_resolution_policy_cls = _forcing_track_type_attr(
            "ForcingResolutionPolicy"
        )

        class _PlainAdapter:
            def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
                raise AssertionError("dispatch test must not actually fetch")

        explicit_policy = forcing_resolution_policy_cls(
            cycle_cadence_hours=1.0, max_cycle_age_hours=2.0, max_retries=9
        )

        result = run_forecast_cycle_flow(
            station_store=FakeStationStore(),
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            adapter=_PlainAdapter(),
            forcing_resolution_policy=explicit_policy,
            models={},
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result is not None

    def test_flow_boundary_rejects_a_value_that_is_not_a_forcing_resolution_policy(
        self,
    ) -> None:
        """Locks T8a review finding 4 (minor): the public flow parameter must
        be typed as the CONCRETE `ForcingResolutionPolicy | None` -- not
        `object | None`, which Prefect's pydantic-backed parameter
        validation accepts unconditionally, letting a malformed value cross
        the flow boundary and fail only later, deep inside T8b, when a
        policy attribute is accessed. With the concrete annotation, Prefect
        rejects a structurally wrong value at the boundary itself."""
        import prefect.exceptions

        class _PlainAdapter:
            def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
                raise AssertionError("dispatch test must not actually fetch")

        with pytest.raises(prefect.exceptions.ParameterTypeError):
            run_forecast_cycle_flow(
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                weather_forecast_store=FakeWeatherForecastStore(),
                forecast_store=FakeForecastStore(),
                model_state_store=FakeModelStateStore(),
                artifact_store=FakeModelArtifactStore(),
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                adapter=_PlainAdapter(),
                forcing_resolution_policy=object(),  # type: ignore[arg-type]
                models={},
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )


class _RaisingAdapter:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
        raise self._exc


class _AlwaysRaisingForecastStore:
    """Wraps a ``FakeForecastStore``, raising on every ``store_forecast``
    call — Plan 116's acceptance scenario: a TOTAL ``store_forecast``
    failure. Delegates every other method (including read paths the flow
    doesn't call here) to the inner fake."""

    def __init__(self) -> None:
        self._inner = FakeForecastStore()

    def store_forecast(self, forecast: object) -> object:
        raise StoreError("simulated total store_forecast failure")

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _FailAfterNForecastStore:
    """Wraps a ``FakeForecastStore``; the first ``n`` calls to
    ``store_forecast`` succeed and delegate to the inner fake, every call
    after that raises ``StoreError`` — Plan 116 fixer round major 1's
    acceptance scenario: a group cycle where an EARLIER forecast stores
    fine and a LATER one hits a fatal store outage mid-cycle."""

    def __init__(self, *, n: int) -> None:
        self._inner = FakeForecastStore()
        self._n = n
        self.calls = 0

    def store_forecast(self, forecast: OperationalForecast) -> object:
        self.calls += 1
        if self.calls > self._n:
            raise StoreError("simulated store_forecast failure after N successes")
        return self._inner.store_forecast(forecast)


class _FailAfterNForecastStoreWithDbError:
    """Same shape as ``_FailAfterNForecastStore`` but the failure is a RAW
    ``sqlalchemy.exc.OperationalError`` rather than ``StoreError`` — Plan
    116 fixer round blocker: ``PgForecastStore.store_forecast`` never
    translates SQLAlchemy failures into ``StoreError``, so a real database
    outage (connection loss, deadlock, ...) reaches the flow as exactly
    this kind of exception, not a ``StoreError``."""

    def __init__(self, *, n: int) -> None:
        self._inner = FakeForecastStore()
        self._n = n
        self.calls = 0

    def store_forecast(self, forecast: OperationalForecast) -> object:
        self.calls += 1
        if self.calls > self._n:
            raise sa_exc.OperationalError(
                "INSERT INTO forecasts ...", {}, Exception("connection lost")
            )
        return self._inner.store_forecast(forecast)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _only_nwp_delivery_record(
    health_store: FakePipelineHealthStore,
) -> PipelineHealthRecord:
    records = [
        r
        for r in health_store._records
        if r.check_type == PipelineCheckType.NWP_DELIVERY
    ]
    assert len(records) == 1, records
    return records[0]


class TestRecapNwpDeliveryWatchdog:
    """Plan 082 Task 2G (Flow-1 half): per-category NWP_DELIVERY records +
    HARD-ABORT vs degrade-to-runoff-only outcome."""

    def _run_with_adapter(
        self, adapter: object
    ) -> tuple[object, FakePipelineHealthStore]:
        health_store = FakePipelineHealthStore()
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            pipeline_health_store=health_store,
        )
        return outcome, health_store

    def test_config_error_hard_aborts_with_critical_record(self) -> None:
        from sapphire_flow.adapters.recap_gateway import RecapConfigurationError

        outcome, health_store = self._run_with_adapter(
            _RaisingAdapter(RecapConfigurationError("bad hru", field="hru_code"))
        )

        assert outcome is None
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL
        assert record.detail["reason"] == "config_error"
        assert record.detail["field"] == "hru_code"

    def test_all_unmappable_hard_aborts_with_critical_record(self) -> None:
        from sapphire_flow.adapters.recap_gateway import GatewayResolutionError

        sid = StationId(uuid4())
        outcome, health_store = self._run_with_adapter(
            _RaisingAdapter(GatewayResolutionError("all unmappable", station_id=sid))
        )

        assert outcome is None
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL
        assert record.detail["reason"] == "all_unmappable"

    def test_auth_error_hard_aborts_with_critical_record(self) -> None:
        from sapphire_flow.adapters.recap_gateway import RecapAuthError

        outcome, health_store = self._run_with_adapter(
            _RaisingAdapter(RecapAuthError("unauthorized", status_code=401))
        )

        assert outcome is None
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL
        assert record.detail["reason"] == "auth"
        assert record.detail["status_code"] == 401

    def test_source_data_missing_degrades_to_runoff_only_with_warning_record(
        self,
    ) -> None:
        from sapphire_flow.adapters.recap_gateway import RecapDataUnavailableError

        outcome, health_store = self._run_with_adapter(
            _RaisingAdapter(
                RecapDataUnavailableError(
                    "not published yet", code="source_data_missing"
                )
            )
        )

        assert isinstance(outcome, _NwpFetchOutcome)
        assert outcome.nwp_unavailable is True
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.WARNING
        assert record.detail["reason"] == "source_data_missing"

    def test_recap_staleness_negative_control_no_icon_rows(self) -> None:
        # An IFS-only Nepal deploy with a fresh ifs_ecmwf cycle and NO
        # icon_ch2_eps rows must NOT trip CRITICAL staleness.
        nwp_store = FakeWeatherForecastStore()
        nwp_store.store_weather_forecasts(
            [
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=StationId(uuid4()),
                    nwp_source="ifs_ecmwf",
                    cycle_time=_NOW,
                    valid_time=_NOW,
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=0,
                    value=1.0,
                    created_at=_NOW,
                )
            ]
        )
        health_store = FakePipelineHealthStore()

        stale = _check_nwp_grid_staleness(
            nwp_store,
            health_store,
            expected_delivery_offset_hours=5.0,
            checked_at=_NOW,
            cycle_time=_NOW,
            forecast_source="ifs_ecmwf",
        )

        assert stale is False
        assert health_store._records == []

    def test_meteoswiss_staleness_positive_control_still_critical(self) -> None:
        # Converse: MeteoSwiss provider + an old icon_ch2_eps cycle still
        # trips CRITICAL — parameterizing the source did not disable the
        # existing MeteoSwiss check.
        nwp_store = FakeWeatherForecastStore()
        old_cycle = ensure_utc(_NOW - timedelta(hours=100))
        nwp_store.store_weather_forecasts(
            [
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=StationId(uuid4()),
                    nwp_source="icon_ch2_eps",
                    cycle_time=old_cycle,
                    valid_time=old_cycle,
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=0,
                    value=1.0,
                    created_at=old_cycle,
                )
            ]
        )
        health_store = FakePipelineHealthStore()

        stale = _check_nwp_grid_staleness(
            nwp_store,
            health_store,
            expected_delivery_offset_hours=5.0,
            checked_at=_NOW,
            cycle_time=_NOW,
            forecast_source="icon_ch2_eps",
        )

        assert stale is True
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL


class TestNwpFetchTaskFailureReason:
    """Plan 223 D5/D6 — the ``_fetch_nwp_task`` boundary: the generic
    ``except Exception`` clause is the only place that still holds the
    exception, so it is where the sanitised cause must be constructed and
    attached to the outcome instead of collapsing to bare ``None``."""

    def _run_with_adapter(self, adapter: object) -> object:
        sid = StationId(uuid4())
        source = StationWeatherSource(
            station_id=sid,
            nwp_source=_NWP_SOURCE,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.FORECAST,
        )
        return _fetch_nwp_task.fn(
            adapter=adapter,  # type: ignore[arg-type]
            station_configs=[source],
            cycle_time=_NOW,
            weather_forecast_store=FakeWeatherForecastStore(),
            clock=_clock,
        )

    def test_generic_exception_returns_outcome_with_generic_reason(self) -> None:
        outcome = self._run_with_adapter(_RaisingAdapter(RuntimeError("boom")))

        assert isinstance(outcome, _NwpFetchOutcome)
        assert outcome.fetch_failure_reason == "nwp_fetch_failed"

    def test_budget_exceeded_file_count_names_the_cap_with_numbers(self) -> None:
        exc = BudgetExceededError(
            "GRIB file count exceeded: 501 > 500",
            kind="file_count",
            observed=501,
            limit=500,
        )
        outcome = self._run_with_adapter(_RaisingAdapter(exc))

        assert isinstance(outcome, _NwpFetchOutcome)
        assert outcome.fetch_failure_reason == "nwp_file_count_exceeded: 501 > 500"

    def test_budget_exceeded_byte_cap_names_the_cap_with_numbers(self) -> None:
        exc = BudgetExceededError(
            "Download size cap exceeded: 999 > 500",
            kind="byte",
            observed=999,
            limit=500,
        )
        outcome = self._run_with_adapter(_RaisingAdapter(exc))

        assert isinstance(outcome, _NwpFetchOutcome)
        assert outcome.fetch_failure_reason == "nwp_byte_exceeded: 999 > 500"

    def test_url_bearing_exception_never_reaches_the_reason(self) -> None:
        leaking = AdapterError(
            "NWP fetch failed: https://rgw.cscs.ch/bucket/x.grib2"
            "?AWSAccessKeyId=SECRET_KEY_ID&Signature=SECRET_SIG&Expires=9999999999"
        )
        outcome = self._run_with_adapter(_RaisingAdapter(leaking))

        assert isinstance(outcome, _NwpFetchOutcome)
        reason = outcome.fetch_failure_reason
        assert reason == "nwp_fetch_failed"
        assert reason is not None
        assert "AWSAccessKeyId" not in reason
        assert "SECRET" not in reason
        assert "https://" not in reason


def _snow_ws(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="ifs_ecmwf",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.FORECAST,
    )


class TestComputeRequiredSnow:
    """Plan 145 2a: the pre-submission per-station required-future-snow map."""

    def test_active_snow_model_contributes_its_snow_features(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _compute_required_snow

        sid = StationId(uuid4())
        model_id = ModelId("snow_model")

        class _SnowModel:
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset({"swe", "precipitation"}),
                static_features=frozenset(),
                supported_time_steps=frozenset({timedelta(days=1)}),
                lookback_steps=1,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            )

        assignment = ModelAssignment(
            station_id=sid,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        required = _compute_required_snow(
            {sid: [assignment]},
            {model_id: _SnowModel()},  # type: ignore[dict-item]
        )
        assert required == {sid: frozenset({"swe"})}

    def test_inactive_assignment_contributes_nothing(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _compute_required_snow

        sid = StationId(uuid4())
        model_id = ModelId("snow_model")

        class _SnowModel:
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset({"swe"}),
                static_features=frozenset(),
                supported_time_steps=frozenset({timedelta(days=1)}),
                lookback_steps=1,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            )

        # Deliberately NOT active-filtered (as _active_only would do); this
        # locks that the map builder does not need to re-filter -- but it also
        # proves an unresolved model contributes nothing, below.
        assignment = ModelAssignment(
            station_id=sid,
            model_id=ModelId("missing_model"),
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        required = _compute_required_snow(
            {sid: [assignment]},
            {model_id: _SnowModel()},  # type: ignore[dict-item]
        )
        assert required == {}

    def test_past_only_snow_model_contributes_nothing(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _compute_required_snow

        sid = StationId(uuid4())
        model_id = ModelId("antecedent_snow_model")

        class _PastOnlySnowModel:
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset({"swe"}),
                future_dynamic_features=frozenset(),
                static_features=frozenset(),
                supported_time_steps=frozenset({timedelta(days=1)}),
                lookback_steps=1,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            )

        assignment = ModelAssignment(
            station_id=sid,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        required = _compute_required_snow(
            {sid: [assignment]},
            {model_id: _PastOnlySnowModel()},  # type: ignore[dict-item]
        )
        assert required == {}

    def test_non_snow_model_contributes_nothing(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _compute_required_snow

        sid = StationId(uuid4())
        assignment = ModelAssignment(
            station_id=sid,
            model_id=_MODEL_ID,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        required = _compute_required_snow(
            {sid: [assignment]},
            {_MODEL_ID: FakeStationForecastModel()},  # type: ignore[dict-item]
        )
        assert required == {}


class TestReconcileSnowCoverageNaN:
    """Plan 145 review escalation: a required snow parameter present as rows
    that are all-NaN must be treated as MISSING coverage, not covered -- a
    NaN forcing value is never usable, and ``_covered_snow_parameters``
    previously counted mere row presence (via the ``parameter`` column)
    regardless of whether the ``value`` column was finite."""

    def test_all_nan_parameter_is_reported_missing(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _reconcile_snow_coverage

        sid = StationId(uuid4())
        rows = [
            {
                "valid_time": ensure_utc(_NOW + timedelta(hours=h)),
                "parameter": "swe",
                "member_id": None,
                "value": float("nan"),
            }
            for h in (1, 2, 3)
        ]
        forecast = BasinAverageForecast(
            nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(rows)
        )
        missing = _reconcile_snow_coverage(
            required_snow={sid: frozenset({"swe"})},
            bound_station_ids=frozenset({sid}),
            forecasts={sid: forecast},
        )
        assert missing == {sid: frozenset({"swe"})}

    def test_at_least_one_finite_value_is_covered(self) -> None:
        from sapphire_flow.flows.run_forecast_cycle import _reconcile_snow_coverage

        sid = StationId(uuid4())
        rows = [
            {
                "valid_time": ensure_utc(_NOW + timedelta(hours=1)),
                "parameter": "swe",
                "member_id": None,
                "value": 1.0,
            },
            {
                "valid_time": ensure_utc(_NOW + timedelta(hours=2)),
                "parameter": "swe",
                "member_id": None,
                "value": float("nan"),
            },
        ]
        forecast = BasinAverageForecast(
            nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(rows)
        )
        missing = _reconcile_snow_coverage(
            required_snow={sid: frozenset({"swe"})},
            bound_station_ids=frozenset({sid}),
            forecasts={sid: forecast},
        )
        assert missing == {}


class TestSnowForecastWiring:
    """Plan 145 2a/2b/2c/2d: capability-gated, station-scoped snow fetch wired
    into ``_fetch_nwp_task``, riding the SAME resolved IFS cycle, folding
    per-(hru,variable) unavailability into the returned outcome."""

    def _snow_forecast_result_factory(
        self, station_id: StationId, *, unavailable: dict | None = None
    ) -> Callable[[UtcDatetime], object]:
        """A cycle-aware factory: builds the returned forecast's ``cycle_time``
        from the ACTUAL argument ``fetch_snow_forecast`` is called with, not a
        value baked in at fixture-construction time. A fixed (non-factory)
        result would let a cycle-consistency bug (D4) hide behind a passing
        test — the stored record must reflect what was actually fetched under,
        not merely what the fake happened to be built with."""
        from sapphire_flow.types.weather import SnowForecastFetchResult

        def factory(cycle_time: UtcDatetime) -> object:
            rows = [
                {
                    "valid_time": ensure_utc(cycle_time + timedelta(hours=h)),
                    "parameter": "swe",
                    "member_id": None,
                    "value": 1.0,
                }
                for h in (1, 2, 3)
            ]
            forecast = BasinAverageForecast(
                nwp_source="ifs_ecmwf", cycle_time=cycle_time, values=pl.DataFrame(rows)
            )
            return SnowForecastFetchResult(
                forecasts={station_id: forecast}, unavailable=unavailable or {}
            )

        return factory

    def test_non_snow_capable_adapter_is_skipped_no_snow_fetch(self) -> None:
        # A plain WeatherForecastSource (no fetch_snow_forecast method) must NOT
        # be probed for snow, even when required_snow is non-empty.
        sid = StationId(uuid4())
        adapter = FakeWeatherForecastSource(result={})
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={sid: frozenset({"swe"})},
        )
        assert outcome is not None
        assert outcome.snow_unavailable is False

    def test_recap_adapter_non_snow_station_zero_snow_calls(self) -> None:
        sid = StationId(uuid4())
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        adapter = FakeSnowCapableWeatherForecastSource(result={})
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={},  # no station requires snow
        )
        assert outcome is not None
        assert adapter.snow_calls == []
        assert outcome.snow_unavailable is False

    def test_required_snow_station_missing_binding_sets_snow_unavailable(
        self,
    ) -> None:
        # A station requiring snow but with NO matching forecast binding in
        # this batch (config gap) must not silently degrade to HEALTHY --
        # it never got a chance to fetch what it needs (minor review finding).
        sid = StationId(uuid4())
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        adapter = FakeSnowCapableWeatherForecastSource(result={})
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [],  # no forecast binding at all for `sid`
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={sid: frozenset({"swe"})},
        )
        assert outcome is not None
        assert adapter.snow_calls == []
        assert outcome.snow_unavailable is True

    def test_snow_fetched_stored_and_broadcast_ready(self) -> None:
        sid = StationId(uuid4())
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        nwp_store = FakeWeatherForecastStore()
        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=self._snow_forecast_result_factory(sid),
        )
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            nwp_store,
            _clock,
            required_snow={sid: frozenset({"swe"})},
        )
        assert outcome is not None
        assert outcome.snow_unavailable is False
        assert len(adapter.snow_calls) == 1
        # required_snow reached the adapter call (major finding: it was
        # previously computed but discarded before the fetch).
        _, _, snow_required_arg = adapter.snow_calls[0]
        assert snow_required_arg == {sid: frozenset({"swe"})}
        # Snow was stored -- readback proves store_weather_forecasts was called.
        stored = nwp_store.fetch_weather_forecasts(
            station_id=sid,
            nwp_source="ifs_ecmwf",
            cycle_time=_NOW,
            parameters=["swe"],
        )
        assert len(stored) == 3
        assert all(r.member_id is None for r in stored)

    def test_snow_rides_the_resolved_ifs_cycle_not_the_nominal_request(self) -> None:
        # IFS falls back to an OLDER cycle; snow must be fetched+stored under
        # that SAME resolved cycle, not the nominal request (Plan 145 D4). The
        # snow fake's factory builds the returned forecast FROM the cycle
        # argument it actually receives, so a bug that threads the wrong cycle
        # into the snow fetch is caught by the STORED record, not merely by
        # the call argument.
        sid = StationId(uuid4())
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        resolved_cycle = ensure_utc(_NOW - timedelta(hours=6))
        ifs_result = _make_basin_avg_result([sid], cycle_time=resolved_cycle)
        nwp_store = FakeWeatherForecastStore()
        adapter = FakeSnowCapableWeatherForecastSource(
            result=ifs_result,
            snow_result=self._snow_forecast_result_factory(sid),
        )

        _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            nwp_store,
            _clock,
            required_snow={sid: frozenset({"swe"})},
        )

        assert len(adapter.snow_calls) == 1
        _, snow_cycle_arg, _ = adapter.snow_calls[0]
        assert snow_cycle_arg == resolved_cycle

        # Co-retrieval: BOTH the IFS forecast and the snow forecast must be
        # readable from the store under the exact RESOLVED cycle (not the
        # nominal _NOW request) -- the exact mismatch the plan required this
        # test to catch.
        ifs_stored = nwp_store.fetch_weather_forecasts(
            station_id=sid,
            nwp_source=_NWP_SOURCE,
            cycle_time=resolved_cycle,
        )
        snow_stored = nwp_store.fetch_weather_forecasts(
            station_id=sid,
            nwp_source="ifs_ecmwf",
            cycle_time=resolved_cycle,
            parameters=["swe"],
        )
        assert len(ifs_stored) > 0
        assert len(snow_stored) == 3
        assert all(r.cycle_time == resolved_cycle for r in (*ifs_stored, *snow_stored))

    def test_unavailable_snow_variable_sets_snow_unavailable_outcome(self) -> None:
        from sapphire_flow.types.weather import GatewayHruName
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        sid = StationId(uuid4())
        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=self._snow_forecast_result_factory(
                sid,
                unavailable={GatewayHruName("hru_x"): frozenset({"snow_depth"})},
            ),
        )
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={sid: frozenset({"swe", "snow_depth"})},
        )
        assert outcome is not None
        assert outcome.snow_unavailable is True

    def test_snow_success_with_zero_rows_sets_snow_unavailable(self) -> None:
        # A successful (no exception, EMPTY `unavailable`) but zero-row
        # response for a required station -- e.g. a still-materializing
        # variable that returns 200/zero-rows rather than raising -- is a
        # real coverage gap that `bool(snow_result.unavailable)` alone can
        # never see (review fold-in, major).
        from sapphire_flow.types.weather import SnowForecastFetchResult
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        sid = StationId(uuid4())
        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=SnowForecastFetchResult(forecasts={}, unavailable={}),
        )
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={sid: frozenset({"swe"})},
        )
        assert outcome is not None
        assert outcome.snow_unavailable is True

    def test_resolver_skipped_station_among_required_sets_snow_unavailable(
        self,
    ) -> None:
        # ONE required-snow station resolves and returns rows; a SECOND
        # required station is skipped by the resolver (unmappable to a
        # polygon) -- `_require_some_resolved` only fails when ALL stations
        # are unmappable, so the skipped station produces neither a forecast
        # NOR an `unavailable` entry. Per-station reconciliation must catch
        # it anyway (review fold-in, major).
        from sapphire_flow.types.weather import SnowForecastFetchResult
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        resolved_sid = StationId(uuid4())
        skipped_sid = StationId(uuid4())
        rows = [
            {
                "valid_time": ensure_utc(_NOW + timedelta(hours=h)),
                "parameter": "swe",
                "member_id": None,
                "value": 1.0,
            }
            for h in (1, 2, 3)
        ]
        resolved_forecast = BasinAverageForecast(
            nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(rows)
        )
        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=SnowForecastFetchResult(
                forecasts={resolved_sid: resolved_forecast}, unavailable={}
            ),
        )
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(resolved_sid), _snow_ws(skipped_sid)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={
                resolved_sid: frozenset({"swe"}),
                skipped_sid: frozenset({"swe"}),
            },
        )
        assert outcome is not None
        assert outcome.snow_unavailable is True

    def test_one_variable_missing_rows_while_another_succeeds_sets_snow_unavailable(
        self,
    ) -> None:
        # A successful response (no exception, EMPTY `unavailable`) covers
        # `swe` but never accumulated a row for `snow_depth` at the SAME
        # station -- another required variable's success must not mask a
        # sibling variable's shortfall (review fold-in, major).
        from sapphire_flow.types.weather import SnowForecastFetchResult
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        sid = StationId(uuid4())
        rows = [
            {
                "valid_time": ensure_utc(_NOW + timedelta(hours=h)),
                "parameter": "swe",
                "member_id": None,
                "value": 1.0,
            }
            for h in (1, 2, 3)
        ]
        forecast = BasinAverageForecast(
            nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(rows)
        )
        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=SnowForecastFetchResult(
                forecasts={sid: forecast}, unavailable={}
            ),
        )
        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            required_snow={sid: frozenset({"swe", "snow_depth"})},
        )
        assert outcome is not None
        assert outcome.snow_unavailable is True

    def test_snow_unavailable_marks_cycle_degraded_not_healthy(self) -> None:
        outcome_healthy = _forecast_cycle_health(
            stations_attempted=1,
            stations_failed=0,
            alert_suppressed=False,
            nwp_grid_stale=False,
            fallback_priority_drift=False,
            snow_unavailable=False,
        )
        outcome_degraded = _forecast_cycle_health(
            stations_attempted=1,
            stations_failed=0,
            alert_suppressed=False,
            nwp_grid_stale=False,
            fallback_priority_drift=False,
            snow_unavailable=True,
        )
        assert outcome_healthy == ForecastCycleHealth.HEALTHY
        assert outcome_degraded == ForecastCycleHealth.DEGRADED


class TestNwpDeliveryPartialDivergence:
    """Plan 154 D4, acceptance test 12: reconcile requested-vs-returned station
    ids on a dict-return adapter's forecast delivery so a per-HRU-contained
    partial delivery is diagnosed loudly (CRITICAL, cycle DEGRADED) rather than
    silently read as "those stations have no NWP forcing this cycle"."""

    def _basin_forecast(self, sid: StationId) -> BasinAverageForecast:
        rows = [
            {
                "valid_time": ensure_utc(_NOW + timedelta(hours=h)),
                "parameter": "precipitation",
                "member_id": 0,
                "value": 1.0,
            }
            for h in (1, 2, 3)
        ]
        return BasinAverageForecast(
            nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(rows)
        )

    def test_partial_delivery_alarms_critical_and_serves_healthy_station(
        self,
    ) -> None:
        # Acceptance test 12 (main case): two stations requested, one returned
        # -- the complete station's forecast is stored (served), a queryable
        # NWP_DELIVERY CRITICAL pipeline_health record names the missing
        # station, and the outcome carries a partial flag. Fails against
        # unmodified `_fetch_nwp_task`: no reconciliation exists today, so a
        # missing station is silently unnoticed (no record, no flag).
        sid_ok = StationId(uuid4())
        sid_missing = StationId(uuid4())
        adapter = FakeWeatherForecastSource(
            result={sid_ok: self._basin_forecast(sid_ok)}
        )
        nwp_store = FakeWeatherForecastStore()
        health_store = FakePipelineHealthStore()

        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid_ok), _snow_ws(sid_missing)],
            _NOW,
            nwp_store,
            _clock,
            pipeline_health_store=health_store,
        )

        assert outcome is not None
        assert outcome.nwp_delivery_partial is True
        # The complete station's forecast WAS persisted (served normally).
        stored = nwp_store.fetch_weather_forecasts(
            station_id=sid_ok,
            nwp_source="ifs_ecmwf",
            cycle_time=_NOW,
            parameters=["precipitation"],
        )
        assert len(stored) == 3
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL
        assert str(sid_missing) in record.detail["missing_station_ids"]
        assert str(sid_ok) not in record.detail["missing_station_ids"]

    def test_equal_count_substitution_still_alarms(self) -> None:
        # Review fold-in (minor): detection must gate on the SET DIFFERENCE,
        # not on cardinality. Requested {A, B} but returned {A, X} has EQUAL
        # counts, so a `len(returned) < len(requested)` guard silently skips
        # the alarm even though B is genuinely missing. Fails against the
        # cardinality-based guard; passes against the set-difference one.
        sid_ok = StationId(uuid4())
        sid_missing = StationId(uuid4())
        sid_unexpected = StationId(uuid4())
        adapter = FakeWeatherForecastSource(
            result={
                sid_ok: self._basin_forecast(sid_ok),
                sid_unexpected: self._basin_forecast(sid_unexpected),
            }
        )
        nwp_store = FakeWeatherForecastStore()
        health_store = FakePipelineHealthStore()

        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid_ok), _snow_ws(sid_missing)],
            _NOW,
            nwp_store,
            lambda: _NOW,
            pipeline_health_store=health_store,
        )

        assert outcome is not None
        assert outcome.nwp_delivery_partial is True
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL
        assert str(sid_missing) in record.detail["missing_station_ids"]

    def test_partial_delivery_logs_error_event(self) -> None:
        # Fixer-round finding (minor): the CRITICAL pipeline_health record is
        # locked above, but nothing pinned the `nwp.delivery_partial`
        # structured-log EVENT itself -- silently downgrading it to WARNING
        # (or dropping it) would leave every other Plan 154 test green.
        # Capture logs and assert exactly one `nwp.delivery_partial` event at
        # ERROR with the correct station/count fields.
        import structlog.testing

        sid_ok = StationId(uuid4())
        sid_missing = StationId(uuid4())
        adapter = FakeWeatherForecastSource(
            result={sid_ok: self._basin_forecast(sid_ok)}
        )
        nwp_store = FakeWeatherForecastStore()
        health_store = FakePipelineHealthStore()

        with structlog.testing.capture_logs() as captured:
            outcome = _fetch_nwp_task(
                adapter,  # type: ignore[arg-type]
                [_snow_ws(sid_ok), _snow_ws(sid_missing)],
                _NOW,
                nwp_store,
                _clock,
                pipeline_health_store=health_store,
            )

        assert outcome is not None
        partial_events = [
            event for event in captured if event.get("event") == "nwp.delivery_partial"
        ]
        assert len(partial_events) == 1
        event = partial_events[0]
        assert event.get("log_level") == "error"
        assert event.get("missing_station_ids") == [str(sid_missing)]
        assert event.get("requested") == 2
        assert event.get("returned") == 1

    def test_partial_delivery_degrades_cycle_health(self) -> None:
        outcome_healthy = _forecast_cycle_health(
            stations_attempted=1,
            stations_failed=0,
            alert_suppressed=False,
            nwp_grid_stale=False,
            fallback_priority_drift=False,
            nwp_delivery_partial=False,
        )
        outcome_degraded = _forecast_cycle_health(
            stations_attempted=1,
            stations_failed=0,
            alert_suppressed=False,
            nwp_grid_stale=False,
            fallback_priority_drift=False,
            nwp_delivery_partial=True,
        )
        assert outcome_healthy == ForecastCycleHealth.HEALTHY
        assert outcome_degraded == ForecastCycleHealth.DEGRADED

    def test_empty_mapping_is_not_divergence_no_alarm(self) -> None:
        # Boundary case (D4): an EMPTY mapping is today's legitimate no-op-NWP
        # success (Plan 154 D3 on the adapter side) -- it must record NO
        # divergence and must NOT alarm, proving reconciliation is restricted
        # to a non-empty PROPER SUBSET (0 < returned < requested).
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        adapter = FakeWeatherForecastSource(result={})
        health_store = FakePipelineHealthStore()

        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid_a), _snow_ws(sid_b)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            pipeline_health_store=health_store,
        )

        assert outcome is not None
        assert outcome.nwp_delivery_partial is False
        nwp_delivery_records = [
            r
            for r in health_store._records
            if r.check_type == PipelineCheckType.NWP_DELIVERY
        ]
        assert nwp_delivery_records == []

    def test_full_delivery_is_not_divergence_no_alarm(self) -> None:
        # Every requested station returned -- not a proper subset, no alarm.
        sid_a = StationId(uuid4())
        adapter = FakeWeatherForecastSource(result={sid_a: self._basin_forecast(sid_a)})
        health_store = FakePipelineHealthStore()

        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid_a)],
            _NOW,
            FakeWeatherForecastStore(),
            _clock,
            pipeline_health_store=health_store,
        )

        assert outcome is not None
        assert outcome.nwp_delivery_partial is False
        assert health_store._records == []


class TestNwpDeliveryPartialDivergenceFullFlow:
    """Plan 154 review fold-in (major): acceptance test 12 must be proven at
    ``run_forecast_cycle_flow`` end-to-end, not merely at the isolated
    ``_fetch_nwp_task``/``_forecast_cycle_health`` unit level -- neither of
    which invokes the flow or proves the flag actually threads into final
    cycle health, that the healthy station is really forecast, or that the
    missing station's own (lower-priority, non-NWP) fallback assignment
    produces a forecast instead of the station going dark. Omitting the
    wiring at ``run_forecast_cycle.py`` (health -> `nwp_delivery_partial=`)
    would leave `TestNwpDeliveryPartialDivergence`'s unit tests green while
    this fails."""

    def test_healthy_station_forecasts_missing_station_falls_back_cycle_degraded(
        self,
    ) -> None:
        sid_ok = StationId(uuid4())
        sid_missing = StationId(uuid4())
        fallback_id = ModelId("local_fallback")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (sid_ok, sid_missing):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                basin_store=basin_store,
            )

        # sid_missing ALSO carries a lower-priority, non-NWP fallback
        # assignment -- D4's "the affected HRU's stations fall back locally".
        # This model has NO future_dynamic_features, so it survives the
        # per-model `assess_future_coverage` gate even though sid_missing
        # never received any NWP records this cycle (it was the divergence
        # gap), while the higher-priority NWP-fed `_MODEL_ID` assignment is
        # skipped for insufficient coverage and the chain falls through.
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=sid_missing,
                model_id=fallback_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        artifact_store.store_artifact(
            model_id=fallback_id,
            artifact_bytes=b"fake_artifact",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid_missing,
            status=ModelArtifactStatus.ACTIVE,
        )

        # Adapter delivers ONLY sid_ok's forecast (a dict-return adapter after
        # per-HRU containment) -- sid_missing is the non-empty-proper-subset
        # divergence gap the flow must reconcile.
        dict_result = _make_basin_avg_result([sid_ok])
        adapter = FakeWeatherForecastSource(result=dict_result)

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={  # type: ignore[dict-item]
                _MODEL_ID: _SmallFakeModel(),
                fallback_id: _NativeFakeModel(),
            },
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        stored_station_ids = {
            fc.station_id for fc in forecast_store._forecasts.values()
        }
        # The complete station's forecast IS produced (served normally).
        assert sid_ok in stored_station_ids
        # The missing station is NEVER dropped from the cycle -- its own
        # local fallback assignment still produces a forecast.
        assert sid_missing in stored_station_ids

        # Fixer-round finding (minor): "some forecast exists" alone would
        # still pass if a broken flow wrongly picked the higher-priority
        # NWP-fed `_MODEL_ID` assignment for sid_missing (e.g. reading stale
        # or partially-populated NWP rows instead of correctly skipping that
        # assignment for insufficient coverage). Pin that sid_missing's
        # stored forecast actually came from `fallback_id`. (Not asserting
        # `nwp_cycle_source` here: it is a cycle-wide field -- PRIMARY
        # whenever the CYCLE fetched/used NWP at all, set once and reused for
        # every station's `OperationalForecast` this cycle -- not a
        # per-assignment "did THIS model consume NWP" signal, so it is
        # PRIMARY for sid_missing's fallback forecast too and cannot
        # distinguish the two models here; `model_id` is the correct and
        # sufficient discriminator.)
        missing_forecasts = [
            fc
            for fc in forecast_store._forecasts.values()
            if fc.station_id == sid_missing
        ]
        assert len(missing_forecasts) == 1
        assert missing_forecasts[0].model_id == fallback_id

        # The gap is diagnosed loudly rather than silently: a queryable
        # CRITICAL NWP_DELIVERY record names exactly the missing station.
        record = _only_nwp_delivery_record(pipeline_health_store)
        assert record.status == PipelineHealthStatus.CRITICAL
        assert str(sid_missing) in record.detail["missing_station_ids"]
        assert str(sid_ok) not in record.detail["missing_station_ids"]

        # Cycle health reflects the anomaly -- DEGRADED, not silently HEALTHY.
        assert result.health is ForecastCycleHealth.DEGRADED


class TestSnowStoreBroadcastAssembleComposed:
    """Plan 145 review fold-in (minor): the READY plan's Phase 2d locked a
    COMPOSED store->broadcast acceptance test -- ``_fetch_nwp_task`` (fetch +
    store) followed by ``assemble_station_operational_inputs`` (broadcast +
    pivot) -- for control-only and partial-member IFS batches. Proves the
    wiring between persistence and assembly, not merely that each layer
    works in isolation."""

    class _SnowEnsembleModel:
        data_requirements = ModelDataRequirements(
            target_parameters=frozenset(),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset({"precipitation", "swe"}),
            static_features=frozenset(),
            supported_time_steps=frozenset({timedelta(hours=1)}),
            lookback_steps=1,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            ensemble_mode=EnsembleMode.ENSEMBLE,
        )

    @pytest.mark.parametrize(
        "ifs_members", [(0,), (0, 1)], ids=["control_only", "partial_members"]
    )
    def test_snow_broadcasts_to_every_present_member_after_store_and_assemble(
        self, ifs_members: tuple[int, ...]
    ) -> None:
        from sapphire_flow.services.operational_inputs import (
            assemble_station_operational_inputs,
        )
        from sapphire_flow.types.weather import SnowForecastFetchResult
        from tests.fakes.fake_adapters import (
            FakeSnowCapableWeatherForecastSource,
            FakeWeatherReanalysisSource,
        )

        sid = StationId(uuid4())
        station_store = FakeStationStore()
        station_store.store_station(make_station_config(station_id=sid))
        basin_store = FakeBasinStore()
        obs_store = FakeObservationStore()
        state_store = FakeModelStateStore()
        nwp_store = FakeWeatherForecastStore()
        reanalysis = FakeWeatherReanalysisSource()

        valid_times = [ensure_utc(_NOW + timedelta(hours=h)) for h in (1, 2, 3)]
        ifs_rows = [
            {
                "valid_time": vt,
                "parameter": "precipitation",
                "member_id": member,
                "value": float(i + member),
            }
            for i, vt in enumerate(valid_times)
            for member in ifs_members
        ]
        ifs_result = {
            sid: BasinAverageForecast(
                nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(ifs_rows)
            )
        }
        snow_rows = [
            {
                "valid_time": vt,
                "parameter": "swe",
                "member_id": None,
                "value": 1.0,
            }
            for vt in valid_times
        ]
        snow_forecast = BasinAverageForecast(
            nwp_source="ifs_ecmwf", cycle_time=_NOW, values=pl.DataFrame(snow_rows)
        )
        adapter = FakeSnowCapableWeatherForecastSource(
            result=ifs_result,
            snow_result=SnowForecastFetchResult(
                forecasts={sid: snow_forecast}, unavailable={}
            ),
        )

        outcome = _fetch_nwp_task(
            adapter,  # type: ignore[arg-type]
            [_snow_ws(sid)],
            _NOW,
            nwp_store,
            _clock,
            required_snow={sid: frozenset({"swe"})},
        )
        assert outcome is not None
        assert outcome.snow_unavailable is False

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=self._SnowEnsembleModel(),  # type: ignore[arg-type]
            model_id=ModelId("snow_ensemble_model"),
            issue_time=_NOW,
            cycle_time=outcome.cycle_time,
            nwp_source="ifs_ecmwf",
            forcing_source=reanalysis,  # type: ignore[arg-type]
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=5,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        inputs, _ = result
        columns = set(inputs.data.future_dynamic.columns)
        for member in ifs_members:
            assert f"swe_{member}" in columns
            assert f"precipitation_{member}" in columns
        assert "swe" not in columns
        assert "precipitation" not in columns


class TestSnowForecastCycleIntegration:
    """Plan 145 Phase 2d's full end-to-end acceptance scenarios (review
    fold-in): prove the COMPOSED wiring -- superset requirements_override ->
    assemble (relaxed guard) -> per-model ``assess_future_coverage`` gate ->
    fallback loop -> SUCCESSFUL station result -- through a real
    ``run_forecast_cycle_flow`` call, not just at each layer's unit boundary.
    """

    def _mixed_station(
        self,
    ) -> tuple[
        StationId,
        ModelId,
        ModelId,
        FakeStationStore,
        FakeObservationStore,
        FakeWeatherForecastStore,
        FakeModelArtifactStore,
        FakeForecastStore,
        FakeModelStateStore,
        FakeAlertStore,
        FakeClimBaselineStore,
        FakeBasinStore,
        FakeHistoricalForcingStore,
    ]:
        sid = StationId(uuid4())
        snow_id = ModelId("snow_fed")
        native_id = ModelId("native_fallback")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )

        # Snow-fed model FIRST (higher priority = lower number) so the
        # fallback loop must actually suppress it before reaching native.
        for model_id, priority in ((snow_id, 0), (native_id, 1)):
            station_store.store_model_assignment(
                ModelAssignment(
                    station_id=sid,
                    model_id=model_id,
                    time_step=timedelta(hours=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=priority,
                    created_at=_NOW,
                )
            )
            artifact_store.store_artifact(
                model_id=model_id,
                artifact_bytes=b"fake_artifact",
                training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
                training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
                trained_at=_NOW,
                station_id=sid,
                status=ModelArtifactStatus.ACTIVE,
            )

        return (
            sid,
            snow_id,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forecast_store,
            state_store,
            alert_store,
            baseline_store,
            basin_store,
            forcing_store,
        )

    def test_mixed_assignment_all_snow_missing_falls_back_to_non_snow_success(
        self,
    ) -> None:
        # Snow-fed model (priority 0) + native fallback (priority 1), EVERY
        # required snow row absent via a CONTAINED Gateway failure
        # (`unavailable` non-empty). Pre-fix, the assembly `return None` trap
        # on the empty future-snow read skipped the WHOLE station -- including
        # the native fallback (Problem Sec3.4 / the plan's must-fail-red case).
        from sapphire_flow.types.weather import GatewayHruName, SnowForecastFetchResult
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        (
            sid,
            snow_id,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forecast_store,
            state_store,
            alert_store,
            baseline_store,
            basin_store,
            forcing_store,
        ) = self._mixed_station()

        snow_model = _SnowFedFakeModel()
        models = {snow_id: snow_model, native_id: _NativeFakeModel()}

        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=SnowForecastFetchResult(
                forecasts={},
                unavailable={GatewayHruName("hru_test"): frozenset({"swe"})},
            ),
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,  # type: ignore[arg-type]
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert isinstance(result, ForecastCycleResult)
        assert result.stations_succeeded == 1
        assert result.stations_failed == 0
        # A snow outage that suppresses the preferred snow model while a
        # non-snow fallback still succeeds is DEGRADED, not silently HEALTHY.
        assert result.health == ForecastCycleHealth.DEGRADED
        # The per-model coverage gate suppressed the snow model -- predict was
        # never reached for it (proves suppression, not merely "no forecast").
        assert snow_model.predict_called is False
        assert forecast_store.fetch_latest_forecast(sid, model_id=native_id) is not None
        assert forecast_store.fetch_latest_forecast(sid, model_id=snow_id) is None

    def test_snow_only_model_with_native_fallback_zero_stored_snow_succeeds(
        self,
    ) -> None:
        # Same shape, but the snow fetch succeeds with ZERO rows and NO
        # Gateway failure (`unavailable` empty) -- e.g. an unsubscribed/
        # not-yet-materializing variable rather than an outage. The snow-only
        # model must still degrade to an empty future frame and get
        # suppressed, the native fallback must still succeed. Review fold-in
        # (major): a required station with ZERO rows and no exception is a
        # real coverage gap the reconciliation must catch -- this cycle is
        # DEGRADED (the snow-fed model was suppressed for lack of data), not
        # silently HEALTHY. (Pre-fix, `bool(snow_result.unavailable)` was the
        # ONLY health signal, so this exact case reported HEALTHY.)
        from sapphire_flow.types.weather import SnowForecastFetchResult
        from tests.fakes.fake_adapters import FakeSnowCapableWeatherForecastSource

        (
            sid,
            snow_id,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forecast_store,
            state_store,
            alert_store,
            baseline_store,
            basin_store,
            forcing_store,
        ) = self._mixed_station()

        snow_model = _SnowFedFakeModel()
        models = {snow_id: snow_model, native_id: _NativeFakeModel()}

        adapter = FakeSnowCapableWeatherForecastSource(
            result={},
            snow_result=SnowForecastFetchResult(forecasts={}, unavailable={}),
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,  # type: ignore[arg-type]
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert isinstance(result, ForecastCycleResult)
        assert result.stations_succeeded == 1
        assert result.stations_failed == 0
        assert result.health == ForecastCycleHealth.DEGRADED
        assert snow_model.predict_called is False
        assert forecast_store.fetch_latest_forecast(sid, model_id=native_id) is not None
        assert forecast_store.fetch_latest_forecast(sid, model_id=snow_id) is None


class TestRecapStalenessThresholdWiring:
    """Codex review Finding 2 (major): the Flow-1 watchdog must use
    RecapGatewayConfig.staleness_threshold_hours DIRECTLY for a Recap
    deployment, not the MeteoSwiss expected_delivery_offset_hours * 6h
    cadence heuristic (which silently overrides it with the ~30h default)."""

    def _write_recap_config(
        self, tmp_path: Path, *, staleness_threshold_hours: float
    ) -> Path:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[adapters.recap_gateway]\n"
            'base_url = "https://recap.example.org"\n'
            "timeout_s = 120\n"
            "verify_tls = true\n"
            f"staleness_threshold_hours = {staleness_threshold_hours}\n"
            'hru_metadata_source = "manual_gpkg_upload"\n'
            "max_retries = 3\n"
        )
        return config_path

    def test_loader_reads_configured_threshold_not_default(
        self, tmp_path: Path
    ) -> None:
        from sapphire_flow.flows.run_forecast_cycle import (
            _load_recap_staleness_threshold_hours,
        )

        config_path = self._write_recap_config(tmp_path, staleness_threshold_hours=6.0)

        threshold = _load_recap_staleness_threshold_hours(str(config_path))

        assert threshold == 6.0

    def test_recap_threshold_trips_critical_where_default_offset_would_not(
        self, tmp_path: Path
    ) -> None:
        from sapphire_flow.flows.run_forecast_cycle import (
            _load_recap_staleness_threshold_hours,
        )

        config_path = self._write_recap_config(tmp_path, staleness_threshold_hours=6.0)
        threshold = _load_recap_staleness_threshold_hours(str(config_path))

        nwp_store = FakeWeatherForecastStore()
        old_cycle = ensure_utc(_NOW - timedelta(hours=12))
        nwp_store.store_weather_forecasts(
            [
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=StationId(uuid4()),
                    nwp_source="ifs_ecmwf",
                    cycle_time=old_cycle,
                    valid_time=old_cycle,
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=0,
                    value=1.0,
                    created_at=old_cycle,
                )
            ]
        )

        # Baseline: the OLD (buggy) call shape -- MeteoSwiss default
        # expected_delivery_offset_hours=5.0 * 6h cadence = 30h -- must NOT
        # flag a 12h-old grid as stale. This pins the pre-fix behavior this
        # finding exploited (the configured 6h Recap threshold was silently
        # ignored in favor of this ~30h default).
        baseline_health_store = FakePipelineHealthStore()
        baseline_stale = _check_nwp_grid_staleness(
            nwp_store,
            baseline_health_store,
            expected_delivery_offset_hours=5.0,
            checked_at=_NOW,
            cycle_time=_NOW,
            forecast_source="ifs_ecmwf",
        )
        assert baseline_stale is False
        assert baseline_health_store._records == []

        # Fixed wiring: the SAME 12h-old grid, fed the RecapGatewayConfig
        # threshold this test loaded from the TOML file, DOES trip CRITICAL.
        health_store = FakePipelineHealthStore()
        stale = _check_nwp_grid_staleness(
            nwp_store,
            health_store,
            expected_delivery_offset_hours=5.0,
            checked_at=_NOW,
            cycle_time=_NOW,
            forecast_source="ifs_ecmwf",
            staleness_max_age_hours=threshold,
        )

        assert stale is True
        record = _only_nwp_delivery_record(health_store)
        assert record.status == PipelineHealthStatus.CRITICAL


class TestGridExtractorSelection:
    def test_default_build_grid_constructs_mesh_extractor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With no injected grid_extractor and an archive path configured, the
        # flow constructs MeshBasinExtractor (default) — never the regular-grid
        # ExactExtractGridExtractor.
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        adapter = FakeWeatherForecastSource(result={})
        with (
            patch(
                "sapphire_flow.preprocessing.mesh_basin_extractor.MeshBasinExtractor"
            ) as mesh_cls,
            patch(
                "sapphire_flow.preprocessing.exact_extract_grid_extractor.ExactExtractGridExtractor",
                side_effect=AssertionError("exactextract must not be constructed"),
            ),
        ):
            run_forecast_cycle_flow(
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                weather_forecast_store=FakeWeatherForecastStore(),
                forecast_store=FakeForecastStore(),
                model_state_store=FakeModelStateStore(),
                artifact_store=FakeModelArtifactStore(),
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                adapter=adapter,
                models={},
                config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
                grid_store=FakeNwpGridStore(),
            )

        mesh_cls.assert_called_once()

    def test_injected_adapter_honors_exactextract_selector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An injected adapter must NOT force the default mesh extractor: the
        # configured grid_extractor selector is honored independently of the
        # adapter-build path.
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
grid_extractor = "exactextract"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        adapter = FakeWeatherForecastSource(result={})
        with (
            patch(
                "sapphire_flow.preprocessing.exact_extract_grid_extractor.ExactExtractGridExtractor"
            ) as exact_cls,
            patch(
                "sapphire_flow.preprocessing.mesh_basin_extractor.MeshBasinExtractor",
                side_effect=AssertionError("mesh must not be constructed"),
            ),
        ):
            run_forecast_cycle_flow(
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                weather_forecast_store=FakeWeatherForecastStore(),
                forecast_store=FakeForecastStore(),
                model_state_store=FakeModelStateStore(),
                artifact_store=FakeModelArtifactStore(),
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                adapter=adapter,
                models={},
                config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
                grid_store=FakeNwpGridStore(),
            )

        exact_cls.assert_called_once()

    def test_injected_adapter_honors_mesh_selector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
grid_extractor = "mesh"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        adapter = FakeWeatherForecastSource(result={})
        with (
            patch(
                "sapphire_flow.preprocessing.mesh_basin_extractor.MeshBasinExtractor"
            ) as mesh_cls,
            patch(
                "sapphire_flow.preprocessing.exact_extract_grid_extractor.ExactExtractGridExtractor",
                side_effect=AssertionError("exactextract must not be constructed"),
            ),
        ):
            run_forecast_cycle_flow(
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                weather_forecast_store=FakeWeatherForecastStore(),
                forecast_store=FakeForecastStore(),
                model_state_store=FakeModelStateStore(),
                artifact_store=FakeModelArtifactStore(),
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                adapter=adapter,
                models={},
                config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
                grid_store=FakeNwpGridStore(),
            )

        mesh_cls.assert_called_once()

    def test_injected_adapter_skips_meteoswiss_field_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression (codex P2): an injected adapter must NOT trigger full
        # MeteoSwiss-only config validation. A config with enabled=true but
        # omitting MeteoSwiss-only fields (stac_base_url/scratch_path/...) must
        # not raise ConfigurationError — the injected adapter bypasses the
        # MeteoSwiss build path entirely.
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = true
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        adapter = FakeWeatherForecastSource(result={})
        result = run_forecast_cycle_flow(
            station_store=FakeStationStore(),
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            adapter=adapter,
            models={},
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
        )

        assert isinstance(result, ForecastCycleResult)


class _SmallFakeGroupModel(FakeGroupForecastModel):
    """Group fake with the same compact data window as the flow station fake."""

    artifact_scope = ArtifactScope.GROUP
    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = FakeGroupForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


def _store_group_run(
    group_store: FakeStationGroupStore,
    artifact_store: FakeModelArtifactStore,
    model_id: ModelId,
    station_ids: frozenset[StationId],
    *,
    priority: int = 2,
) -> StationGroup:
    group = StationGroup(
        id=StationGroupId(uuid4()),
        name="test-group",
        station_ids=station_ids,
        description=None,
        created_at=_NOW,
    )
    group_store.store_group(group)
    group_store.store_group_model_assignment(
        GroupModelAssignment(
            group_id=group.id,
            model_id=model_id,
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=priority,
            created_at=_NOW,
        )
    )
    artifact_store.store_artifact(
        model_id=model_id,
        artifact_bytes=b"fake_group_artifact",
        training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
        training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
        trained_at=_NOW,
        group_id=group.id,
        status=ModelArtifactStatus.ACTIVE,
    )
    return group


class TestForecastCycle:
    def test_injected_adapter_bypasses_config_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        adapter = FakeWeatherForecastSource(result={})

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1

    def test_constructs_meteoswiss_adapter_when_config_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            f"""
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "{tmp_path / "scratch"}"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        constructed: list[dict[str, object]] = []

        class _PatchedMeteoSwissNwpAdapter:
            def __init__(
                self,
                *,
                stac_base_url: str,
                stac_collection: str,
                scratch_path: Path,
                http_client: object,
                max_fallback_steps: int,
                max_files: int | None,
                cycle_min_age_minutes: int,
                disk_guard_enabled: bool = True,
                **kwargs: object,
            ) -> None:
                constructed.append(
                    {
                        "stac_base_url": stac_base_url,
                        "stac_collection": stac_collection,
                        "scratch_path": scratch_path,
                        "http_client": http_client,
                        "max_fallback_steps": max_fallback_steps,
                        "max_files": max_files,
                        "cycle_min_age_minutes": cycle_min_age_minutes,
                    }
                )

            def fetch_forecasts(
                self,
                station_configs: list[StationWeatherSource],
                cycle_time: UtcDatetime,
            ) -> dict[StationId, BasinAverageForecast]:
                return {}

        with patch(
            "sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter",
            _PatchedMeteoSwissNwpAdapter,
        ):
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert result.stations_succeeded == 1
        assert len(constructed) == 1
        assert constructed[0]["stac_base_url"] == "https://example.test/stac"
        assert constructed[0]["stac_collection"] == "test-collection"
        assert constructed[0]["scratch_path"] == tmp_path / "scratch"
        assert constructed[0]["max_fallback_steps"] == 2
        # Plan 090: the config delivery-delay reaches the adapter (default 105).
        assert constructed[0]["cycle_min_age_minutes"] == 105
        created_client = constructed[0]["http_client"]
        assert isinstance(created_client, httpx.Client)
        assert created_client.is_closed

    def test_max_files_wires_from_config_into_adapter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 086: [adapters.weather_forecast].max_files reaches the adapter.

        Red on main: the flow constructs MeteoSwissNwpAdapter without max_files
        and the config loader carries no such field, so the cap is never wired.
        """
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            f"""
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "{tmp_path / "scratch"}"
max_files = 7
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        constructed: list[int | None] = []

        class _PatchedMeteoSwissNwpAdapter:
            def __init__(
                self,
                *,
                stac_base_url: str,
                stac_collection: str,
                scratch_path: Path,
                http_client: object,
                max_fallback_steps: int,
                max_files: int | None,
                cycle_min_age_minutes: int,
                disk_guard_enabled: bool = True,
                **kwargs: object,
            ) -> None:
                constructed.append(max_files)

            def fetch_forecasts(
                self,
                station_configs: list[StationWeatherSource],
                cycle_time: UtcDatetime,
            ) -> dict[StationId, BasinAverageForecast]:
                return {}

        with patch(
            "sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter",
            _PatchedMeteoSwissNwpAdapter,
        ):
            run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert constructed == [7]

        # Absent max_files → None (unlimited, production default).
        absent_path = _write_forecast_cycle_config(
            tmp_path / "config_absent.toml",
            """
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "/tmp/test-nwp"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(absent_path))
        assert _load_weather_forecast_adapter_config().max_files is None

    def test_adapter_none_with_absent_config_is_runoff_only_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import structlog.testing

        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        with (
            patch(
                "sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter",
                side_effect=AssertionError("adapter must not be constructed"),
            ),
            structlog.testing.capture_logs() as captured,
        ):
            result = run_forecast_cycle_flow(
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                weather_forecast_store=FakeWeatherForecastStore(),
                forecast_store=FakeForecastStore(),
                model_state_store=FakeModelStateStore(),
                artifact_store=FakeModelArtifactStore(),
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                models={},
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert result.stations_attempted == 0
        assert result.errors == ()
        assert any(
            event.get("event") == "forecast_cycle.nwp_disabled_missing_config"
            and event.get("log_level") == "warning"
            for event in captured
        )

    def test_require_nwp_with_disabled_adapter_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.setenv("SAPPHIRE_REQUIRE_NWP", "1")

        with pytest.raises(ConfigurationError, match="SAPPHIRE_REQUIRE_NWP"):
            run_forecast_cycle_flow(
                station_store=FakeStationStore(),
                obs_store=FakeObservationStore(),
                weather_forecast_store=FakeWeatherForecastStore(),
                forecast_store=FakeForecastStore(),
                model_state_store=FakeModelStateStore(),
                artifact_store=FakeModelArtifactStore(),
                alert_store=FakeAlertStore(),
                pipeline_health_store=FakePipelineHealthStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                models={},
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

    def test_runoff_only_ignores_grid_archive_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        real_import = builtins.__import__
        blocked_modules = {
            "sapphire_flow.store.zarr_nwp_grid_store",
            "sapphire_flow.preprocessing.exact_extract_grid_extractor",
        }

        def guarded_import(
            name: str,
            globals: dict[str, object] | None = None,  # noqa: A002 - mirrors __import__ signature
            locals: dict[str, object] | None = None,  # noqa: A002 - mirrors __import__ signature
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name in blocked_modules:
                raise AssertionError(f"disabled NWP must not import {name}")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", guarded_import)

        result = run_forecast_cycle_flow(
            station_store=FakeStationStore(),
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            models={},
            config=_make_config(nwp_grid_archive_base_path="/data/nwp_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.errors == ()

    def test_runoff_only_skips_nwp_task_and_forecasts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import structlog.testing

        monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        sid = StationId(uuid4())
        native_id = ModelId("native_runoff_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        with (
            patch(
                "sapphire_flow.flows.run_forecast_cycle._fetch_nwp_task.submit",
                side_effect=AssertionError("NWP task must not be submitted"),
            ),
            patch(
                "sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter",
                side_effect=AssertionError("adapter must not be constructed"),
            ),
            structlog.testing.capture_logs() as captured,
        ):
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                models={native_id: _NativeFakeModel()},  # type: ignore[dict-item]
                config=_make_config(nwp_grid_archive_base_path="/data/nwp_grids"),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert result.stations_attempted == 1
        assert result.stations_succeeded == 1
        assert result.forecasts_stored == 1
        assert result.errors == ()
        assert any(
            event.get("event") == "forecast_cycle.nwp_disabled"
            and event.get("mode") == "runoff_only"
            and event.get("cycle_time") == _NOW.isoformat()
            for event in captured
        )

    def test_happy_path(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        model = _SmallFakeModel()
        models = {_MODEL_ID: model}

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
            )

        adapter = FakeWeatherForecastSource(result={})  # NWP fetch returns empty dict

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert isinstance(result, ForecastCycleResult)
        assert result.stations_succeeded == 2
        assert result.forecasts_stored == 2
        assert len(forecast_store._forecasts) == 2
        # Warm-up state persisted for both stations
        assert (sid_a, _MODEL_ID) in state_store._states
        assert (sid_b, _MODEL_ID) in state_store._states

    def test_station_dark_writes_pipeline_health_and_degrades_cycle(self) -> None:
        sid_dark = StationId(uuid4())
        sid_ok = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid_dark,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_artifact=False,
        )
        _build_station_and_stores(
            sid_ok,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1
        assert result.stations_failed == 1
        assert result.health is ForecastCycleHealth.DEGRADED
        assert any("produced zero forecasts" in err for err in result.errors)
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_STATION_DARK
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].subject == str(sid_dark)
        assert records[0].detail["reason"] == "all_models_failed"
        assert records[0].detail["assigned_models"] == [str(_MODEL_ID)]
        assert records[0].detail["nwp_enabled"] is True

    # --- Plan 116: forecast-cycle-freshness heartbeat -----------------------
    # (``PipelineCheckType.FORECAST_FRESHNESS``) — a cycle that persists ZERO
    # forecasts must emit a CRITICAL record; a cycle that persists at least
    # one must emit OK; a run with an explicit ``cycle_time`` (backfill/
    # replay) must emit no record at all; and this is a SEPARATE contract
    # from ``ForecastCycleHealth`` — a DEGRADED-but-forecasts-stored cycle
    # must still read OK here. These stay methods of ``TestForecastCycle``
    # (not a new top-level class) so they share its fixtures/imports.

    def test_total_store_forecast_failure_emits_critical_freshness_record(
        self,
    ) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=_AlwaysRaisingForecastStore(),  # type: ignore[arg-type]
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 0
        assert any("Store failed" in err for err in result.errors)

        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["forecasts_stored"] == 0
        assert records[0].cycle_time == _NOW
        assert records[0].subject == "forecast_cycle"

    def test_forecasts_stored_emits_ok_freshness_record(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 1
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK
        assert records[0].detail["forecasts_stored"] == 1

    def test_explicit_cycle_time_backfill_emits_no_freshness_record(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        # Zero forecasts stored (a total store failure) — this WOULD be
        # CRITICAL for a current/scheduled run. Passing an explicit
        # `cycle_time` marks this as a backfill/replay run, which must
        # suppress the heartbeat entirely rather than manufacture a false
        # alarm over currently-healthy production (fetch_recent orders by
        # checked_at, not cycle_time).
        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=_AlwaysRaisingForecastStore(),  # type: ignore[arg-type]
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            cycle_time="2026-01-01T00:00:00+00:00",
        )

        assert result.forecasts_stored == 0
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert records == []

    def test_degraded_but_forecasts_stored_does_not_alarm_freshness(self) -> None:
        """Requirement 1: FORECAST_FRESHNESS is a SEPARATE contract from
        ``ForecastCycleHealth``. Reuses the station-dark scenario (one dark
        station degrades the cycle) alongside a healthy station that DOES
        store a forecast — the cycle reads DEGRADED, but freshness must
        still read OK, not CRITICAL."""
        sid_dark = StationId(uuid4())
        sid_ok = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid_dark,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_artifact=False,
        )
        _build_station_and_stores(
            sid_ok,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.health is ForecastCycleHealth.DEGRADED
        assert result.forecasts_stored == 1
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK

    def test_no_operational_stations_emits_critical_freshness_record(self) -> None:
        """Requirement 3: a cycle with no operational stations is
        execution-healthy (``ForecastCycleHealth.HEALTHY``) but
        product-dark — the freshness heartbeat must still be CRITICAL, not
        skipped."""
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={},
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.health is ForecastCycleHealth.HEALTHY
        assert result.forecasts_stored == 0
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        # Plan 223 D3: this path never constructs a cause (it isn't an NWP
        # fetch failure) -- the detail must NOT grow a spurious
        # `reason: null`/`reason: None` key.
        assert "reason" not in records[0].detail

    def test_no_active_assignment_emits_critical_freshness_record(self) -> None:
        """Requirement 3, a DIFFERENT variant from the no-operational-
        stations case above (fixer round, minor): an operational cycle
        where every station IS operational and has a valid weather-source
        binding, but every one legitimately has NO active model
        assignment (`forecast_cycle.no_assignments` — every station is
        skipped, not failed). Zero forecasts stored either way — the
        freshness heartbeat must still be CRITICAL, not skipped just
        because this is a `continue`, not an error, path."""
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 0
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["forecasts_stored"] == 0

    def test_nwp_fetch_failed_abort_emits_critical_freshness_record(self) -> None:
        """The Plan 100 blackout shape: NWP fetch fails hard and the cycle
        aborts before Phase B. Zero forecasts stored — must still be
        CRITICAL, not silently skipped because the abort path returns
        early."""
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_RaisingAdapter(RuntimeError("simulated NWP fetch failure")),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.health is ForecastCycleHealth.FAILED
        assert result.forecasts_stored == 0
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        # Plan 223 D2/D5: a generic (unclassified) fetch exception still
        # carries a short constructed cause -- never raw str(exc) -- into
        # the record so the alert stops reading as an empty page.
        assert records[0].detail["reason"] == "nwp_fetch_failed"

    def test_nwp_budget_exceeded_file_count_abort_names_the_cap(self) -> None:
        """Plan 223 D6 — the actual 2026-08-29 outage: the file-count
        budget guard trips (`BudgetExceededError(kind="file_count", ...)`)
        and the freshness record must name WHICH cap tripped plus the
        observed/limit numbers, built from the exception's structured
        fields (never `str(exc)`)."""
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_RaisingAdapter(
                BudgetExceededError(
                    "GRIB file count exceeded: 501 > 500",
                    kind="file_count",
                    observed=501,
                    limit=500,
                )
            ),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.health is ForecastCycleHealth.FAILED
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].detail["reason"] == "nwp_file_count_exceeded: 501 > 500"

    def test_nwp_fetch_failure_url_bearing_exception_never_leaks(self) -> None:
        """Plan 223 D2 — a security requirement, not tidiness: MeteoSwiss
        download hrefs are presigned URLs carrying `AWSAccessKeyId` and
        `Signature`. An adapter exception wrapping one verbatim (exactly
        `meteoswiss_nwp.py:670`'s ``f"NWP fetch failed: {exc}"`` shape)
        must never reach the stored `detail` or the alert -- only the
        short constructed generic code."""
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        leaking = AdapterError(
            "NWP fetch failed: https://rgw.cscs.ch/bucket/x.grib2"
            "?AWSAccessKeyId=SECRET_KEY_ID&Signature=SECRET_SIG&Expires=9999999999"
        )

        run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_RaisingAdapter(leaking),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        reason = records[0].detail["reason"]
        assert isinstance(reason, str)
        assert "AWSAccessKeyId" not in reason
        assert "SECRET" not in reason
        assert "https://" not in reason
        assert reason == "nwp_fetch_failed"

    def test_group_only_total_store_forecast_failure_emits_critical_freshness_record(
        self,
    ) -> None:
        """Fixer round (major, take 2): a group-only deployment (no
        station-level model assignments — every station is served
        exclusively through `discover_group_runs`) must ALSO reach the
        freshness emitter on a total `store_forecast` failure — AND the
        fatal `StoreError` must still propagate out of the flow (a total
        store outage is not something the flow can silently absorb;
        `docs/conventions.md:256` — "log, raise to caller"). The group
        forecast-store loop now catches `StoreError` specifically, emits
        the freshness record (forced CRITICAL), and re-raises."""
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid_a, sid_b}),
        )

        with pytest.raises(StoreError):
            run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=_AlwaysRaisingForecastStore(),  # type: ignore[arg-type]
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                pipeline_health_store=pipeline_health_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                group_store=group_store,
                forcing_store=forcing_store,
                adapter=FakeWeatherForecastSource(result={}),
                models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["forecasts_stored"] == 0
        assert records[0].subject == "forecast_cycle"

    def test_group_store_error_mid_cycle_emits_critical_record_and_propagates(
        self,
    ) -> None:
        """Fixer round (major 1): the PREVIOUS fixer round's fix introduced
        the very defect this feature exists to detect. It replaced
        `except StoreError: raise` with a broad `except Exception` that
        swallowed the error — so if one group forecast stores
        successfully and a LATER one raises `StoreError`,
        `forecasts_stored > 0` produced an OK freshness record and the
        flow completed SUCCESSFULLY, losing the fatal storage signal
        entirely. The correct fix gets BOTH properties: emit the
        freshness record using the counter as it stands at that moment
        (forced CRITICAL — a mid-cycle crash is never "OK" just because
        an earlier forecast happened to store first), then RE-RAISE so
        the fatal signal still propagates. Must be RED against the
        broad-`except Exception` version, which returns normally with an
        OK record."""
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid_a, sid_b}),
        )

        # First store_forecast call succeeds (stores fine), the second
        # (a later station's forecast within the same group cycle) raises
        # StoreError — the mid-cycle partial-success scenario.
        failing_store = _FailAfterNForecastStore(n=1)

        with pytest.raises(StoreError):
            run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=failing_store,  # type: ignore[arg-type]
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                pipeline_health_store=pipeline_health_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                group_store=group_store,
                forcing_store=forcing_store,
                adapter=FakeWeatherForecastSource(result={}),
                models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert failing_store.calls >= 2

        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is not PipelineHealthStatus.OK
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["forecasts_stored"] == 1
        assert records[0].subject == "forecast_cycle"

    def test_group_db_error_mid_cycle_emits_critical_record_and_propagates(
        self,
    ) -> None:
        """Fixer round (blocker): the take-2 fix caught `StoreError`
        specifically, but `PgForecastStore.store_forecast` never
        translates SQLAlchemy failures (OperationalError, IntegrityError,
        ...) into `StoreError` — those propagate raw. A real database
        outage mid-cycle therefore never entered the `except StoreError`
        branch at all and fell straight through to the broad
        `except Exception` swallow-and-continue branch below it,
        reproducing the exact silent-failure bug this feature exists to
        detect: one earlier successful store plus a later raw DB failure
        still produced an OK freshness record and a successful flow
        return. The correct fix treats ANY exception from `store_forecast`
        in the group path as fatal: emit the freshness record forced
        CRITICAL using the counter as it stands at that moment, then
        RE-RAISE. Must be RED against a version that still splits
        `except StoreError` (emit+raise) from a separate
        `except Exception` (swallow+continue), which returns normally
        with an OK record for this raw `OperationalError`."""
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid_a, sid_b}),
        )

        # First store_forecast call succeeds (stores fine), the second (a
        # later station's forecast within the same group cycle) raises a
        # RAW sqlalchemy.exc.OperationalError, never a StoreError.
        failing_store = _FailAfterNForecastStoreWithDbError(n=1)

        with pytest.raises(sa_exc.OperationalError):
            run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=failing_store,  # type: ignore[arg-type]
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                pipeline_health_store=pipeline_health_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                group_store=group_store,
                forcing_store=forcing_store,
                adapter=FakeWeatherForecastSource(result={}),
                models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert failing_store.calls >= 2

        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is not PipelineHealthStatus.OK
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["forecasts_stored"] == 1
        assert records[0].subject == "forecast_cycle"

    def test_climatology_floor_writes_forecast_when_nwp_off_and_skill_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            """
[adapters.weather_forecast]
enabled = false
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        monkeypatch.delenv("SAPPHIRE_REQUIRE_NWP", raising=False)

        sid = StationId(uuid4())
        climatology_model = ClimatologyFallbackModel()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            NWP_REGRESSION_MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_nwp=False,
            seed_artifact=False,
        )
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=CLIMATOLOGY_FALLBACK_MODEL_ID,
                time_step=timedelta(hours=24),
                status=ModelAssignmentStatus.ACTIVE,
                priority=100,
                created_at=_NOW,
            )
        )
        artifact_store.store_artifact(
            model_id=CLIMATOLOGY_FALLBACK_MODEL_ID,
            artifact_bytes=_serialized_climatology_artifact(climatology_model),
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            models={
                NWP_REGRESSION_MODEL_ID: _SmallFakeModel(),
                CLIMATOLOGY_FALLBACK_MODEL_ID: climatology_model,
            },  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1
        assert result.forecasts_stored == 1
        stored_forecasts = list(forecast_store._forecasts.values())
        assert len(stored_forecasts) == 1
        assert stored_forecasts[0].model_id == CLIMATOLOGY_FALLBACK_MODEL_ID
        assert stored_forecasts[0].station_id == sid
        assert stored_forecasts[0].ensemble.parameter == "discharge"

    def test_superset_assembly_feeds_nwp_model_despite_native_first(self) -> None:
        # Heterogeneous model set: a native model (no future features) at higher
        # priority than an NWP model (needs future forcing). Pre-fix, inputs were
        # assembled from only the first (native) model's requirements, starving
        # the NWP model of its precipitation/temperature future forcing. The
        # superset assembly must hand the NWP model a populated future_dynamic.
        sid = StationId(uuid4())
        native_id = ModelId("native_fallback")
        nwp_id = ModelId("nwp_regression")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )

        # Native model FIRST (lower priority number) so first-model assembly
        # would pick it and its empty future_dynamic_features.
        for model_id, priority in ((native_id, 0), (nwp_id, 1)):
            station_store.store_model_assignment(
                ModelAssignment(
                    station_id=sid,
                    model_id=model_id,
                    time_step=timedelta(hours=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=priority,
                    created_at=_NOW,
                )
            )
            artifact_store.store_artifact(
                model_id=model_id,
                artifact_bytes=b"fake_artifact",
                training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
                training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
                trained_at=_NOW,
                station_id=sid,
                status=ModelArtifactStatus.ACTIVE,
            )

        nwp_model = _RecordingNwpFakeModel()
        models = {native_id: _NativeFakeModel(), nwp_id: nwp_model}

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert isinstance(result, ForecastCycleResult)
        assert result.stations_succeeded == 1
        # The NWP model's predict was reached and handed its future forcing.
        assert nwp_model.seen_future_dynamic is not None
        precip_cols = [
            c
            for c in nwp_model.seen_future_dynamic.columns
            if c == "precipitation" or c.startswith("precipitation_")
        ]
        assert precip_cols, (
            "NWP model received future_dynamic without any precipitation column: "
            f"{nwp_model.seen_future_dynamic.columns}"
        )
        assert nwp_model.seen_future_dynamic.height > 0

    def test_no_cycle_available_falls_to_runoff_only_not_abort(self) -> None:
        # Plan 090 D3 (Finding 1): the adapter exhausting its fallback budget
        # (NoCycleAvailableError) must NOT abort the whole cycle. NWP is treated
        # as unavailable this run → the native fallback still forecasts with
        # RUNOFF_ONLY provenance; the NWP-consuming model produces nothing.
        sid = StationId(uuid4())
        native_id = ModelId("native_fallback")
        nwp_id = ModelId("nwp_regression")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )
        for model_id, priority in ((native_id, 0), (nwp_id, 1)):
            station_store.store_model_assignment(
                ModelAssignment(
                    station_id=sid,
                    model_id=model_id,
                    time_step=timedelta(hours=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=priority,
                    created_at=_NOW,
                )
            )
            artifact_store.store_artifact(
                model_id=model_id,
                artifact_bytes=b"fake_artifact",
                training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
                training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
                trained_at=_NOW,
                station_id=sid,
                status=ModelArtifactStatus.ACTIVE,
            )

        nwp_model = _RecordingNwpFakeModel()
        models = {native_id: _NativeFakeModel(), nwp_id: nwp_model}

        class _NoCycleAdapter:
            def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
                from sapphire_flow.exceptions import NoCycleAvailableError

                raise NoCycleAvailableError("no adequate cycle within fallback budget")

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_NoCycleAdapter(),
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        # The cycle did NOT abort: the native fallback produced the forecast.
        assert isinstance(result, ForecastCycleResult)
        assert result.stations_succeeded == 1
        assert result.forecasts_stored == 1
        stored = list(forecast_store._forecasts.values())
        assert len(stored) == 1
        fc = stored[0]
        assert fc.model_id == native_id
        assert fc.nwp_cycle_source == NwpCycleSource.RUNOFF_ONLY
        assert fc.nwp_cycle_reference_time is None
        # The NWP-consuming model produced nothing and was never predicted.
        assert all(f.model_id != nwp_id for f in stored)
        assert nwp_model.seen_future_dynamic is None

    def test_nwp_grid_stale_writes_pipeline_health_and_degrades_cycle(self) -> None:
        sid = StationId(uuid4())

        class _StaleLatestCycleWeatherStore(FakeWeatherForecastStore):
            def fetch_latest_cycle_time(self, nwp_source: str) -> UtcDatetime | None:
                return ensure_utc(_NOW - timedelta(hours=31))

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = _StaleLatestCycleWeatherStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 1
        assert result.health is ForecastCycleHealth.DEGRADED
        records = pipeline_health_store.fetch_recent(PipelineCheckType.NWP_DELIVERY)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].subject == "nwp_grid"
        assert records[0].detail == {
            "last_grid_age_hours": 31.0,
            "expected_offset_hours": 5.0,
        }

    def test_fallback_priority_drift_tripwire_degrades_cycle(self) -> None:
        import structlog.testing

        sid = StationId(uuid4())
        fallback_id = ModelId("persistence_fallback")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            fallback_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        with structlog.testing.capture_logs() as captured:
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                pipeline_health_store=pipeline_health_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                adapter=FakeWeatherForecastSource(result={}),
                models={fallback_id: _SmallFakeModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert result.forecasts_stored == 1
        assert result.health is ForecastCycleHealth.DEGRADED
        assert any(
            event.get("event") == "forecast_cycle.fallback_priority_drift"
            and event.get("log_level") == "error"
            for event in captured
        )

    def test_inactive_station_assignment_excluded_from_forecasting(self) -> None:
        # Plan 124: an INACTIVE station model-assignment must NOT be forecast,
        # even if it has the highest (lowest-numbered) priority. Pre-fix, the
        # station path consumed the all-status assignment dict, so the
        # INACTIVE assignment (priority 0) won PRIMARY selection over the
        # ACTIVE one (priority 1) and its forecast was stored instead.
        sid = StationId(uuid4())
        inactive_id = ModelId("inactive_native")
        active_id = ModelId("active_native")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            inactive_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )

        for model_id, priority, status in (
            (inactive_id, 0, ModelAssignmentStatus.INACTIVE),
            (active_id, 1, ModelAssignmentStatus.ACTIVE),
        ):
            station_store.store_model_assignment(
                ModelAssignment(
                    station_id=sid,
                    model_id=model_id,
                    time_step=timedelta(hours=1),
                    status=status,
                    priority=priority,
                    created_at=_NOW,
                )
            )
            artifact_store.store_artifact(
                model_id=model_id,
                artifact_bytes=b"fake_artifact",
                training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
                training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
                trained_at=_NOW,
                station_id=sid,
                status=ModelArtifactStatus.ACTIVE,
            )

        models = {inactive_id: _NativeFakeModel(), active_id: _NativeFakeModel()}

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models=models,  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 1
        assert forecast_store.fetch_latest_forecast(sid, model_id=inactive_id) is None
        assert forecast_store.fetch_latest_forecast(sid, model_id=active_id) is not None

    def test_inactive_fallback_assignment_still_trips_drift_check(self) -> None:
        # Plan 124 boundary guard: the fallback-priority-drift HEALTH check
        # (Plan 100's locked all-status DB-drift contract) must keep seeing
        # INACTIVE rows even though the same INACTIVE assignment is now
        # excluded from forecasting. This proves the fix did NOT touch
        # `_check_fallback_priority_drift` or the raw `model_assignments` it
        # reads.
        import structlog.testing

        sid = StationId(uuid4())
        fallback_id = ModelId("persistence_fallback")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            fallback_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
        )
        # Same low-priority fallback assignment as the ACTIVE-status drift
        # test above, but INACTIVE — the drift detector must still trip.
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=fallback_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.INACTIVE,
                priority=1,
                created_at=_NOW,
            )
        )

        with structlog.testing.capture_logs() as captured:
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                pipeline_health_store=pipeline_health_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                adapter=FakeWeatherForecastSource(result={}),
                models={fallback_id: _SmallFakeModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        # The all-status drift detector still sees the INACTIVE row and
        # degrades health (Plan 100 boundary, unchanged by Plan 124).
        assert result.health is ForecastCycleHealth.DEGRADED
        assert any(
            event.get("event") == "forecast_cycle.fallback_priority_drift"
            and event.get("log_level") == "error"
            for event in captured
        )

    def test_all_active_assignments_forecast_unchanged_by_active_filter(
        self,
    ) -> None:
        # No-regression: when every assignment is already ACTIVE, the new
        # active-only filter is a no-op and behavior matches pre-fix.
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1
        assert result.forecasts_stored == 1
        assert result.health is ForecastCycleHealth.HEALTHY
        assert forecast_store.fetch_latest_forecast(sid, model_id=_MODEL_ID) is not None

    def test_accepts_group_store_kwarg_without_group_runs(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            group_store=group_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 1
        assert len(forecast_store._forecasts) == 1

    def test_station_and_group_paths_coexist_and_feed_alerts(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid_a,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )
        _build_station_and_stores(
            sid_b,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )
        station_store.store_thresholds(
            [_make_forecast_threshold(sid_a), _make_forecast_threshold(sid_b)]
        )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid_a, sid_b}),
            priority=2,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            group_store=group_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={
                _MODEL_ID: _SmallFakeModel(),
                group_model_id: _SmallFakeGroupModel(),
            },  # type: ignore[arg-type]
            config=_make_alerting_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.errors == ()
        assert result.forecasts_stored == 3
        stored_pairs = {
            (forecast.station_id, forecast.model_id)
            for forecast in forecast_store._forecasts.values()
        }
        assert stored_pairs == {
            (sid_a, _MODEL_ID),
            (sid_a, group_model_id),
            (sid_b, group_model_id),
        }

        active_alerts = alert_store.fetch_active_alerts(source=AlertSource.FORECAST)
        alerts_by_station = {alert.station_id: alert for alert in active_alerts}
        assert set(alerts_by_station) == {sid_a, sid_b}
        assert all(alert.status == AlertStatus.RAISED for alert in active_alerts)
        assert set(alerts_by_station[sid_a].model_ids) == {
            _MODEL_ID,
            group_model_id,
        }
        assert (
            alerts_by_station[sid_a].alert_model_strategy
            == ModelCombinationStrategy.POOLED
        )
        assert alerts_by_station[sid_b].model_ids == (group_model_id,)

    def test_fallback_only_forecast_alert_is_suppressed_with_health_record(
        self,
    ) -> None:
        sid = StationId(uuid4())
        fallback_id = ModelId("climatology_fallback")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            fallback_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )
        station_store.store_thresholds([_make_forecast_threshold(sid)])

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={fallback_id: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(enable_forecast_alerts=True),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored == 1
        assert result.alerts_checked is False
        assert result.health is ForecastCycleHealth.DEGRADED
        assert alert_store.fetch_active_alerts(source=AlertSource.FORECAST) == []
        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.ALERT_SUPPRESSED_FALLBACK
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.WARNING
        assert records[0].subject == str(sid)
        assert records[0].detail == {
            "alert_eligibility": [AlertEligibility.NO_EVENT_INFORMATION.value],
            "parameter": ["discharge"],
        }

    def test_group_path_runs_without_station_model_assignments(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        # Fixer round (minor): a pipeline_health_store is now wired through
        # this group-only success scenario so the FORECAST_FRESHNESS
        # heartbeat itself is locked here too (Requirement 3), not just the
        # forecasts-stored bookkeeping.
        pipeline_health_store = FakePipelineHealthStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid_a, sid_b}),
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            pipeline_health_store=pipeline_health_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            group_store=group_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.errors == ()
        assert result.forecasts_stored == 2
        stored_pairs = {
            (forecast.station_id, forecast.model_id)
            for forecast in forecast_store._forecasts.values()
        }
        assert stored_pairs == {
            (sid_a, group_model_id),
            (sid_b, group_model_id),
        }

        records = pipeline_health_store.fetch_recent(
            PipelineCheckType.FORECAST_FRESHNESS
        )
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK
        assert records[0].detail["forecasts_stored"] == 2

    def test_group_path_drops_non_operational_members(self) -> None:
        operational_sid = StationId(uuid4())
        suspended_sid = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            operational_sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )
        _build_station_and_stores(
            suspended_sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            station_status=StationStatus.SUSPENDED,
            seed_model_assignment=False,
            seed_artifact=False,
        )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({operational_sid, suspended_sid}),
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            group_store=group_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.errors == ()
        assert result.stations_attempted == 1
        assert result.forecasts_stored == 1
        stored_pairs = {
            (forecast.station_id, forecast.model_id)
            for forecast in forecast_store._forecasts.values()
        }
        assert stored_pairs == {(operational_sid, group_model_id)}

    def test_group_path_skips_overlapping_same_model_members(self) -> None:
        import structlog.testing

        sid = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        group_store = FakeStationGroupStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_model_assignment=False,
            seed_artifact=False,
        )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid}),
        )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid}),
        )

        with structlog.testing.capture_logs() as captured:
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                group_store=group_store,
                forcing_store=forcing_store,
                adapter=FakeWeatherForecastSource(result={}),
                models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert result.errors == ()
        assert result.forecasts_stored == 1
        stored_pairs = {
            (forecast.station_id, forecast.model_id)
            for forecast in forecast_store._forecasts.values()
        }
        assert stored_pairs == {(sid, group_model_id)}
        assert any(
            event.get("event") == "forecast_cycle.group_duplicate_station_model_skipped"
            for event in captured
        )

    def test_group_phase_skipped_when_group_store_not_injected(self) -> None:
        sid = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={
                _MODEL_ID: _SmallFakeModel(),
                group_model_id: _SmallFakeGroupModel(),
            },  # type: ignore[arg-type]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.errors == ()
        assert result.forecasts_stored == 1
        stored_pairs = {
            (forecast.station_id, forecast.model_id)
            for forecast in forecast_store._forecasts.values()
        }
        assert stored_pairs == {(sid, _MODEL_ID)}

    def test_emits_forecast_run_completed_event(self) -> None:
        """Per-(station, model) run_completed with ensemble_size and lead_time_hours."""
        import structlog.testing

        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        model = _SmallFakeModel()
        models = {_MODEL_ID: model}

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
            )

        adapter = FakeWeatherForecastSource(result={})

        with structlog.testing.capture_logs() as captured:
            run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                adapter=adapter,
                models=models,  # type: ignore[arg-type]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        run_events = [e for e in captured if e.get("event") == "forecast.run_completed"]

        # One event per (station, model). Two stations, one model each.
        assert len(run_events) == 2

        # Old event name is gone.
        assert not any(e.get("event") == "forecast.station_completed" for e in captured)

        # All events carry the new kwargs but never station_id as an explicit
        # kwarg — station_id is bound via structlog.contextvars.bind_contextvars
        # in run_forecast_cycle and arrives through the contextvars merge
        # processor in production, not via capture_logs's stripped chain.
        for event in run_events:
            assert event["ensemble_size"] == 21  # FakeStationForecastModel n_members
            # _SmallFakeModel: 5-step horizon * 1h time_step = 5.0 hours.
            assert event["lead_time_hours"] == 5.0
            assert isinstance(event["duration_ms"], float)
            assert event["duration_ms"] >= 0
            # Do NOT expect station_id as a kwarg — it is context-bound.
            # (capture_logs does not render contextvars into the captured dict.)

    def test_nwp_fetch_failure(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        class _BrokenAdapter:
            def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError("NWP API unavailable")

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=_BrokenAdapter(),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.forecasts_stored == 0
        assert "NWP fetch failed" in result.errors
        assert len(forecast_store._forecasts) == 0

    def test_empty_stations(self) -> None:
        # Station store has no stations at all
        result = run_forecast_cycle_flow(
            station_store=FakeStationStore(),
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            adapter=FakeWeatherForecastSource(result={}),
            models={},
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.stations_succeeded == 0
        assert result.forecasts_stored == 0
        assert result.alerts_checked is False

    def test_non_operational_stations_excluded(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        # Station is ONBOARDING, not OPERATIONAL
        station = make_station_config(
            station_id=sid,
            station_kind=StationKind.RIVER,
            station_status=StationStatus.ONBOARDING,
            measured_parameters=frozenset({"discharge"}),
        )
        station_store.store_station(station)

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            weather_forecast_store=FakeWeatherForecastStore(),
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=FakeModelArtifactStore(),
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=FakeHistoricalForcingStore(),
            adapter=FakeWeatherForecastSource(result={}),
            models={},
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_attempted == 0
        assert result.stations_succeeded == 0

    def test_pooled_combination_stores_individual_and_combined(self) -> None:
        sid = StationId(uuid4())
        model_id_a = ModelId("fake_model_a")
        model_id_b = ModelId("fake_model_b")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        # Register the station with the first model (sets up all base data)
        _build_station_and_stores(
            sid,
            model_id_a,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        # Add a second model assignment (lower priority)
        assignment_b = ModelAssignment(
            station_id=sid,
            model_id=model_id_b,
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=2,
            created_at=_NOW,
        )
        station_store.store_model_assignment(assignment_b)

        # Artifact for the second model
        artifact_store.store_artifact(
            model_id=model_id_b,
            artifact_bytes=b"fake_artifact_b",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )

        models = {model_id_a: _SmallFakeModel(), model_id_b: _SmallFakeModel()}

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models=models,  # type: ignore[arg-type]
            config=_make_config(
                forecast_combination_strategy=ModelCombinationStrategy.POOLED
            ),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1

        stored = list(forecast_store._forecasts.values())
        # Two individual model forecasts + one combined
        assert len(stored) >= 3

        combined = [f for f in stored if f.combination_strategy == "pooled"]
        assert len(combined) >= 1
        assert combined[0].station_id == sid

        individual_model_ids = {
            f.model_id for f in stored if f.combination_strategy is None
        }
        assert model_id_a in individual_model_ids
        assert model_id_b in individual_model_ids

    def test_backstop_unexpected_exception_in_lower_priority_does_not_darken_station(
        self,
    ) -> None:
        """Plan 150 T2 (MAJOR-2, flow-level regression): a station whose
        higher-priority assignment SUCCEEDS and whose lower-priority
        assignment raises an UNANTICIPATED exception (outside
        ``_run_single_model``'s guarded regions) must still persist the
        higher-priority success and must NOT take the station-failure path.
        Against pre-D3 code the exception escapes ``run_all_station_forecasts``
        into the outer ``except Exception`` at ``run_forecast_cycle.py:2285``,
        which darkens the WHOLE station (``stations_failed += 1``) and
        discards the already-recorded success — this test fails then.
        """
        sid = StationId(uuid4())
        model_id_a = ModelId("fake_model_a")
        model_id_b = ModelId("fake_model_b")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        inner_artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            model_id_a,
            station_store,
            obs_store,
            nwp_store,
            inner_artifact_store,
            forcing_store,
        )

        station_store.store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=model_id_b,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        inner_artifact_store.store_artifact(
            model_id=model_id_b,
            artifact_bytes=b"fake_artifact_b",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )
        artifact_store = _RaisingForModelArtifactStore(
            inner_artifact_store, raise_for=model_id_b
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,  # type: ignore[arg-type]
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={model_id_a: _SmallFakeModel(), model_id_b: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(
                forecast_combination_strategy=ModelCombinationStrategy.POOLED
            ),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1
        assert result.stations_failed == 0
        assert not any("produced zero forecasts" in err for err in result.errors)

        stored = list(forecast_store._forecasts.values())
        primary_forecasts = [f for f in stored if f.model_id == model_id_a]
        assert len(primary_forecasts) > 0

        # Proves assignment B was actually reached and its unanticipated
        # exception fired — closes the false-pass path where the flow could
        # stop after A's success without ever invoking B.
        assert artifact_store.raised_for_target is True

    def test_station_skipped_when_model_not_loaded(self) -> None:
        sid = StationId(uuid4())
        missing_model_id = ModelId("model_not_in_registry")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        # Station is registered with an assignment pointing to a model_id
        # that is absent from the models dict passed to run_forecast_cycle_flow.
        _build_station_and_stores(
            sid,
            missing_model_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={},  # deliberately empty — missing_model_id not present
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        # Station attempted but skipped — no forecast produced
        assert result.stations_failed >= 1
        assert result.stations_succeeded == 0
        assert result.forecasts_stored == 0
        assert len(forecast_store._forecasts) == 0

    def test_alerts_checked_when_enabled(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        config = _make_config(enable_forecast_alerts=True)
        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=config,
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 1
        assert result.alerts_checked is True

    def test_gridded_nwp_happy_path(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_store = FakeNwpGridStore()
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))
        config = _make_config(nwp_grid_archive_base_path="/tmp/test_grids")

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=config,
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=grid_store,
            grid_extractor=grid_extractor,
        )

        assert grid_store.archive_count == 1
        assert grid_extractor.call_count == 1
        assert len(grid_extractor.last_configs) == 1
        assert grid_extractor.last_configs[0].nwp_source == _NWP_SOURCE
        assert nwp_store.record_count() > 0
        assert result.stations_succeeded >= 1

    def test_gridded_nwp_no_grid_extractor(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=None,
        )

        # Missing grid_extractor is a no-op NWP phase, NOT a flow-fatal abort.
        # The flow must proceed to per-station forecasting even when the NWP
        # extraction phase performs no work (v0 models may consume zero NWP
        # features). "NWP fetch failed" is reserved for true failures.
        assert "NWP fetch failed" not in result.errors
        assert result.stations_attempted >= 1

    def test_gridded_nwp_extraction_error(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_extractor = FakeGridExtractor(exception=ExtractionError("test"))

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=grid_extractor,
        )

        assert "NWP fetch failed" in result.errors

    def test_gridded_nwp_archive_failure_non_fatal(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_store = FakeNwpGridStore(exception=StoreError("archive broken"))
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))
        config = _make_config(nwp_grid_archive_base_path="/tmp/test_grids")

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=config,
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=grid_store,
            grid_extractor=grid_extractor,
        )

        assert grid_extractor.call_count == 1
        assert nwp_store.record_count() > 0
        assert result.stations_succeeded >= 1

    def test_gridded_nwp_point_path_unchanged(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
            )

        adapter = FakeWeatherForecastSource(result={})

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.stations_succeeded == 2

    def test_gridded_nwp_elevation_band_skipped(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        elev_df = pl.DataFrame(
            {
                "valid_time": [_NOW],
                "parameter": ["precipitation"],
                "member_id": [0],
                "value": [5.0],
            }
        )
        elev_result: dict[StationId, ElevationBandForecast] = {
            sid: ElevationBandForecast(
                nwp_source=_NWP_SOURCE,
                cycle_time=_NOW,
                values=elev_df,
            )
        }
        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_extractor = FakeGridExtractor(result=elev_result)  # type: ignore[arg-type]

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=grid_extractor,
        )

        assert nwp_store.record_count() == 0
        # Task returned cycle_time (not None) — Phase B ran (no early abort)
        assert "NWP fetch failed" not in result.errors

    def test_gridded_nwp_source_filtering(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        # Add a second weather source with a different nwp_source
        other_source = StationWeatherSource(
            station_id=sid,
            nwp_source="other_source",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
        station_store.store_weather_source(other_source)

        adapter = FakeWeatherForecastSource(
            result=_make_gridded_forecast(nwp_source=_NWP_SOURCE)
        )
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=grid_extractor,
        )

        assert len(grid_extractor.last_configs) == 1
        assert grid_extractor.last_configs[0].nwp_source == _NWP_SOURCE
        assert result.stations_succeeded >= 1

    def test_gridded_nwp_archive_skipped_when_no_path(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_store = FakeNwpGridStore()
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),  # no nwp_grid_archive_base_path
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=grid_store,
            grid_extractor=grid_extractor,
        )

        assert grid_store.archive_count == 0
        assert nwp_store.record_count() > 0
        assert result.stations_succeeded >= 1

    def test_gridded_nwp_no_matching_sources(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        # Station has weather source with "other_source", not icon_ch2_eps
        basin_id = BasinId(uuid4())
        basin_id = BasinId(uuid4())
        basin_store.store_basin(
            Basin(
                id=basin_id,
                code=f"basin_{basin_id}",
                name="Test Basin",
                geometry=None,
                area_km2=100.0,
                attributes=None,
                band_geometries=None,
                created_at=_NOW,
                network="test",
            )
        )

        station = make_station_config(
            station_id=sid,
            station_kind=StationKind.RIVER,
            station_status=StationStatus.OPERATIONAL,
            measured_parameters=frozenset({"discharge"}),
            forecast_targets=frozenset({"discharge"}),
            basin_id=basin_id,
        )
        station_store.store_station(station)

        assignment = ModelAssignment(
            station_id=sid,
            model_id=_MODEL_ID,
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_NOW,
        )
        station_store.store_model_assignment(assignment)

        # Weather source with different nwp_source than the grid
        other_source = StationWeatherSource(
            station_id=sid,
            nwp_source="other_source",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
        station_store.store_weather_source(other_source)

        obs_start = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() - 30 * 3600, tz=UTC)
        )
        obs_store = FakeObservationStore()
        observations = make_observations(
            n=30,
            station_id=sid,
            parameter="discharge",
            start=obs_start,
            interval=timedelta(hours=1),
        )
        obs_store.store_observations(observations)

        artifact_store.store_artifact(
            model_id=_MODEL_ID,
            artifact_bytes=b"fake_artifact",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )

        # GriddedForecast has icon_ch2_eps but station has other_source — no match
        adapter = FakeWeatherForecastSource(
            result=_make_gridded_forecast(nwp_source=_NWP_SOURCE)
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=FakeForecastStore(),
            model_state_store=FakeModelStateStore(),
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=FakeGridExtractor(result={}),
        )

        # No station requested this grid's NWP source (no_matching_sources)
        # is a no-op NWP phase, NOT a flow-fatal abort. The flow must proceed
        # to per-station forecasting. "NWP fetch failed" is reserved for true
        # failures (adapter raise, extraction raise, store raise).
        assert "NWP fetch failed" not in result.errors
        assert result.stations_attempted >= 1

    def test_grid_components_skipped_when_archive_path_none(self) -> None:
        from unittest.mock import patch

        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        adapter = FakeWeatherForecastSource(result={})

        boom = RuntimeError(
            "grid component must not be constructed when archive path is None"
        )
        with (
            patch(
                "sapphire_flow.store.zarr_nwp_grid_store.ZarrNwpGridStore",
                side_effect=boom,
            ),
            patch(
                "sapphire_flow.preprocessing.exact_extract_grid_extractor.ExactExtractGridExtractor",
                side_effect=boom,
            ),
        ):
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                adapter=adapter,
                models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert isinstance(result, ForecastCycleResult)


# =========================================================================== #
# epic-088 M3: operational ICON forcing path
#
#   B. deterministic NWP-source selection (icon_ch2_eps / BASIN_AVERAGE), incl.
#      the hyphen -> underscore fallback fix.
#   C. extraction filter (configs_for_source) runs only for the ICON source.
#   F. end-to-end "forecasts use weather": a 21-member ensemble whose discharge
#      rises when the precipitation forcing rises.
# =========================================================================== #


def _make_m3_stores() -> tuple:
    return (
        FakeStationStore(),
        FakeObservationStore(),
        FakeWeatherForecastStore(),
        FakeModelArtifactStore(),
        FakeForecastStore(),
        FakeModelStateStore(),
        FakeAlertStore(),
        FakeClimBaselineStore(),
        FakeBasinStore(),
        FakeHistoricalForcingStore(),
    )


def _run_m3_cycle(stores: tuple, models: dict) -> ForecastCycleResult:
    (
        station_store,
        obs_store,
        nwp_store,
        artifact_store,
        forecast_store,
        state_store,
        alert_store,
        baseline_store,
        basin_store,
        forcing_store,
    ) = stores
    return run_forecast_cycle_flow(
        station_store=station_store,
        obs_store=obs_store,
        weather_forecast_store=nwp_store,
        forecast_store=forecast_store,
        model_state_store=state_store,
        artifact_store=artifact_store,
        alert_store=alert_store,
        baseline_store=baseline_store,
        basin_store=basin_store,
        forcing_store=forcing_store,
        adapter=FakeWeatherForecastSource(result={}),
        models=models,  # type: ignore[arg-type]
        config=_make_config(),
        qc_rules=_empty_qc_rules(),
        clock=_clock,
        rng=random.Random(42),
    )


class TestNwpGridRetentionPrune:
    """Plan 095: the flow body prunes old grid-cube zarrs after a successful NWP
    fetch, using the configured retention window + archive base path."""

    def _run(
        self,
        *,
        monkeypatch: pytest.MonkeyPatch,
        nwp_grid_archive_base_path: str | None,
        nwp_grid_retention_days: int = 3,
    ) -> list[tuple[object, ...]]:
        calls: list[tuple[object, ...]] = []

        def _spy(base_path: object, retention_days: object, clock: object) -> None:
            calls.append((base_path, retention_days, clock))

        monkeypatch.setattr(
            "sapphire_flow.store.zarr_nwp_grid_store.prune_old_cycles", _spy
        )

        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_store = FakeNwpGridStore()
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))
        config = _make_config(
            nwp_grid_archive_base_path=nwp_grid_archive_base_path,
            nwp_grid_retention_days=nwp_grid_retention_days,
        )

        run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=config,
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=grid_store,
            grid_extractor=grid_extractor,
        )
        return calls

    def test_prune_invoked_with_configured_retention_and_base_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._run(
            monkeypatch=monkeypatch,
            nwp_grid_archive_base_path="/tmp/test_grids",
            nwp_grid_retention_days=5,
        )
        assert len(calls) == 1
        base_path, retention_days, clock = calls[0]
        assert str(base_path) == "/tmp/test_grids"
        assert retention_days == 5
        assert clock is _clock

    def test_prune_not_invoked_when_base_path_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._run(monkeypatch=monkeypatch, nwp_grid_archive_base_path=None)
        assert calls == []


class TestNwpExtractionSourceFilter:
    """C. Grid extraction runs only for weather sources whose nwp_source matches
    the grid — i.e. the ICON / BASIN_AVERAGE binding onboarding must create.
    """

    def test_icon_basin_average_source_runs_extraction_and_stores(self) -> None:
        sid = StationId(uuid4())
        extractor = FakeGridExtractor(
            result=_make_basin_avg_result([sid], n_steps=5, n_members=3)
        )
        nwp_store = FakeWeatherForecastStore()
        source = StationWeatherSource(
            station_id=sid,
            nwp_source=_NWP_SOURCE,  # icon_ch2_eps, matches the grid
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.FORECAST,
        )

        out = _fetch_nwp_task.fn(
            adapter=FakeWeatherForecastSource(result=_make_gridded_forecast()),
            station_configs=[source],
            cycle_time=_NOW,
            weather_forecast_store=nwp_store,
            clock=_clock,
            grid_store=None,
            grid_extractor=extractor,
            station_basins={},
            grid_archive_base_path=None,
        )

        assert out is not None and out.cycle_time == _NOW
        assert extractor.call_count == 1
        assert extractor.last_configs == [source]  # filter kept the ICON source
        assert len(nwp_store._records) > 0  # extracted records persisted

    def test_non_icon_source_is_filtered_out_and_extraction_skipped(self) -> None:
        sid = StationId(uuid4())
        extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))
        nwp_store = FakeWeatherForecastStore()
        # Only a camels-ch source: it does NOT match the icon_ch2_eps grid, so the
        # configs_for_source filter is empty -> no_matching_sources (no extraction).
        source = StationWeatherSource(
            station_id=sid,
            nwp_source="camels-ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )

        out = _fetch_nwp_task.fn(
            adapter=FakeWeatherForecastSource(result=_make_gridded_forecast()),
            station_configs=[source],
            cycle_time=_NOW,
            weather_forecast_store=nwp_store,
            clock=_clock,
            grid_store=None,
            grid_extractor=extractor,
            station_basins={},
            grid_archive_base_path=None,
        )

        # a skipped extraction is still a successful NWP no-op
        assert out is not None and out.cycle_time == _NOW
        assert extractor.call_count == 0
        assert nwp_store._records == []

    def test_reanalysis_role_with_matching_nwp_source_name_is_still_filtered_out(
        self,
    ) -> None:
        """Plan 115a: configs_for_source filters by role == FORECAST AND a
        matching nwp_source -- a name match alone is not enough. A REANALYSIS
        binding whose nwp_source happens to equal the grid's nwp_source (e.g.
        a Nepal reanalysis product sharing a name with the forecast product)
        must never be handed to the forecast extractor."""
        sid = StationId(uuid4())
        extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))
        nwp_store = FakeWeatherForecastStore()
        source = StationWeatherSource(
            station_id=sid,
            nwp_source=_NWP_SOURCE,  # name matches the grid...
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,  # ...but the role does not.
        )

        out = _fetch_nwp_task.fn(
            adapter=FakeWeatherForecastSource(result=_make_gridded_forecast()),
            station_configs=[source],
            cycle_time=_NOW,
            weather_forecast_store=nwp_store,
            clock=_clock,
            grid_store=None,
            grid_extractor=extractor,
            station_basins={},
            grid_archive_base_path=None,
        )

        assert out is not None and out.cycle_time == _NOW
        assert extractor.call_count == 0
        assert nwp_store._records == []

    def test_pre_extracted_dict_outcome_uses_forecast_cycle_not_request(self) -> None:
        # A pre-extracted (dict) adapter that snapped / fell back to an older
        # published cycle: records are persisted under each forecast's OWN
        # cycle_time, so the outcome must report THAT resolved cycle — not the
        # nominal request — or Phase B's readback + provenance mismatch and the
        # forecast is skipped / mis-recorded against the request cycle.
        sid = StationId(uuid4())
        resolved = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() - 6 * 3600, tz=UTC)
        )
        nwp_store = FakeWeatherForecastStore()
        source = StationWeatherSource(
            station_id=sid,
            nwp_source=_NWP_SOURCE,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.FORECAST,
        )
        pre_extracted = _make_basin_avg_result(
            [sid], n_steps=5, n_members=3, cycle_time=resolved
        )

        out = _fetch_nwp_task.fn(
            adapter=FakeWeatherForecastSource(result=pre_extracted),
            station_configs=[source],
            cycle_time=_NOW,
            weather_forecast_store=nwp_store,
            clock=_clock,
            grid_store=None,
            grid_extractor=FakeGridExtractor(result={}),
            station_basins={},
            grid_archive_base_path=None,
        )

        # resolved cycle comes from the forecasts, NOT the nominal request (_NOW)
        assert out is not None and out.cycle_time == resolved
        assert len(nwp_store._records) > 0


class TestPreExtractedDictFallbackProvenance:
    """Codex round 2 (new MAJOR): the dict-return (Recap) path must record
    ``fallback_used=True`` when the adapter walked back to an OLDER published
    cycle. Pre-fix it returned ``fallback_used=False`` unconditionally, so a
    fallback forecast was mis-recorded as ``NwpCycleSource.PRIMARY``, hiding
    that fallback data was used."""

    def _run(self, resolved_cycle: UtcDatetime) -> _NwpFetchOutcome | None:
        sid = StationId(uuid4())
        nwp_store = FakeWeatherForecastStore()
        source = StationWeatherSource(
            station_id=sid,
            nwp_source=_NWP_SOURCE,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.FORECAST,
        )
        pre_extracted = _make_basin_avg_result([sid], cycle_time=resolved_cycle)
        return _fetch_nwp_task.fn(
            adapter=FakeWeatherForecastSource(result=pre_extracted),
            station_configs=[source],
            cycle_time=_NOW,  # nominal request, on a 6h cadence boundary
            weather_forecast_store=nwp_store,
            clock=_clock,
            grid_store=None,
            grid_extractor=FakeGridExtractor(result={}),
            station_basins={},
            grid_archive_base_path=None,
        )

    def test_older_resolved_cycle_marks_fallback(self) -> None:
        # Nominal cycle (_NOW) unpublished; the adapter returned data at the
        # previous 6h IFS cycle. That is a FALLBACK.
        resolved = ensure_utc(_NOW - timedelta(hours=6))
        out = self._run(resolved)
        assert out is not None
        assert out.cycle_time == resolved
        assert out.fallback_used is True

    def test_nominal_resolved_cycle_marks_primary(self) -> None:
        # Negative control: the adapter returned data at the nominal cycle ->
        # PRIMARY, not fallback.
        out = self._run(_NOW)
        assert out is not None
        assert out.cycle_time == _NOW
        assert out.fallback_used is False


class TestDictPathFallbackFullFlowProvenance:
    """Codex round 2 (new MAJOR), end-to-end: a dict-return adapter whose
    forecast carries an older resolved cycle must yield a STORED forecast with
    ``nwp_cycle_source=FALLBACK`` and ``nwp_cycle_reference_time`` = the older
    resolved cycle — not PRIMARY @ nominal."""

    def _stores(self, sid: StationId) -> tuple[object, ...]:
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )
        return (
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forecast_store,
            state_store,
            alert_store,
            baseline_store,
            basin_store,
            forcing_store,
        )

    def test_dict_fallback_forecast_records_fallback_source(self) -> None:
        sid = StationId(uuid4())
        (
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forecast_store,
            state_store,
            alert_store,
            baseline_store,
            basin_store,
            forcing_store,
        ) = self._stores(sid)

        resolved_cycle = ensure_utc(_NOW - timedelta(hours=6))
        # A pre-extracted dict result (as RecapGatewayForecastAdapter returns)
        # whose forecast cycle_time is the OLDER published cycle.
        dict_result = _make_basin_avg_result([sid], cycle_time=resolved_cycle)
        adapter = FakeWeatherForecastSource(result=dict_result)

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=FakeGridExtractor(result={}),
        )

        assert result.forecasts_stored >= 1
        stored = list(forecast_store._forecasts.values())
        assert stored, "expected a stored forecast; station was skipped"
        assert all(fc.nwp_cycle_source == NwpCycleSource.FALLBACK for fc in stored)
        assert all(fc.nwp_cycle_reference_time == resolved_cycle for fc in stored)


class TestDeterministicNwpSourceSelection:
    """B. Phase B must select the FORECAST-role binding explicitly, via
    ``StationStore.fetch_forecast_binding`` — never by fetch order, by a
    same-name heuristic, or by a hardcoded fallback string. Plan 115a retired
    the ``_select_nwp_source`` heuristic (exact-ICON pass, first-BASIN_AVERAGE
    pass, ``icon_ch2_eps`` fallback) entirely.
    """

    def test_prefers_icon_source_over_camels_when_both_present(self) -> None:
        sid = StationId(uuid4())
        stores = _make_m3_stores()
        station_store = stores[0]
        obs_store = stores[1]
        nwp_store = stores[2]
        artifact_store = stores[3]
        forcing_store = stores[9]

        # camels-ch stored FIRST => weather_sources[0] under the old code. Its NWP
        # source has NO stored records, so the old "pick the first source" logic
        # reads the wrong source and skips the station (forecasts_stored == 0).
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="camels-ch",
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        # Appends the icon_ch2_eps source + seeds icon NWP records for readback.
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = _run_m3_cycle(stores, {_MODEL_ID: _SmallFakeModel()})

        # Only reachable if icon_ch2_eps (BASIN_AVERAGE) was selected deterministically.
        assert result.forecasts_stored == 1

    def test_zero_weather_sources_is_loudly_skipped_not_defaulted(self) -> None:
        """Locks the ONE accepted behaviour change from Plan 115a (umbrella §5):
        a station with zero weather-source bindings used to silently forecast
        via the hardcoded ``icon_ch2_eps`` fallback string. That heuristic is
        retired — ``fetch_forecast_binding`` now raises ``ConfigurationError``
        (0 matches), and the flow must contain it: record the station as
        failed exactly once, with an error message, and keep running the
        cycle for everyone else (the function-level ``try`` has no ``except``,
        only a ``finally``, so an uncontained raise here would abort the
        entire cycle)."""
        sid = StationId(uuid4())
        stores = _make_m3_stores()
        station_store = stores[0]
        obs_store = stores[1]
        nwp_store = stores[2]
        artifact_store = stores[3]
        forcing_store = stores[9]

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )
        # Remove ALL weather sources so fetch_forecast_binding has 0 matches.
        station_store._weather_sources.clear()

        result = _run_m3_cycle(stores, {_MODEL_ID: _SmallFakeModel()})

        assert result.stations_failed == 1
        assert result.forecasts_stored == 0
        assert any("weather-source config" in e for e in result.errors)

    def test_two_basin_average_bindings_route_forecast_by_role(self) -> None:
        """The Nepal shape on Swiss infrastructure: a station with TWO
        BASIN_AVERAGE bindings — one FORECAST, one REANALYSIS — routes the
        forecast path to the FORECAST one, selected by role, never by name,
        fetch order, or an ICON-name heuristic."""
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="camels-ch",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="icon_ch2_eps",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.FORECAST,
            )
        )

        binding = station_store.fetch_forecast_binding(sid)

        assert binding.nwp_source == "icon_ch2_eps"
        assert binding.role == WeatherSourceRole.FORECAST

    def test_inactive_forecast_binding_is_still_selected(self) -> None:
        """Locks the deliberate no-status-filter decision (Plan 115a §5): an
        INACTIVE binding is still selected today (nothing filters on status),
        and this plan adds no such filter — that is its own, separate plan.
        This must not silently drift in later."""
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="icon_ch2_eps",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.INACTIVE,
                role=WeatherSourceRole.FORECAST,
            )
        )

        binding = station_store.fetch_forecast_binding(sid)

        assert binding.nwp_source == "icon_ch2_eps"
        assert binding.status == WeatherSourceStatus.INACTIVE


class TestForecastBindingContainment:
    """Plan 115a §5: forecast-binding resolution happens ONCE, up front, for
    every operational station, before Phase A (the shared NWP prefetch, which
    runs BEFORE the per-station loop). A single station with a broken binding
    must not abort the whole cycle — the flow-level ``try`` has NO ``except``,
    only a ``finally``, so an uncontained ``ConfigurationError`` anywhere in
    this path aborts the cycle for every station and every group.
    """

    def test_one_broken_binding_does_not_abort_other_stations(self) -> None:
        """Soundness: this must fail against an implementation that lets the
        ConfigurationError from fetch_forecast_binding escape uncaught (the
        whole call would raise instead of returning a result with the good
        stations still forecast)."""
        good_sid_1 = StationId(uuid4())
        good_sid_2 = StationId(uuid4())
        bad_sid = StationId(uuid4())
        stores = _make_m3_stores()
        station_store = stores[0]
        obs_store = stores[1]
        nwp_store = stores[2]
        artifact_store = stores[3]
        forcing_store = stores[9]

        for sid in (good_sid_1, good_sid_2, bad_sid):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
            )
        # Give bad_sid a SECOND FORECAST binding (different nwp_source, so the
        # upsert doesn't conflict) -> 2 FORECAST matches -> fetch_forecast_binding
        # raises ConfigurationError instead of picking one.
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=bad_sid,
                nwp_source="icon_ch2_eps_v2",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.FORECAST,
            )
        )

        result = _run_m3_cycle(stores, {_MODEL_ID: _SmallFakeModel()})

        assert result.stations_failed == 1
        assert result.stations_succeeded == 2
        assert result.forecasts_stored == 2
        matching_errors = [e for e in result.errors if str(bad_sid) in e]
        assert len(matching_errors) == 1
        assert result.health != ForecastCycleHealth.FAILED

    def test_broken_binding_excluded_from_phase_a_prefetch(self) -> None:
        """Soundness: this must fail against an implementation that contains
        the raise only inside the per-station loop (:1498), since the shared
        NWP prefetch (:1242-1300) runs first and consumes flat_weather_configs
        — the bad station's binding must never reach it, or it would poison
        the shared fetch for every other station."""
        good_sid = StationId(uuid4())
        bad_sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (good_sid, bad_sid):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                basin_store=basin_store,
            )
        # Break bad_sid's binding the same way: a second FORECAST binding.
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=bad_sid,
                nwp_source="icon_ch2_eps_v2",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.FORECAST,
            )
        )

        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([good_sid]))

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=None,
            grid_extractor=grid_extractor,
        )

        # Phase A still ran for the good station -- extraction was not aborted.
        assert grid_extractor.call_count == 1
        extracted_station_ids = {c.station_id for c in grid_extractor.last_configs}
        # Only the good station's binding reached the shared prefetch; the bad
        # station's binding was excluded before Phase A, not merely skipped
        # later in the per-station loop.
        assert extracted_station_ids == {good_sid}
        assert result.stations_failed == 1
        assert result.stations_succeeded == 1

    def test_all_stations_broken_binding_reports_failures_not_fatal_abort(
        self,
    ) -> None:
        """Plan 115a §5 / adversarial-review blocker: when EVERY operational
        station fails forecast-binding resolution, ``flat_weather_configs`` is
        empty. Phase A must NOT be submitted in that case -- submitting it
        against an empty station list reaches an adapter (e.g.
        ``ReplayNwpAdapter``) that raises on empty ``station_configs``,
        ``_fetch_nwp_task`` converts that to ``None``, and the flow used to
        take the fatal-abort return path, ERASING the per-station failure
        accounting already recorded (stations_failed / errors /
        failed_station_ids) in favour of a bogus 0/0 result.

        Soundness: the adapter below raises ``AdapterError`` if it is EVER
        called with an empty ``station_configs`` list -- exactly like
        ``ReplayNwpAdapter.fetch_forecasts``. Against the pre-fix code this
        test fails (the flow surfaces a fatal 0/0 result, or the
        AdapterError escapes). Against the fixed code, Phase A is skipped
        entirely (adapter never called) and every operational station is
        accounted for as failed.
        """
        bad_sid_1 = StationId(uuid4())
        bad_sid_2 = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        for sid in (bad_sid_1, bad_sid_2):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                basin_store=basin_store,
            )
            # Break every station's binding the same way as
            # TestForecastBindingContainment: a second FORECAST binding ->
            # fetch_forecast_binding raises ConfigurationError (0 or 2+
            # matches are both "broken"; 2+ is used here).
            station_store.store_weather_source(
                StationWeatherSource(
                    station_id=sid,
                    nwp_source="icon_ch2_eps_v2",
                    extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                    status=WeatherSourceStatus.ACTIVE,
                    role=WeatherSourceRole.FORECAST,
                )
            )

        class _RaisesOnEmptyAdapter:
            call_count = 0

            def fetch_forecasts(
                self, station_configs: list[object], cycle_time: object
            ) -> object:
                _RaisesOnEmptyAdapter.call_count += 1
                if not station_configs:
                    raise AdapterError("station_configs is empty")
                raise AssertionError(
                    "adapter must not be called with non-empty configs in this test"
                )

        adapter = _RaisesOnEmptyAdapter()

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,  # type: ignore[arg-type]
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=None,
        )

        # Phase A must not have been submitted at all -- the adapter is never
        # reached when every operational station's binding is broken.
        assert _RaisesOnEmptyAdapter.call_count == 0
        assert isinstance(result, ForecastCycleResult)
        assert result.stations_attempted == 2
        assert result.stations_failed == 2
        assert result.stations_succeeded == 0
        assert result.forecasts_stored == 0
        matching_errors_1 = [e for e in result.errors if str(bad_sid_1) in e]
        matching_errors_2 = [e for e in result.errors if str(bad_sid_2) in e]
        assert len(matching_errors_1) == 1
        assert len(matching_errors_2) == 1


class _MonotonicEnsembleModel(FakeStationForecastModel):
    """Ensemble model (fanned out over member-suffixed forcing) whose discharge is
    a strictly increasing function of the precipitation input — the minimal fake
    that proves "the forecast uses weather" end-to-end.
    """

    from sapphire_flow.types.model import ModelDataRequirements

    alert_eligibility = AlertEligibility.SKILL_FORECAST
    data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=20,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.ENSEMBLE,
    )

    def predict(self, artifact, inputs, rng, prior_state=None):  # type: ignore[no-untyped-def]
        fd = inputs.data.future_dynamic.sort("timestamp")
        rows = [
            {"valid_time": vt, "member_id": 0, "value": 100.0 + 5.0 * float(p)}
            for vt, p in zip(
                fd["timestamp"].to_list(),
                fd["precipitation"].to_list(),
                strict=True,
            )
        ]
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ens = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m³/s",
            time_step=inputs.time_step,
            values=df,
        )
        return ({"discharge": ens}, None)  # stateless -> fan-out safe


def _make_ensemble_nwp_records(
    station_id: StationId,
    precip_by_member: dict[int, float],
    *,
    n_steps: int = 5,
) -> list[WeatherForecastRecord]:
    records: list[WeatherForecastRecord] = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for member, precip in precip_by_member.items():
            records.append(
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=station_id,
                    nwp_source=_NWP_SOURCE,
                    cycle_time=_NOW,
                    valid_time=vt,
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.POINT,
                    band_id=None,
                    member_id=member,
                    value=float(precip),
                    created_at=_NOW,
                )
            )
            records.append(
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=station_id,
                    nwp_source=_NWP_SOURCE,
                    cycle_time=_NOW,
                    valid_time=vt,
                    parameter="temperature",
                    spatial_type=SpatialRepresentation.POINT,
                    band_id=None,
                    member_id=member,
                    value=10.0,
                    created_at=_NOW,
                )
            )
    return records


class TestForecastsUseWeatherEndToEnd:
    def _run_with_precip(
        self, precip_by_member: dict[int, float]
    ) -> tuple[FakeForecastStore, ForecastCycleResult]:
        sid = StationId(uuid4())
        stores = _make_m3_stores()
        station_store = stores[0]
        obs_store = stores[1]
        nwp_store = stores[2]
        artifact_store = stores[3]
        forecast_store = stores[4]
        forcing_store = stores[9]

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            seed_nwp=False,
        )
        nwp_store.store_weather_forecasts(
            _make_ensemble_nwp_records(sid, precip_by_member)
        )

        result = _run_m3_cycle(stores, {_MODEL_ID: _MonotonicEnsembleModel()})
        return forecast_store, result

    def test_21_member_forecast_rises_with_precipitation(self) -> None:
        baseline = {m: 2.0 + float(m) for m in range(21)}
        raised = {m: 2.0 + float(m) + 30.0 for m in range(21)}  # +30 mm every member

        store_lo, res_lo = self._run_with_precip(baseline)
        store_hi, res_hi = self._run_with_precip(raised)

        assert res_lo.forecasts_stored == 1
        assert res_hi.forecasts_stored == 1

        fc_lo = next(iter(store_lo._forecasts.values()))
        fc_hi = next(iter(store_hi._forecasts.values()))

        # 21-member ensemble carrying the ICON member ids 0..20.
        assert fc_lo.ensemble.member_count == 21
        assert fc_hi.ensemble.member_count == 21
        assert set(fc_lo.ensemble.values["member_id"].to_list()) == set(range(21))

        lo_vals = fc_lo.ensemble.values["value"].to_list()
        hi_vals = fc_hi.ensemble.values["value"].to_list()
        # Raising precip lifts EVERY discharge member: the forecast uses weather.
        assert min(hi_vals) > max(lo_vals)


class TestForecastProvenance:
    """epic-088 M4: the cycle records honest NWP provenance on each forecast.

    Runoff-only → RUNOFF_ONLY + null reference time (NOT PRIMARY + a faked
    time). NWP-on with a fresh primary cycle → PRIMARY + the resolved cycle.
    A fallback cycle (adapter walked back >=1 step) → FALLBACK.
    """

    def test_runoff_only_records_runoff_only_source_and_null_reference(self) -> None:
        # RED on main: the runoff branch hardcodes nwp_cycle_source=PRIMARY and
        # sets nwp_cycle_reference_time to the resolved (faked) clock cycle.
        sid = StationId(uuid4())
        native_id = ModelId("native_runoff_model")

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            native_id,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        # No adapter + absent config => runoff-only mode.
        with patch(
            "sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter",
            side_effect=AssertionError("adapter must not be constructed"),
        ):
            result = run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=state_store,
                artifact_store=artifact_store,
                alert_store=alert_store,
                baseline_store=baseline_store,
                basin_store=basin_store,
                forcing_store=forcing_store,
                models={native_id: _NativeFakeModel()},  # type: ignore[dict-item]
                config=_make_config(),
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
            )

        assert result.forecasts_stored == 1
        stored = list(forecast_store._forecasts.values())
        assert len(stored) == 1
        fc = stored[0]
        assert fc.nwp_cycle_source == NwpCycleSource.RUNOFF_ONLY
        assert fc.nwp_cycle_reference_time is None

    def test_nwp_primary_records_primary_source_and_resolved_cycle(self) -> None:
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        # Fresh primary cycle: default fallback_used=False on the gridded result.
        adapter = FakeWeatherForecastSource(result=_make_gridded_forecast())
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=grid_extractor,
        )

        assert result.forecasts_stored >= 1
        stored = list(forecast_store._forecasts.values())
        assert all(fc.nwp_cycle_source == NwpCycleSource.PRIMARY for fc in stored)
        assert all(fc.nwp_cycle_reference_time == _NOW for fc in stored)

    def test_fallback_cycle_records_fallback_source(self) -> None:
        # RED on main: (1) GriddedForecast has no fallback_used field, and
        # (2) the flow hardcodes PRIMARY regardless of the adapter's fallback.
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        base = _make_gridded_forecast()
        fallback_grid = GriddedForecast(
            nwp_source=base.nwp_source,
            cycle_time=base.cycle_time,
            values=base.values,
            fallback_used=True,
        )
        adapter = FakeWeatherForecastSource(result=fallback_grid)
        grid_extractor = FakeGridExtractor(result=_make_basin_avg_result([sid]))

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=grid_extractor,
        )

        assert result.forecasts_stored >= 1
        stored = list(forecast_store._forecasts.values())
        assert all(fc.nwp_cycle_source == NwpCycleSource.FALLBACK for fc in stored)
        assert all(fc.nwp_cycle_reference_time is not None for fc in stored)

    def test_fallback_cycle_reports_resolved_cycle_not_request(self) -> None:
        # RED on the pre-fix code: the flow tags the stored NWP records + the
        # provenance with the NOMINAL request cycle (_NOW), so a FALLBACK
        # forecast records the WRONG (too-new) nwp_cycle_reference_time and
        # understates NWP age. Here the adapter resolves an OLDER published
        # cycle (request - 6h) than the request, and the cycle-reflecting
        # extractor stores records under whatever cycle the flow passes it, so
        # the assertions catch both a wrong reference time AND a readback skip.
        sid = StationId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        state_store = FakeModelStateStore()
        alert_store = FakeAlertStore()
        baseline_store = FakeClimBaselineStore()
        basin_store = FakeBasinStore()
        forcing_store = FakeHistoricalForcingStore()

        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            basin_store=basin_store,
        )

        resolved_cycle = ensure_utc(_NOW - timedelta(hours=6))
        # The adapter walked back 6h: the grid's own cycle_time is the OLDER
        # published cycle; the request (via _clock) is _NOW.
        fallback_grid = GriddedForecast(
            nwp_source=_NWP_SOURCE,
            cycle_time=resolved_cycle,
            values=_make_gridded_forecast(cycle_time=resolved_cycle).values,
            fallback_used=True,
        )
        adapter = FakeWeatherForecastSource(result=fallback_grid)
        grid_extractor = _CycleReflectingGridExtractor([sid])

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=state_store,
            artifact_store=artifact_store,
            alert_store=alert_store,
            baseline_store=baseline_store,
            basin_store=basin_store,
            forcing_store=forcing_store,
            adapter=adapter,
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(nwp_grid_archive_base_path="/tmp/test_grids"),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            grid_store=FakeNwpGridStore(),
            grid_extractor=grid_extractor,
        )

        # Records were extracted/stored under the RESOLVED cycle...
        assert grid_extractor.seen_cycle_times == [resolved_cycle]
        # ...and the forecast is PRODUCED (readback at the same resolved cycle
        # finds those records — no skip).
        assert result.forecasts_stored >= 1
        stored = list(forecast_store._forecasts.values())
        assert stored, "expected a stored forecast; station was skipped"
        assert all(fc.nwp_cycle_source == NwpCycleSource.FALLBACK for fc in stored)
        # Provenance reflects the TRUE resolved (older) cycle, not the request.
        assert all(fc.nwp_cycle_reference_time == resolved_cycle for fc in stored)


def _active_curve(station_id: StationId) -> RatingCurve:
    return RatingCurve(
        id=RatingCurveId(uuid4()),
        station_id=station_id,
        version=1,
        valid_from=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
        valid_to=None,
        points=[
            {"water_level": 1.0, "discharge": 10.0},
            {"water_level": 2.0, "discharge": 45.0},
        ],
        interpolation=InterpolationMethod.LINEAR,
        uploaded_by=None,
        created_at=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
    )


def _bare_forecast(station_id: StationId) -> OperationalForecast:
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=_MODEL_ID,
        model_artifact_id=None,
        issued_at=_NOW,
        nwp_cycle_reference_time=None,
        nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
        representation=EnsembleRepresentation.MEMBERS,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=make_forecast_ensemble(station_id=station_id, n_members=3, n_steps=4),
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestBindRatingCurveHelper:
    def test_feature_off_is_noop(self) -> None:
        sid = StationId(uuid4())
        fc = _bare_forecast(sid)
        bound = _bind_rating_curve(fc, None)
        assert bound is fc
        assert bound.rating_curve_id is None

    def test_binds_curve_and_logs_bound(self) -> None:
        import structlog.testing

        sid = StationId(uuid4())
        curve = _active_curve(sid)
        fc = _bare_forecast(sid)
        with structlog.testing.capture_logs() as captured:
            bound = _bind_rating_curve(fc, {sid: curve})
        assert bound.rating_curve_id == curve.id
        assert any(e["event"] == "rating_curve.bound" for e in captured)

    def test_absent_curve_is_noop_and_logs_skipped(self) -> None:
        import structlog.testing

        sid = StationId(uuid4())
        fc = _bare_forecast(sid)
        with structlog.testing.capture_logs() as captured:
            bound = _bind_rating_curve(fc, {})  # feature on, no curve for sid
        assert bound.rating_curve_id is None
        assert any(e["event"] == "rating_curve.bind_skipped" for e in captured)


class TestForecastCycleRatingCurveBinding:
    def test_primary_path_binds_curve(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        forcing_store = FakeHistoricalForcingStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )
        curve = _active_curve(sid)
        rating_curve_store = FakeRatingCurveStore()
        rating_curve_store.store_rating_curve(curve)

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=FakeModelStateStore(),
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            rating_curve_store=rating_curve_store,
        )

        assert result.forecasts_stored >= 1
        stored = list(forecast_store._forecasts.values())
        assert stored
        assert all(f.rating_curve_id == curve.id for f in stored)

    def test_no_op_when_store_absent(self) -> None:
        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        forcing_store = FakeHistoricalForcingStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=FakeModelStateStore(),
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
        )

        assert result.forecasts_stored >= 1
        stored = list(forecast_store._forecasts.values())
        assert stored
        assert all(f.rating_curve_id is None for f in stored)

    def test_combination_path_binds_individual_and_combined(self) -> None:
        sid = StationId(uuid4())
        model_id_a = ModelId("fake_model_a")
        model_id_b = ModelId("fake_model_b")
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        forcing_store = FakeHistoricalForcingStore()
        _build_station_and_stores(
            sid,
            model_id_a,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            forcing_store,
        )
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=model_id_b,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        artifact_store.store_artifact(
            model_id=model_id_b,
            artifact_bytes=b"fake_artifact_b",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )
        curve = _active_curve(sid)
        rating_curve_store = FakeRatingCurveStore()
        rating_curve_store.store_rating_curve(curve)

        run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=FakeModelStateStore(),
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={model_id_a: _SmallFakeModel(), model_id_b: _SmallFakeModel()},  # type: ignore[dict-item]
            config=_make_config(
                forecast_combination_strategy=ModelCombinationStrategy.POOLED
            ),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            rating_curve_store=rating_curve_store,
        )

        stored = list(forecast_store._forecasts.values())
        # Individual (sites 2) + combined (site 3) forecasts.
        assert len(stored) >= 3
        assert any(f.combination_strategy == "pooled" for f in stored)
        assert all(f.rating_curve_id == curve.id for f in stored)

    def test_group_path_binds_curve(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group_model_id = ModelId("fake_group_model")
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        forcing_store = FakeHistoricalForcingStore()
        group_store = FakeStationGroupStore()
        for sid in (sid_a, sid_b):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                station_store,
                obs_store,
                nwp_store,
                artifact_store,
                forcing_store,
                seed_model_assignment=False,
                seed_artifact=False,
            )
        _store_group_run(
            group_store,
            artifact_store,
            group_model_id,
            frozenset({sid_a, sid_b}),
        )
        curve_a = _active_curve(sid_a)
        curve_b = _active_curve(sid_b)
        rating_curve_store = FakeRatingCurveStore()
        rating_curve_store.store_rating_curve(curve_a)
        rating_curve_store.store_rating_curve(curve_b)

        result = run_forecast_cycle_flow(
            station_store=station_store,
            obs_store=obs_store,
            weather_forecast_store=nwp_store,
            forecast_store=forecast_store,
            model_state_store=FakeModelStateStore(),
            artifact_store=artifact_store,
            alert_store=FakeAlertStore(),
            baseline_store=FakeClimBaselineStore(),
            basin_store=FakeBasinStore(),
            group_store=group_store,
            forcing_store=forcing_store,
            adapter=FakeWeatherForecastSource(result={}),
            models={group_model_id: _SmallFakeGroupModel()},  # type: ignore[dict-item]
            config=_make_config(),
            qc_rules=_empty_qc_rules(),
            clock=_clock,
            rng=random.Random(42),
            rating_curve_store=rating_curve_store,
        )

        assert result.forecasts_stored == 2
        by_station = {f.station_id: f for f in forecast_store._forecasts.values()}
        assert by_station[sid_a].rating_curve_id == curve_a.id
        assert by_station[sid_b].rating_curve_id == curve_b.id


# ---------------------------------------------------------------------------
# Plan 151 T8a — DORMANT helper tests (D11, D28, D30). Each helper is
# defined but has ZERO production call sites from the cycle body in this
# run; tests here call the helper directly. T8b installs them.
# ---------------------------------------------------------------------------


def _flow_attr(name: str) -> object:
    """Guarded lookup for a Plan 151 T8a symbol that may not exist yet --
    fails as a genuine assertion (never a collection-time ImportError) so
    red-first failures are meaningful before the symbol is implemented."""
    import sapphire_flow.flows.run_forecast_cycle as _rfc_module

    attr = getattr(_rfc_module, name, None)
    assert attr is not None, (
        f"{name} not yet implemented in flows/run_forecast_cycle.py"
    )
    return attr


def _forcing_track_type_attr(name: str) -> object:
    from sapphire_flow.types import forcing_track as _ft_module

    attr = getattr(_ft_module, name, None)
    assert attr is not None, f"{name} not yet implemented in types/forcing_track.py"
    return attr


def _forcing_track_key() -> ForcingTrackKey:
    return ForcingTrackKey(
        nwp_source="ifs_ecmwf",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=timedelta(hours=6),
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset({FeatureName("tp")}),
    )


def _default_transient_exc(call_number: int) -> BaseException:
    return RecapTransientError(f"transient failure #{call_number}")


class _RetryCountingCandidateSource:
    """Fails `fail_times` calls with ``exc_factory(call_number)``, then
    succeeds. Defaults to `RecapTransientError` -- the ONLY exception D31
    marks retryable at this task; `retry_condition_fn` must not retry
    anything else (review fold-in, major)."""

    def __init__(
        self,
        fail_times: int,
        exc_factory: Callable[[int], BaseException] = _default_transient_exc,
    ) -> None:
        self.fail_times = fail_times
        self.exc_factory = exc_factory
        self.calls = 0

    def fetch_requirement(
        self,
        track: ForcingTrackKey,
        stations: list[object],
        nominal_cycle: UtcDatetime,
    ) -> RawFetchOutcome:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc_factory(self.calls)
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=nominal_cycle, stations={}
        )

    def expected_member_ids(self, track: ForcingTrackKey) -> frozenset[int]:
        return frozenset()


class TestFetchForcingCandidateTaskRetries:
    """Plan 151 T8a red-first (D28) + review fold-in (major, D31):
    retries= is a RUNTIME value taken from
    ``ForcingResolutionPolicy.max_retries`` via ``.with_options(retries=...)``
    — defined only, T8b installs the caller. ``retry_condition_fn`` scopes
    retries to `RecapTransientError` ONLY; every other typed Recap
    exception and any unanticipated bug must propagate on the FIRST
    failure, never retried, regardless of the configured `retries=` budget."""

    def test_resolves_at_exact_configured_retry_count(self) -> None:
        fetch_forcing_candidate_task = _flow_attr("fetch_forcing_candidate_task")

        source = _RetryCountingCandidateSource(fail_times=2)
        task_with_retries = fetch_forcing_candidate_task.with_options(retries=2)

        outcome = task_with_retries(source, _forcing_track_key(), [], _NOW)  # type: ignore[arg-type]

        assert outcome.status is RawFetchStatus.FETCHED
        assert source.calls == 3

    def test_fails_one_retry_short_of_the_configured_count(self) -> None:
        fetch_forcing_candidate_task = _flow_attr("fetch_forcing_candidate_task")

        source = _RetryCountingCandidateSource(fail_times=2)
        task_under_retries = fetch_forcing_candidate_task.with_options(retries=1)

        with pytest.raises(RecapTransientError, match="transient failure"):
            task_under_retries(source, _forcing_track_key(), [], _NOW)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "make_exc",
        [
            lambda: RecapAuthError("unauthorized", status_code=401),
            lambda: RecapConfigurationError("bad hru", field="hru_code"),
            lambda: RecapPayloadIntegrityError("corrupt payload"),
            lambda: RuntimeError("unanticipated bug"),
        ],
        ids=[
            "RecapAuthError",
            "RecapConfigurationError",
            "RecapPayloadIntegrityError",
            "unexpected-exception",
        ],
    )
    def test_fatal_and_unexpected_exceptions_are_never_retried(
        self, make_exc: Callable[[], BaseException]
    ) -> None:
        """Locks T8a review finding 2 (major): a `retry_condition_fn`-less
        task retries on ANY exception once `retries=` is configured. Even
        with a generous retry budget, a fatal typed Recap exception (or an
        unanticipated bug) must fail on the FIRST call -- D31's typed
        taxonomy reserves retry for `RecapTransientError` alone."""
        fetch_forcing_candidate_task = _flow_attr("fetch_forcing_candidate_task")

        exc = make_exc()
        source = _RetryCountingCandidateSource(
            fail_times=1, exc_factory=lambda _call_number: exc
        )
        task_with_retries = fetch_forcing_candidate_task.with_options(retries=3)

        with pytest.raises(type(exc)):
            task_with_retries(source, _forcing_track_key(), [], _NOW)  # type: ignore[arg-type]

        assert source.calls == 1


class TestEmitFreshnessOnFatalExit:
    """Plan 151 T8a red-first (Plan 116 contract): direct tests only, T8b
    installs this at every new fatal per-track-resolution exit."""

    def test_emits_one_forced_critical_record_and_reraises_unchanged(self) -> None:
        emit_freshness_on_fatal_exit = _flow_attr("emit_freshness_on_fatal_exit")

        health_store = FakePipelineHealthStore()
        cause = ValueError("root cause")
        exc = RuntimeError("fatal store failure")
        exc.__cause__ = cause

        with pytest.raises(RuntimeError) as exc_info:
            emit_freshness_on_fatal_exit(
                health_store,
                cycle_time_param=None,
                resolved_cycle_time=_NOW,
                forecasts_stored=3,
                checked_at=_NOW,
                exc=exc,
            )

        assert exc_info.value is exc
        assert str(exc_info.value) == "fatal store failure"
        assert exc_info.value.__cause__ is cause

        records = health_store.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL
        assert records[0].detail["forecasts_stored"] == 3
        assert records[0].cycle_time == _NOW

    def test_forced_critical_even_when_forecasts_already_stored(self) -> None:
        # A mid-cycle fatal crash is never "OK" merely because some
        # forecast(s) stored fine before the fatal one (Plan 116 fixer
        # round, major 1 -- the same rule the existing GROUP-store call
        # site already applies).
        emit_freshness_on_fatal_exit = _flow_attr("emit_freshness_on_fatal_exit")

        health_store = FakePipelineHealthStore()

        with pytest.raises(RuntimeError):
            emit_freshness_on_fatal_exit(
                health_store,
                cycle_time_param=None,
                resolved_cycle_time=_NOW,
                forecasts_stored=5,
                checked_at=_NOW,
                exc=RuntimeError("fatal"),
            )

        records = health_store.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
        assert records[0].status is PipelineHealthStatus.CRITICAL

    def test_cycle_time_param_set_emits_nothing(self) -> None:
        # Mirrors _emit_forecast_freshness_record's own backfill/replay
        # exemption -- still re-raises.
        emit_freshness_on_fatal_exit = _flow_attr("emit_freshness_on_fatal_exit")

        health_store = FakePipelineHealthStore()

        with pytest.raises(RuntimeError):
            emit_freshness_on_fatal_exit(
                health_store,
                cycle_time_param="2026-01-01T00",
                resolved_cycle_time=_NOW,
                forecasts_stored=0,
                checked_at=_NOW,
                exc=RuntimeError("fatal"),
            )

        assert health_store.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS) == []


class TestResolveCombinedForcingCycle:
    """Plan 151 T8a red-first (D11): the cross-cycle combination preflight
    -- a PURE helper, direct tests only, T8b installs it."""

    @staticmethod
    def _result(sid: StationId, *forecasts: OperationalForecast) -> object:
        from sapphire_flow.services.run_station_forecast import StationForecastResult

        return StationForecastResult(
            station_id=sid,
            model_id=_MODEL_ID,
            artifact_id=ArtifactId(uuid4()),
            forecasts=list(forecasts),
            new_state=None,
            ensembles={},
        )

    def test_two_distinct_non_null_cycles_report_mismatch(self) -> None:
        from dataclasses import replace

        cross_cycle_mismatch_cls = _flow_attr("CrossCycleMismatch")
        resolve_combined_forcing_cycle = _flow_attr("resolve_combined_forcing_cycle")

        sid = StationId(uuid4())
        cycle_a = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        cycle_b = ensure_utc(datetime(2026, 1, 1, 6, tzinfo=UTC))
        combinable = {
            ModelId("model_a"): self._result(
                sid, replace(_bare_forecast(sid), nwp_cycle_reference_time=cycle_a)
            ),
            ModelId("model_b"): self._result(
                sid, replace(_bare_forecast(sid), nwp_cycle_reference_time=cycle_b)
            ),
        }

        result = resolve_combined_forcing_cycle(combinable)  # type: ignore[arg-type]

        assert isinstance(result, cross_cycle_mismatch_cls)
        assert result.cycles == frozenset({cycle_a, cycle_b})

    def test_one_non_null_plus_one_trackless_null_does_not_mismatch(self) -> None:
        from dataclasses import replace

        resolve_combined_forcing_cycle = _flow_attr("resolve_combined_forcing_cycle")

        sid = StationId(uuid4())
        cycle_a = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
        combinable = {
            ModelId("model_a"): self._result(
                sid, replace(_bare_forecast(sid), nwp_cycle_reference_time=cycle_a)
            ),
            # LinearRegressionDaily-shaped: trackless, nwp_cycle_reference_time=None.
            ModelId("model_b"): self._result(sid, _bare_forecast(sid)),
        }

        result = resolve_combined_forcing_cycle(combinable)  # type: ignore[arg-type]

        assert result == cycle_a

    def test_all_null_reports_no_cycle(self) -> None:
        resolve_combined_forcing_cycle = _flow_attr("resolve_combined_forcing_cycle")

        sid = StationId(uuid4())
        combinable = {
            ModelId("model_a"): self._result(sid, _bare_forecast(sid)),
            ModelId("model_b"): self._result(sid, _bare_forecast(sid)),
        }

        result = resolve_combined_forcing_cycle(combinable)  # type: ignore[arg-type]

        assert result is None


class TestPerTrackEligibleStations:
    """Plan 151 T8a red-first (D30): the group-overlap discovery helper --
    a pure function, direct tests only, T8b applies it."""

    class _FakeCandidateAwareAdapter:
        def fetch_requirement(
            self,
            track: ForcingTrackKey,
            stations: list[object],
            nominal_cycle: UtcDatetime,
        ) -> RawFetchOutcome:
            raise AssertionError("eligibility test must not actually fetch")

        def expected_member_ids(self, track: ForcingTrackKey) -> frozenset[int]:
            return frozenset()

    def test_grouped_station_excluded_ungrouped_sibling_included(self) -> None:
        per_track_eligible_stations = _flow_attr("per_track_eligible_stations")

        sid_grouped = StationId(uuid4())
        sid_ungrouped = StationId(uuid4())
        group_store = FakeStationGroupStore()
        group_store.store_group(
            StationGroup(
                id=StationGroupId(uuid4()),
                name="test-group",
                station_ids=frozenset({sid_grouped}),
                description=None,
                created_at=_NOW,
            )
        )

        eligible = per_track_eligible_stations(
            self._FakeCandidateAwareAdapter(),
            [sid_grouped, sid_ungrouped],
            group_store,
        )

        assert eligible == frozenset({sid_ungrouped})

    def test_legacy_adapter_yields_empty_set(self) -> None:
        per_track_eligible_stations = _flow_attr("per_track_eligible_stations")

        sid = StationId(uuid4())

        eligible = per_track_eligible_stations(
            FakeWeatherForecastSource(result={}), [sid], FakeStationGroupStore()
        )

        assert eligible == frozenset()

    def test_no_group_store_all_candidate_aware_stations_eligible(self) -> None:
        per_track_eligible_stations = _flow_attr("per_track_eligible_stations")

        sid = StationId(uuid4())

        eligible = per_track_eligible_stations(
            self._FakeCandidateAwareAdapter(), [sid], None
        )

        assert eligible == frozenset({sid})


def _point_forecast_result(
    cycle_time: UtcDatetime,
    *,
    n_steps: int = 10,
    members: frozenset[int] = frozenset({0}),
    features: tuple[str, ...] = ("precipitation", "temperature"),
    nwp_source: str = _NWP_SOURCE,
) -> PointForecast:
    """Builds a pre-extracted per-station forecast payload -- the shape
    ``fetch_requirement`` returns (D31) -- with hourly valid_times starting
    one step after ``cycle_time``, mirroring ``_make_nwp_records``'s own
    cadence so the SAME underlying values reach the model whichever route
    (legacy pre-seeded store vs. per-track adapter fetch) delivers them."""
    rows: list[dict[str, object]] = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(cycle_time.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for param in features:
            for m in sorted(members):
                rows.append(
                    {
                        "valid_time": vt,
                        "parameter": param,
                        "member_id": m,
                        "value": float(step + m),
                    }
                )
    return PointForecast(
        nwp_source=nwp_source, cycle_time=cycle_time, values=pl.DataFrame(rows)
    )


class _FakeCandidateAwareSource:
    """A FUNCTIONAL ``CandidateAwareForecastSource`` fake for flow-level
    goldens (Plan 151 T8b) -- returns real per-station payloads (mirroring
    recap's pre-extracted dict shape, D31) so the per-track path genuinely
    projects, resolves, commits and assembles, rather than merely proving a
    dispatch decision on a stub."""

    def __init__(
        self,
        *,
        results_by_cycle: dict[UtcDatetime, dict[StationId, object]] | None = None,
        member_ids: frozenset[int] = frozenset({0}),
        raise_on_cycle: dict[UtcDatetime, Callable[[], BaseException]] | None = None,
    ) -> None:
        self._results_by_cycle = results_by_cycle or {}
        self._member_ids = member_ids
        self._raise_on_cycle = raise_on_cycle or {}
        self.fetch_calls: list[UtcDatetime] = []
        # Plan 151 T8b fixer round 2: absent-at-cycle keyed by the track's own
        # feature set, so two tracks on one station can resolve to DIFFERENT
        # cycles -- what the cross-cycle preflight actually guards.
        self.absent_for_features: dict[frozenset[str], set[UtcDatetime]] = {}

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> dict[StationId, object]:
        # A migrated adapter satisfies BOTH `CandidateAwareForecastSource`
        # AND the base `WeatherForecastSource` (D6) -- a group-member
        # station stays on the LEGACY Phase A path (D30-overlap), which
        # calls this method, not `fetch_requirement`.
        return {
            sc.station_id: _point_forecast_result(cycle_time) for sc in station_configs
        }

    def fetch_requirement(
        self,
        track: ForcingTrackKey,
        stations: list[object],
        nominal_cycle: UtcDatetime,
    ) -> RawFetchOutcome:
        self.fetch_calls.append(nominal_cycle)
        if nominal_cycle in self._raise_on_cycle:
            raise self._raise_on_cycle[nominal_cycle]()
        absent = self.absent_for_features.get(frozenset(track.features))
        if absent is not None and nominal_cycle in absent:
            return RawFetchOutcome(
                status=RawFetchStatus.ABSENT_AT_CYCLE, cycle=nominal_cycle, stations={}
            )
        stations_result = self._results_by_cycle.get(nominal_cycle)
        if not stations_result:
            return RawFetchOutcome(
                status=RawFetchStatus.ABSENT_AT_CYCLE, cycle=nominal_cycle, stations={}
            )
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED,
            cycle=nominal_cycle,
            stations=stations_result,  # type: ignore[arg-type]
        )

    def expected_member_ids(self, track: ForcingTrackKey) -> frozenset[int]:
        return self._member_ids


def _make_full_stores() -> dict[str, object]:
    return {
        "station_store": FakeStationStore(),
        "obs_store": FakeObservationStore(),
        "nwp_store": FakeWeatherForecastStore(),
        "artifact_store": FakeModelArtifactStore(),
        "forecast_store": FakeForecastStore(),
        "state_store": FakeModelStateStore(),
        "alert_store": FakeAlertStore(),
        "baseline_store": FakeClimBaselineStore(),
        "basin_store": FakeBasinStore(),
        "forcing_store": FakeHistoricalForcingStore(),
    }


def _run_cycle_with_stores(
    stores: dict[str, object], *, adapter: object, models: dict, **overrides: object
) -> ForecastCycleResult:
    kwargs: dict[str, object] = {
        "station_store": stores["station_store"],
        "obs_store": stores["obs_store"],
        "weather_forecast_store": stores["nwp_store"],
        "forecast_store": stores["forecast_store"],
        "model_state_store": stores["state_store"],
        "artifact_store": stores["artifact_store"],
        "alert_store": stores["alert_store"],
        "baseline_store": stores["baseline_store"],
        "basin_store": stores["basin_store"],
        "forcing_store": stores["forcing_store"],
        "adapter": adapter,
        "models": models,
        "config": _make_config(),
        "qc_rules": _empty_qc_rules(),
        "clock": _clock,
        "rng": random.Random(42),
    }
    kwargs.update(overrides)
    return run_forecast_cycle_flow(**kwargs)  # type: ignore[arg-type]


# Plan 151 T8b golden -- the CANONICAL PERSISTED-OUTPUT SNAPSHOT (plan
# `docs/plans/151-forecast-redesign-phase3-track-resolution-assembly.md`
# ~line 982). Frozen from the pre-T8 tree (main `351bac3`, before any T8b
# dispatch code exists) by running this exact station/model/forcing scenario
# through the (then-only) legacy route -- committed as data, never
# regenerated from post-T8b code. A fixed station id (rather than
# `uuid4()`) is required so BOTH routing goldens below produce output that
# can be compared field-for-field against this one file.
_T8B_ROUTING_STATION_ID = StationId(UUID("11111111-1111-1111-1111-111111111111"))
_T8B_CANONICAL_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "plan151_t8b_canonical_snapshot.json"
)
# Excluded as identity/clock noise per the plan: id, created_at, updated_at.
_T8B_CANONICAL_FIELDS = (
    "station_id",
    "model_id",
    "nwp_cycle_reference_time",
    "nwp_cycle_source",
    "representation",
    "status",
    "warm_up_source",
    "observation_staleness_hours",
    "input_quality",
    "input_quality_flags",
    "qc_status",
    "qc_flags",
    "combination_strategy",
    "source_model_ids",
)


def _plain(obj: object) -> object:
    """Recursively reduces enums/dataclasses/datetimes to JSON-comparable
    plain values -- mirrors the serialisation used to freeze the canonical
    snapshot itself, so both sides of the comparison go through the same
    reduction."""
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return ensure_utc(obj).isoformat()
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    return obj


def _forecast_to_canonical_dict(fc: OperationalForecast) -> dict[str, object]:
    out: dict[str, object] = {
        field: _plain(getattr(fc, field)) for field in _T8B_CANONICAL_FIELDS
    }
    out["station_id"] = str(fc.station_id)
    out["model_id"] = str(fc.model_id)
    out["source_model_ids"] = (
        sorted(str(m) for m in fc.source_model_ids) if fc.source_model_ids else []
    )
    ens = fc.ensemble
    vdf = ens.values.sort(["member_id", "valid_time"])
    out["ensemble_member_ids"] = sorted(set(vdf["member_id"].to_list()))
    out["ensemble_valid_times"] = [
        ensure_utc(t).isoformat() for t in vdf["valid_time"].to_list()
    ]
    out["ensemble_values"] = vdf["value"].to_list()
    return out


def _assert_matches_canonical_snapshot(
    forecast_store: FakeForecastStore,
    pipeline_health_store: FakePipelineHealthStore,
    result: ForecastCycleResult,
) -> None:
    """Plan 151 T8b golden -- full-field comparison against the frozen
    pre-T8 canonical snapshot (plan ~line 982). Output equality alone
    cannot distinguish a mis-routed station from a correctly-routed one on
    homogeneous data -- this is the "byte-identical" half of that
    requirement; the CALL assertions in each test are the other half."""
    canonical = json.loads(_T8B_CANONICAL_SNAPSHOT_PATH.read_text())

    stored = sorted(
        forecast_store._forecasts.values(),  # type: ignore[attr-defined]
        key=lambda fc: (str(fc.station_id), str(fc.model_id)),
    )
    actual_forecasts = [_forecast_to_canonical_dict(fc) for fc in stored]
    assert actual_forecasts == canonical["forecasts"]
    assert result.forecasts_stored == canonical["forecasts_stored"]

    freshness_records = pipeline_health_store.fetch_recent(
        check_type=PipelineCheckType.FORECAST_FRESHNESS
    )
    actual_freshness = [
        {"level": r.status.value, "forecasts_stored": r.detail.get("forecasts_stored")}
        for r in freshness_records
    ]
    assert len(freshness_records) == canonical["freshness_count"]
    assert actual_freshness == canonical["freshness"]


class TestT8bPerTrackRouting:
    """Plan 151 T8b golden -- ROUTING (D12/D30), both directions named
    explicitly. Output equality alone cannot distinguish a mis-routed
    station from a correctly-routed one on homogeneous data, so each
    direction asserts on the CALL as well as the output."""

    def test_candidate_aware_non_grouped_station_takes_the_per_track_route(
        self,
    ) -> None:
        sid = _T8B_ROUTING_STATION_ID
        stores = _make_full_stores()
        pipeline_health_store = FakePipelineHealthStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        source = _FakeCandidateAwareSource(
            results_by_cycle={_NOW: {sid: _point_forecast_result(_NOW)}}
        )

        with (
            patch(
                "sapphire_flow.services.run_station_forecast."
                "run_all_station_forecasts_per_track",
                wraps=run_all_station_forecasts_per_track,
            ) as per_track_spy,
            patch(
                "sapphire_flow.services.run_station_forecast.run_all_station_forecasts",
                wraps=run_all_station_forecasts,
            ) as legacy_spy,
        ):
            result = _run_cycle_with_stores(
                stores,
                adapter=source,
                models={_MODEL_ID: _SmallFakeModel()},
                pipeline_health_store=pipeline_health_store,
            )

        per_track_spy.assert_called_once()
        legacy_spy.assert_not_called()
        assert result.stations_succeeded == 1
        stored = list(stores["forecast_store"]._forecasts.values())  # type: ignore[attr-defined]
        assert len(stored) == 1
        assert stored[0].nwp_cycle_reference_time == _NOW
        assert stored[0].nwp_cycle_source is NwpCycleSource.PRIMARY
        _assert_matches_canonical_snapshot(
            stores["forecast_store"],  # type: ignore[arg-type]
            pipeline_health_store,
            result,
        )

    def test_legacy_adapter_station_never_reaches_a_per_track_entry_point(
        self,
    ) -> None:
        # Same station id, same underlying forcing content (mirroring
        # `_point_forecast_result`'s shape via the legacy `fetch_forecasts`
        # protocol method rather than `fetch_requirement`) as the per-track
        # golden above -- the ONLY thing that should differ is which route
        # carries it, so both must reduce to the SAME frozen canonical
        # snapshot.
        sid = _T8B_ROUTING_STATION_ID
        stores = _make_full_stores()
        pipeline_health_store = FakePipelineHealthStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )

        with (
            patch(
                "sapphire_flow.services.track_resolution.resolve_candidate",
                wraps=resolve_candidate,
            ) as resolve_spy,
            patch(
                "sapphire_flow.services.track_resolution.commit_track",
                wraps=commit_track,
            ) as commit_spy,
            patch(
                "sapphire_flow.services.track_assembly.assemble_assignment_inputs",
                wraps=assemble_assignment_inputs,
            ) as assemble_spy,
            patch(
                "sapphire_flow.services.run_station_forecast."
                "run_all_station_forecasts_per_track",
                wraps=run_all_station_forecasts_per_track,
            ) as per_track_spy,
            patch(
                "sapphire_flow.services.run_station_forecast.run_all_station_forecasts",
                wraps=run_all_station_forecasts,
            ) as legacy_spy,
        ):
            result = _run_cycle_with_stores(
                stores,
                adapter=FakeWeatherForecastSource(
                    result={sid: _point_forecast_result(_NOW)}
                ),
                models={_MODEL_ID: _SmallFakeModel()},
                pipeline_health_store=pipeline_health_store,
            )

        resolve_spy.assert_not_called()
        commit_spy.assert_not_called()
        assemble_spy.assert_not_called()
        per_track_spy.assert_not_called()
        legacy_spy.assert_called_once()
        assert result.stations_succeeded == 1
        _assert_matches_canonical_snapshot(
            stores["forecast_store"],  # type: ignore[arg-type]
            pipeline_health_store,
            result,
        )


class TestT8bStationExceptionContainment:
    """Plan 151 T8b fixer round -- major finding: the per-track branch had
    no station-level exception containment, unlike the legacy branch right
    below it in the same loop. An unanticipated exception ANYWHERE in the
    per-track combination arm (here: `build_combined_forecasts`, called
    unguarded) must degrade to ONE failed station, never crash the whole
    `run_forecast_cycle_flow` run for every station in the cycle."""

    def test_unexpected_exception_in_per_track_combination_fails_one_station_only(
        self,
    ) -> None:
        sid_broken = StationId(uuid4())
        sid_healthy = StationId(uuid4())
        stores = _make_full_stores()
        for sid in (sid_broken, sid_healthy):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                stores["station_store"],  # type: ignore[arg-type]
                stores["obs_store"],  # type: ignore[arg-type]
                stores["nwp_store"],  # type: ignore[arg-type]
                stores["artifact_store"],  # type: ignore[arg-type]
                stores["forcing_store"],  # type: ignore[arg-type]
                seed_nwp=False,
            )
        model_id_b = ModelId("fake_model_b")
        for sid in (sid_broken, sid_healthy):
            stores["station_store"].store_model_assignment(  # type: ignore[attr-defined]
                ModelAssignment(
                    station_id=sid,
                    model_id=model_id_b,
                    time_step=timedelta(hours=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=2,
                    created_at=_NOW,
                )
            )
            stores["artifact_store"].store_artifact(  # type: ignore[attr-defined]
                model_id=model_id_b,
                artifact_bytes=b"fake_artifact_b",
                training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
                training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
                trained_at=_NOW,
                station_id=sid,
                status=ModelArtifactStatus.ACTIVE,
            )
        # Both models share the SAME nwp_source track -> same resolved cycle
        # -> the D11 cross-cycle preflight passes and the POOLED combination
        # arm (the one calling `build_combined_forecasts`) is reached for
        # BOTH stations.
        source = _FakeCandidateAwareSource(
            results_by_cycle={
                _NOW: {
                    sid_broken: _point_forecast_result(_NOW),
                    sid_healthy: _point_forecast_result(_NOW),
                }
            }
        )

        with patch(
            "sapphire_flow.services.forecast_combination.build_combined_forecasts",
            side_effect=lambda *a, **kw: (
                (_ for _ in ()).throw(RuntimeError("boom"))
                if kw.get("station_id") == sid_broken
                else build_combined_forecasts(*a, **kw)
            ),
        ):
            result = _run_cycle_with_stores(
                stores,
                adapter=source,
                models={_MODEL_ID: _SmallFakeModel(), model_id_b: _SmallFakeModel()},
                config=_make_config(
                    forecast_combination_strategy=ModelCombinationStrategy.POOLED
                ),
            )

        # The crash in ONE station's combination arm must not propagate out
        # of the flow (that is the whole point of the finding) -- and the
        # OTHER station must still succeed, not be darkened by the first
        # station's bug. (Before the fix, this RuntimeError propagated out
        # of `run_forecast_cycle_flow` entirely -- this assertion set is
        # simply never reached, the test errors instead of failing.)
        assert result.stations_failed == 1
        assert result.stations_succeeded == 1
        assert any(str(sid_broken) in e for e in result.errors)
        combined_forecasts_by_station = {
            fc.station_id
            for fc in stores["forecast_store"]._forecasts.values()  # type: ignore[attr-defined]
            if fc.combination_strategy is not None
        }
        assert sid_healthy in combined_forecasts_by_station
        assert sid_broken not in combined_forecasts_by_station


class TestT8bAssemblyContainment:
    """Plan 151 T8b fixer round 2 -- CONTAINMENT AT ASSEMBLY.

    Round 1 contained the forecast/persist arm only. Per-station context
    assembly (observation / station / basin / forcing reads) still ran for
    EVERY eligible station in one pre-loop pass, so one station's failed read
    aborted the cycle for all its siblings. These tests fail RED against that
    version: the exception escapes the flow entirely and the healthy station
    never forecasts."""

    def test_assembly_failure_for_one_station_leaves_siblings_forecasting(
        self,
    ) -> None:
        sid_broken = StationId(uuid4())
        sid_healthy = StationId(uuid4())
        stores = _make_full_stores()
        for sid in (sid_broken, sid_healthy):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                stores["station_store"],  # type: ignore[arg-type]
                stores["obs_store"],  # type: ignore[arg-type]
                stores["nwp_store"],  # type: ignore[arg-type]
                stores["artifact_store"],  # type: ignore[arg-type]
                stores["forcing_store"],  # type: ignore[arg-type]
                seed_nwp=False,
            )
        source = _FakeCandidateAwareSource(
            results_by_cycle={
                _NOW: {
                    sid_broken: _point_forecast_result(_NOW),
                    sid_healthy: _point_forecast_result(_NOW),
                }
            }
        )
        import sapphire_flow.services.track_assembly as _ta

        real_assemble = _ta.assemble_assignment_inputs

        def _boom(**kwargs: object) -> object:
            if kwargs.get("station_id") == sid_broken:
                raise RuntimeError("assembly blew up for one station")
            return real_assemble(**kwargs)  # type: ignore[arg-type]

        with patch.object(_ta, "assemble_assignment_inputs", _boom):
            result = _run_cycle_with_stores(
                stores,
                adapter=source,
                models={_MODEL_ID: _SmallFakeModel()},
            )

        # The flow must COMPLETE -- before the fix the RuntimeError propagated
        # out of `run_forecast_cycle_flow` and this line was never reached.
        stored = {
            fc.station_id
            for fc in stores["forecast_store"]._forecasts.values()  # type: ignore[attr-defined]
        }
        assert sid_healthy in stored, "the healthy station must still forecast"
        assert result.stations_succeeded >= 1


class TestT8bMultiTimeStepModelIsSkipped:
    """Plan 151 T8b fixer round 2 -- OWNER RULING 2026-08-28.

    A model may legitimately declare more than one supported time step
    (`docs/spec/types-and-protocols.md`). Per-track assembly assumes exactly
    one and raises. The ruling: SKIP that model for that station and let the
    run continue -- never take the cycle down."""

    def test_model_declaring_two_time_steps_does_not_abort_the_cycle(self) -> None:
        sid_multi = StationId(uuid4())
        sid_healthy = StationId(uuid4())
        stores = _make_full_stores()
        for sid in (sid_multi, sid_healthy):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                stores["station_store"],  # type: ignore[arg-type]
                stores["obs_store"],  # type: ignore[arg-type]
                stores["nwp_store"],  # type: ignore[arg-type]
                stores["artifact_store"],  # type: ignore[arg-type]
                stores["forcing_store"],  # type: ignore[arg-type]
                seed_nwp=False,
            )
        # The multi-step model must actually be ASSIGNED to a station, or it
        # never runs and this test proves nothing. (First draft of this test
        # omitted the assignment and passed against the unfixed code -- the
        # exact vacuity this round is fixing elsewhere.)
        multi_id = ModelId("multi_step")
        stores["station_store"].store_model_assignment(  # type: ignore[attr-defined]
            ModelAssignment(
                station_id=sid_multi,
                model_id=multi_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        stores["artifact_store"].store_artifact(  # type: ignore[attr-defined]
            model_id=multi_id,
            artifact_bytes=b"fake_artifact_multi",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid_multi,
            status=ModelArtifactStatus.ACTIVE,
        )
        source = _FakeCandidateAwareSource(
            results_by_cycle={
                _NOW: {
                    sid_multi: _point_forecast_result(_NOW),
                    sid_healthy: _point_forecast_result(_NOW),
                }
            }
        )
        result = _run_cycle_with_stores(
            stores,
            adapter=source,
            models={
                _MODEL_ID: _SmallFakeModel(),
                multi_id: _MultiTimeStepFakeModel(),
            },
        )
        stored = {
            fc.station_id
            for fc in stores["forecast_store"]._forecasts.values()  # type: ignore[attr-defined]
        }
        assert sid_healthy in stored
        assert result.stations_succeeded >= 1


class TestT8bMemberSetThreading:
    """Plan 151 T8b golden -- MEMBER SET (R3). The source-derived exact
    member set is obtained ONCE per track and threaded into BOTH
    `resolve_candidate` and every `assemble_assignment_inputs` call for that
    track. This test fails RED if the `expected_member_ids=` argument is
    omitted from the `assemble_assignment_inputs` call -- the specific
    regression it exists to catch."""

    def test_expected_member_ids_reaches_assemble_assignment_inputs(self) -> None:
        sid = StationId(uuid4())
        stores = _make_full_stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        expected = frozenset({0, 1, 2})
        source = _FakeCandidateAwareSource(
            results_by_cycle={
                _NOW: {sid: _point_forecast_result(_NOW, members=expected)}
            },
            member_ids=expected,
        )

        with patch(
            "sapphire_flow.services.track_assembly.assemble_assignment_inputs",
            wraps=assemble_assignment_inputs,
        ) as assemble_spy:
            result = _run_cycle_with_stores(
                stores, adapter=source, models={_MODEL_ID: _SmallFakeModel()}
            )

        assert result.stations_succeeded == 1
        assert assemble_spy.call_count >= 1
        for call in assemble_spy.call_args_list:
            assert call.kwargs["expected_member_ids"] == expected


class TestT8bFailureRoutes:
    """Plan 151 T8b golden -- THE TWO NEWLY-ACTIVATED FAILURE ROUTES (R4).
    T8b owns the mapping from track results into run inputs; both cases
    assert the cause AND that the fallback chain still produced a station
    forecast."""

    def _two_assignment_station(
        self, stores: dict[str, object], sid: StationId
    ) -> None:
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        fallback_id = ModelId("fake_fallback_model")
        stores["station_store"].store_model_assignment(  # type: ignore[attr-defined]
            ModelAssignment(
                station_id=sid,
                model_id=fallback_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        stores["artifact_store"].store_artifact(  # type: ignore[attr-defined]
            model_id=fallback_id,
            artifact_bytes=b"fake_artifact_fallback",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )

    def test_walkback_exhaustion_records_missing_context_and_fallback_succeeds(
        self,
    ) -> None:
        sid = StationId(uuid4())
        stores = _make_full_stores()
        self._two_assignment_station(stores, sid)
        # Every candidate cycle is absent -> walk-back exhausts within the
        # (small) configured bound.
        source = _FakeCandidateAwareSource(results_by_cycle={})

        result = _run_cycle_with_stores(
            stores,
            adapter=source,
            models={
                _MODEL_ID: _SmallFakeModel(),
                ModelId("fake_fallback_model"): _NativeFakeModel(),
            },
        )

        assert result.stations_succeeded == 1
        stored = list(stores["forecast_store"]._forecasts.values())  # type: ignore[attr-defined]
        assert len(stored) >= 1
        assert {f.model_id for f in stored} == {ModelId("fake_fallback_model")}

    def test_station_unavailable_at_resolved_cycle_records_track_unavailable(
        self,
    ) -> None:
        sid = StationId(uuid4())
        other_sid = StationId(uuid4())
        stores = _make_full_stores()
        self._two_assignment_station(stores, sid)
        # A SIBLING station completes the candidate so the track resolves,
        # but `sid` itself is absent from the fetched payload -> resolved
        # track, THIS station unavailable (TRACK_UNAVAILABLE, not
        # MISSING_CONTEXT).
        source = _FakeCandidateAwareSource(
            results_by_cycle={_NOW: {other_sid: _point_forecast_result(_NOW)}}
        )

        result = _run_cycle_with_stores(
            stores,
            adapter=source,
            models={
                _MODEL_ID: _SmallFakeModel(),
                ModelId("fake_fallback_model"): _NativeFakeModel(),
            },
        )

        assert result.stations_succeeded == 1
        stored = list(stores["forecast_store"]._forecasts.values())  # type: ignore[attr-defined]
        assert {f.model_id for f in stored} == {ModelId("fake_fallback_model")}


class TestT8bFreshnessOnFatalResolution:
    """Plan 151 T8b golden -- FRESHNESS ON EVERY FATAL RESOLUTION EXIT,
    PARAMETERISED (R5). Each of the four fatal classes propagates out of
    `resolve_candidate`'s `fetch_candidate` call (auth/config/payload-
    integrity, D7's locked mapping) or `commit_track`'s persist (store
    failure) and must emit EXACTLY ONE forced-CRITICAL FORECAST_FRESHNESS
    record before re-raising."""

    @pytest.mark.parametrize(
        "make_exc",
        [
            lambda: RecapAuthError("unauthorized", status_code=401),
            lambda: RecapConfigurationError("bad hru", field="hru_code"),
            lambda: RecapPayloadIntegrityError("corrupt payload"),
            # Plan 151 T8b fixer round 2: the legacy path has always treated
            # this as fatal-with-a-health-record; T8b's tuple omitted it, so a
            # Recap deployment with an unpopulated polygon table failed the
            # flow with NO freshness record -- silent where Plan 116 demands
            # loud. This row fails RED against that version.
            lambda: GatewayResolutionError(
                "no station resolved", station_id=StationId(uuid4())
            ),
        ],
        ids=[
            "RecapAuthError",
            "RecapConfigurationError",
            "RecapPayloadIntegrityError",
            "GatewayResolutionError",
        ],
    )
    def test_fatal_fetch_error_emits_one_critical_record_and_propagates(
        self, make_exc: Callable[[], BaseException]
    ) -> None:
        sid = StationId(uuid4())
        stores = _make_full_stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        exc = make_exc()
        source = _FakeCandidateAwareSource(raise_on_cycle={_NOW: lambda: exc})
        health_store = FakePipelineHealthStore()

        with pytest.raises(type(exc)):
            _run_cycle_with_stores(
                stores,
                adapter=source,
                models={_MODEL_ID: _SmallFakeModel()},
                pipeline_health_store=health_store,
            )

        records = health_store.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL

    def test_fatal_store_failure_during_commit_emits_one_critical_record(
        self,
    ) -> None:
        sid = StationId(uuid4())
        stores = _make_full_stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        source = _FakeCandidateAwareSource(
            results_by_cycle={_NOW: {sid: _point_forecast_result(_NOW)}}
        )
        health_store = FakePipelineHealthStore()

        class _RaisingWeatherForecastStore:
            """Delegates to a real fake for everything EXCEPT the
            per-track commit's persist call, which fails fatally --
            composition (not a raising ``__getattr__``) so introspection by
            Prefect/pydantic on unrelated dunder attributes stays inert."""

            def __init__(self, inner: FakeWeatherForecastStore) -> None:
                self._inner = inner

            def store_weather_forecasts(self, records: object) -> None:
                raise StoreError("simulated store outage")

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        with pytest.raises(StoreError):
            _run_cycle_with_stores(
                stores,
                adapter=source,
                models={_MODEL_ID: _SmallFakeModel()},
                pipeline_health_store=health_store,
                weather_forecast_store=_RaisingWeatherForecastStore(
                    stores["nwp_store"]  # type: ignore[arg-type]
                ),
            )

        records = health_store.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL


class TestT8bCrossCyclePreflight:
    """Plan 151 T8b golden -- CROSS-CYCLE PREFLIGHT (D11 + Plan 116).

    REWRITTEN in fixer round 2 because the previous version was VACUOUS. It
    added a SECOND FORECAST-role weather source to force a second track, but
    a station has exactly one forecast binding by design and
    ``FakeStationStore.fetch_forecast_binding`` raises when there is not
    exactly one. The flow therefore failed on the binding, long before the
    preflight, and the test's zero-write / CRITICAL assertions passed for the
    wrong reason. Proved by experiment: with the preflight branch disabled
    outright the old test still reported ``1 passed``.

    This version drives two tracks the only legitimate way -- two models whose
    FUTURE feature sets differ -- and makes ONE of those tracks absent at the
    nominal cycle so it walks back to an older one. Two combinable assignments
    then genuinely resolve to two different cycles."""

    def test_mismatched_cycles_write_nothing_and_force_critical_freshness(
        self,
    ) -> None:
        sid = StationId(uuid4())
        stores = _make_full_stores()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        model_id_b = ModelId("fake_model_b")
        stores["station_store"].store_model_assignment(  # type: ignore[attr-defined]
            ModelAssignment(
                station_id=sid,
                model_id=model_id_b,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        stores["artifact_store"].store_artifact(  # type: ignore[attr-defined]
            model_id=model_id_b,
            artifact_bytes=b"fake_artifact_b",
            training_period_start=ensure_utc(datetime(2020, 1, 1, tzinfo=UTC)),
            training_period_end=ensure_utc(datetime(2025, 12, 31, tzinfo=UTC)),
            trained_at=_NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )
        older_cycle = _NOW - timedelta(hours=6)
        source = _FakeCandidateAwareSource(
            results_by_cycle={
                _NOW: {sid: _point_forecast_result(_NOW)},
                older_cycle: {sid: _point_forecast_result(older_cycle)},
            }
        )
        # The precipitation-only track is ABSENT at the nominal cycle, so it
        # walks back to `older_cycle`; the full-feature track resolves at
        # `_NOW`. Two combinable assignments, two different resolved cycles.
        source.absent_for_features = {frozenset({"precipitation"}): {_NOW}}

        result = _run_cycle_with_stores(
            stores,
            adapter=source,
            models={_MODEL_ID: _SmallFakeModel(), model_id_b: _PrecipOnlyFakeModel()},
            config=_make_config(
                forecast_combination_strategy=ModelCombinationStrategy.POOLED
            ),
        )

        # The station must write NOTHING and be reported failed. These
        # assertions are what the old version could not reach.
        assert result.stations_failed == 1
        assert result.stations_succeeded == 0
        assert not [
            fc
            for fc in stores["forecast_store"]._forecasts.values()  # type: ignore[attr-defined]
            if fc.station_id == sid
        ]
        # ... and the failure must be the CROSS-CYCLE one specifically, not
        # any other error that happens to darken the station. Without this
        # the test would pass on a binding error again.
        assert any("cycle" in e.lower() for e in result.errors), result.errors
        assert result.forecasts_stored == 0


class TestT8bGroupOverlapStaysLegacy:
    """Plan 151 T8b golden -- D30-overlap-deferral. A station that is BOTH
    group-member and per-track-eligible stays on the LEGACY path in Phase 3;
    only its ungrouped sibling is served per-track."""

    def test_grouped_station_excluded_from_eligible_set_at_flow_level(self) -> None:
        grouped_sid = StationId(uuid4())
        ungrouped_sid = StationId(uuid4())
        stores = _make_full_stores()
        for sid in (grouped_sid, ungrouped_sid):
            _build_station_and_stores(
                sid,
                _MODEL_ID,
                stores["station_store"],  # type: ignore[arg-type]
                stores["obs_store"],  # type: ignore[arg-type]
                stores["nwp_store"],  # type: ignore[arg-type]
                stores["artifact_store"],  # type: ignore[arg-type]
                stores["forcing_store"],  # type: ignore[arg-type]
                seed_nwp=False,
            )
        group_store = FakeStationGroupStore()
        group_store.store_group(
            StationGroup(
                id=StationGroupId(uuid4()),
                name="overlap-group",
                station_ids=frozenset({grouped_sid}),
                description=None,
                created_at=_NOW,
            )
        )
        source = _FakeCandidateAwareSource(
            results_by_cycle={
                _NOW: {
                    grouped_sid: _point_forecast_result(_NOW),
                    ungrouped_sid: _point_forecast_result(_NOW),
                }
            }
        )

        with patch(
            "sapphire_flow.services.run_station_forecast."
            "run_all_station_forecasts_per_track",
            wraps=run_all_station_forecasts_per_track,
        ) as per_track_spy:
            _run_cycle_with_stores(
                stores,
                adapter=source,
                models={_MODEL_ID: _SmallFakeModel()},
                group_store=group_store,
            )

        per_track_stations = {
            call.kwargs["station_id"] for call in per_track_spy.call_args_list
        }
        assert ungrouped_sid in per_track_stations
        assert grouped_sid not in per_track_stations


def _fi_heterogeneous_requirement() -> object:
    from sapphire_flow.adapters import forecast_interface as fi_boundary

    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            timedelta(hours=1): fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            future_known={
                                "nwp": {
                                    "precipitation": fi_boundary.FutureKnownVariable(
                                        future_steps=2,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    ),
                                    "temperature": fi_boundary.FutureKnownVariable(
                                        future_steps=10,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.DEG_C,
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
    )


class _HeterogeneousFakeFIModel:
    """A REAL FI model (per ``docs/model_interface.md``) whose only branch
    declares TWO future_known variables at DIFFERENT ``future_steps`` --
    D10a's flagship shape. ``predict`` returns a genuine
    ``fi_boundary.ModelSuccess`` so the golden proves delivery, not merely
    construction."""

    def __init__(self) -> None:
        from sapphire_flow.adapters import forecast_interface as fi_boundary

        self._input_requirement = _fi_heterogeneous_requirement()
        self.artifact_scope = fi_boundary.FIArtifactScope.STATION

    @property
    def input_requirement(self) -> object:
        return self._input_requirement

    def train(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError

    def predict(
        self,
        artifact: object,
        *,
        inputs: object,
        issue_datetime: UtcDatetime,
        rng: random.Random,
    ) -> object:
        from sapphire_flow.adapters import forecast_interface as fi_boundary

        values = [10.0 + step for step in range(10)]
        data = pl.DataFrame(
            {
                "issue_datetime": [issue_datetime] * len(values),
                "datetime": [
                    ensure_utc(
                        datetime.fromtimestamp(
                            issue_datetime.timestamp() + (step + 1) * 3600, tz=UTC
                        )
                    )
                    for step in range(len(values))
                ],
                "value": values,
            }
        ).with_columns(
            pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
            pl.col("datetime").cast(pl.Datetime("us", "UTC")),
        )
        variable = fi_boundary.VariableOutput(
            metadata=fi_boundary.VariableMetadata(
                unit=fi_boundary.Unit.M3_PER_S,
                timedelta=timedelta(hours=1),
                forecast_horizon=len(values),
                offset=0,
            ),
            deterministic=fi_boundary.DeterministicData(data=data),
            flags=frozenset(),
            status=fi_boundary.VariableStatus.SUCCESS,
        )
        return fi_boundary.ModelSuccess(
            output=fi_boundary.ModelOutput(
                model_name="hetero-fi",
                issue_datetime=issue_datetime,
                variables={"station": {"discharge": variable}},
            )
        )

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


class TestT8bHeterogeneousStation:
    """Plan 151 T8b golden -- HETEROGENEOUS STATION (D10a). Reachable ONLY
    because the runner's per-feature coverage reads the per-track CONTRACT
    instead of the scalar ``model.data_requirements`` -- with the legacy
    scalar path this station would fail `INSUFFICIENT_COVERAGE` on its
    short (2-step) feature even though its long (10-step) feature is fully
    covered."""

    def test_heterogeneous_horizons_reach_predict_and_succeed(self) -> None:
        from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter

        sid = StationId(uuid4())
        stores = _make_full_stores()
        model_id = ModelId("fi_hetero_model")
        _build_station_and_stores(
            sid,
            model_id,
            stores["station_store"],  # type: ignore[arg-type]
            stores["obs_store"],  # type: ignore[arg-type]
            stores["nwp_store"],  # type: ignore[arg-type]
            stores["artifact_store"],  # type: ignore[arg-type]
            stores["forcing_store"],  # type: ignore[arg-type]
            seed_nwp=False,
        )
        adapter_model = ForecastInterfaceAdapter(
            _HeterogeneousFakeFIModel(),  # type: ignore[arg-type]
            station_code_resolver=lambda _sid: "TEST-001",
        )
        adapter_model.alert_eligibility = AlertEligibility.SKILL_FORECAST  # type: ignore[attr-defined]
        # precipitation's own series is a strict 2-step PREFIX of
        # temperature's 10-step series (both start at cycle + 1h) -- the
        # straightforward "differently-truncated" shape, not the staggered
        # one T2's own fixer round separately locks.
        payload = PointForecast(
            nwp_source=_NWP_SOURCE,
            cycle_time=_NOW,
            values=pl.concat(
                [
                    _point_forecast_result(
                        _NOW, n_steps=2, features=("precipitation",)
                    ).values,
                    _point_forecast_result(
                        _NOW, n_steps=10, features=("temperature",)
                    ).values,
                ]
            ),
        )
        source = _FakeCandidateAwareSource(results_by_cycle={_NOW: {sid: payload}})

        result = _run_cycle_with_stores(
            stores, adapter=source, models={model_id: adapter_model}
        )

        assert result.stations_succeeded == 1
        stored = list(stores["forecast_store"]._forecasts.values())  # type: ignore[attr-defined]
        assert len(stored) == 1
        assert stored[0].model_id == model_id
        assert stored[0].ensemble.forecast_horizon_steps == 10
