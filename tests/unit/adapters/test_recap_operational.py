from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pandas as pd
import pytest

from sapphire_flow.adapters.recap_gateway import (
    EcmwfApiLike,
    GatewayHruName,
    GatewayPolygonName,
    GatewayPolygonRef,
    RecapConfigurationError,
    RecapPayloadIntegrityError,
)
from sapphire_flow.adapters.recap_operational import (
    RecapGatewayOperationalAdapter,
    parse_operational_samples,
)
from sapphire_flow.config.operational_weather import OperationalWeatherConfig
from sapphire_flow.preprocessing.recap_operational_weather import reduce_daily_window
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import WeatherProductIdentity
from sapphire_flow.types.station import StationWeatherSource

if TYPE_CHECKING:
    from recap_client.http import ParquetHttpClient


FIXTURES = Path(__file__).parents[2] / "fixtures" / "recap" / "operational"
START = ensure_utc(datetime(2026, 10, 7, tzinfo=UTC))
ISSUE = ensure_utc(datetime(2026, 11, 6, tzinfo=UTC))
END = ensure_utc(ISSUE + timedelta(days=10))
LIMIT = 64 * 1024 * 1024


@pytest.fixture
def precipitation() -> pd.DataFrame:
    return pd.read_parquet(FIXTURES / "precipitation.parquet")


class TestQualifiedOperationalReduction:
    def test_actual_memberless_payload_all_21_stations_40_days(
        self, precipitation: pd.DataFrame
    ) -> None:
        temperature = pd.read_parquet(FIXTURES / "temperature.parquet")
        polygons = [
            str(column)
            for column in precipitation.columns
            if str(column).startswith("g_")
        ]
        assert len(polygons) == 21
        for number, polygon in enumerate(polygons, start=1):
            p_samples = parse_operational_samples(
                precipitation,
                polygon=polygon,
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )
            t_samples = parse_operational_samples(
                temperature,
                polygon=polygon,
                parameter="temperature",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )
            window = reduce_daily_window(
                station_id=StationId(UUID(int=number)),
                binding_fingerprint="a" * 64,
                issue_time=ISSUE,
                start=START,
                end=END,
                samples=p_samples + t_samples,
            )
            assert len(window.days) == 40
            assert len(p_samples) == 688
            assert all(day.precipitation_mm >= 0 for day in window.days)
            assert p_samples[-1].interval_end == END
            assert p_samples[-1].product is WeatherProductIdentity.CONTROL

    def test_conflicting_optional_member_rejected(
        self, precipitation: pd.DataFrame
    ) -> None:
        precipitation.loc[precipitation.index[0], "source_meta"] = '{"member":"1"}'
        with pytest.raises(RecapPayloadIntegrityError, match="invalid operational"):
            parse_operational_samples(
                precipitation,
                polygon="g_225",
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=LIMIT,
            )

    def test_oversize_response_rejected(self, precipitation: pd.DataFrame) -> None:
        with pytest.raises(RecapPayloadIntegrityError, match="byte limit"):
            parse_operational_samples(
                precipitation,
                polygon="g_225",
                parameter="precipitation",
                start=START,
                end=END,
                max_response_bytes=1,
            )

    def test_missing_required_row_is_not_filled(
        self, precipitation: pd.DataFrame
    ) -> None:
        temperature = pd.read_parquet(FIXTURES / "temperature.parquet")
        rows = parse_operational_samples(
            precipitation.iloc[1:],
            polygon="g_225",
            parameter="precipitation",
            start=START,
            end=END,
            max_response_bytes=LIMIT,
        ) + parse_operational_samples(
            temperature,
            polygon="g_225",
            parameter="temperature",
            start=START,
            end=END,
            max_response_bytes=LIMIT,
        )
        with pytest.raises(ValueError, match="gap"):
            reduce_daily_window(
                station_id=StationId(UUID(int=1)),
                binding_fingerprint="a" * 64,
                issue_time=ISSUE,
                start=START,
                end=END,
                samples=rows,
            )


class RecordingApi:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[dict[str, object]] = []

    def operational(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.frames[str(kwargs["ifs_variable_name"])]


class PolygonResolver:
    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef:
        return GatewayPolygonRef(
            hru_name=GatewayHruName("nepal21_20261012"),
            polygon_name=GatewayPolygonName("g_225"),
            station_id=source.station_id,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
        )


def make_binding() -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(UUID(int=1)),
        nwp_source="recap_ecmwf_operational",
        role=WeatherSourceRole.OPERATIONAL,
        status=WeatherSourceStatus.ACTIVE,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
    )


class TestRecapGatewayOperationalAdapter:
    def test_short_model_window_uses_minimum_provider_history_and_buffer(self) -> None:
        api = RecordingApi(
            {
                "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
                "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
            }
        )
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", api),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        source = make_binding()
        start = ensure_utc(ISSUE - timedelta(days=2))
        result = adapter.fetch_windows(
            sources=[source], issue_time=ISSUE, start=start, end=ISSUE
        )
        assert len(api.calls) == 2
        assert all(
            call["start_date"] == (ISSUE - timedelta(days=14)).date()
            for call in api.calls
        )
        assert all(call["subdaily_resolution"] == 6 for call in api.calls)
        assert all(
            "value_agg_fn" not in call and "unit_conversion_fn" not in call
            for call in api.calls
        )
        assert result[source.station_id].start == start
        assert result[source.station_id].end == ISSUE + timedelta(days=1)

    def test_incomplete_optional_buffer_does_not_invalidate_required_past(self) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        api = RecordingApi(
            {key: frame.loc[frame.index < ISSUE] for key, frame in frames.items()}
        )
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", api),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        source = make_binding()
        result = adapter.fetch_windows(
            sources=[source], issue_time=ISSUE, start=START, end=ISSUE
        )
        assert result[source.station_id].end == ISSUE

    def test_invalid_binding_is_rejected_before_any_call(self) -> None:
        api = RecordingApi({})
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", api),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        with pytest.raises(RecapConfigurationError, match="binding"):
            adapter.fetch_windows(
                sources=[replace(make_binding(), role=WeatherSourceRole.REANALYSIS)],
                issue_time=ISSUE,
                start=START,
                end=ISSUE,
            )
        assert api.calls == []


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_parquet_df(self, path: str, params: dict[str, object]) -> pd.DataFrame:
        self.calls.append((path, params))
        return pd.DataFrame()


class TestPinnedSdkRequestContract:
    def test_independent_future_endpoint_preserves_run_and_untransformed_values(
        self,
    ) -> None:
        from recap_client.ecmwf import EcmwfApi

        transport = RecordingTransport()
        api = EcmwfApi(cast("ParquetHttpClient", transport))
        api.ifs_forecast(
            variable="tp",
            run_date=ISSUE,
            run_hour=6,
            hru_code="nepal21_20261012",
            ifs_type="fc",
            member="fc",
            include_provenance=True,
            horizon_days=15,
        )
        assert transport.calls == [
            (
                "exports/ifs/forecast",
                {
                    "variable": "tp",
                    "run_date": "2026-11-06",
                    "run_hour": 6,
                    "hru_code": "nepal21_20261012",
                    "ifs_type": "fc",
                    "member": "fc",
                    "level_type": "sfc",
                    "horizon_days": 15,
                    "include_provenance": True,
                },
            )
        ]

    def test_combined_endpoint_uses_fill_cycles_without_member_or_transforms(
        self,
    ) -> None:
        from recap_client.ecmwf import EcmwfApi

        transport = RecordingTransport()
        api = EcmwfApi(cast("ParquetHttpClient", transport))
        api.operational(
            hru_code="nepal21_20261012",
            start_date=START,
            era5_variable_name="total_precipitation",
            ifs_variable_name="tp",
            subdaily_resolution=6,
            include_provenance=True,
        )
        assert transport.calls == [
            (
                "exports/ecmwf/operational",
                {
                    "hru_code": "nepal21_20261012",
                    "start_date": "2026-10-07",
                    "era5_variable_name": "total_precipitation",
                    "ifs_variable_name": "tp",
                    "subdaily_resolution": 6,
                    "era5_dataset_name": "reanalysis-era5-land",
                    "include_provenance": True,
                },
            )
        ]


class TestOptionalBufferIntegrity:
    @pytest.mark.parametrize("value", [float("nan"), -1.0])
    @pytest.mark.parametrize("scope", ["required", "optional"])
    def test_numeric_failure_is_confined_to_optional_scope(
        self,
        value: float,
        scope: str,
    ) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        timestamp = ISSUE if scope == "optional" else START
        frames["tp"].loc[timestamp, "g_225"] = value
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        if scope == "required":
            with pytest.raises(RecapPayloadIntegrityError, match="numeric/day"):
                adapter.fetch_windows(
                    sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
                )
        else:
            result = adapter.fetch_windows(
                sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
            )
            assert result[make_binding().station_id].end == ISSUE

    def test_invalid_optional_value_does_not_hide_other_leg_identity(self) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        frames["tp"].loc[ISSUE, "g_225"] = float("nan")
        frames["2t"].loc[ISSUE, "source_meta"] = '{"member":"1"}'
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        with pytest.raises(RecapPayloadIntegrityError, match="invalid operational"):
            adapter.fetch_windows(
                sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
            )

    def test_invalid_optional_time_grid_omits_only_buffer(self) -> None:
        frames = {
            "tp": pd.read_parquet(FIXTURES / "precipitation.parquet"),
            "2t": pd.read_parquet(FIXTURES / "temperature.parquet"),
        }
        frames["tp"] = frames["tp"].rename(index={ISSUE: ISSUE + timedelta(minutes=30)})
        adapter = RecapGatewayOperationalAdapter(
            api=cast("EcmwfApiLike", RecordingApi(frames)),
            resolver=PolygonResolver(),
            config=OperationalWeatherConfig(),
        )
        result = adapter.fetch_windows(
            sources=[make_binding()], issue_time=ISSUE, start=START, end=ISSUE
        )
        assert result[make_binding().station_id].end == ISSUE
