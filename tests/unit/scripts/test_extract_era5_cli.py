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
from scripts.dhm_precip.era5_extract_manifest import points_root
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


def _published_dirs(data_root: Path) -> list[Path]:
    root = points_root(data_root)
    if not root.exists():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name != ".staging"),
        key=lambda p: p.name,
    )


def _latest_published_dir(data_root: Path) -> Path:
    """P6 — discovery is a documented convention, not production code: the
    highest `NNNN` whose manifest is present. Tests inspect the directory
    layout directly (the accepted consequence of P6 — see the plan)."""
    for candidate in reversed(_published_dirs(data_root)):
        if (candidate / "extraction_manifest.json").exists():
            return candidate
    raise AssertionError(f"no published bundle found under {points_root(data_root)}")


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


class TestExitCodeDispatch:
    """D9 (MINOR, 2026-08-16) — `Era5AcquisitionError` preceded its own
    subclass `Era5StorageError` in the dispatch table, so every storage
    failure exited 4 (a post-condition failure) instead of 5; and a raw
    `OSError` escaped `main()` entirely, surfacing as an uncaught traceback
    rather than the documented storage exit code."""

    def test_storage_error_maps_to_5_not_4(self) -> None:
        from scripts.dhm_precip.era5_errors import Era5StorageError

        assert extract_era5._exit_code_for(Era5StorageError("disk full")) == 5

    def test_every_subclass_precedes_its_base_in_the_dispatch_table(self) -> None:
        table = extract_era5._EXIT_BY_ERROR
        for i, (exc_type, _code) in enumerate(table):
            for later_type, _later_code in table[i + 1 :]:
                assert not (
                    issubclass(later_type, exc_type) and later_type is not exc_type
                ), (
                    f"{later_type.__name__} is a SUBCLASS of {exc_type.__name__}, "
                    "which is listed before it — the first match wins, so the "
                    "subclass entry can never be reached"
                )

    def test_a_raw_oserror_exits_5(self, tmp_path: Path) -> None:
        data_root = _build_data_root(tmp_path)

        class _ExplodingDownloader:
            def download(self, *, spec: object, dest_dir: Path) -> tuple[Path, ...]:  # noqa: ARG002
                raise OSError("no space left on device")

        code = extract_era5.main(
            ["--stage", "orography", "--data-root", str(data_root)],
            clock=_CLOCK,
            orography_downloader=_ExplodingDownloader(),
            orography_raw_reader=_fake_raw_reader,
            coords_path=data_root / "station_coordinates.csv",
            expected_stations=frozenset({Station("A"), Station("B")}),
            request_area=_AREA,
            params=_TEST_PARAMS,
        )
        assert code == 5

    def test_missing_annual_product_exits_2_not_5(self, tmp_path: Path) -> None:
        """MINOR (2026-08-17) — a missing annual product used to reach
        `checksum_file`, which wraps `FileNotFoundError` as
        `Era5StorageError` (exit 5). D9 assigns a missing product to exit
        code 2 (inputs absent), not a storage failure."""
        from scripts.dhm_precip.era5_manifest import product_artifact_path
        from scripts.dhm_precip.era5_request import STUDY_YEARS

        data_root = _build_data_root(tmp_path)
        product_artifact_path(STUDY_YEARS[0], data_root).unlink()
        assert _run_all(data_root) == 2


class TestCliHelp:
    def test_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            extract_era5.build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0


class TestPublishBundle:
    def test_happy_path_publishes_a_numbered_bundle(self, tmp_path: Path) -> None:
        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        published = _latest_published_dir(data_root)
        assert published.name.startswith("0000-")
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

    def test_rerun_with_unchanged_inputs_publishes_a_new_numbered_directory(
        self, tmp_path: Path
    ) -> None:
        """P1/P1a/P2 (redesigned 2026-08-17) — there is no adoption, no
        quarantine and no `CURRENT` pointer. A re-run with identical inputs
        computes the SAME `extraction_identity` but is allocated the NEXT
        free run number; the first bundle is left completely untouched, and
        no `.orphan-*` path is ever created (the round-3 window is
        unrepresentable now — tested here by absence)."""
        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        first = _latest_published_dir(data_root)
        first_identity = first.name.split("-", 1)[1]

        assert _run_all(data_root) == 0
        second = _latest_published_dir(data_root)
        second_identity = second.name.split("-", 1)[1]
        assert first_identity == second_identity
        assert first.name != second.name

        published = sorted(p.name for p in _published_dirs(data_root))
        assert published == [f"0000-{first_identity}", f"0001-{first_identity}"]
        assert not any("orphan" in name for name in published)
        # The first bundle survives, complete and unmodified.
        assert (first / "extraction_manifest.json").exists()
        # No staging directory is left behind by the publish.
        assert not (points_root(data_root) / ".staging" / first_identity).exists()

    def test_changed_input_yields_a_new_identity_and_the_old_one_survives(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_data_root(tmp_path, ramp_intercept=0.5)
        assert _run_all(data_root, ramp_intercept=0.5) == 0
        first_identity = _latest_published_dir(data_root).name.split("-", 1)[1]

        # Changing a value-affecting input (a source sha256, via a different
        # ramp) must change extraction_identity — the source product's own
        # sha256 is part of the identity. Rebuild with a different ramp
        # BEFORE the second run so the manifest's sha256s genuinely differ.
        data_root2 = _build_data_root(
            tmp_path.with_name(tmp_path.name + "-2"), ramp_intercept=9.0
        )
        assert _run_all(data_root2, ramp_intercept=9.0) == 0
        second_identity = _latest_published_dir(data_root2).name.split("-", 1)[1]
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
        assert _published_dirs(data_root) == []

    def test_manifest_carries_the_full_required_provenance(
        self, tmp_path: Path
    ) -> None:
        """D9/D11 (MAJOR, 2026-08-16) — the manifest omitted required
        provenance: only four `OrographySpec` fields were serialised, the
        source record's paths/sha256s/sizes were collapsed to `n_files`, the
        diagnostic's terminal hour / sample size / timestamp were dropped,
        and D11's per-station accounting was absent entirely."""
        import json

        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        published = _latest_published_dir(data_root)
        manifest = json.loads((published / "extraction_manifest.json").read_text())

        # 1. EVERY OrographySpec field, not four of them.
        spec = manifest["orography_spec"]
        for field_name in (
            "source",
            "product_id",
            "product_version",
            "download_url",
            "licence_name",
            "licence_version",
            "licence_url",
            "source_crs",
            "vertical_reference",
            "units",
            "no_data_sentinel",
            "aggregation_rule_id",
            "conversion_rule",
            "probe_date",
            "rejected_candidates",
        ):
            assert field_name in spec, field_name
        assert spec["licence_url"] == OBSERVED_OROGRAPHY_SPEC.licence_url

        # 2. The source record's per-file path/sha256/size, not `n_files`.
        source_record = manifest["orography_source_record"]
        assert source_record["downloaded_files"]
        for entry in source_record["downloaded_files"]:
            assert set(entry) >= {"path", "sha256", "size_bytes"}
            assert entry["size_bytes"] > 0
        assert source_record["raster_sha256"]
        assert source_record["orography_route_identity"]

        # 3. The cited diagnostic, whole.
        diagnostic = manifest["accumulation_diagnostic"]
        assert set(diagnostic) >= {
            "window_id",
            "source_sha256",
            "reset_hour",
            "terminal_hour",
            "monotone_within_day",
            "sample_size_days",
            "recorded_at",
        }

        # 4. D11's per-station accounting, per operator.
        accounting = manifest["station_accounting"]
        assert set(accounting) == {"NEAREST", "BILINEAR"}
        for per_operator in accounting.values():
            assert set(per_operator) == {"A", "B"}
            for entry in per_operator.values():
                assert set(entry) >= {
                    "n_hours",
                    "n_finite",
                    "n_nan",
                    "first_nan_valid_time",
                    "last_nan_valid_time",
                }
                assert entry["n_hours"] == entry["n_finite"] + entry["n_nan"]
                assert entry["n_nan"] == 0
                assert entry["first_nan_valid_time"] is None

    def test_published_series_follow_the_d9_netcdf_schema(self, tmp_path: Path) -> None:
        """D9/D5.0 (MAJOR, 2026-08-16) — the D9 NetCDF schema was not
        implemented: `station` used h5netcdf's VARIABLE-length string
        default instead of a fixed-length string coord, and there was no
        semantic UTC attribute on `valid_time`.

        D5.0 is deliberately NOT "fixed" here: on-disk time stays CF-encoded
        and timezone-NAIVE, because a tz-aware coordinate cannot be written
        by the pinned encoder and a validator rejecting a naive axis would
        reject every real M-A4 product.
        """
        import h5py

        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        published = _latest_published_dir(data_root)

        for name in ("series_nearest.nc", "series_bilinear.nc"):
            path = published / name
            with h5py.File(path, "r") as raw:
                station = raw["station"]
                # Fixed-length: an |S1 char array over a string dimension,
                # NOT h5py's variable-length string special dtype.
                assert h5py.check_string_dtype(station.dtype) is None or (
                    h5py.check_string_dtype(station.dtype).length is not None
                ), f"{name}: station is a variable-length string"
                assert station.dtype.kind == "S", name
                assert station.ndim == 2, name

            with xr.open_dataset(path, engine="h5netcdf") as reopened:
                loaded = reopened.load()
                assert loaded["valid_time"].attrs.get("timezone") == "UTC", name
                # D5.0 — still naive on disk, denoting UTC.
                assert np.issubdtype(loaded["valid_time"].dtype, np.datetime64), name
                enc = loaded["valid_time"].encoding
                assert str(enc["units"]).startswith("hours since 1970-01-01"), name
                assert str(enc["dtype"]) == "int64", name
                penc = loaded["precipitation_mm_per_h"].encoding
                assert penc["zlib"] is True, name
                assert penc["complevel"] == 4, name
                assert penc["chunksizes"] is not None, name
                assert np.isnan(float(penc["_FillValue"])), name
                assert str(penc["dtype"]) == "float32", name

    def test_the_identity_covers_the_full_encoding_spec(self) -> None:
        """D7 — the identity's "full encoding spec" omitted the
        precipitation fill value, compression and chunking, and the station
        encoding entirely, so a change to any of them produced the same
        identity and the stale bundle was reused."""
        spec = extract_era5.POINTS_OUTPUT_ENCODING_SPEC
        assert set(spec["precipitation_mm_per_h"]) >= {
            "dtype",
            "zlib",
            "complevel",
            "chunk_stations",
            "chunk_hours",
            "_FillValue",
        }
        assert set(spec["valid_time"]) >= {"units", "dtype", "semantic_timezone_attr"}
        assert set(spec["station"]) >= {"dtype", "fixed_length"}

    def test_changing_any_encoding_field_changes_the_identity(self) -> None:
        from scripts.dhm_precip.era5_extract_manifest import extraction_identity

        base_kwargs: dict[str, object] = {
            "operator_id": "NEAREST",
            "coordinate_table_sha256": "a" * 64,
            "source_sha256s": ("b" * 64,),
            "orography_identity": "c" * 64,
            "jjas_months": (6, 7, 8, 9),
            "djf_months": (12, 1, 2),
            "mam_months": (3, 4, 5),
            "on_months": (10, 11),
            "wet_threshold_mm_per_h": 0.2,
            "wet_threshold_side": ">=",
            "zero_policy": "exclude_zero",
            "quantile_definition": "linear",
            "quantile_grid": (0.5,),
            "station_elevation_datum": "UNKNOWN",
            "orography_elevation_datum": "LOCAL_MSL",
            "output_schema_version": "1",
            "output_format": "netcdf4_h5netcdf",
            "output_dtype": "float32",
        }
        spec = extract_era5.POINTS_OUTPUT_ENCODING_SPEC
        base = extraction_identity(**base_kwargs, output_encoding=spec)  # type: ignore[arg-type]
        for variable, fields in spec.items():
            for field_name in fields:
                mutated = {k: dict(v) for k, v in spec.items()}
                mutated[variable][field_name] = "MUTATED"
                changed = extraction_identity(**base_kwargs, output_encoding=mutated)  # type: ignore[arg-type]
                assert base != changed, f"{variable}.{field_name} is not hashed"

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


class TestOrographyRouteChangeForcesRematerialisation:
    """D7 (BLOCKER, 2026-08-17) — the reuse guard checked the wrong thing.
    The CLI reused ANY `OrographySourceRecord` that merely had a
    `raster_path` set, never comparing its `orography_route_identity` to the
    CURRENT frozen spec. A changed spec (new URL, product version, vertical
    reference, conversion rule) therefore reused the raster built from the
    OLD route while the extraction manifest serialised the NEW one — the
    artefact stating provenance it does not have, which is the defect class
    the identity split existed to remove.

    A changed spec is a legitimate, expected event, so the required response
    is RE-MATERIALISATION, not a raised error.
    """

    def test_changed_spec_rematerialises_and_the_manifest_matches_the_new_route(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        from dataclasses import replace as dataclass_replace

        from scripts.dhm_precip.era5_orography import (
            orography_raster_path,
            orography_route_identity,
            orography_source_record_path,
            read_orography_source_record,
        )
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        route_a = orography_route_identity(OBSERVED_OROGRAPHY_SPEC)
        record_a = read_orography_source_record(orography_source_record_path(data_root))
        assert record_a is not None
        assert record_a.orography_route_identity == route_a

        # The frozen spec is re-probed: same bytes at a new product version.
        spec_b = dataclass_replace(
            OBSERVED_OROGRAPHY_SPEC, product_version="re-probed 2026-08-17"
        )
        route_b = orography_route_identity(spec_b)
        assert route_b != route_a
        monkeypatch.setattr(extract_era5, "OBSERVED_OROGRAPHY_SPEC", spec_b)

        assert _run_all(data_root) == 0

        record_b = read_orography_source_record(orography_source_record_path(data_root))
        assert record_b is not None
        assert record_b.orography_route_identity == route_b, (
            "the record still names the OLD route — the raster was reused "
            "across a spec change"
        )
        assert orography_raster_path(data_root, route_identity=route_b).exists()

        published = _latest_published_dir(data_root)
        manifest = json.loads((published / "extraction_manifest.json").read_text())
        assert manifest["orography_spec"]["product_version"] == spec_b.product_version
        assert (
            manifest["orography_source_record"]["orography_route_identity"] == route_b
        ), "the manifest serialises the NEW spec beside the OLD route identity"
        assert manifest["orography_source_record"]["raster_path"] == str(
            orography_raster_path(data_root, route_identity=route_b).relative_to(
                data_root
            )
        )


class TestRealOrographyDownloaderAssertsAgreement:
    """D7 (BLOCKER, round 4 + P7a, grill-me 2026-08-17) —
    `RealOrographyDownloader.download()` used to take `spec` and ignore it
    entirely (`# noqa: ARG002` suppressing the exact lint finding that named
    this), hard-coding the CDS request. A changed spec must now raise
    BEFORE any request is issued.

    Every test here monkeypatches `cdsapi.Client` to raise immediately,
    REGARDLESS of whether the fix under test is present — so running this
    against the pre-fix code (buggy code proceeds straight past the missing
    assertion into `cdsapi.Client(retry_max=1)`) can never make a live
    network call under a real `~/.cdsapirc`, only ever hit the (patched)
    marker.

    MAJOR (2026-08-17 review) — client CONSTRUCTION is now itself wrapped
    into `Era5OrographyError` (mirroring `era5_acquire.RealCdsClient`'s own
    precedent, `TestRealClientRedactsExceptions`), so the marker exception
    below surfaces WRAPPED, not raw — tests that need to prove "we reached
    real client construction" now match on the WRAPPED message instead of
    the marker's own type, exactly as the acquisition-side precedent does."""

    class _NeverReachedError(Exception):
        pass

    _MARKER_TEXT = (
        "cdsapi.Client must never be constructed once the spec "
        "assertion should have raised first"
    )

    def _block_real_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise self._NeverReachedError(self._MARKER_TEXT)

        monkeypatch.setattr("cdsapi.Client", _boom)

    def test_mismatched_product_id_raises_before_touching_cdsapi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace as dataclass_replace

        from scripts.dhm_precip.era5_errors import Era5OrographyError
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        self._block_real_client(monkeypatch)
        bad_spec = dataclass_replace(
            OBSERVED_OROGRAPHY_SPEC,
            product_id="reanalysis-era5-land:2m_temperature",
        )
        with pytest.raises(Era5OrographyError, match="product_id"):
            extract_era5.RealOrographyDownloader().download(
                spec=bad_spec, dest_dir=tmp_path
            )

    def test_mismatched_download_url_does_not_raise_it_is_operator_attested(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BLOCKER (2026-08-17 fixer round 3) — `download_url` is a browser
        landing page `cdsapi.Client().retrieve()` never consumes; there is
        nothing in the real request to verify it against. A mutated
        `download_url` must therefore reach the real client (it is
        OPERATOR-ATTESTED, not asserted), proving this is no longer the
        tautological check that used to compare it against a second
        hardcoded module constant."""
        from dataclasses import replace as dataclass_replace

        from scripts.dhm_precip.era5_errors import Era5OrographyError
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        self._block_real_client(monkeypatch)
        changed_spec = dataclass_replace(
            OBSERVED_OROGRAPHY_SPEC,
            download_url="https://cds.climate.copernicus.eu/datasets/some-other-dataset",
        )
        with pytest.raises(Era5OrographyError, match=self._MARKER_TEXT):
            extract_era5.RealOrographyDownloader().download(
                spec=changed_spec, dest_dir=tmp_path
            )

    def test_matching_spec_passes_the_assertion_and_reaches_the_real_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A spec that DOES match must not raise at the assertion — it must
        get exactly as far as constructing `cdsapi.Client`, proving the
        assertion does not also block a legitimate, unchanged spec."""
        from scripts.dhm_precip.era5_errors import Era5OrographyError
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        self._block_real_client(monkeypatch)
        with pytest.raises(Era5OrographyError, match=self._MARKER_TEXT):
            extract_era5.RealOrographyDownloader().download(
                spec=OBSERVED_OROGRAPHY_SPEC, dest_dir=tmp_path
            )


class TestRealOrographyDownloaderIssuesTheDeclaredRequest:
    """BLOCKER (2026-08-17 fixer round 3) — the OLD `download_url`
    "verification" compared `spec.download_url` against a second hardcoded
    module constant that `cdsapi.Client().retrieve()` never reads at all,
    so it could never catch a downloader that actually issued the wrong
    request. These tests instead CAPTURE the real
    `dataset`/`payload`/`target` arguments `RealOrographyDownloader.
    download()` passes to `cdsapi.Client().retrieve()` — via a fake
    `cdsapi.Client` substituted for the real one — and assert against them
    directly: a genuine test of the implementation's actual behaviour, not
    of two same-module literals matching each other."""

    class _CapturingClient:
        captured: list[tuple[str, dict[str, object], str]] = []

        def __init__(self, **_kwargs: object) -> None:
            pass

        def retrieve(
            self, dataset: str, payload: dict[str, object], target: str
        ) -> None:
            type(self).captured.append((dataset, payload, target))

    def test_the_captured_request_matches_the_declared_dataset_variable_and_area(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC
        from scripts.dhm_precip.era5_request import DEFAULT_REQUEST_SPEC

        self._CapturingClient.captured = []
        monkeypatch.setattr("cdsapi.Client", self._CapturingClient)

        result = extract_era5.RealOrographyDownloader().download(
            spec=OBSERVED_OROGRAPHY_SPEC, dest_dir=tmp_path
        )

        assert len(self._CapturingClient.captured) == 1
        dataset, payload, target = self._CapturingClient.captured[0]
        assert dataset == "reanalysis-era5-land"
        assert payload["variable"] == ["geopotential"]
        assert payload["area"] == list(DEFAULT_REQUEST_SPEC.area)
        assert target == str(tmp_path / "era5_land_geopotential.nc")
        assert result == (tmp_path / "era5_land_geopotential.nc",)

    def test_the_captured_request_matches_the_manifests_effective_cds_request(
        self, tmp_path: Path
    ) -> None:
        """The manifest's `effective_cds_request` (P7a's real
        machine-verified provenance) must describe the SAME request this
        downloader actually issues, not merely a plausible-looking one.
        `RealOrographyDownloader` always requests `DEFAULT_REQUEST_SPEC.
        area` (module-level, never the injected `request_area` — see
        `_assert_spec_matches_request`'s docstring), which is why this is
        checked against `DEFAULT_REQUEST_SPEC.area`, not the test's own
        `_AREA` override."""
        import json

        from scripts.dhm_precip.era5_request import DEFAULT_REQUEST_SPEC

        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        published = _latest_published_dir(data_root)
        manifest = json.loads((published / "extraction_manifest.json").read_text())
        effective = manifest["orography_spec"]["provenance"]["effective_cds_request"]
        assert effective["dataset"] == "reanalysis-era5-land"
        assert effective["variable"] == ["geopotential"]
        assert effective["area"] == list(DEFAULT_REQUEST_SPEC.area)


class TestRealOrographyDownloaderWrapsClientFailures:
    """MAJOR (2026-08-17 review) — client construction and `.retrieve()`
    were unguarded: any exception (missing credentials, a CDS rejection, a
    transport error) escaped as an arbitrary exception (exit 1) with an
    UN-REDACTED message, unlike the established acquisition client
    (`era5_acquire.RealCdsClient`). Both must now be reclassified into
    `Era5OrographyError` (D9 exit code 3) with the message redacted."""

    def test_client_construction_failure_is_reclassified_and_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.dhm_precip.era5_errors import Era5OrographyError
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        secret = "sk-super-secret-token"
        monkeypatch.setenv("CDSAPI_KEY", secret)

        def _boom(**_kwargs: object) -> None:
            raise RuntimeError(f"missing ~/.cdsapirc, key={secret}")

        monkeypatch.setattr("cdsapi.Client", _boom)
        with pytest.raises(Era5OrographyError) as excinfo:
            extract_era5.RealOrographyDownloader().download(
                spec=OBSERVED_OROGRAPHY_SPEC, dest_dir=tmp_path
            )
        assert secret not in str(excinfo.value)
        assert "REDACTED" in str(excinfo.value)

    def test_retrieve_failure_is_reclassified_and_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.dhm_precip.era5_errors import Era5OrographyError
        from scripts.dhm_precip.era5_orography_spec import OBSERVED_OROGRAPHY_SPEC

        secret = "sk-super-secret-token"
        monkeypatch.setenv("CDSAPI_KEY", secret)

        class _RaisingClient:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def retrieve(
                self, _dataset: str, _payload: dict[str, object], _target: str
            ) -> None:
                raise RuntimeError(f"CDS rejected the request, key={secret}")

        monkeypatch.setattr("cdsapi.Client", _RaisingClient)
        with pytest.raises(Era5OrographyError) as excinfo:
            extract_era5.RealOrographyDownloader().download(
                spec=OBSERVED_OROGRAPHY_SPEC, dest_dir=tmp_path
            )
        assert secret not in str(excinfo.value)
        assert "REDACTED" in str(excinfo.value)


class TestOrographySpecProvenanceLabelling:
    """D7 P7a (slim review, grill-me 2026-08-17) — the manifest must label
    which `OrographySpec` fields are machine-verified versus
    operator-attested, so a reader cannot mistake the latter for the
    former."""

    def test_machine_verified_and_attested_field_sets_are_disjoint(self) -> None:
        assert extract_era5.MACHINE_VERIFIED_SPEC_FIELDS.isdisjoint(
            extract_era5.OPERATOR_ATTESTED_SPEC_FIELDS
        )

    def test_machine_verified_fields_are_the_ones_p7a_names(self) -> None:
        """BLOCKER (2026-08-17 fixer round 3) — `download_url` moved OUT of
        this set: `cdsapi.Client().retrieve()` never consumes it, so it can
        never be machine-verified against the actual request."""
        assert {
            "product_id",
            "conversion_rule",
        } == extract_era5.MACHINE_VERIFIED_SPEC_FIELDS

    def test_attested_fields_are_the_ones_p7a_names(self) -> None:
        assert {
            "product_version",
            "download_url",
            "licence_name",
            "licence_version",
            "licence_url",
            "vertical_reference",
            "source_crs",
        } == extract_era5.OPERATOR_ATTESTED_SPEC_FIELDS

    def test_published_manifest_carries_the_provenance_labels(
        self, tmp_path: Path
    ) -> None:
        import json

        data_root = _build_data_root(tmp_path)
        assert _run_all(data_root) == 0
        published = _latest_published_dir(data_root)
        manifest = json.loads((published / "extraction_manifest.json").read_text())
        provenance = manifest["orography_spec"]["provenance"]
        assert set(provenance["machine_verified_fields"]) == {
            "product_id",
            "conversion_rule",
        }
        assert set(provenance["operator_attested_fields"]) == {
            "product_version",
            "download_url",
            "licence_name",
            "licence_version",
            "licence_url",
            "vertical_reference",
            "source_crs",
        }


class TestRealOrographyRawReaderHandlesServiceShapedResponses:
    """BLOCKER (2026-08-17) — the reader used to strip a dimension only if
    it was literally named `time`; the real ERA5-Land raw contract
    (`era5_acquire.py:319`) uses `valid_time`. A one-timestamp
    `z(valid_time, latitude, longitude)` response stayed 3-D and was handed
    unchanged to the 2-D aggregator. Every synthetic test reader (including
    `_fake_raw_reader` above) returns an already-2-D array, so nothing
    previously exercised this path."""

    def _write_service_shaped(
        self, path: Path, *, temporal_dim: str, n_stamps: int = 1
    ) -> None:
        lat = np.round(np.linspace(_AREA[2], _AREA[0], _LAT_COUNT), 10)
        lon = np.round(np.linspace(_AREA[1], _AREA[3], _LON_COUNT), 10)
        phi = (
            1500.0
            + 10.0 * np.arange(_LAT_COUNT)[:, None]
            + 1.0 * np.arange(_LON_COUNT)[None, :]
        ) * 9.80665
        stacked = np.broadcast_to(phi, (n_stamps, _LAT_COUNT, _LON_COUNT))
        stamps = np.array(["2026-01-01T00:00:00"], dtype="datetime64[ns]") + np.arange(
            n_stamps
        ) * np.timedelta64(1, "h")
        ds = xr.Dataset(
            {"z": ([temporal_dim, "latitude", "longitude"], stacked.copy())},
            coords={temporal_dim: stamps, "latitude": lat, "longitude": lon},
        )
        ds.to_netcdf(path)

    def test_a_service_shaped_valid_time_response_reduces_to_2d(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "geopotential.nc"
        self._write_service_shaped(path, temporal_dim="valid_time")
        values, lat, lon = extract_era5._real_orography_raw_reader(path)
        assert values.shape == (_LAT_COUNT, _LON_COUNT)
        assert lat.shape == (_LAT_COUNT,)
        assert lon.shape == (_LON_COUNT,)

    def test_a_service_shaped_time_response_still_reduces_to_2d(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "geopotential.nc"
        self._write_service_shaped(path, temporal_dim="time")
        values, lat, lon = extract_era5._real_orography_raw_reader(path)
        assert values.shape == (_LAT_COUNT, _LON_COUNT)

    def test_more_than_one_valid_time_stamp_raises(self, tmp_path: Path) -> None:
        from scripts.dhm_precip.era5_errors import Era5OrographyError

        path = tmp_path / "geopotential.nc"
        self._write_service_shaped(path, temporal_dim="valid_time", n_stamps=2)
        with pytest.raises(Era5OrographyError, match="valid_time"):
            extract_era5._real_orography_raw_reader(path)

    def test_already_2d_response_still_works(self, tmp_path: Path) -> None:
        path = tmp_path / "geopotential.nc"
        lat = np.round(np.linspace(_AREA[2], _AREA[0], _LAT_COUNT), 10)
        lon = np.round(np.linspace(_AREA[1], _AREA[3], _LON_COUNT), 10)
        phi = np.ones((_LAT_COUNT, _LON_COUNT)) * 9.80665 * 1500.0
        ds = xr.Dataset(
            {"z": (["latitude", "longitude"], phi)},
            coords={"latitude": lat, "longitude": lon},
        )
        ds.to_netcdf(path)
        values, lat_out, lon_out = extract_era5._real_orography_raw_reader(path)
        assert values.shape == (_LAT_COUNT, _LON_COUNT)
