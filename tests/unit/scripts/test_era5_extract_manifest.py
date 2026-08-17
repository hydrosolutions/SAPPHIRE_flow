"""Plan 174 (M-A5) task 4a — D7's `extraction_identity`, bundle publication,
and the manifest, exercised directly (cheaper than the full CLI).

M-11 (review finding): the identity test must cover EVERY declared
value-affecting input, not a subset — each parameter below is varied in
turn and must change the resulting identity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.era5_errors import ExtractionPostConditionError
from scripts.dhm_precip.era5_extract_manifest import (
    D9_PAYLOAD_FILES,
    ExtractionIdentityInputs,
    ExtractionManifest,
    checksum_file,
    extraction_identity,
    points_root,
    prepare_staging_dir,
    publish_bundle,
    read_extraction_manifest,
    recompute_extraction_identity,
    write_extraction_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path

_BASE_KWARGS: dict[str, object] = {
    "operator_id": "NEAREST",
    "coordinate_table_sha256": "a" * 64,
    "source_sha256s_by_year": {"2001": "b" * 64, "2002": "c" * 64},
    "orography_identity": "d" * 64,
    "jjas_months": (6, 7, 8, 9),
    "djf_months": (12, 1, 2),
    "mam_months": (3, 4, 5),
    "on_months": (10, 11),
    "wet_threshold_mm_per_h": 0.2,
    "wet_threshold_side": ">=",
    "zero_policy": "exclude_zero",
    "quantile_definition": "linear",
    "quantile_grid": (0.5, 0.9, 0.99),
    "station_elevation_datum": "UNKNOWN",
    "orography_elevation_datum": "LOCAL_MSL",
    "output_schema_version": "1",
    "output_format": "netcdf4_h5netcdf",
    "output_dtype": "float32",
    "output_encoding": {
        "valid_time": {"units": "hours since 1970-01-01 00:00:00", "dtype": "int64"}
    },
}


class TestExtractionIdentityCoversEveryDeclaredInput:
    """M-11 — each field D7 declares as part of the identity, varied alone,
    must change the digest. A broken implementation that forgets to hash
    one of these fields would pass a narrower test; this one would not."""

    def test_base_case_is_reproducible(self) -> None:
        assert extraction_identity(**_BASE_KWARGS) == extraction_identity(
            **_BASE_KWARGS
        )

    @pytest.mark.parametrize(
        ("field", "override"),
        [
            ("operator_id", "BILINEAR"),
            ("coordinate_table_sha256", "z" * 64),
            ("source_sha256s_by_year", {"2001": "z" * 64}),
            ("orography_identity", "z" * 64),
            ("jjas_months", (7, 8, 9)),
            ("djf_months", (1, 2)),
            ("mam_months", (4, 5)),
            ("on_months", (11,)),
            ("wet_threshold_mm_per_h", 0.5),
            ("wet_threshold_side", ">"),
            ("zero_policy", "include_zero"),
            ("quantile_definition", "nearest"),
            ("quantile_grid", (0.5,)),
            ("station_elevation_datum", "EGM2008"),
            ("orography_elevation_datum", "EGM96"),
            ("output_schema_version", "2"),
            ("output_format", "netcdf4_scipy"),
            ("output_dtype", "float64"),
            ("output_encoding", {"valid_time": {"units": "x", "dtype": "int32"}}),
            ("extraction_code_version", "2"),
        ],
    )
    def test_changing_the_field_alone_changes_the_identity(
        self, field: str, override: object
    ) -> None:
        base = extraction_identity(**_BASE_KWARGS)
        changed = extraction_identity(**{**_BASE_KWARGS, field: override})
        assert base != changed, f"{field} is not covered by extraction_identity"

    def test_swapping_two_years_source_hashes_changes_the_identity(self) -> None:
        """D7 — the identity must cover WHICH YEAR each product hash belongs
        to, not merely the multiset of hashes. `sorted(source_sha256s)` over
        a bare list discarded that association, so exchanging the BYTES of
        two annual products (2001 holding 2002's content and vice versa)
        left `extraction_identity` unchanged — a materially different input
        set published under an already-used identity."""
        swapped = extraction_identity(
            **{
                **_BASE_KWARGS,
                "source_sha256s_by_year": {"2001": "c" * 64, "2002": "b" * 64},
            }
        )
        assert extraction_identity(**_BASE_KWARGS) != swapped

    def test_the_year_hash_mapping_is_hashed_order_independently(self) -> None:
        """The mapping's KEY ORDER is an accident of iteration, not an
        input: the same (year -> sha256) associations must reproduce the
        same digest however the mapping was built."""
        reordered = extraction_identity(
            **{
                **_BASE_KWARGS,
                "source_sha256s_by_year": {"2002": "c" * 64, "2001": "b" * 64},
            }
        )
        assert extraction_identity(**_BASE_KWARGS) == reordered


_STATIONS = ("alpha", "beta")
_CLOCK_NOW = datetime(2026, 8, 16, tzinfo=UTC)


def _write_payload_files(directory: Path, *, marker: str = "x") -> None:
    """A REAL D9 payload set — the P4a reconcile check AND the full D9
    schema validator (station uniqueness/equality, required columns/enum
    values, series dims/dtype/time-axis/encoding/attributes, the STATION-
    scope sensitivity matrix) both run the same reopen-and-validate bundle
    validator the staging path must pass, so a two-column stub is no longer
    sufficient (that stub is exactly what `TestPublishEnforcesFullD9Schema`
    below proves used to publish). The series are written through
    `points_xarray_encoding` — the SAME production spec the reopen validator
    checks against — so this fixture can never drift from what the real
    writer emits."""
    import numpy as np
    import polars as pl
    import xarray as xr

    from scripts.dhm_precip.era5_extract_manifest import points_xarray_encoding

    valid_time = np.array(
        ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"], dtype="datetime64[ns]"
    )
    values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
    encoding = points_xarray_encoding((len(_STATIONS), valid_time.size))
    for name in ("series_nearest.nc", "series_bilinear.nc"):
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        ds.to_netcdf(directory / name, engine="h5netcdf", encoding=encoding)
    pl.DataFrame(
        {
            "station": list(_STATIONS),
            "lat": [26.0, 26.1],
            "lon": [85.0, 85.1],
            "grid_lat": [26.0, 26.1],
            "grid_lon": [85.0, 85.1],
            "grid_i": [0, 1],
            "grid_j": [0, 1],
            "offset_km": [0.0, 0.0],
            "station_elev_m": [1500.0, 1900.0],
            "station_elevation_datum": ["UNKNOWN", "UNKNOWN"],
            "orography_elev_m": [1490.0, 1880.0],
            "orography_elevation_datum": ["LOCAL_MSL", "LOCAL_MSL"],
            "orography_source": ["MODEL_OROGRAPHY", "MODEL_OROGRAPHY"],
            "orography_product_id": ["p", "p"],
            "orography_product_version": ["1", "1"],
            "elev_mismatch_m": [10.0, 20.0],
            "datum_reconciled": ["UNRECONCILED", "UNRECONCILED"],
            "shared_cell_id": ["0_0", "1_1"],
            "stations_in_cell": [1, 1],
            "marker": [marker, marker],
        }
    ).write_csv(directory / "station_grid_elevation.csv")
    # A genuinely complete sensitivity matrix (M-BLOCKER, 2026-08-17
    # review): a STATION-scope row for EVERY station (exact-set equality,
    # not a subset), with the SAME row count per station (self-consistency
    # completeness), plus the ACROSS_STATION summary row.
    pl.DataFrame(
        {
            "scope": ["STATION", "STATION", "ACROSS_STATION"],
            "station": [*_STATIONS, None],
            "season": ["ALL", "ALL", "ALL"],
            "statistic": ["QUANTILE", "QUANTILE", "QUANTILE"],
            "quantile": [0.5, 0.5, 0.5],
            "nearest_value": [1.0, 1.0, 1.0],
            "bilinear_value": [1.0, 1.0, 1.0],
            "delta_absolute": [0.0, 0.0, 0.0],
            "delta_unit": ["MM_PER_H", "MM_PER_H", "MM_PER_H"],
            "ratio": [1.0, 1.0, 1.0],
            "n_hours_common_finite": [3, 3, 3],
            "n_hours_excluded": [0, 0, 0],
            "n_wet_nearest": [1, 1, 1],
            "n_wet_bilinear": [1, 1, 1],
            "sign_agreement_fraction": [None, None, 1.0],
        }
    ).write_csv(directory / "operator_sensitivity.csv")


def _payload_sha256s(directory: Path) -> dict[str, str]:
    return {name: checksum_file(directory / name) for name in D9_PAYLOAD_FILES}


_MANIFEST_IDENTITY_INPUTS: dict[str, object] = ExtractionIdentityInputs(
    **_BASE_KWARGS  # type: ignore[arg-type]
).canonical_payload()
_MANIFEST_OROGRAPHY_SPEC: dict[str, object] = {
    "source": "MODEL_OROGRAPHY",
    "product_id": "reanalysis-era5-land:geopotential",
    "product_version": "v1",
    "download_url": "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
    "licence_name": "Licence to use Copernicus Products",
    "licence_version": "1.0",
    "licence_url": "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=licence",
    "source_crs": "WGS84 (EPSG:4326)",
    "vertical_reference": "LOCAL_MSL",
    "units": "m**2 s**-2",
    "no_data_sentinel": "NaN",
    "aggregation_rule_id": "era5_land_orography_mean_of_contained_cells_v1",
    "conversion_rule": "geopotential_g0",
    "probe_date": "2026-08-16",
    "rejected_candidates": [],
    "provenance": {
        "machine_verified_fields": ["conversion_rule", "product_id"],
        "operator_attested_fields": [
            "download_url",
            "licence_name",
            "licence_url",
            "licence_version",
            "product_version",
            "source_crs",
            "vertical_reference",
        ],
    },
}
_MANIFEST_OROGRAPHY_SOURCE_RECORD: dict[str, object] = {
    "orography_route_identity": "r" * 64,
    "orography_identity": "o" * 64,
    "fetched_at": _CLOCK_NOW.isoformat(),
    "downloaded_files": [{"path": "raw.nc", "sha256": "d" * 64, "size_bytes": 1}],
    "raster_path": "orography.nc",
    "raster_sha256": "e" * 64,
    "raster_schema_version": "1",
}
_MANIFEST_ACCUMULATION_DIAGNOSTIC: dict[str, object] = {
    "window_id": "2021-oct",
    "source_sha256": "f" * 64,
    "reset_hour": 0,
    "terminal_hour": 23,
    "monotone_within_day": True,
    "sample_size_days": 31,
    "recorded_at": _CLOCK_NOW.isoformat(),
}
_MANIFEST_STATION_ACCOUNTING: dict[str, dict[str, dict[str, object]]] = {
    "NEAREST": {
        station: {
            "n_hours": 3,
            "n_finite": 3,
            "n_nan": 0,
            "first_nan_valid_time": None,
            "last_nan_valid_time": None,
        }
        for station in _STATIONS
    },
    "BILINEAR": {
        station: {
            "n_hours": 3,
            "n_finite": 3,
            "n_nan": 0,
            "first_nan_valid_time": None,
            "last_nan_valid_time": None,
        }
        for station in _STATIONS
    },
}


def _write_manifest(
    directory: Path, *, identity: str, payload_sha256s: dict[str, str]
) -> None:
    write_extraction_manifest(
        ExtractionManifest(
            orography_identity="o",
            extraction_identity=identity,
            operator_id="NEAREST",
            coordinate_table_sha256="a" * 64,
            source_sha256s_by_year={"2001": "b" * 64},
            payload_sha256s=payload_sha256s,
            orography_spec=_MANIFEST_OROGRAPHY_SPEC,
            orography_source_record=_MANIFEST_OROGRAPHY_SOURCE_RECORD,
            accumulation_diagnostic=_MANIFEST_ACCUMULATION_DIAGNOSTIC,
            station_accounting=_MANIFEST_STATION_ACCOUNTING,
            identity_inputs=_MANIFEST_IDENTITY_INPUTS,
            generated_at=_CLOCK_NOW,
        ),
        directory / "extraction_manifest.json",
    )


def _staged_bundle(data_root: Path, *, identity: str, marker: str = "x") -> Path:
    staging = prepare_staging_dir(data_root, identity=identity)
    _write_payload_files(staging, marker=marker)
    _write_manifest(
        staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
    )
    return staging


def _publish(
    staged: Path, data_root: Path, identity: str, *, expected_hour_count: int = 3
) -> Path:
    return publish_bundle(
        staged,
        data_root=data_root,
        identity=identity,
        expected_station_count=2,
        expected_hour_count=expected_hour_count,
    )


def _marker_of(directory: Path) -> str:
    import polars as pl

    return (
        pl.read_csv(directory / "station_grid_elevation.csv")["marker"].unique().item()
    )


def _published_dirs(data_root: Path) -> list[str]:
    root = points_root(data_root)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != ".staging")


class TestPublishBundleMechanics:
    def test_publish_creates_a_numbered_directory_named_by_the_identity(
        self, tmp_path: Path
    ) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        final_dir = _publish(staged, tmp_path, "abc123")
        assert final_dir.name == "0000-abc123"
        assert final_dir.exists()
        for name in D9_PAYLOAD_FILES:
            assert (final_dir / name).exists(), name

    def test_manifest_never_hashes_itself(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="abc123")
        manifest = read_extraction_manifest(staged / "extraction_manifest.json")
        assert manifest is not None
        assert "extraction_manifest.json" not in manifest.payload_sha256s


class TestStagingDirIsolation:
    """MAJOR (2026-08-17 review) — `staging_dir` used to be
    `.staging/<identity>`, SHARED by every run under that identity, and
    `prepare_staging_dir` `rmtree`d whatever was already there before
    recreating it. Two concurrent SAME-identity runs could therefore delete
    or interleave each other's staged payload. Each call now allocates a
    fresh, per-invocation-unique directory."""

    def test_two_calls_under_the_same_identity_get_different_directories(
        self, tmp_path: Path
    ) -> None:
        first = prepare_staging_dir(tmp_path, identity="same-id")
        second = prepare_staging_dir(tmp_path, identity="same-id")
        assert first != second
        assert first.exists()
        assert second.exists()

    def test_preparing_a_second_staging_dir_does_not_delete_the_first(
        self, tmp_path: Path
    ) -> None:
        first = prepare_staging_dir(tmp_path, identity="same-id")
        (first / "marker.txt").write_text("first")
        second = prepare_staging_dir(tmp_path, identity="same-id")
        (second / "marker.txt").write_text("second")
        # BUG (pre-fix): the second call `rmtree`d the shared directory the
        # first call was still using, deleting "first"'s content.
        assert (first / "marker.txt").read_text() == "first"
        assert (second / "marker.txt").read_text() == "second"

    def test_concurrent_same_identity_staging_never_collides_or_interleaves(
        self, tmp_path: Path
    ) -> None:
        """Proven under REAL concurrency: several threads, all preparing a
        staging directory under the SAME identity, released from a barrier
        together. Every thread's own marker file must survive untouched —
        against the old shared-path `rmtree` design, a LATER thread deletes
        an EARLIER thread's directory out from under it, deterministically
        losing markers (not merely a flaky interleaving)."""
        import threading

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        results: list[Path | None] = [None] * n_threads
        errors: list[BaseException] = []

        def _prepare(i: int) -> None:
            barrier.wait()
            try:
                staging = prepare_staging_dir(tmp_path, identity="same-id")
                (staging / "marker.txt").write_text(str(i))
                results[i] = staging
            except BaseException as exc:  # noqa: BLE001 - surfaced on the main thread
                errors.append(exc)

        threads = [
            threading.Thread(target=_prepare, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        paths = [p for p in results if p is not None]
        assert len(paths) == n_threads
        assert len({str(p) for p in paths}) == n_threads
        for i, staging in enumerate(paths):
            assert (staging / "marker.txt").read_text() == str(i)


class TestPerRunUniquePublication:
    """P1/P1a/P2 (redesigned 2026-08-17) — **there is no adoption, no
    quarantine and no `CURRENT` pointer.** Every run publishes into a fresh,
    per-run-unique numbered directory; every previous bundle is left
    untouched, and nothing is ever renamed after the fact. Three review
    rounds each found a blocker inside the old "adopt if the manifest
    reconciles" / quarantine design; the redesign removes the branch and the
    window instead of patching either again.
    """

    def test_rerun_with_the_same_identity_gets_a_new_run_number(
        self, tmp_path: Path
    ) -> None:
        first = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="first"),
            tmp_path,
            "same-id",
        )
        second = _publish(
            _staged_bundle(tmp_path, identity="same-id", marker="second"),
            tmp_path,
            "same-id",
        )
        assert first.name == "0000-same-id"
        assert second.name == "0001-same-id"
        # The first bundle survives completely untouched — nothing renamed,
        # nothing quarantined, nothing deleted.
        assert first.exists()
        assert _marker_of(first) == "first"
        assert _marker_of(second) == "second"
        assert _published_dirs(tmp_path) == ["0000-same-id", "0001-same-id"]
        # No `.orphan-*` path is ever created — the round-3 window is
        # unrepresentable now, tested here by absence.
        assert not any("orphan" in name for name in _published_dirs(tmp_path))

    def test_different_identities_each_get_their_own_numbered_directory(
        self, tmp_path: Path
    ) -> None:
        first = _publish(_staged_bundle(tmp_path, identity="id-a"), tmp_path, "id-a")
        second = _publish(_staged_bundle(tmp_path, identity="id-b"), tmp_path, "id-b")
        assert first.name == "0000-id-a"
        assert second.name == "0001-id-b"

    def test_there_is_no_current_pointer_file(self, tmp_path: Path) -> None:
        _publish(_staged_bundle(tmp_path, identity="abc123"), tmp_path, "abc123")
        assert not (points_root(tmp_path) / "CURRENT").exists()

    def test_run_number_allocation_retries_past_a_concurrent_reservation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P1a — allocation must be robust to a race: a concurrent winner
        can reserve a number AFTER this process's scan already ran (the scan
        is only an optimisation, per P1a). Simulate that by stubbing the
        scan to report no existing numbers while `.run-0000.reserve`
        already exists on disk (the IDENTITY-INDEPENDENT marker a
        concurrent winner would have created). The allocator must hit
        `FileExistsError` reserving 0000, retry to 0001, succeed, and never
        touch the concurrently-reserved marker."""
        import scripts.dhm_precip.era5_extract_manifest as manifest_mod

        manifest_mod.points_root(tmp_path).mkdir(parents=True, exist_ok=True)
        reservation = manifest_mod.points_root(tmp_path) / ".run-0000.reserve"
        reservation.touch()
        monkeypatch.setattr(manifest_mod, "_taken_run_numbers", lambda _root: [])

        final_dir = _publish(
            _staged_bundle(tmp_path, identity="abc123"), tmp_path, "abc123"
        )
        assert final_dir.name == "0001-abc123"
        # The concurrently-reserved marker is untouched, and no directory
        # for run number 0000 was ever created.
        assert reservation.exists()
        assert not (manifest_mod.points_root(tmp_path) / "0000-abc123").exists()

    def test_two_different_identities_racing_for_the_same_number_diverge(
        self, tmp_path: Path
    ) -> None:
        """BLOCKER (2026-08-17 review) — the OLD allocator reserved a
        number by `mkdir`ing the IDENTITY-LABELLED directory directly, so
        two DIFFERENT identities could both `mkdir` `0000-<identity>`
        successfully (the paths never collide) and both be assigned run
        number 0000 — an ambiguous, identity-dependent order. Simulate the
        race directly: pre-create the identity-INDEPENDENT reservation
        marker `.run-0000.reserve` (as if a concurrent process racing under
        a DIFFERENT identity had already won number 0000), then allocate
        under identity "b". It must land on 0001, never 0000, and no
        `0000-b` directory may ever exist."""
        import scripts.dhm_precip.era5_extract_manifest as manifest_mod

        manifest_mod.points_root(tmp_path).mkdir(parents=True, exist_ok=True)
        (manifest_mod.points_root(tmp_path) / ".run-0000.reserve").touch()

        target = manifest_mod.allocate_published_dir(tmp_path, identity="b")
        assert target.name == "0001-b"
        assert not (manifest_mod.points_root(tmp_path) / "0000-b").exists()

    def test_concurrent_allocation_under_different_identities_never_collides(
        self, tmp_path: Path
    ) -> None:
        """BLOCKER (2026-08-17 review), proven under REAL concurrency (not a
        simulated pre-existing marker): several threads, each allocating
        under a DIFFERENT identity, released from a barrier at the same
        instant. Every result must land on a DISTINCT run number. Against
        the OLD (buggy) allocator this is not flaky — it fails
        deterministically, because every thread's scan sees an empty
        `points_root` and each then `mkdir`s its OWN identity-labelled
        `0000-identity-i` path, which never collide with each other."""
        import threading

        import scripts.dhm_precip.era5_extract_manifest as manifest_mod

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        results: list[Path | None] = [None] * n_threads
        errors: list[BaseException] = []

        def _allocate(i: int) -> None:
            barrier.wait()
            try:
                results[i] = manifest_mod.allocate_published_dir(
                    tmp_path, identity=f"identity-{i}"
                )
            except BaseException as exc:  # noqa: BLE001 - surfaced on the main thread
                errors.append(exc)

        threads = [
            threading.Thread(target=_allocate, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        run_numbers = [int(p.name.split("-", 1)[0]) for p in results if p is not None]
        assert len(run_numbers) == n_threads
        assert len(set(run_numbers)) == n_threads, (
            f"expected {n_threads} distinct run numbers, got {run_numbers}"
        )


class TestPublishRefusesAnInvalidBundle:
    """P4/P4a — validation moves INSIDE `publish_bundle`, applying exactly
    the predicate discovery would apply. A staging directory that fails
    validation is never allocated a numbered directory at all — nothing is
    published, and no partial bundle is left where discovery could find it.
    """

    def test_publish_refuses_a_malformed_staged_bundle(self, tmp_path: Path) -> None:
        staged = _staged_bundle(tmp_path, identity="broken")
        (staged / "series_nearest.nc").unlink()
        with pytest.raises(ExtractionPostConditionError, match="series_nearest.nc"):
            _publish(staged, tmp_path, "broken")
        # Nothing was published — no numbered directory was ever created.
        assert _published_dirs(tmp_path) == []

    def test_publish_refuses_a_payload_modified_after_its_hash_was_computed(
        self, tmp_path: Path
    ) -> None:
        """P4a — the manifest's `payload_sha256s` must reconcile against the
        actual file bytes. A payload edited after `write_extraction_manifest`
        ran must fail publication, not merely a later discovery read.

        The tampering here preserves every D9-required column/value (only
        the extra, non-required `marker` column changes) so this exercises
        the P4a sha256 reconciliation specifically, not the schema checks
        covered separately by `TestPublishEnforcesFullD9Schema`."""
        import polars as pl

        staged = _staged_bundle(tmp_path, identity="tampered")
        frame = pl.read_csv(staged / "station_grid_elevation.csv")
        frame = frame.with_columns(pl.lit("tampered").alias("marker"))
        frame.write_csv(staged / "station_grid_elevation.csv")
        with pytest.raises(
            ExtractionPostConditionError, match="station_grid_elevation.csv"
        ):
            _publish(staged, tmp_path, "tampered")
        assert _published_dirs(tmp_path) == []


class TestPublishEnforcesFullD9Schema:
    """BLOCKER (2026-08-17 review) — the D9 validator used to check only
    variable presence, station COUNTS and CSV readability, so a subtly
    broken writer could hash a malformed payload and publish successfully.
    Each case below passed the OLD validator (proven by stashing this fix
    and re-running); each must now be refused.

    Every tampered file is rewritten AFTER the manifest so the manifest's
    `payload_sha256s` reconcile against the tampered bytes — isolating the
    SCHEMA check under test from the separate P4a sha256-reconciliation
    check above.
    """

    def _restaged(
        self, tmp_path: Path, *, identity: str, elevation_csv_text: str
    ) -> Path:
        staging = _staged_bundle(tmp_path, identity=identity)
        (staging / "station_grid_elevation.csv").write_text(elevation_csv_text)
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        return staging

    def test_duplicate_station_rows_in_the_elevation_csv_are_refused(
        self, tmp_path: Path
    ) -> None:
        header = (
            "station,lat,lon,grid_lat,grid_lon,grid_i,grid_j,offset_km,"
            "station_elev_m,station_elevation_datum,orography_elev_m,"
            "orography_elevation_datum,orography_source,orography_product_id,"
            "orography_product_version,elev_mismatch_m,datum_reconciled,"
            "shared_cell_id,stations_in_cell\n"
        )
        row = (
            "alpha,26.0,85.0,26.0,85.0,0,0,0.0,1500.0,UNKNOWN,1490.0,"
            "LOCAL_MSL,MODEL_OROGRAPHY,p,1,10.0,UNRECONCILED,0_0,1\n"
        )
        staging = self._restaged(
            tmp_path, identity="dup-station", elevation_csv_text=header + row + row
        )
        with pytest.raises(ExtractionPostConditionError, match="duplicate"):
            _publish(staging, tmp_path, "dup-station")
        assert _published_dirs(tmp_path) == []

    def test_elevation_stations_disagreeing_with_the_series_are_refused(
        self, tmp_path: Path
    ) -> None:
        import polars as pl

        staging = _staged_bundle(tmp_path, identity="mismatched-station")
        frame = pl.read_csv(staging / "station_grid_elevation.csv")
        frame = frame.with_columns(pl.Series("station", ["alpha", "gamma"]))
        frame.write_csv(staging / "station_grid_elevation.csv")
        _write_manifest(
            staging,
            identity="mismatched-station",
            payload_sha256s=_payload_sha256s(staging),
        )
        with pytest.raises(ExtractionPostConditionError, match="station set"):
            _publish(staging, tmp_path, "mismatched-station")
        assert _published_dirs(tmp_path) == []

    def test_elevation_csv_missing_required_d9_columns_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The exact shape the OLD `_write_payload_files` fixture used
        (`station, marker` only, two of the eighteen required D9 columns) —
        proof that a subtly broken writer emitting almost none of the
        required columns used to publish successfully."""
        staging = self._restaged(
            tmp_path,
            identity="missing-columns",
            elevation_csv_text="station,marker\nalpha,x\nbeta,x\n",
        )
        with pytest.raises(ExtractionPostConditionError, match="required column"):
            _publish(staging, tmp_path, "missing-columns")
        assert _published_dirs(tmp_path) == []


class TestPublishEnforcesRemainingD9Invariants:
    """BLOCKER (2026-08-17 review) — the validator checked only INCREASING
    (not exactly hourly/equal) series axes, accepted a sensitivity station
    set that was merely a SUBSET, permitted NULL enum columns, and validated
    only the manifest identity STRING — never the required provenance/
    accounting sections. Each case below passed the OLD validator (proven
    by stashing this fix and re-running); each must now be refused."""

    def _series_with(
        self, staging: Path, *, name: str, valid_time_stamps: tuple[str, ...]
    ) -> None:
        import numpy as np
        import xarray as xr

        from scripts.dhm_precip.era5_extract_manifest import points_xarray_encoding

        valid_time = np.array(valid_time_stamps, dtype="datetime64[ns]")
        values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        ds.to_netcdf(
            staging / name,
            engine="h5netcdf",
            encoding=points_xarray_encoding(values.shape),
        )

    def test_a_gap_in_the_valid_time_axis_is_refused(self, tmp_path: Path) -> None:
        identity = "gap-axis"
        staging = _staged_bundle(tmp_path, identity=identity)
        # 00, 01, 03 — skips 02: strictly increasing, but not hourly.
        self._series_with(
            staging,
            name="series_nearest.nc",
            valid_time_stamps=("2021-01-01T00", "2021-01-01T01", "2021-01-01T03"),
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="hourly"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_wrong_compression_level_on_reopen_is_refused(self, tmp_path: Path) -> None:
        import numpy as np
        import xarray as xr

        from scripts.dhm_precip.era5_extract_manifest import points_xarray_encoding

        identity = "wrong-complevel"
        staging = _staged_bundle(tmp_path, identity=identity)
        valid_time = np.array(
            ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"],
            dtype="datetime64[ns]",
        )
        values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
        encoding = points_xarray_encoding(values.shape)
        encoding["precipitation_mm_per_h"]["complevel"] = 1  # frozen spec says 4
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        ds.to_netcdf(
            staging / "series_nearest.nc", engine="h5netcdf", encoding=encoding
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="complevel"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_a_truncated_series_shorter_than_the_declared_coverage_is_refused(
        self, tmp_path: Path
    ) -> None:
        """BLOCKER (2026-08-17 review, P4) — `_write_payload_files` writes
        exactly 3 hours; the OLD validator checked only that those 3 hours
        were exactly hourly, which a severely truncated (e.g. 6-YEAR-short)
        bundle would still satisfy. Declaring the true expected coverage
        (4, here) must refuse a 3-hour bundle."""
        identity = "truncated-coverage"
        staging = _staged_bundle(tmp_path, identity=identity)
        with pytest.raises(ExtractionPostConditionError, match="coverage"):
            _publish(staging, tmp_path, identity, expected_hour_count=4)
        assert _published_dirs(tmp_path) == []

    def test_mismatched_valid_time_axes_between_operators_is_refused(
        self, tmp_path: Path
    ) -> None:
        """BLOCKER (2026-08-17 review, P4) — nothing previously compared the
        two series' `valid_time` axes; a bilinear file written against a
        SHIFTED (but still exactly-hourly, still same-length) axis still
        published, silently pairing mismatched hours."""
        identity = "shifted-bilinear-axis"
        staging = _staged_bundle(tmp_path, identity=identity)
        self._series_with(
            staging,
            name="series_bilinear.nc",
            valid_time_stamps=(
                "2021-01-01T01",
                "2021-01-01T02",
                "2021-01-01T03",
            ),
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="valid_time"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_a_non_finite_value_in_the_primary_series_is_refused(
        self, tmp_path: Path
    ) -> None:
        """BLOCKER (2026-08-17 review, P4) — D11.2 ("no non-finite value in
        the PRIMARY series") used to be checked only in memory, before
        writing; nothing re-verified it on the REOPENED bundle P4a is
        supposed to trust instead of the writer's own word. Bilinear is
        NOT required to be finite (D11.3), so only the nearest file is
        tampered here."""
        import numpy as np
        import xarray as xr

        from scripts.dhm_precip.era5_extract_manifest import points_xarray_encoding

        identity = "non-finite-primary"
        staging = _staged_bundle(tmp_path, identity=identity)
        valid_time = np.array(
            ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"],
            dtype="datetime64[ns]",
        )
        values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
        values[0, 1] = np.nan
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        ds.to_netcdf(
            staging / "series_nearest.nc",
            engine="h5netcdf",
            encoding=points_xarray_encoding(values.shape),
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="non-finite"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_wrong_valid_time_encoding_dtype_on_reopen_is_refused(
        self, tmp_path: Path
    ) -> None:
        """BLOCKER (2026-08-17 review, P4) — the pinned `valid_time`
        units/dtype is hashed whole (`output_encoding`) into
        `extraction_identity` but was never checked on reopen."""
        import numpy as np
        import xarray as xr

        from scripts.dhm_precip.era5_extract_manifest import points_xarray_encoding

        identity = "wrong-time-dtype"
        staging = _staged_bundle(tmp_path, identity=identity)
        valid_time = np.array(
            ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"],
            dtype="datetime64[ns]",
        )
        values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
        encoding = points_xarray_encoding(values.shape)
        encoding["valid_time"]["dtype"] = "int32"  # frozen spec says int64
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        ds.to_netcdf(
            staging / "series_nearest.nc", engine="h5netcdf", encoding=encoding
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="dtype"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_variable_length_station_encoding_is_refused(self, tmp_path: Path) -> None:
        """BLOCKER (2026-08-17 review, P4) — D9's fixed-length `station`
        encoding (`dtype="S1"`) is hashed but was never checked on reopen;
        xarray/h5netcdf's variable-length default still published."""
        import numpy as np
        import xarray as xr

        identity = "variable-length-station"
        staging = _staged_bundle(tmp_path, identity=identity)
        valid_time = np.array(
            ["2021-01-01T00", "2021-01-01T01", "2021-01-01T02"],
            dtype="datetime64[ns]",
        )
        values = np.zeros((len(_STATIONS), valid_time.size), dtype=np.float32)
        ds = xr.Dataset(
            {"precipitation_mm_per_h": (["station", "valid_time"], values)},
            coords={"station": list(_STATIONS), "valid_time": valid_time},
        )
        ds["valid_time"].attrs["timezone"] = "UTC"
        # No 'station' encoding entry at all — xarray/h5netcdf's
        # variable-length string default, not the frozen fixed-length S1.
        ds.to_netcdf(
            staging / "series_nearest.nc",
            engine="h5netcdf",
            encoding={
                "precipitation_mm_per_h": {
                    "dtype": "float32",
                    "zlib": True,
                    "complevel": 4,
                    "chunksizes": (1, valid_time.size),
                    "_FillValue": float("nan"),
                },
                "valid_time": {
                    "units": "hours since 1970-01-01 00:00:00",
                    "dtype": "int64",
                },
            },
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="station"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_null_orography_source_in_the_elevation_csv_is_refused(
        self, tmp_path: Path
    ) -> None:
        header = (
            "station,lat,lon,grid_lat,grid_lon,grid_i,grid_j,offset_km,"
            "station_elev_m,station_elevation_datum,orography_elev_m,"
            "orography_elevation_datum,orography_source,orography_product_id,"
            "orography_product_version,elev_mismatch_m,datum_reconciled,"
            "shared_cell_id,stations_in_cell\n"
        )
        # orography_source deliberately BLANK for 'alpha'.
        row_alpha = (
            "alpha,26.0,85.0,26.0,85.0,0,0,0.0,1500.0,UNKNOWN,1490.0,"
            "LOCAL_MSL,,p,1,10.0,UNRECONCILED,0_0,1\n"
        )
        row_beta = (
            "beta,26.1,85.1,26.1,85.1,1,1,0.0,1900.0,UNKNOWN,1880.0,"
            "LOCAL_MSL,MODEL_OROGRAPHY,p,1,20.0,UNRECONCILED,1_1,1\n"
        )
        identity = "null-enum"
        staging = _staged_bundle(tmp_path, identity=identity)
        (staging / "station_grid_elevation.csv").write_text(
            header + row_alpha + row_beta
        )
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="null"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_sensitivity_missing_one_stations_rows_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A merely-SUBSET station set (missing 'beta' entirely) used to be
        accepted (`<=`); it must now be refused — exact equality."""
        import polars as pl

        identity = "sensitivity-subset"
        staging = _staged_bundle(tmp_path, identity=identity)
        frame = pl.read_csv(staging / "operator_sensitivity.csv")
        frame = frame.filter(
            (pl.col("scope") != "STATION") | (pl.col("station") == "alpha")
        )
        frame.write_csv(staging / "operator_sensitivity.csv")
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="station set"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_sensitivity_uneven_row_counts_per_station_is_refused(
        self, tmp_path: Path
    ) -> None:
        """Every station present, but 'alpha' carries an EXTRA (duplicate)
        row 'beta' lacks — the complete station/season/statistic/quantile
        matrix self-consistency check must catch this even though the
        station SET is equal."""
        import polars as pl

        identity = "sensitivity-uneven"
        staging = _staged_bundle(tmp_path, identity=identity)
        frame = pl.read_csv(staging / "operator_sensitivity.csv")
        extra_alpha_row = frame.filter(
            (pl.col("scope") == "STATION") & (pl.col("station") == "alpha")
        )
        frame = pl.concat([frame, extra_alpha_row])
        frame.write_csv(staging / "operator_sensitivity.csv")
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="duplicate"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_sensitivity_equal_row_counts_but_different_keys_is_refused(
        self, tmp_path: Path
    ) -> None:
        """MAJOR (2026-08-17 review) — the OLD 'complete matrix' check
        compared only ROW COUNTS per station: 'alpha' reporting q0.5 and
        'beta' reporting q0.99 are BOTH one row each, so equal counts alone
        blessed two stations covering entirely DIFFERENT quantiles. The
        exact composite-key-set check must catch this even though the
        counts agree."""
        import polars as pl

        identity = "sensitivity-different-keys"
        staging = _staged_bundle(tmp_path, identity=identity)
        frame = pl.read_csv(staging / "operator_sensitivity.csv")
        frame = frame.with_columns(
            pl.when((pl.col("scope") == "STATION") & (pl.col("station") == "beta"))
            .then(pl.lit(0.99))
            .otherwise(pl.col("quantile"))
            .alias("quantile")
        )
        frame.write_csv(staging / "operator_sensitivity.csv")
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="key set"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_sensitivity_across_station_missing_a_station_scope_key_is_refused(
        self, tmp_path: Path
    ) -> None:
        """MAJOR (2026-08-17 review) — publication previously validated
        NOTHING about ACROSS_STATION coverage: dropping it entirely, or
        leaving it out of step with the STATION-scope key set, still
        published."""
        import polars as pl

        identity = "sensitivity-across-station-missing"
        staging = _staged_bundle(tmp_path, identity=identity)
        frame = pl.read_csv(staging / "operator_sensitivity.csv")
        frame = frame.filter(pl.col("scope") != "ACROSS_STATION")
        frame.write_csv(staging / "operator_sensitivity.csv")
        _write_manifest(
            staging, identity=identity, payload_sha256s=_payload_sha256s(staging)
        )
        with pytest.raises(ExtractionPostConditionError, match="ACROSS_STATION"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_manifest_with_an_incomplete_orography_spec_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "incomplete-orography-spec"
        staging = _staged_bundle(tmp_path, identity=identity)
        write_extraction_manifest(
            ExtractionManifest(
                orography_identity="o",
                extraction_identity=identity,
                operator_id="NEAREST",
                coordinate_table_sha256="a" * 64,
                source_sha256s_by_year={"2001": "b" * 64},
                payload_sha256s=_payload_sha256s(staging),
                orography_spec={},  # empty — previously published cleanly
                orography_source_record=_MANIFEST_OROGRAPHY_SOURCE_RECORD,
                accumulation_diagnostic=_MANIFEST_ACCUMULATION_DIAGNOSTIC,
                station_accounting=_MANIFEST_STATION_ACCOUNTING,
                identity_inputs=_MANIFEST_IDENTITY_INPUTS,
                generated_at=_CLOCK_NOW,
            ),
            staging / "extraction_manifest.json",
        )
        with pytest.raises(ExtractionPostConditionError, match="orography_spec"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_manifest_with_an_empty_station_accounting_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "empty-station-accounting"
        staging = _staged_bundle(tmp_path, identity=identity)
        write_extraction_manifest(
            ExtractionManifest(
                orography_identity="o",
                extraction_identity=identity,
                operator_id="NEAREST",
                coordinate_table_sha256="a" * 64,
                source_sha256s_by_year={"2001": "b" * 64},
                payload_sha256s=_payload_sha256s(staging),
                orography_spec=_MANIFEST_OROGRAPHY_SPEC,
                orography_source_record=_MANIFEST_OROGRAPHY_SOURCE_RECORD,
                accumulation_diagnostic=_MANIFEST_ACCUMULATION_DIAGNOSTIC,
                station_accounting={},  # empty — previously published cleanly
                identity_inputs=_MANIFEST_IDENTITY_INPUTS,
                generated_at=_CLOCK_NOW,
            ),
            staging / "extraction_manifest.json",
        )
        with pytest.raises(ExtractionPostConditionError, match="station_accounting"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_manifest_with_incomplete_identity_inputs_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "incomplete-identity-inputs"
        staging = _staged_bundle(tmp_path, identity=identity)
        write_extraction_manifest(
            ExtractionManifest(
                orography_identity="o",
                extraction_identity=identity,
                operator_id="NEAREST",
                coordinate_table_sha256="a" * 64,
                source_sha256s_by_year={"2001": "b" * 64},
                payload_sha256s=_payload_sha256s(staging),
                orography_spec=_MANIFEST_OROGRAPHY_SPEC,
                orography_source_record=_MANIFEST_OROGRAPHY_SOURCE_RECORD,
                accumulation_diagnostic=_MANIFEST_ACCUMULATION_DIAGNOSTIC,
                station_accounting=_MANIFEST_STATION_ACCOUNTING,
                identity_inputs={},  # empty — the digest becomes uninterpretable
                generated_at=_CLOCK_NOW,
            ),
            staging / "extraction_manifest.json",
        )
        with pytest.raises(ExtractionPostConditionError, match="identity_inputs"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []


class TestPublishEnforcesProvenanceAndAccountingValues:
    """MAJOR (2026-08-17 review) — `_assert_required_sections_present` used
    to check KEY PRESENCE only: an `orography_spec` missing P7a's
    `provenance` labels, an empty `downloaded_files` list, a FAILING
    accumulation diagnostic, an arbitrary `station_accounting` operator/
    station map, or counts that did not reconcile to `n_hours` — all
    previously published cleanly once the payload hashes matched. A
    matching hash proves the bytes were not tampered AFTER the manifest was
    written; it proves nothing about whether the manifest's claims were
    true to begin with."""

    def _manifest_with(
        self,
        staging: Path,
        *,
        identity: str,
        orography_spec: dict[str, object] | None = None,
        orography_source_record: dict[str, object] | None = None,
        accumulation_diagnostic: dict[str, object] | None = None,
        station_accounting: dict[str, dict[str, dict[str, object]]] | None = None,
    ) -> None:
        write_extraction_manifest(
            ExtractionManifest(
                orography_identity="o",
                extraction_identity=identity,
                operator_id="NEAREST",
                coordinate_table_sha256="a" * 64,
                source_sha256s_by_year={"2001": "b" * 64},
                payload_sha256s=_payload_sha256s(staging),
                orography_spec=orography_spec
                if orography_spec is not None
                else _MANIFEST_OROGRAPHY_SPEC,
                orography_source_record=orography_source_record
                if orography_source_record is not None
                else _MANIFEST_OROGRAPHY_SOURCE_RECORD,
                accumulation_diagnostic=accumulation_diagnostic
                if accumulation_diagnostic is not None
                else _MANIFEST_ACCUMULATION_DIAGNOSTIC,
                station_accounting=station_accounting
                if station_accounting is not None
                else _MANIFEST_STATION_ACCOUNTING,
                identity_inputs=_MANIFEST_IDENTITY_INPUTS,
                generated_at=_CLOCK_NOW,
            ),
            staging / "extraction_manifest.json",
        )

    def test_orography_spec_missing_provenance_labels_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "missing-provenance"
        staging = _staged_bundle(tmp_path, identity=identity)
        spec = {k: v for k, v in _MANIFEST_OROGRAPHY_SPEC.items() if k != "provenance"}
        self._manifest_with(staging, identity=identity, orography_spec=spec)
        with pytest.raises(ExtractionPostConditionError, match="provenance"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_orography_spec_with_overlapping_provenance_labels_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A field claimed BOTH machine-verified AND operator-attested is
        exactly the false-provenance-by-construction defect P7a exists to
        rule out."""
        identity = "overlapping-provenance"
        staging = _staged_bundle(tmp_path, identity=identity)
        spec = dict(_MANIFEST_OROGRAPHY_SPEC)
        spec["provenance"] = {
            "machine_verified_fields": ["product_id", "download_url"],
            "operator_attested_fields": ["download_url", "product_version"],
        }
        self._manifest_with(staging, identity=identity, orography_spec=spec)
        with pytest.raises(ExtractionPostConditionError, match="disjoint"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_orography_source_record_with_no_downloaded_files_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "no-downloaded-files"
        staging = _staged_bundle(tmp_path, identity=identity)
        record = dict(_MANIFEST_OROGRAPHY_SOURCE_RECORD)
        record["downloaded_files"] = []
        self._manifest_with(staging, identity=identity, orography_source_record=record)
        with pytest.raises(ExtractionPostConditionError, match="downloaded_files"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_orography_source_record_with_a_bad_sha256_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "bad-sha256"
        staging = _staged_bundle(tmp_path, identity=identity)
        record = dict(_MANIFEST_OROGRAPHY_SOURCE_RECORD)
        record["downloaded_files"] = [
            {"path": "raw.nc", "sha256": "not-a-sha256", "size_bytes": 1}
        ]
        self._manifest_with(staging, identity=identity, orography_source_record=record)
        with pytest.raises(ExtractionPostConditionError, match="sha256"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_a_failing_accumulation_diagnostic_is_refused(self, tmp_path: Path) -> None:
        """D5.2 — publication requires a PASSING diagnostic; a recorded
        FAILING one (monotone_within_day=False) must never publish, even
        though every required KEY is present."""
        identity = "failing-diagnostic"
        staging = _staged_bundle(tmp_path, identity=identity)
        diagnostic = dict(_MANIFEST_ACCUMULATION_DIAGNOSTIC)
        diagnostic["monotone_within_day"] = False
        self._manifest_with(
            staging, identity=identity, accumulation_diagnostic=diagnostic
        )
        with pytest.raises(ExtractionPostConditionError, match="monotone_within_day"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_station_accounting_with_a_third_operator_key_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "extra-operator"
        staging = _staged_bundle(tmp_path, identity=identity)
        accounting = dict(_MANIFEST_STATION_ACCOUNTING)
        accounting["CUBIC_SPLINE"] = accounting["NEAREST"]
        self._manifest_with(staging, identity=identity, station_accounting=accounting)
        with pytest.raises(ExtractionPostConditionError, match="operator keys"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_station_accounting_missing_a_station_the_series_carries_is_refused(
        self, tmp_path: Path
    ) -> None:
        identity = "accounting-missing-station"
        staging = _staged_bundle(tmp_path, identity=identity)
        accounting = {
            op: {s: v for s, v in by_station.items() if s != "beta"}
            for op, by_station in _MANIFEST_STATION_ACCOUNTING.items()
        }
        self._manifest_with(staging, identity=identity, station_accounting=accounting)
        with pytest.raises(ExtractionPostConditionError, match="station set"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []

    def test_station_accounting_counts_that_do_not_reconcile_is_refused(
        self, tmp_path: Path
    ) -> None:
        """D11 — `n_finite + n_nan + n_inf` must equal `n_hours`; a manifest
        that carries every REQUIRED key but inconsistent VALUES (e.g. a
        copy-paste error) must be refused, not merely a missing key."""
        identity = "inconsistent-counts"
        staging = _staged_bundle(tmp_path, identity=identity)
        accounting = {
            op: dict(by_station)
            for op, by_station in _MANIFEST_STATION_ACCOUNTING.items()
        }
        accounting["NEAREST"] = dict(accounting["NEAREST"])
        accounting["NEAREST"]["alpha"] = {
            **accounting["NEAREST"]["alpha"],
            "n_hours": 5,  # frozen fixture's n_finite/n_nan sum to 3, not 5
        }
        self._manifest_with(staging, identity=identity, station_accounting=accounting)
        with pytest.raises(ExtractionPostConditionError, match="do not reconcile"):
            _publish(staging, tmp_path, identity)
        assert _published_dirs(tmp_path) == []


class TestExtractionIdentityRecomputation:
    """MAJOR (2026-08-17 review) — `extraction_identity` disappeared once
    computed: nothing downstream could recompute or interpret the digest.
    `ExtractionIdentityInputs` + `recompute_extraction_identity` close that
    gap; this proves the round-trip actually works and actually detects a
    mutation."""

    def test_recompute_matches_the_original_digest(self) -> None:
        inputs = ExtractionIdentityInputs(**_BASE_KWARGS)  # type: ignore[arg-type]
        original = extraction_identity(**_BASE_KWARGS)  # type: ignore[arg-type]
        assert recompute_extraction_identity(inputs.canonical_payload()) == original

    def test_recompute_detects_a_mutated_snapshot(self) -> None:
        inputs = ExtractionIdentityInputs(**_BASE_KWARGS)  # type: ignore[arg-type]
        original = extraction_identity(**_BASE_KWARGS)  # type: ignore[arg-type]
        payload = inputs.canonical_payload()
        payload["value_inputs"]["wet_threshold_mm_per_h"] = 999.0  # type: ignore[index]
        assert recompute_extraction_identity(payload) != original
