"""Plan 191 task T4 — narrow ERA5-Land t2m point extraction.

D8 seam tests (never decision tests): every artefact asserts on what is
PHYSICALLY ON DISK — the written series file's data variable name and
`units` attr, the manifest's `referenced_precipitation_bundle_identity`
key, and station/stamp counts read back from a REOPENED file, never from
the in-memory object that wrote it. #194's fifth bug passed 18 tests
against an inert mechanism precisely because nothing checked the disk.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest
import xarray as xr

from scripts.dhm_precip import extract_era5_t2m
from scripts.dhm_precip.domain_types import (
    ExtractionOperator,
    Station,
    StationCoordinate,
)
from scripts.dhm_precip.era5_errors import (
    Era5StorageError,
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
    NonFiniteExtractionError,
)
from scripts.dhm_precip.era5_extract import ExtractedSeries, extract_nearest_series
from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    write_extraction_manifest,
)
from scripts.dhm_precip.era5_manifest import (
    Era5ProvenanceManifest,
    OperatorProvenance,
    TransformYearRecord,
    checksum_file,
    manifest_path_for,
    product_artifact_path,
    with_transform_year,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import STUDY_YEARS, expected_grid_shape
from scripts.dhm_precip.params import DEFAULT_PARAMS

if TYPE_CHECKING:
    from pathlib import Path

_AREA = (26.2, 85.0, 26.0, 85.2)  # north, west, south, east -> 3x3 grid
_HOUR = np.timedelta64(1, "h")
_CLOCK = lambda: datetime(2026, 8, 20, tzinfo=UTC)  # noqa: E731
_STATIONS: tuple[tuple[str, float, float], ...] = (
    ("A", 26.05, 85.05),
    ("B", 26.15, 85.15),
)
_TEST_PARAMS = replace(DEFAULT_PARAMS, expected_station_count=len(_STATIONS))


def _full_year_valid_time(year: int) -> np.ndarray:
    start = np.datetime64(f"{year:04d}-01-01T00:00:00")
    end = np.datetime64(f"{year:04d}-12-31T23:00:00")
    return np.arange(start, end + _HOUR, _HOUR)


def _t2m_product_ds(
    *, year: int, value_degc: float = 10.0, non_finite_index: int | None = None
) -> xr.Dataset:
    lat_count, lon_count = expected_grid_shape(_AREA)
    north, west, south, east = _AREA
    lat = np.round(np.linspace(south, north, lat_count), 10)
    lon = np.round(np.linspace(west, east, lon_count), 10)
    valid_time = _full_year_valid_time(year)
    values = np.full(
        (valid_time.size, lat_count, lon_count), value_degc, dtype=np.float32
    )
    if non_finite_index is not None:
        values[non_finite_index, 1, 1] = np.nan
    ds = xr.Dataset(
        {"temperature": (["valid_time", "latitude", "longitude"], values)},
        coords={"valid_time": valid_time, "latitude": lat, "longitude": lon},
    )
    ds["temperature"].attrs["units"] = "degC"
    ds.attrs.update(
        {
            "transform_version": "1",
            "output_schema_version": "1",
            "source_dataset": "reanalysis-era5-land",
            "units_conversion": "kelvin_to_celsius_subtract_273.15",
        }
    )
    return ds


def _write_t2m_product(path: Path, ds: xr.Dataset) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(
        path,
        engine="h5netcdf",
        encoding={
            "temperature": {"dtype": "float32"},
            "valid_time": {
                "units": "hours since 1970-01-01 00:00:00",
                "dtype": "int64",
            },
        },
    )


def _write_coords_csv(path: Path) -> None:
    pl.DataFrame(
        {
            "station": [s[0] for s in _STATIONS],
            "excel_col": [f"{s[0]} (mm)" for s in _STATIONS],
            "lat": [s[1] for s in _STATIONS],
            "lon": [s[2] for s in _STATIONS],
            "elev": [1500.0, 1600.0],
        }
    ).write_csv(path)


def _build_t2m_root(tmp_path: Path, *, non_finite_year: int | None = None) -> Path:
    data_root = tmp_path / "t2m"
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
        variable="2m_temperature",
    )
    for year in STUDY_YEARS:
        ds = _t2m_product_ds(
            year=year, non_finite_index=0 if year == non_finite_year else None
        )
        path = product_artifact_path(
            year,
            data_root,
            variable_code="t2m",
            product_dir_name="degc",
            unit_label="degc",
        )
        _write_t2m_product(path, ds)
        sha = checksum_file(path)
        manifest = with_transform_year(
            manifest,
            TransformYearRecord(
                product_year=year,
                transform_identity="x",
                sha256=sha,
                accumulation_convention="instantaneous",
                units_conversion="kelvin_to_celsius_subtract_273.15",
                packing=None,
                non_finite_cell_count=0,
                dropped_boundary_stamp=None,
                transformed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    write_manifest_atomic(manifest, manifest_path_for(data_root))
    return data_root


def _station_coord(name: str, lat: float, lon: float) -> StationCoordinate:
    return StationCoordinate(
        station=Station(name), excel_col=f"{name} (mm)", lat=lat, lon=lon, elev_m=1500.0
    )


def _true_grid() -> dict[str, ExtractedSeries]:
    """The grid cell every station ACTUALLY resolves to on `_AREA`'s
    3x3 grid, computed the same way `extract_era5_t2m.run` does — so the
    fake precipitation bundle below can assert genuine D6 agreement."""
    ds = _t2m_product_ds(year=STUDY_YEARS[0])
    return {
        name: extract_nearest_series(
            ds, _station_coord(name, lat, lon), variable="temperature"
        )
        for name, lat, lon in _STATIONS
    }


def _build_precip_bundle(
    precip_root: Path,
    *,
    corrupt_station: str | None = None,
    extraction_identity: str = "precip-identity-abc123",
    omit_elevation_csv: bool = False,
    tamper_elevation_csv_after_hashing: bool = False,
) -> Path:
    bundle_dir = precip_root / "era5_land" / "points" / "0000-fake"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    payload_sha256s: dict[str, str] = {}
    if not omit_elevation_csv:
        rows: list[dict[str, object]] = []
        for name, series in _true_grid().items():
            grid_i = series.grid_i + (1 if name == corrupt_station else 0)
            rows.append(
                {
                    "station": name,
                    "grid_i": grid_i,
                    "grid_j": series.grid_j,
                    "grid_lat": series.grid_lat,
                    "grid_lon": series.grid_lon,
                }
            )
        elevation_csv_path = bundle_dir / "station_grid_elevation.csv"
        pl.DataFrame(rows).write_csv(elevation_csv_path)
        # Finding 2: the manifest must record the checksum of the bytes as
        # actually written, so a real run reconciles cleanly.
        payload_sha256s["station_grid_elevation.csv"] = checksum_file(
            elevation_csv_path
        )
        if tamper_elevation_csv_after_hashing:
            # Simulate a bundle corrupted AFTER its manifest was published:
            # the recorded checksum still describes the ORIGINAL bytes, but
            # the file on disk has since changed underneath it.
            elevation_csv_path.write_text(
                elevation_csv_path.read_text() + "\n# tampered after hashing"
            )

    manifest = ExtractionManifest(
        orography_identity="oro-x",
        extraction_identity=extraction_identity,
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256="0" * 64,
        source_sha256s_by_year={"2020": "0" * 64},
        payload_sha256s=payload_sha256s,
        orography_spec={},
        orography_source_record={},
        accumulation_diagnostic={},
        generated_at=_CLOCK(),
    )
    write_extraction_manifest(manifest, bundle_dir / "extraction_manifest.json")
    return bundle_dir


class TestGridAgreementD6:
    """D6 — never assumed, always verified. A red-first requirement: this
    must fail loudly against a corrupted precipitation elevation table."""

    def test_matching_grid_passes_silently(self, tmp_path: Path) -> None:
        bundle_dir = _build_precip_bundle(tmp_path)
        extract_era5_t2m.assert_grid_matches_precipitation_bundle(
            _true_grid(), bundle_dir / "station_grid_elevation.csv"
        )

    def test_mismatched_grid_i_is_falsified_and_raises(self, tmp_path: Path) -> None:
        bundle_dir = _build_precip_bundle(tmp_path, corrupt_station="A")
        with pytest.raises(ExtractionPostConditionError, match="FALSIFIED"):
            extract_era5_t2m.assert_grid_matches_precipitation_bundle(
                _true_grid(), bundle_dir / "station_grid_elevation.csv"
            )

    def test_missing_elevation_csv_raises_input_absent(self, tmp_path: Path) -> None:
        with pytest.raises(ExtractionInputAbsentError):
            extract_era5_t2m.assert_grid_matches_precipitation_bundle(
                _true_grid(), tmp_path / "does_not_exist.csv"
            )

    def test_station_absent_from_elevation_table_raises(self, tmp_path: Path) -> None:
        bundle_dir = tmp_path / "era5_land" / "points" / "0000-fake"
        bundle_dir.mkdir(parents=True)
        pl.DataFrame(
            {
                "station": ["OTHER"],
                "grid_i": [0],
                "grid_j": [0],
                "grid_lat": [26.0],
                "grid_lon": [85.0],
            }
        ).write_csv(bundle_dir / "station_grid_elevation.csv")
        with pytest.raises(ExtractionPostConditionError, match="absent"):
            extract_era5_t2m.assert_grid_matches_precipitation_bundle(
                _true_grid(), bundle_dir / "station_grid_elevation.csv"
            )


class TestDiscoverPrecipBundleReconcilesElevationChecksum:
    """Finding 2 — D6 rests on "elevations are REUSED, never re-derived",
    which is only safe if the reused `station_grid_elevation.csv` is
    actually the file the referenced manifest's identity names.
    `_discover_precip_bundle` used to accept any syntactically readable
    manifest without reconciling the CSV's bytes against the manifest's own
    `payload_sha256s` — a corrupted table would silently pass through and
    be trusted for the rest of the run."""

    def test_matching_checksum_discovers_the_bundle(self, tmp_path: Path) -> None:
        bundle_dir = _build_precip_bundle(tmp_path)
        discovered_dir, identity = extract_era5_t2m._discover_precip_bundle(tmp_path)
        assert discovered_dir == bundle_dir
        assert identity == "precip-identity-abc123"

    def test_tampered_elevation_csv_is_rejected_not_trusted(
        self, tmp_path: Path
    ) -> None:
        _build_precip_bundle(tmp_path, tamper_elevation_csv_after_hashing=True)
        with pytest.raises(ExtractionPostConditionError, match="sha256"):
            extract_era5_t2m._discover_precip_bundle(tmp_path)

    def test_tampered_bundle_stops_the_full_run(self, tmp_path: Path) -> None:
        """The typed failure must actually reach the CLI's exit-code
        mapping (exit 4) — never a warning, never a silent pass-through."""
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root, tamper_elevation_csv_after_hashing=True)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 4
        assert not (
            extract_era5_t2m.t2m_points_dir(data_root) / "series_t2m_degc.nc"
        ).exists()


class TestExtractionIdentity:
    def test_identity_is_deterministic(self) -> None:
        kwargs = dict(
            operator_id="NEAREST",
            coordinate_table_sha256="a" * 64,
            source_sha256s_by_year={"2020": "b" * 64},
            referenced_precipitation_bundle_identity="precip-x",
            output_schema_version="1",
            output_dtype="float32",
        )
        assert extract_era5_t2m.t2m_extraction_identity(
            **kwargs
        ) == extract_era5_t2m.t2m_extraction_identity(**kwargs)

    def test_changing_the_referenced_bundle_changes_the_identity(self) -> None:
        base = dict(
            operator_id="NEAREST",
            coordinate_table_sha256="a" * 64,
            source_sha256s_by_year={"2020": "b" * 64},
            output_schema_version="1",
            output_dtype="float32",
        )
        first = extract_era5_t2m.t2m_extraction_identity(
            referenced_precipitation_bundle_identity="precip-x", **base
        )
        second = extract_era5_t2m.t2m_extraction_identity(
            referenced_precipitation_bundle_identity="precip-y", **base
        )
        assert first != second


class TestExitCodeDispatch:
    def test_every_subclass_precedes_its_base_in_the_dispatch_table(self) -> None:
        table = extract_era5_t2m._EXIT_BY_ERROR
        for i, (exc_type, _code) in enumerate(table):
            for later_type, _later_code in table[i + 1 :]:
                assert not (
                    issubclass(later_type, exc_type) and later_type is not exc_type
                ), (
                    f"{later_type.__name__} is a SUBCLASS of {exc_type.__name__}, "
                    "listed before it — the first match wins"
                )

    def test_storage_error_maps_to_5(self) -> None:
        assert extract_era5_t2m._exit_code_for(Era5StorageError("x")) == 5

    def test_non_finite_extraction_error_maps_to_4_via_the_catchall(self) -> None:
        # NonFiniteExtractionError is NOT ExtractionPostConditionError — it
        # only reaches exit code 4 through the Era5AcquisitionError catchall.
        assert extract_era5_t2m._exit_code_for(NonFiniteExtractionError("x")) == 4


class TestRealRunSeamArtifacts:
    """D8: the published series/manifest are validated by REOPENING them
    from disk, never by inspecting the in-memory objects that wrote them."""

    def test_happy_path_publishes_a_series_and_manifest_on_disk(
        self, tmp_path: Path
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 0

        series_path = extract_era5_t2m.t2m_points_dir(data_root) / "series_t2m_degc.nc"
        manifest_path = (
            extract_era5_t2m.t2m_points_dir(data_root) / "extraction_manifest.json"
        )
        assert series_path.exists()
        assert manifest_path.exists()

        # Seam: reopen the WRITTEN file — never trust the in-memory object.
        with xr.open_dataset(series_path, engine="h5netcdf") as reopened:
            loaded = reopened.load()
            assert extract_era5_t2m.T2M_DATA_VARIABLE in loaded
            assert loaded[extract_era5_t2m.T2M_DATA_VARIABLE].attrs["units"] == "degC"
            assert loaded["valid_time"].attrs["timezone"] == "UTC"
            assert loaded.sizes["station"] == len(_STATIONS)
            assert loaded.sizes["valid_time"] == 52608
            assert bool(
                np.isfinite(loaded[extract_era5_t2m.T2M_DATA_VARIABLE].values).all()
            )

        manifest_on_disk = json.loads(manifest_path.read_text())
        assert (
            manifest_on_disk["referenced_precipitation_bundle_identity"]
            == "precip-identity-abc123"
        )
        assert manifest_on_disk["referenced_precipitation_bundle_identity"] != ""
        assert manifest_on_disk["station_count"] == len(_STATIONS)
        assert manifest_on_disk["stamp_count"] == 52608
        assert manifest_on_disk["extraction_identity"]

    def test_manifest_records_identity_only_never_a_run_numbered_path(
        self, tmp_path: Path
    ) -> None:
        """The reviewer's fix: `allocate_published_dir` hands out a fresh
        `<NNNN>-<identity>` directory on every gate-suite run (P1a — no
        adoption, no dedup), so a run-numbered PATH recorded today can point
        at a directory pruned tomorrow. The manifest must carry the
        precipitation bundle's IDENTITY only — a real 64-char sha256 hex
        digest, physically on disk — and no `points/<NNNN>-` path fragment
        anywhere in the JSON text."""
        real_identity = hashlib.sha256(b"a real precipitation bundle").hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", real_identity)

        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root, extraction_identity=real_identity)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 0

        manifest_path = (
            extract_era5_t2m.t2m_points_dir(data_root) / "extraction_manifest.json"
        )
        manifest_text = manifest_path.read_text()

        # No run-number-prefixed path fragment anywhere in the JSON text.
        assert re.search(r"points/\d{4}-", manifest_text) is None
        assert "referenced_precipitation_bundle_path" not in manifest_text

        manifest_on_disk = json.loads(manifest_text)
        assert manifest_on_disk["referenced_precipitation_bundle_identity"] == (
            real_identity
        )
        assert re.fullmatch(
            r"[0-9a-f]{64}",
            manifest_on_disk["referenced_precipitation_bundle_identity"],
        )

    def test_d6_mismatch_stops_the_run(self, tmp_path: Path) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root, corrupt_station="A")
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 4
        assert not (
            extract_era5_t2m.t2m_points_dir(data_root) / "series_t2m_degc.nc"
        ).exists()

    def test_missing_precip_bundle_exits_2(self, tmp_path: Path) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"  # never built
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 2

    def test_non_finite_t2m_value_exits_4(self, tmp_path: Path) -> None:
        data_root = _build_t2m_root(tmp_path, non_finite_year=STUDY_YEARS[0])
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)

        exit_code = extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )
        assert exit_code == 4


class TestAtomicPublishOfSeriesAndManifest:
    """Finding 1 — the series and its manifest must publish as ONE pair.
    Before the fix, `_write_t2m_series_netcdf` replaced the series' final
    path BEFORE the manifest was written, so a manifest-write failure left
    a NEW series beside a STALE or ABSENT manifest — a published artefact
    its own manifest described falsely. These simulate that failure and
    assert there is never a half-published pair: either the previous pair
    survives byte-for-byte, or (with no previous pair) nothing at all is
    published."""

    def _run(self, tmp_path: Path, *, data_root: Path, precip_root: Path) -> int:
        coords_path = tmp_path / "station_coordinates.csv"
        _write_coords_csv(coords_path)
        return extract_era5_t2m.main(
            [
                "--data-root",
                str(data_root),
                "--precip-data-root",
                str(precip_root),
            ],
            clock=_CLOCK,
            coords_path=coords_path,
            expected_stations=frozenset({Station(s[0]) for s in _STATIONS}),
            params=_TEST_PARAMS,
            request_area=_AREA,
        )

    def test_manifest_write_failure_with_no_previous_pair_publishes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated manifest write failure")

        monkeypatch.setattr(extract_era5_t2m, "_write_t2m_manifest", _boom)

        exit_code = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert exit_code == 5
        assert not extract_era5_t2m.t2m_points_dir(data_root).exists()

    def test_manifest_write_failure_leaves_the_previous_pair_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = _build_t2m_root(tmp_path)
        precip_root = tmp_path / "precip"
        _build_precip_bundle(precip_root)

        first_exit = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert first_exit == 0
        points_dir = extract_era5_t2m.t2m_points_dir(data_root)
        series_path = points_dir / "series_t2m_degc.nc"
        manifest_path = points_dir / "extraction_manifest.json"
        original_series_sha256 = checksum_file(series_path)
        original_manifest_text = manifest_path.read_text()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated manifest write failure")

        monkeypatch.setattr(extract_era5_t2m, "_write_t2m_manifest", _boom)

        second_exit = self._run(tmp_path, data_root=data_root, precip_root=precip_root)
        assert second_exit == 5

        # The PREVIOUS pair survives byte-for-byte — never a new series
        # beside a stale/absent manifest — and no orphaned staging/backup
        # directory is left behind either.
        assert checksum_file(series_path) == original_series_sha256
        assert manifest_path.read_text() == original_manifest_text
        stray = [
            p.name for p in points_dir.parent.iterdir() if p.name.startswith(".points")
        ]
        assert stray == []


class TestPublishT2mBundleSwapRollback:
    """A focused test of `_publish_t2m_bundle`'s own rollback: a failure
    DURING the swap itself (not merely before staging becomes visible) must
    restore the previous pair, never leave `points_dir` half-replaced or
    missing."""

    def test_swap_failure_restores_the_previous_pair(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        era5_land_dir = tmp_path / "era5_land"
        points_dir = era5_land_dir / "points"
        points_dir.mkdir(parents=True)
        (points_dir / "series_t2m_degc.nc").write_bytes(b"old-series")
        (points_dir / "extraction_manifest.json").write_text('{"old": true}')

        staged_dir = era5_land_dir / ".points_staging-test"
        staged_dir.mkdir()
        (staged_dir / "series_t2m_degc.nc").write_bytes(b"new-series")
        (staged_dir / "extraction_manifest.json").write_text('{"new": true}')

        real_replace = extract_era5_t2m.os.replace
        call_count = {"n": 0}

        def _flaky_replace(src: object, dst: object) -> None:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated swap failure")
            real_replace(src, dst)

        monkeypatch.setattr(extract_era5_t2m.os, "replace", _flaky_replace)

        with pytest.raises(Era5StorageError):
            extract_era5_t2m._publish_t2m_bundle(era5_land_dir, points_dir, staged_dir)

        assert (points_dir / "series_t2m_degc.nc").read_bytes() == b"old-series"
        assert (points_dir / "extraction_manifest.json").read_text() == '{"old": true}'
        assert not (era5_land_dir / ".points.prev").exists()
