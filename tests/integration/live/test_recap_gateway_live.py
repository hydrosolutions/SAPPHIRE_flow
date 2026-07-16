"""Live recap Data Gateway smoke tests (Plan 082 Task 1B).

Excluded by default (markers ``live`` + ``live_recap``); skipped whenever
``RECAP_API_KEY`` is absent so this never blocks default CI. Run manually:

    RECAP_API_KEY=... uv run pytest tests/integration/live/test_recap_gateway_live.py \
        -m 'live and live_recap' -v

Covers: fc/pf member=1 shape, member-bound rejections (0/51), precip/
temperature range after conversion, snow endpoint shape (hs/rof/swe), the
Task 1C fixture GeoPackage's ``g_<...>`` column echo + one-column-per-band
behavior, and the ``source``/``source_run`` provenance columns.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from recap_client import ApiClientConfig, ApiValidationError, RecapClient

from sapphire_flow.adapters.recap_gateway import (
    _kelvin_to_celsius,  # pyright: ignore[reportPrivateUsage]
    _metres_to_mm,  # pyright: ignore[reportPrivateUsage]
)

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "recap"
_JSON_FIXTURE = _FIXTURE_DIR / "compliant_test_basins.json"

_RECAP_API_KEY = os.environ.get("RECAP_API_KEY")
_RECAP_BASE_URL = os.environ.get("RECAP_BASE_URL", "https://recap.ieasyhydro.org")

pytestmark = [
    pytest.mark.live,
    pytest.mark.live_recap,
    pytest.mark.skipif(
        _RECAP_API_KEY is None,
        reason="RECAP_API_KEY not set — skipped by default (Plan 082 Task 1B)",
    ),
]


@pytest.fixture(scope="module")
def client() -> RecapClient:
    config = ApiClientConfig(base_url=_RECAP_BASE_URL, api_key=_RECAP_API_KEY)
    return RecapClient(config)


@pytest.fixture(scope="module")
def hru_code() -> str:
    fixture = json.loads(_JSON_FIXTURE.read_text())
    return str(fixture["hru_name"])


@pytest.fixture(scope="module")
def band_names() -> list[str]:
    fixture = json.loads(_JSON_FIXTURE.read_text())
    return sorted(n for n in fixture["names"] if "_band_" in n)


class TestIfsForecastShape:
    def test_fc_member_shape(self, client: RecapClient, hru_code: str) -> None:
        now = datetime.now(UTC)
        df = client.ecmwf.ifs_forecast(
            variable="tp",
            run_date=now,
            run_hour=0,
            hru_code=hru_code,
            ifs_type="fc",
        )
        assert len(df) > 0
        assert "source" in df.columns
        assert "source_run" in df.columns

    def test_pf_member_1_shape(self, client: RecapClient, hru_code: str) -> None:
        now = datetime.now(UTC)
        df = client.ecmwf.ifs_forecast(
            variable="tp",
            run_date=now,
            run_hour=0,
            hru_code=hru_code,
            ifs_type="pf",
            member="1",
        )
        assert len(df) > 0

    def test_member_0_pf_is_rejected(self, client: RecapClient, hru_code: str) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ApiValidationError):
            client.ecmwf.ifs_forecast(
                variable="tp",
                run_date=now,
                run_hour=0,
                hru_code=hru_code,
                ifs_type="pf",
                member="0",
            )

    def test_member_51_pf_is_rejected(self, client: RecapClient, hru_code: str) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ApiValidationError):
            client.ecmwf.ifs_forecast(
                variable="tp",
                run_date=now,
                run_hour=0,
                hru_code=hru_code,
                ifs_type="pf",
                member="51",
            )


class TestReanalysisRangeAfterConversion:
    def test_precipitation_and_temperature_plausible_range(
        self, client: RecapClient, hru_code: str
    ) -> None:
        end = datetime.now(UTC)
        start = end - timedelta(days=2)
        precip_df = client.ecmwf.era5_land_reanalysis(
            variable="total_precipitation",
            start_date=start,
            end_date=end,
            hru_code=hru_code,
        )
        temp_df = client.ecmwf.era5_land_reanalysis(
            variable="2m_temperature",
            start_date=start,
            end_date=end,
            hru_code=hru_code,
        )
        numeric_precip = precip_df.drop(
            columns=["source", "source_run"], errors="ignore"
        )
        numeric_temp = temp_df.drop(columns=["source", "source_run"], errors="ignore")
        precip_mm = numeric_precip.select_dtypes("number").map(_metres_to_mm)
        temp_c = numeric_temp.select_dtypes("number").map(_kelvin_to_celsius)
        assert (precip_mm.to_numpy() >= 0).all()
        assert (temp_c.to_numpy() > -60).all()
        assert (temp_c.to_numpy() < 60).all()


class TestSnowEndpointShape:
    @pytest.mark.parametrize("variable", ["hs", "rof", "swe"])
    def test_snow_reanalysis_shape(
        self, client: RecapClient, hru_code: str, variable: str
    ) -> None:
        end = datetime.now(UTC)
        start = end - timedelta(days=2)
        df = client.snow.reanalysis(
            hru_code=hru_code,
            variable=variable,
            start_date=start,
            end_date=end,
        )
        assert len(df) > 0
        assert "source" in df.columns
        assert "source_run" in df.columns


class TestBandColumnEcho:
    def test_band_polygon_names_echoed_as_columns(
        self, client: RecapClient, hru_code: str, band_names: list[str]
    ) -> None:
        end = datetime.now(UTC)
        start = end - timedelta(days=2)
        df = client.ecmwf.era5_land_reanalysis(
            variable="total_precipitation",
            start_date=start,
            end_date=end,
            hru_code=hru_code,
        )
        columns = set(df.columns)
        assert set(band_names).issubset(columns), (
            f"expected one column per band {band_names!r}; got {sorted(columns)!r}"
        )
