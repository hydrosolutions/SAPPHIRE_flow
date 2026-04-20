"""Live-STAC smoke test for MeteoSwissNwpAdapter.

Gated behind the ``live_stac`` marker — skipped in normal CI. Run manually
(``uv run pytest -m live_stac``) after an adapter change that touches
fetch semantics, or on a schedule, to confirm that MeteoSwiss STAC hasn't
drifted away from the contract baked into the adapter (see
``docs/research/063-meteoswiss-stac-probe.md``).

Failure signals: STAC schema drift, MeteoSwiss pagination change, signed
URL format change, or the v0 allowlist tokens ceasing to be published.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.weather import GriddedForecast


@pytest.mark.live_stac
def test_fetch_latest_cycle_returns_gridded_forecast(tmp_path):  # type: ignore[no-untyped-def]
    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0),
    )
    adapter = MeteoSwissNwpAdapter(
        stac_base_url="https://data.geo.admin.ch/api/stac/v1",
        stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
        scratch_path=tmp_path,
        http_client=http_client,
    )
    now = ensure_utc(datetime.now(UTC))
    result = adapter.fetch_forecasts(station_configs=[], cycle_time=now)
    assert isinstance(result, GriddedForecast)
    assert result.nwp_source == "icon_ch2_eps"
    ds = result.values
    # v0 allowlist (tp + t_2m) — after convert_raw_dataset, tp becomes
    # `precipitation` and t_2m becomes `temperature`.
    assert {"precipitation", "temperature"}.issubset(set(ds.data_vars))
    assert "member" in ds.dims
    assert ds.sizes["member"] >= 20
