"""Live recap Data Gateway smoke tests (Plan 082 Task 1B).

Exercises OUR merged adapters (``RecapGatewayReanalysisAdapter`` /
``RecapGatewayForecastAdapter``) plus a few raw-client shape checks against a
REAL Gateway-registered HRU. Excluded by default (markers ``live`` +
``live_recap``); skipped whenever ``RECAP_API_KEY`` is absent. Run manually::

    RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py \
        -m 'live and live_recap' -v

Config via env (defaults target the DHM test basin registered 2026-07-17):
- ``RECAP_API_KEY``       — required; token for the Gateway data API.
- ``RECAP_BASE_URL``      — default ``https://recap.ieasyhydro.org/sdk``.
- ``RECAP_TEST_HRU``      — Gateway HRU registration name (default ``12300``).
- ``RECAP_TEST_POLYGON``  — feature/column name inside the HRU (default ``g_123``).
- ``RECAP_TEST_REANALYSIS_YEAR`` — a year with ERA5-Land backfill (default ``2024``).

Subscription-gated legs (``pf`` ensemble members, ``JSNOW`` snow) are marked
``xfail(strict=False)`` so they auto-flip to xpass once the HRU is subscribed,
without failing the suite while it is not.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from recap_client import ApiClientConfig, RecapClient

from sapphire_flow.adapters.recap_gateway import (
    GatewayHruName,
    GatewayPolygonName,
    GatewayPolygonRef,
    RecapGatewayForecastAdapter,
    RecapGatewayReanalysisAdapter,
    _kelvin_to_celsius,  # pyright: ignore[reportPrivateUsage]
    _metres_to_mm,  # pyright: ignore[reportPrivateUsage]
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource

_RECAP_API_KEY = os.environ.get("RECAP_API_KEY")
_RECAP_BASE_URL = os.environ.get("RECAP_BASE_URL", "https://recap.ieasyhydro.org/sdk")
_HRU = os.environ.get("RECAP_TEST_HRU", "12300")
_POLYGON = os.environ.get("RECAP_TEST_POLYGON", "g_123")
_YEAR = int(os.environ.get("RECAP_TEST_REANALYSIS_YEAR", "2024"))
_REAN_START = ensure_utc(datetime(_YEAR, 6, 1, tzinfo=UTC))
_REAN_END = ensure_utc(datetime(_YEAR, 6, 5, tzinfo=UTC))
_SID = StationId(UUID("00000000-0000-0000-0000-000000000123"))

pytestmark = [
    pytest.mark.live,
    pytest.mark.live_recap,
    pytest.mark.skipif(
        _RECAP_API_KEY is None,
        reason="RECAP_API_KEY not set — skipped by default (Plan 082 Task 1B)",
    ),
]


class _LiveResolver:
    """Static resolver: maps any station to the fixed test HRU/polygon."""

    def resolve(self, source: object) -> GatewayPolygonRef:
        return GatewayPolygonRef(
            hru_name=GatewayHruName(_HRU),
            polygon_name=GatewayPolygonName(_POLYGON),
            station_id=_SID,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
        )


@pytest.fixture(scope="module")
def client() -> RecapClient:
    return RecapClient(
        ApiClientConfig(base_url=_RECAP_BASE_URL, api_key=_RECAP_API_KEY)
    )


def _station(*, nwp_source: str, role: WeatherSourceRole) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=_SID,
        nwp_source=nwp_source,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=role,
    )


class TestReanalysisAdapterLive:
    """End-to-end: our reanalysis adapter over live ERA5-Land backfill."""

    def test_fetch_reanalysis_returns_typed_converted_rows(
        self, client: RecapClient
    ) -> None:
        adapter = RecapGatewayReanalysisAdapter(client=client, resolver=_LiveResolver())
        rows = adapter.fetch_reanalysis(
            [_station(nwp_source="era5_land", role=WeatherSourceRole.REANALYSIS)],
            _REAN_START,
            _REAN_END,
            ["precipitation", "temperature"],
        )
        assert rows, "expected ERA5-Land rows for the backfilled window"
        params = {r.parameter for r in rows}
        assert params == {"precipitation", "temperature"}
        assert all(r.source == "recap_era5_land_reanalysis" for r in rows)
        precip = [r.value for r in rows if r.parameter == "precipitation"]
        temp = [r.value for r in rows if r.parameter == "temperature"]
        assert all(v >= 0 for v in precip), "precip (mm) must be non-negative"
        assert all(-60 < v < 60 for v in temp), (
            "temperature (°C) out of plausible range"
        )


class TestForecastAdapterLive:
    """End-to-end: our forecast adapter assembles the IFS ensemble.

    xfail until the HRU is subscribed to IFS ``pf`` — the adapter requires the
    full 1×fc + 50×pf ensemble and hard-aborts on a missing pf subscription.
    """

    @pytest.mark.xfail(
        reason="HRU not yet subscribed to IFS pf; xpasses once subscribed",
        strict=False,
    )
    def test_fetch_forecasts_assembles_ensemble(self, client: RecapClient) -> None:
        adapter = RecapGatewayForecastAdapter(client=client, resolver=_LiveResolver())
        result = adapter.fetch_forecasts(
            [_station(nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST)],
            ensure_utc(datetime.now(UTC)),
        )
        assert _SID in result
        assert getattr(result[_SID], "cycle_time", None) is not None


class TestRawClientForecastShape:
    """Raw-client shape checks for the fc (HRES) control member (subscribed)."""

    def test_fc_control_member_has_provenance_columns(
        self, client: RecapClient
    ) -> None:
        df = client.ecmwf.ifs_forecast(
            variable="tp",
            run_date=datetime.now(UTC),
            run_hour=0,
            hru_code=_HRU,
            ifs_type="fc",
        )
        assert len(df) > 0
        assert _POLYGON in df.columns
        assert "source" in df.columns
        assert "source_run" in df.columns


class TestRawClientReanalysisConversion:
    """Raw-client range check after our unit conversions, on the backfill window."""

    def test_precip_and_temperature_plausible_after_conversion(
        self, client: RecapClient
    ) -> None:
        precip_df = client.ecmwf.era5_land_reanalysis(
            variable="total_precipitation",
            start_date=_REAN_START,
            end_date=_REAN_END,
            hru_code=_HRU,
        )
        temp_df = client.ecmwf.era5_land_reanalysis(
            variable="2m_temperature",
            start_date=_REAN_START,
            end_date=_REAN_END,
            hru_code=_HRU,
        )
        precip_mm = (
            precip_df.drop(columns=["source", "source_run"], errors="ignore")
            .select_dtypes("number")
            .map(_metres_to_mm)
        )
        temp_c = (
            temp_df.drop(columns=["source", "source_run"], errors="ignore")
            .select_dtypes("number")
            .map(_kelvin_to_celsius)
        )
        assert (precip_mm.to_numpy() >= 0).all()
        assert (temp_c.to_numpy() > -60).all()
        assert (temp_c.to_numpy() < 60).all()


class TestSnowUnavailable:
    """Snow legs — xfail until JSNOW data is subscribed AND arriving for the HRU."""

    @pytest.mark.xfail(
        reason="JSNOW not delivering for the HRU yet; xpasses once snow arrives",
        strict=False,
    )
    @pytest.mark.parametrize("variable", ["hs", "rof", "swe"])
    def test_snow_reanalysis_shape(self, client: RecapClient, variable: str) -> None:
        df = client.snow.reanalysis(
            hru_code=_HRU,
            variable=variable,
            start_date=_REAN_START,
            end_date=_REAN_END,
            allow_missing_data=True,
        )
        assert len(df) > 0
