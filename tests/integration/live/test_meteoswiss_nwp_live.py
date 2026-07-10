"""Live MeteoSwiss STAC integration test for Plan 067 T5.

Excluded by default (marker live_stac). Run manually:
    uv run pytest tests/integration/live/test_meteoswiss_nwp_live.py -v -m live_stac

The test probes MeteoSwiss reachability; if unreachable, it skips
(does not fail) to avoid spurious CI failures on outages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import numpy as np
import pytest

from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter
from sapphire_flow.exceptions import NoCycleAvailableError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.weather import GriddedForecast

if TYPE_CHECKING:
    from pathlib import Path

STAC_BASE_URL = "https://data.geo.admin.ch/api/stac/v1"
STAC_COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch2"
COLLECTION_URL = f"{STAC_BASE_URL}/collections/{STAC_COLLECTION}"
SKIP_TIMEOUT = 5.0


def _meteoswiss_reachable() -> bool:
    """Skip-heuristic per Plan 067 D5: GET collection URL with 5 s timeout.

    Reachable (proceed) iff HTTP status is non-5xx. Network timeout, connect
    error, or 5xx -> skip (not fail).
    """
    try:
        with httpx.Client(timeout=SKIP_TIMEOUT) as client:
            resp = client.get(COLLECTION_URL)
    except httpx.HTTPError:
        return False
    return 200 <= resp.status_code < 500


@pytest.fixture(scope="module")
def skip_if_meteoswiss_unreachable() -> None:
    if not _meteoswiss_reachable():
        pytest.skip("MeteoSwiss STAC unreachable (Plan 067 T5/D5 skip heuristic)")


@pytest.fixture
def live_adapter(tmp_path: Path) -> MeteoSwissNwpAdapter:
    """Smoke-scope MeteoSwiss adapter.

    Plan 067 D2: max_fallback_steps=2 = ceil(12.0 / 6.0), matching the
    default DeploymentConfig.nwp_max_fallback_age_hours=12.0 policy.
    ICON-CH2-EPS publishes every 6 h (Plan 067 T1.b).

    The HTTP timeout (read=300 s) is sized for the smoke-scope workload
    (``max_files=4`` below, ~25-30 MB). Production callers construct their
    own client with longer timeouts to accommodate the full 489-file fetch
    per cycle.
    """
    return MeteoSwissNwpAdapter(
        stac_base_url=STAC_BASE_URL,
        stac_collection=STAC_COLLECTION,
        scratch_path=tmp_path / "nwp",
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0),
        ),
        max_fallback_steps=2,
        max_files=4,
        # Plan 105: live smoke test exercises STAC fetch, not disk limits.
        disk_guard_enabled=False,
    )


@pytest.mark.live_stac
class TestMeteoSwissLiveFetch:
    """Exercises the real MeteoSwiss endpoint with the current adapter."""

    def test_fetch_and_parse_smoke(
        self,
        skip_if_meteoswiss_unreachable: None,
        live_adapter: MeteoSwissNwpAdapter,
    ) -> None:
        """Smoke-scope live test: probe → resolve → fetch 4 files → parse → convert.

        Scope: max_files=4 covers 1 step × 2 variables (tp, t_2m) × 2 variants
        (ctrl, perturb) on MeteoSwiss's current iteration order. Verifies the
        live STAC endpoint still responds with the expected schema and that
        the full fetch+parse+convert chain works end-to-end against real data.

        Does NOT guarantee full-cycle correctness — that's covered by
        tests/unit/adapters/test_meteoswiss_nwp_real.py against committed
        ICON-CH2-EPS fixtures. Production validation is via the forecast-cycle
        invocation in Plan 046 §A3 step 8 (dress rehearsal).

        If MeteoSwiss changes item iteration order and this test becomes flaky,
        bump max_files to 8 (covers 2 steps → tolerates any single-step order
        oddity) before considering any deeper refactor.
        """
        now_utc = ensure_utc(datetime.now(tz=UTC))

        try:
            resolved = live_adapter.resolve_cycle_time(now_utc)
        except NoCycleAvailableError as exc:
            pytest.skip(
                f"Adapter reports no cycle within policy "
                f"(max_fallback_steps=2, age<=12h): {exc}"
            )

        # The gridded adapter ignores station_configs (noqa-flagged unused
        # arg in fetch_forecasts); pass an empty list.
        result = live_adapter.fetch_forecasts(
            station_configs=[],
            cycle_time=resolved,
        )

        # The gridded path returns GriddedForecast, not the per-station dict.
        assert isinstance(result, GriddedForecast)
        ds = result.values

        # Phase 1 confirmed the client-side allowlist is tp + t_2m. The
        # adapter's convert_raw_dataset renames these to precipitation and
        # temperature on the returned GriddedForecast.values dataset.
        assert "precipitation" in ds.data_vars, (
            f"Expected 'precipitation' (from tp allowlist) in fetched dataset; "
            f"got: {list(ds.data_vars)}"
        )
        assert "temperature" in ds.data_vars, (
            f"Expected 'temperature' (from t_2m allowlist) in fetched dataset; "
            f"got: {list(ds.data_vars)}"
        )

        # At least one time/step coord (hourly forecast horizon).
        assert ds.sizes.get("valid_time", 0) > 0 or ds.sizes.get("step", 0) > 0, (
            f"Expected at least one time/step coord; dims: {dict(ds.sizes)}"
        )

        # At least one finite value per variable across all dims.
        for var_name in ("precipitation", "temperature"):
            var = ds[var_name]
            assert np.isfinite(var.values).any(), (
                f"Variable {var_name!r} has no finite values"
            )
