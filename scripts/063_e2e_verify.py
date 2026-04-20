# ruff: noqa: T201, S101, E501
"""Plan 063 end-to-end fetch-path verification.

Runs the MeteoSwissNwpAdapter against the live STAC endpoint and asserts
fetch-path exit criteria — file count within budget, every filename in
the allowlist, scratch usage ≤ 4 GB, and wall time < 10 min. Exits 0 on
PASS, non-zero on assertion failure.

Usage (local shell, outside Docker):

    uv run python scripts/063_e2e_verify.py

Usage (dev Docker Compose stack):

    DB_PASSWORD=$(cat ./secrets/db_password)
    docker compose exec -T \
        -e DATABASE_URL="postgresql+psycopg://sapphire:${DB_PASSWORD}@postgres:5432/sapphire" \
        -e SAPPHIRE_CONFIG=/app/config.toml \
        -e PREFECT_API_URL=http://prefect-server:4200/api \
        prefect-worker uv run python scripts/063_e2e_verify.py

Requires network access to `data.geo.admin.ch`. Downloads ~1 GB of GRIB2
files for the v0 allowlist (precipitation + temperature). Not a CI test
— run manually after any change to `MeteoSwissNwpAdapter`.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import time
from datetime import UTC, datetime

import httpx

from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter


def main() -> int:
    scratch = pathlib.Path("/tmp/meteoswiss_nwp_e2e")
    scratch.mkdir(parents=True, exist_ok=True)

    adapter = MeteoSwissNwpAdapter(
        stac_base_url="https://data.geo.admin.ch/api/stac/v1",
        stac_collection="ch.meteoschweiz.ogd-forecasting-icon-ch2",
        scratch_path=scratch,
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0)
        ),
    )

    t0 = time.monotonic()
    now = datetime.now(UTC)
    cycle = adapter.resolve_cycle_time(now)
    print(f"resolved cycle: {cycle.isoformat()}")

    free_before = shutil.disk_usage(scratch).free
    result = adapter.fetch_forecasts(station_configs=[], cycle_time=cycle)
    elapsed = time.monotonic() - t0
    free_after = shutil.disk_usage(scratch).free

    cycle_dir = scratch / cycle.strftime("%Y%m%dT%H%M")
    downloaded = list(cycle_dir.rglob("*.grib2"))
    allowlist = {tok for tok, _, _ in adapter.PARAM_GROUPS}

    # (a) file count within budget: 2 vars × 2 types × 121 steps = 484 max
    assert len(downloaded) <= 484, f"too many files: {len(downloaded)}"
    # (b) every filename contains an allowlisted stac_token
    bad = [f for f in downloaded if not any(f"-{tok}-" in f.name for tok in allowlist)]
    assert not bad, f"non-allowlisted files: {bad[:5]}"
    # (c) scratch usage within cap (must not have consumed > 4 GB)
    consumed_gb = (free_before - free_after) / 1024**3
    assert consumed_gb <= 4.0, f"scratch consumed {consumed_gb:.1f} GB"
    # (d) per-cycle directory present
    assert cycle_dir.exists(), f"expected per-cycle dir {cycle_dir}"
    # (e) wall time
    assert elapsed < 600, f"fetch took {elapsed:.0f}s, expected < 600s"

    # (f) GriddedForecast contract
    ds = result.values  # type: ignore[union-attr]
    expected_vars = {"precipitation", "temperature"}
    assert expected_vars.issubset(set(ds.data_vars)), (
        f"expected {expected_vars}, got {set(ds.data_vars)}"
    )
    assert "member" in ds.dims and ds.sizes["member"] >= 20, (
        f"expected member dim ≥ 20, got {ds.sizes.get('member')}"
    )

    print(
        f"PASS: {len(downloaded)} files, {consumed_gb:.2f} GB, "
        f"{elapsed:.0f}s, members={ds.sizes['member']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
