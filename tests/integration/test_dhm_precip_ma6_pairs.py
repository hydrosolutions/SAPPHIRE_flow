"""Finding 2 (Plan 184 T1 independent review, 2026-08-20) — a real-data
regression test over T1's production path.

The prescribed T1 Verify line (`tests/unit/scripts/test_ma6_pairs.py` +
`tests/integration/test_dhm_precip_reproduction.py`) cannot fail if T1's
own production wiring breaks: the unit suite never imports
`load_gauge_masked_population` or any combined production entry point
(only the pure `build_gauge_masked_population` core and hand-built
fixtures), and the reproduction gate never imports `ma6_pairs` at all. So
production workbook loading, bundle discovery, masking, exclusion and
pairing could all break while both prescribed gates stayed green —
including D11's "measured empty" assurance, which is only meaningful
against the real delivery.

This test closes that gap: it exercises the real production path
end-to-end — load the real workbook, discover the published precipitation
bundle, build the gauge-only population, build the paired population —
gated on `DHM_PRECIP_ERA5_ROOT` + `DHM_PRECIP_XLSX` exactly like the
existing gated real-data tests (skipif, no other skip condition; see
`test_dhm_precip_era5_extraction.py` and `test_dhm_precip_reproduction.py`
for the same pattern). This is a test-only addition — no runner, no CLI;
T6 (Plan 184) still owns the runner.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.dhm_precip.ma6_pairs import (
    build_paired_population,
    discover_precip_bundle,
    load_gauge_masked_population,
)

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DHM_PRECIP_ERA5_ROOT") and os.environ.get("DHM_PRECIP_XLSX")),
    reason=(
        "DHM_PRECIP_ERA5_ROOT and DHM_PRECIP_XLSX unset — the only skip condition here"
    ),
)


def test_production_path_pairs_every_live_station_against_the_published_bundle() -> (
    None
):
    population = load_gauge_masked_population()

    assert len(population.by_station) == 26, "expected 26/26 live stations retained"
    assert population.excluded == (), (
        "the exclusion list is expected EMPTY on this delivery (D11) — a "
        "MEASURED result, not a skipped step"
    )
    assert len(population.accounting) > 0, (
        "non-empty accounting rows are the evidence the exclusion "
        "computation actually ran, distinguishing measured-empty from "
        "not-computed (D11)"
    )

    data_root = Path(os.environ["DHM_PRECIP_ERA5_ROOT"])
    bundle_dir, _manifest = discover_precip_bundle(data_root)

    paired = build_paired_population(population, bundle_dir)

    assert set(paired) == set(population.by_station)
    total_common_rows = sum(series.frame.height for series in paired.values())
    assert total_common_rows > 0, (
        "the paired population must have a positive common-row count "
        "across stations — an empty pairing would mean the join, the "
        "bundle discovery, or the mask silently produced nothing (D2)"
    )


if __name__ == "__main__":
    pytest.main([__file__])
