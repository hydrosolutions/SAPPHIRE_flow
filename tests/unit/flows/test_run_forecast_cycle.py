from __future__ import annotations

import builtins
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import numpy as np
import polars as pl
import pytest
import xarray as xr

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import ConfigurationError, ExtractionError, StoreError
from sapphire_flow.flows.run_forecast_cycle import (
    ForecastCycleResult,
    _load_weather_forecast_adapter_config,
    run_forecast_cycle_flow,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import (
    ModelArtifactStatus,
    ModelAssignmentStatus,
    ModelCombinationStrategy,
    SpatialRepresentation,
    StationKind,
    StationStatus,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import BasinId, ModelId, StationId
from sapphire_flow.types.station import ModelAssignment, StationWeatherSource
from sapphire_flow.types.weather import (
    BasinAverageForecast,
    ElevationBandForecast,
    GriddedForecast,
    WeatherForecastRecord,
)
from tests.conftest import make_observations, make_station_config
from tests.fakes.fake_adapters import FakeGridExtractor, FakeWeatherForecastSource
from tests.fakes.fake_models import FakeStationForecastModel
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
    FakeStationStore,
    FakeWeatherForecastStore,
)

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
) -> dict[StationId, BasinAverageForecast]:
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
            cycle_time=_NOW,
            values=df,
        )
    return result


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
        station_status=StationStatus.OPERATIONAL,
        measured_parameters=frozenset({"discharge"}),
        forecast_targets=frozenset({"discharge"}),
        basin_id=basin_id,
    )
    station_store.store_station(station)

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
            ) -> None:
                constructed.append(
                    {
                        "stac_base_url": stac_base_url,
                        "stac_collection": stac_collection,
                        "scratch_path": scratch_path,
                        "http_client": http_client,
                        "max_fallback_steps": max_fallback_steps,
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
        created_client = constructed[0]["http_client"]
        assert isinstance(created_client, httpx.Client)
        assert created_client.is_closed

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
                models={_MODEL_ID: _SmallFakeModel()},  # type: ignore[dict-item]
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
