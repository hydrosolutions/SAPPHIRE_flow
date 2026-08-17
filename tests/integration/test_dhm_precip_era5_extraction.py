"""Plan 174 (M-A5) task 4b — the real-data extraction gate.

D10: `DHM_PRECIP_ERA5_ROOT` unset is the ONLY skip condition. When set, this
test does not special-case any failure mode — `extract_era5.run()` itself
already enforces every D10 condition (file count, checksum, D9 schema on
reopen, temporal coverage, the diagnostic record, station bounds,
non-finite values) as a hard raise, so an uncaught exception here IS the
"fail, never skip, never warn" behaviour D10 requires. `run()` is called
directly (not `main()`) so a failure surfaces as a real pytest failure, not
a swallowed exit code.

Also requires `DHM_PRECIP_XLSX` (2d's workbook-derived station-set boundary
input) unless a coordinate table + expected-station set are separately
injected — in practice both env vars are set together for a real run.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from scripts.dhm_precip import extract_era5
from scripts.dhm_precip.era5_extract_manifest import points_root

pytestmark = pytest.mark.skipif(
    not os.environ.get("DHM_PRECIP_ERA5_ROOT"),
    reason="DHM_PRECIP_ERA5_ROOT unset — the only skip condition here (D10)",
)


def test_real_data_extraction_publishes_a_complete_bundle() -> None:
    from pathlib import Path

    import polars as pl
    import xarray as xr

    data_root = Path(os.environ["DHM_PRECIP_ERA5_ROOT"])
    args = extract_era5.build_parser().parse_args(
        ["--stage", "all", "--data-root", str(data_root)]
    )
    exit_code = extract_era5.run(args, clock=lambda: datetime.now(UTC))
    assert exit_code == 0

    # P6 — discovery is a documented convention, not production code: the
    # highest `NNNN` whose manifest is present. There is no `CURRENT`
    # pointer (P2).
    candidates = sorted(
        (
            p
            for p in points_root(data_root).iterdir()
            if p.is_dir() and p.name != ".staging"
        ),
        key=lambda p: p.name,
    )
    published = next(
        p for p in reversed(candidates) if (p / "extraction_manifest.json").exists()
    )

    elevation = pl.read_csv(published / "station_grid_elevation.csv")
    assert elevation.height == 26, "expected exactly 26 stations (D8)"
    assert set(elevation["orography_source"].unique()) <= {
        "MODEL_OROGRAPHY",
        "DEM_PROXY",
    }

    sensitivity = pl.read_csv(published / "operator_sensitivity.csv")
    assert sensitivity.height > 0

    with xr.open_dataset(published / "series_nearest.nc", engine="h5netcdf") as ds:
        loaded = ds.load()
        assert loaded.sizes["station"] == 26
        # D11.2: the primary (nearest) series must be entirely finite —
        # `run()` would already have raised NonFiniteExtractionError
        # otherwise, so this is a closing structural confirmation.
        import numpy as np

        assert bool(np.isfinite(loaded["precipitation_mm_per_h"].values).all())

    manifest_path = published / "extraction_manifest.json"
    assert manifest_path.exists()
