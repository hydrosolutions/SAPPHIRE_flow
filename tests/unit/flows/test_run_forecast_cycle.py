from __future__ import annotations

import builtins
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

import httpx
import numpy as np
import polars as pl
import pytest
import xarray as xr

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import (
    AdapterError,
    ConfigurationError,
    ExtractionError,
    StoreError,
)
from sapphire_flow.flows.run_forecast_cycle import (
    ForecastCycleResult,
    _check_nwp_grid_staleness,
    _fetch_nwp_task,
    _load_weather_forecast_adapter_config,
    _NwpFetchOutcome,
    run_forecast_cycle_flow,
)
from sapphire_flow.models.climatology_fallback import (
    ClimatologyArtifact,
    ClimatologyFallbackModel,
)
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
    ForecastCycleHealth,
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
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    NWP_REGRESSION_MODEL_ID,
    BasinId,
    ModelId,
    StationGroupId,
    StationId,
)
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
    WeatherForecastRecord,
)
from tests.conftest import make_observations, make_station_config
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
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

if TYPE_CHECKING:
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

        adapter = _build_recap_forecast_adapter(
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


class _RaisingAdapter:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch_forecasts(self, *args: object, **kwargs: object) -> object:
        raise self._exc


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
