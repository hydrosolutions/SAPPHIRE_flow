"""Plan 174 (M-A5) task 4a — CLI: identity, bundle publication, manifest,
exit codes (D7/D9/D10). All fixtures are synthetic (constraint 1/2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest
import xarray as xr

from scripts.dhm_precip import extract_era5
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.era5_extract_manifest import current_pointer_path, points_root
from scripts.dhm_precip.era5_manifest import (
    AccumulationDiagnosticRecord,
    Era5ProvenanceManifest,
    OperatorProvenance,
    PackingAccounting,
    RawWindowRecord,
    TransformYearRecord,
    checksum_file,
    manifest_path_for,
    product_artifact_path,
    with_accumulation_diagnostic,
    with_raw_window,
    with_transform_year,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import STUDY_YEARS, expected_grid_shape
from scripts.dhm_precip.fixtures import (
    build_era5_land_product_fixture,
    write_era5_land_product_fixture,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

_AREA = (26.3, 85.0, 26.0, 85.3)
_LAT_COUNT, _LON_COUNT = expected_grid_shape(_AREA)
_CLOCK = lambda: datetime(2026, 8, 16, tzinfo=UTC)  # noqa: E731


class _FakeOrographyDownloader:
    def download(self, *, spec: object, dest_dir: Path) -> tuple[Path, ...]:  # noqa: ARG002
        target = dest_dir / "geopotential.nc"
        oro_lat = np.round(np.linspace(_AREA[2], _AREA[0], _LAT_COUNT), 10)
        oro_lon = np.round(np.linspace(_AREA[1], _AREA[3], _LON_COUNT), 10)
        elev = (
            1500.0
            + 10.0 * np.arange(_LAT_COUNT)[:, None]
            + 1.0 * np.arange(_LON_COUNT)[None, :]
        )
        phi = (elev * 9.80665).astype(np.float64)
        ds = xr.Dataset(
            {"z": (["latitude", "longitude"], phi)},
            coords={"latitude": oro_lat, "longitude": oro_lon},
        )
        ds.to_netcdf(target)
        return (target,)


def _fake_raw_reader(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with xr.open_dataset(path) as ds:
        return (
            np.asarray(ds["z"].values),
            np.asarray(ds["latitude"].values),
            np.asarray(ds["longitude"].values),
        )


def _build_data_root(tmp_path: Path, *, ramp_intercept: float = 0.5) -> Path:
    data_root = tmp_path / "dhm_precip"
    provenance = OperatorProvenance(
        cds_portal_url="https://example.org/portal",
        dataset_landing_page_url="https://example.org/dataset",
        licence_name="Licence",
        licence_version="1.0",
        licence_accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    manifest = Era5ProvenanceManifest(
        dataset="reanalysis-era5-land",
        client_package_version="0.7.7",
        operator_provenance=provenance,
    )
    for year in STUDY_YEARS:
        ds = build_era5_land_product_fixture(
            year=year, area=_AREA, ramp_intercept=ramp_intercept
        )
        path = product_artifact_path(year, data_root)
        write_era5_land_product_fixture(ds, path)
        sha = checksum_file(path)
        manifest = with_transform_year(
            manifest,
            TransformYearRecord(
                product_year=year,
                transform_identity="x",
                sha256=sha,
                accumulation_convention="x",
                units_conversion="x",
                packing=PackingAccounting(
                    packing_corrected_cells=0,
                    max_correction_mm=0.0,
                    mass_adjustment_mm=0.0,
                ),
                non_finite_cell_count=0,
                dropped_boundary_stamp=None,
                transformed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    # B5 — the passing predicate now cross-checks the diagnostic's
    # `source_sha256` against the manifest's OWN raw-window record for that
    # window, so the fixture must carry that provenance.
    manifest = with_raw_window(
        manifest,
        RawWindowRecord(
            window_id="2021-10",
            dataset="reanalysis-era5-land",
            request_payload={},
            raw_request_identity="r",
            sha256="a" * 64,
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    diag = AccumulationDiagnosticRecord(
        window_id="2021-10",
        source_sha256="a" * 64,
        reset_hour=1,
        terminal_hour=0,
        monotone_within_day=True,
        sample_size_days=31,
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    manifest = with_accumulation_diagnostic(manifest, diag)
    write_manifest_atomic(manifest, manifest_path_for(data_root))

    coords_path = data_root / "station_coordinates.csv"
    pl.DataFrame(
        {
            "station": ["A", "B"],
            "excel_col": ["A (mm)", "B (mm)"],
            "lat": [26.05, 26.2],
            "lon": [85.05, 85.2],
            "elev": [1500.0, 1900.0],
        }
    ).write_csv(coords_path)
    return data_root


# The synthetic box holds 2 stations, not the production 26, so the D8/2d
# cardinality tripwire is re-pinned on the injected frozen parameter object
# (never bypassed — the CLI still enforces whatever the object says).
_TEST_PARAMS = replace(DEFAULT_PARAMS, expected_station_count=2)


def _run_all(
    data_root: Path,
    *,
    ramp_intercept: float = 0.5,  # noqa: ARG001
    expected_stations: frozenset[Station] | None = None,
    params: object | None = None,
) -> int:
    # Through `main()` (not `run()` directly) so exceptions are translated
    # to exit codes exactly as the real CLI entry point does (D9).
    return extract_era5.main(
        ["--stage", "all", "--data-root", str(data_root)],
        clock=_CLOCK,
        orography_downloader=_FakeOrographyDownloader(),
        orography_raw_reader=_fake_raw_reader,
        coords_path=data_root / "station_coordinates.csv",
        expected_stations=(
            expected_stations
            if expected_stations is not None
            else frozenset({Station("A"), Station("B")})
        ),
        request_area=_AREA,
        params=params if params is not None else _TEST_PARAMS,
    )


class TestCliHelp:
    def test_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            extract_era5.build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0


class TestPublishBundle:
    def test_happy_path_publishes_and_sets_current(self, tmp_path: Path) -> None:
        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        current = current_pointer_path(data_root).read_text().strip()
        published = points_root(data_root) / current
        for name in (
            "series_nearest.nc",
            "series_bilinear.nc",
            "station_grid_elevation.csv",
            "operator_sensitivity.csv",
            "extraction_manifest.json",
        ):
            assert (published / name).exists(), name
        elevation = pl.read_csv(published / "station_grid_elevation.csv")
        assert elevation.height == 2

    def test_rerun_with_unchanged_inputs_adopts_the_same_identity(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        first_identity = current_pointer_path(data_root).read_text().strip()
        assert _run_all(data_root) == 0
        second_identity = current_pointer_path(data_root).read_text().strip()
        assert first_identity == second_identity
        # Exactly one identity directory (the re-run adopted it, it did not
        # quarantine a good bundle as an orphan).
        identity_dirs = [
            p.name
            for p in points_root(data_root).iterdir()
            if p.is_dir() and p.name != ".staging"
        ]
        assert identity_dirs == [first_identity]

    def test_changed_input_yields_a_new_identity_and_the_old_one_survives(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_data_root(tmp_path, ramp_intercept=0.5)
        assert _run_all(data_root, ramp_intercept=0.5) == 0
        first_identity = current_pointer_path(data_root).read_text().strip()

        # Changing a value-affecting input (a source sha256, via a different
        # ramp) must change extraction_identity — the source product's own
        # sha256 is part of the identity. Rebuild with a different ramp
        # BEFORE the second run so the manifest's sha256s genuinely differ.
        data_root2 = _build_data_root(
            tmp_path.with_name(tmp_path.name + "-2"), ramp_intercept=9.0
        )
        assert _run_all(data_root2, ramp_intercept=9.0) == 0
        second_identity = current_pointer_path(data_root2).read_text().strip()
        assert first_identity != second_identity

    def test_no_passing_diagnostic_record_exits_4(self, tmp_path: Path) -> None:
        data_root = _build_data_root(tmp_path)
        # Overwrite the manifest with one that carries NO diagnostic record.
        from scripts.dhm_precip.era5_manifest import read_manifest

        manifest = read_manifest(manifest_path_for(data_root))
        assert manifest is not None
        stripped = manifest.__class__(
            dataset=manifest.dataset,
            client_package_version=manifest.client_package_version,
            operator_provenance=manifest.operator_provenance,
            raw_windows=manifest.raw_windows,
            transformed_years=manifest.transformed_years,
            accumulation_diagnostics={},
        )
        write_manifest_atomic(stripped, manifest_path_for(data_root))
        assert _run_all(data_root) == 4

    def test_inventory_of_the_wrong_size_exits_4_before_any_extraction(
        self, tmp_path: Path
    ) -> None:
        """D8/2d (blocker) — the count is pinned INDEPENDENTLY of the
        inventory. Here the coordinate table and the inventory agree
        perfectly (they are both {A}), which is exactly the state the old
        equality check waved through; the pinned count of 2 must still stop
        the run, and stop it before anything is published."""
        data_root = _build_data_root(tmp_path)
        pl.DataFrame(
            {
                "station": ["A"],
                "excel_col": ["A (mm)"],
                "lat": [26.05],
                "lon": [85.05],
                "elev": [1500.0],
            }
        ).write_csv(data_root / "station_coordinates.csv")
        assert _run_all(data_root, expected_stations=frozenset({Station("A")})) == 4
        assert not current_pointer_path(data_root).exists()

    def test_station_outside_grid_exits_4(self, tmp_path: Path) -> None:
        data_root = _build_data_root(tmp_path)
        coords_path = data_root / "station_coordinates.csv"
        pl.DataFrame(
            {
                "station": ["A", "B"],
                "excel_col": ["A (mm)", "B (mm)"],
                "lat": [40.0, 26.2],  # A is far outside the box
                "lon": [85.05, 85.2],
                "elev": [1500.0, 1900.0],
            }
        ).write_csv(coords_path)
        assert _run_all(data_root) == 4

    def test_missing_orography_spec_route_is_a_typed_orography_failure(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_data_root(tmp_path)

        class _FailingDownloader:
            def download(self, *, spec: object, dest_dir: Path) -> tuple[Path, ...]:  # noqa: ARG002
                from scripts.dhm_precip.era5_errors import Era5OrographyError

                raise Era5OrographyError("no route reachable in this window")

        parser = extract_era5.build_parser()
        args = parser.parse_args(
            ["--stage", "orography", "--data-root", str(data_root)]
        )
        with pytest.raises(Exception, match="no route reachable"):
            extract_era5.run(
                args,
                clock=_CLOCK,
                orography_downloader=_FailingDownloader(),
                orography_raw_reader=_fake_raw_reader,
                request_area=_AREA,
            )
