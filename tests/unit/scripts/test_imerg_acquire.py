"""Plan 211 (M-A5b) task T1 — granule-name construction, window/box
arithmetic and missing-granule accounting, exercised entirely against a fake
HTTP client (no network in CI). The one real network probe permitted by the
plan (the D1 contract observation) was run manually against the live GES
DISC archive during implementation — see the module docstring's residual-
risk note in `imerg_acquire.py`; it is not repeated here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import h5py
import numpy as np
import pytest

from scripts.dhm_precip import imerg_acquire as ia
from scripts.dhm_precip import imerg_extract as ie
from scripts.dhm_precip.era5_request import STUDY_AREA

if TYPE_CHECKING:
    from pathlib import Path

# --- a synthetic granule fixture, carrying the D1 structure OBSERVED on the
# real GES DISC probe granule (2026-08-28) — never a download inside a test.


def _write_fake_granule(
    path: Path,
    *,
    fill_value: float = -9999.9,
    value_at_station: float = 1.5,
    lat_count: int = 1800,
    lon_count: int = 3600,
    product_version: str = "07B",
    file_header_filename: str | None = None,
) -> None:
    """`file_header_filename` overrides the name embedded in the granule's
    own `FileHeader.FileName` — the field D1's revision cross-check compares
    against the PATH (same axis). Fixture files are written under a scratch
    name and then copied to their real granule name by the fake client, so
    every fixture that will be served as a granule must declare that real
    name here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lat = np.round(np.linspace(-89.95, 89.95, lat_count), 6).astype(np.float32)
    lon = np.round(np.linspace(-179.95, 179.95, lon_count), 6).astype(np.float32)
    precip = np.full((1, lon_count, lat_count), fill_value, dtype=np.float32)
    # place a real value near the middle so extraction tests have something
    # to find without depending on exact index arithmetic
    precip[0, lon_count // 2, lat_count // 2] = value_at_station
    with h5py.File(path, "w") as f:
        f.attrs["FileHeader"] = (
            f"FileName={file_header_filename or path.name};\n"
            "AlgorithmVersion=3IMERGHH;\n"
            f"ProductVersion={product_version};\n"
        ).encode()
        grid = f.create_group("Grid")
        grid.attrs["GridHeader"] = (
            b"BinMethod=ARITHMETIC_MEAN;\nRegistration=CENTER;\n"
            b"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
            b"NorthBoundingCoordinate=90;\nSouthBoundingCoordinate=-90;\n"
            b"EastBoundingCoordinate=180;\nWestBoundingCoordinate=-180;\n"
            b"Origin=SOUTHWEST;\n"
        )
        grid.create_dataset("lat", data=lat)
        grid.create_dataset("lon", data=lon)
        ds = grid.create_dataset("precipitation", data=precip)
        ds.attrs["DimensionNames"] = b"time,lon,lat"
        ds.attrs["units"] = b"mm/hr"
        ds.attrs["_FillValue"] = fill_value


@dataclass
class FakeImergHttpClient:
    """No-network double for `ImergHttpClient`. `listings` maps a directory
    URL to the raw text it should return; `granule_bytes` maps a full
    granule URL to the fixture bytes `download_to_path` should write.
    `missing_urls` simulates a 404."""

    listings: dict[str, str] = field(default_factory=dict)
    granule_bytes: dict[str, Path] = field(default_factory=dict)
    missing_urls: set[str] = field(default_factory=set)
    list_calls: list[str] = field(default_factory=list)
    download_calls: list[str] = field(default_factory=list)

    def list_directory(self, url: str) -> str:
        self.list_calls.append(url)
        if url in self.missing_urls:
            raise ia.ImergGranuleMissingError(f"no listing for {url}")
        return self.listings.get(url, "")

    def download_to_path(self, *, url: str, target: Path) -> None:
        self.download_calls.append(url)
        if url in self.missing_urls:
            raise ia.ImergGranuleMissingError(f"granule not found: {url}")
        source = self.granule_bytes[url]
        target.write_bytes(source.read_bytes())


def _contract_kwargs() -> dict[str, Any]:
    """Every `ImergReadContract` field except `granule_revision`, so a test
    can vary exactly one of them."""
    return {
        "hdf5_variable_path": ia.HDF5_VARIABLE_PATH,
        "dimension_names": ("time", "lon", "lat"),
        "coordinate_registration": "CENTER",
        "longitude_convention": "SIGNED_180",
        "units": "mm/hr",
        "fill_value": -9999.9,
        "grid_shape": ia.EXPECTED_GRID_SHAPE,
        "lat_vector": tuple(np.linspace(-89.95, 89.95, 1800).tolist()),
        "lon_vector": tuple(np.linspace(-179.95, 179.95, 3600).tolist()),
        "grid_spacing_deg": 0.1,
        "file_header_product_version": "07B",
    }


def _reference_read_contract() -> dict[str, object]:
    """A contract that SATISFIES D1, not merely one carrying its field names.
    ⛔ These fixtures used `dict.fromkeys(fields, "recorded")` and still
    derived as COMPLETE — key presence was the whole check."""
    return ia.ImergReadContract(
        **_contract_kwargs(),
        granule_revision=ia.PINNED_GRANULE_REVISION_PER_PLAN,
    ).as_manifest_dict()


def _derivation_complete_manifest(
    *, granule_checksums: dict[str, str] | None = None, **overrides: Any
) -> ia.ImergAcquisitionManifest:
    """A manifest that DERIVES as COMPLETE: every half-hour start of the
    pinned D5 window is accounted for, retrieved or missing. Each test
    overrides the one field it is about."""
    checksums = granule_checksums or {}
    retrieved_starts = {ia.parse_granule_filename(name)[0].start for name in checksums}
    fields: dict[str, Any] = {
        "route": ia.ROUTE,
        "collection_short_name": ia.COLLECTION_SHORT_NAME,
        "granule_revision": ia.PINNED_GRANULE_REVISION_PER_PLAN,
        "requested_window_start": ia.FIRST_GRANULE_START,
        "requested_window_end": ia.LAST_GRANULE_START,
        "box": ia.STUDY_BOX,
        "read_contract": _reference_read_contract(),
        "requested": ia.EXPECTED_GRANULE_COUNT,
        "retrieved": len(checksums),
        "missing": tuple(
            start.isoformat()
            for start in ia.all_granule_starts()
            if start not in retrieved_starts
        ),
        "granule_checksums": checksums,
        "granule_retrieved_at": {name: _clock() for name in checksums},
        "retrospective": True,
        "generated_at": _clock(),
    }
    fields.update(overrides)
    return ia.ImergAcquisitionManifest(**fields)


def _clock() -> datetime:
    return datetime(2026, 8, 28, 13, 0, tzinfo=UTC)


def _sleep(_seconds: float) -> None:
    return None


# --- granule-name construction (D1) ---


class TestGranuleIdentity:
    def test_filename_matches_the_observed_gesdisc_naming_pattern(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        assert (
            granule.filename(revision="V07B")
            == "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )

    def test_remote_url_uses_year_and_day_of_year_directory(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 30, tzinfo=UTC))
        url = granule.remote_url(revision="V07B")
        assert url == (
            f"{ia.GESDISC_BASE_URL}/2026/240/"
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S073000-E075959.0450.V07B.HDF5"
        )

    def test_minute_of_day_is_the_start_time_minutes_since_midnight(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 23, 30, tzinfo=UTC))
        assert granule.minute_of_day == 23 * 60 + 30

    @pytest.mark.parametrize(
        "minute",
        [1, 15, 29, 45, 59],
    )
    def test_construction_rejects_a_non_half_hour_aligned_start(
        self, minute: int
    ) -> None:
        with pytest.raises(ValueError, match="half-hour aligned"):
            ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, minute, tzinfo=UTC))

    def test_construction_rejects_a_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="tz-aware UTC"):
            ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0))  # noqa: DTZ001

    def test_parse_granule_filename_round_trips_a_real_observed_name(self) -> None:
        name = "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
        granule, revision = ia.parse_granule_filename(name)
        assert granule.start == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        assert revision == "V07C"

    def test_parse_granule_filename_rejects_an_unrecognised_pattern(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="does not match"):
            ia.parse_granule_filename("not-an-imerg-granule.HDF5")


class TestResolveGranuleFilename:
    def test_finds_the_matching_entry_regardless_of_revision_letter(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        listing = (
            '<a href="3B-HHR-E.MS.MRG.3IMERG.20260828-S063000-E065959.0390.'
            'V07C.HDF5">x</a>\n'
            '<a href="3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.'
            'V07C.HDF5">x</a>\n'
        )
        assert (
            ia.resolve_granule_filename(listing, granule)
            == "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
        )

    def test_raises_missing_when_no_entry_matches(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        with pytest.raises(ia.ImergGranuleMissingError):
            ia.resolve_granule_filename("<a href='unrelated.HDF5'>x</a>", granule)

    def test_raises_on_directory_ambiguity(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        listing = (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5\n"
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5\n"
        )
        with pytest.raises(ia.ImergReadContractError, match="ambiguous"):
            ia.resolve_granule_filename(listing, granule)


# --- window/box arithmetic (D5) ---


class TestWindowArithmetic:
    def test_granule_count_equals_the_measured_105216(self) -> None:
        # DERIVED from the pinned window's arithmetic, never restated as a
        # bare literal (D10) — and it still has to equal the measured 105,216.
        starts = list(ia.all_granule_starts())
        assert ia.EXPECTED_GRANULE_COUNT == len(starts) == 105_216

    def test_first_granule_start_is_one_hour_before_the_study_axis(self) -> None:
        # D5: the axis's first hour (2020-01-01 00:00) needs the granules of
        # 2019-12-31 23:00-24:00 -> retrieval starts at 23:00 the day before.
        assert datetime(2019, 12, 31, 23, 0, tzinfo=UTC) == ia.FIRST_GRANULE_START

    def test_last_granule_start_is_22_30_not_23_30(self) -> None:
        # D5: the axis's last hour (2025-12-31 23:00) is built from granules
        # starting at 22:00 and 22:30 that day -> the LAST granule requested
        # is 22:30, never 23:30.
        assert datetime(2025, 12, 31, 22, 30, tzinfo=UTC) == ia.LAST_GRANULE_START

    def test_all_granule_starts_are_exactly_half_hour_spaced_with_no_gaps(
        self,
    ) -> None:
        starts = list(ia.all_granule_starts())
        diffs = {b - a for a, b in zip(starts, starts[1:], strict=False)}
        assert diffs == {timedelta(minutes=30)}

    def test_study_box_is_imported_verbatim_from_era5_request(self) -> None:
        assert ia.STUDY_BOX == STUDY_AREA == (31, 80, 26, 89)


# --- D1 read contract, observed on a synthetic granule ---


class TestReadContract:
    def test_observed_contract_matches_the_real_probe_granule_structure(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        contract = ia.observe_read_contract(path)
        assert contract.hdf5_variable_path == "/Grid/precipitation"
        assert contract.dimension_names == ("time", "lon", "lat")
        assert contract.coordinate_registration == "CENTER"
        assert contract.longitude_convention == "SIGNED_180"
        assert contract.units == "mm/hr"
        assert contract.granule_revision == "V07B"
        assert contract.file_header_product_version == "07B"
        assert contract.grid_shape == (1, 3600, 1800)

    def test_a_wrong_units_attribute_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        with h5py.File(path, "a") as f:
            f["Grid/precipitation"].attrs["units"] = b"mm"
        with pytest.raises(ia.ImergReadContractError, match="units"):
            ia.observe_read_contract(path)

    def test_assert_contract_consistent_passes_for_an_identical_contract(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        contract = ia.observe_read_contract(path)
        ia.assert_contract_consistent(contract, frozen=contract)

    def test_assert_contract_consistent_raises_on_a_revision_mismatch(self) -> None:
        """Constructed directly (not via `observe_read_contract`, which now
        enforces the D1 pin and would reject a V07C-revision granule before
        this intra-run consistency check is ever reached) — this test is
        about `assert_contract_consistent`'s own drift-within-a-run
        behaviour, orthogonal to the pin."""
        base_kwargs = _contract_kwargs()
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        observed = ia.ImergReadContract(granule_revision="V07C", **base_kwargs)
        with pytest.raises(ia.ImergReadContractError, match="V07C.*V07B|V07B.*V07C"):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_a_lat_vector_perturbed_below_six_decimals_is_rejected(self) -> None:
        """D1 (locking) — the stated REASON the vectors are frozen: "two
        conforming granules ... can map the same station to different cells
        ... verifying only the field name is not enough". Every other field
        is identical here; only one latitude moves, by 1e-9. ⛔ That is
        BELOW the six decimals the vectors used to be rounded to, so the
        rounding itself defeated the exact-vector requirement."""
        base_kwargs = _contract_kwargs()
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        perturbed = dict(base_kwargs)
        lat = base_kwargs["lat_vector"]
        assert isinstance(lat, tuple)
        perturbed["lat_vector"] = (lat[0] + 1e-9, *lat[1:])
        observed = ia.ImergReadContract(granule_revision="V07B", **perturbed)
        with pytest.raises(ia.ImergReadContractError, match="other than revision"):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_a_lon_vector_shifted_by_one_cell_is_rejected(self) -> None:
        base_kwargs = _contract_kwargs()
        frozen = ia.ImergReadContract(granule_revision="V07B", **base_kwargs)
        shifted = dict(base_kwargs)
        lon = base_kwargs["lon_vector"]
        assert isinstance(lon, tuple)
        shifted["lon_vector"] = tuple(v + 0.1 for v in lon)
        observed = ia.ImergReadContract(granule_revision="V07B", **shifted)
        with pytest.raises(ia.ImergReadContractError, match="other than revision"):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_observed_vectors_are_exact_never_rounded_to_six_decimals(
        self, tmp_path: Path
    ) -> None:
        """D1 (locking) — the vectors are recorded EXACTLY. The fixture's
        own float32 -89.95 reads back as -89.94999694824219; a six-decimal
        round would record -89.95 and silently absorb any drift smaller
        than 1e-6."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        contract = ia.observe_read_contract(path)
        assert contract.lat_vector[0] == float(np.float32(-89.95))
        assert contract.lat_vector[0] != round(float(np.float32(-89.95)), 6)


class TestRevisionPin:
    """D1 (locking) — 'Current Early is V07B; if a granule carries another
    revision, stop and report rather than blending.' The pin is ENFORCED,
    not documentary."""

    def test_observe_read_contract_rejects_a_granule_whose_revision_is_not_v07b(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
        )
        _write_fake_granule(path, product_version="07C")
        with pytest.raises(ia.ImergReadContractError, match="V07C.*V07B|V07B.*V07C"):
            ia.observe_read_contract(path)

    def test_observe_read_contract_rejects_a_path_embedded_name_disagreement(
        self, tmp_path: Path
    ) -> None:
        """The PATH says the pinned V07B, but the granule's own embedded
        `FileHeader.FileName` says V07C — a renamed file. The revision must
        never be inferred from the path alone."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(
            path,
            file_header_filename=(
                "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07C.HDF5"
            ),
        )
        with pytest.raises(ia.ImergReadContractError, match="disagrees"):
            ia.observe_read_contract(path)

    def test_the_early_run_rt_h5_embedded_extension_is_not_a_revision_drift(
        self, tmp_path: Path
    ) -> None:
        """⛔ MEASURED 2026-08-31 on the real GES DISC granule
        `3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.HDF5`: its
        own `FileHeader.FileName` reads `...V07B.RT-H5`, because EARLY *is*
        the real-time run while the archive stores the file as `.HDF5`. The
        two names differ in EXTENSION ONLY — the revision field agreed
        (`V07B` both sides) — but the cross-check compared them with the
        archive pattern, whose `.HDF5` literal no real Early granule's
        embedded name can satisfy. The probe therefore halted on EVERY
        granule, reporting a revision disagreement that did not exist."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.HDF5"
        )
        _write_fake_granule(
            path,
            file_header_filename=(
                "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.RT-H5"
            ),
        )
        assert ia.observe_read_contract(path).granule_revision == "V07B"

    def test_an_rt_h5_embedded_name_of_the_wrong_revision_is_still_rejected(
        self, tmp_path: Path
    ) -> None:
        """Accepting the RT extension must not weaken the pin itself: a
        `.RT-H5` embedded name carrying a DIFFERENT revision is still a
        renamed file."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.HDF5"
        )
        _write_fake_granule(
            path,
            file_header_filename=(
                "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07C.RT-H5"
            ),
        )
        with pytest.raises(ia.ImergReadContractError, match="disagrees"):
            ia.observe_read_contract(path)

    def test_an_embedded_name_of_the_right_revision_but_wrong_time_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Plan 224 review — the `.RT-H5` relaxation must not degrade the
        cross-check to a REVISION comparison. Extraction takes a granule's
        timestamp from its PATH, so an archive file whose own embedded name
        carries the pinned revision but a DIFFERENT half-hour is contents from
        one time filed under another: the COMPLETE names must agree."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.HDF5"
        )
        _write_fake_granule(
            path,
            file_header_filename=(
                "3B-HHR-E.MS.MRG.3IMERG.20200715-S003000-E005959.0030.V07B.RT-H5"
            ),
        )
        with pytest.raises(ia.ImergReadContractError, match="disagrees"):
            ia.observe_read_contract(path)

    def test_an_unparseable_embedded_name_is_rejected(self, tmp_path: Path) -> None:
        """A `FileHeader.FileName` that is not an IMERG name at all must be
        refused as such, not silently compared as an opaque string."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.HDF5"
        )
        _write_fake_granule(path, file_header_filename="not-an-imerg-name.HDF5")
        with pytest.raises(ia.ImergReadContractError, match="naming pattern"):
            ia.observe_read_contract(path)

    def test_the_archive_filename_parser_stays_strict_about_its_extension(
        self,
    ) -> None:
        """⛔ Only the EMBEDDED name may carry `.RT-H5`. `parse_granule_
        filename` reads names off the archive listing and the raw/ directory,
        where the extension is `.HDF5`; relaxing it there would let a
        differently-stored file be treated as an acquired granule."""
        with pytest.raises(ia.ImergReadContractError, match="naming pattern"):
            ia.parse_granule_filename(
                "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.RT-H5"
            )

    def test_a_product_version_that_differs_from_the_filename_is_not_a_violation(
        self, tmp_path: Path
    ) -> None:
        """⛔ The 2026-08-28 probe MEASURED a granule whose filename (and
        embedded `FileHeader.FileName`) read V07C while its
        `FileHeader.ProductVersion` read `07B`: the reprocessing-generation
        letter and the ATBD product version are different axes. Equating
        them would reject legitimate granules, so `ProductVersion` is
        RECORDED as its own frozen contract field and never compared with
        the filename revision."""
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path, product_version="07A")
        contract = ia.observe_read_contract(path)
        assert contract.granule_revision == "V07B"
        assert contract.file_header_product_version == "07A"

    def test_a_granule_missing_the_file_header_attribute_is_rejected(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / (
            "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E072959.0420.V07B.HDF5"
        )
        _write_fake_granule(path)
        with h5py.File(path, "a") as f:
            del f.attrs["FileHeader"]
        with pytest.raises(ia.ImergReadContractError, match="FileHeader"):
            ia.observe_read_contract(path)


# --- D10 disk projection ---


class TestDiskProjection:
    def test_projection_multiplies_probe_size_by_the_measured_granule_count(
        self,
    ) -> None:
        projection = ia.compute_projection(
            probe_granule_bytes=8_000_000, free_disk_bytes=10**12
        )
        assert projection.projected_total_bytes == 8_000_000 * 105_216
        assert projection.fits is True

    def test_projection_reports_a_projection_that_does_not_fit(self) -> None:
        projection = ia.compute_projection(
            probe_granule_bytes=8_000_000, free_disk_bytes=1
        )
        assert projection.fits is False


# --- acquisition manifest write/read round-trip ---


class TestAcquisitionManifest:
    def test_round_trips_through_disk(self, tmp_path: Path) -> None:
        manifest = ia.ImergAcquisitionManifest(
            route=ia.ROUTE,
            collection_short_name=ia.COLLECTION_SHORT_NAME,
            granule_revision="V07B",
            requested_window_start=ia.FIRST_GRANULE_START,
            requested_window_end=ia.LAST_GRANULE_START,
            box=ia.STUDY_BOX,
            read_contract={"units": "mm/hr"},
            requested=1,
            retrieved=1,
            missing=(),
            granule_checksums={"a.HDF5": "deadbeef"},
            granule_retrieved_at={"a.HDF5": _clock()},
            retrospective=True,
            generated_at=_clock(),
        )
        path = tmp_path / "acquisition_manifest.json"
        ia.write_acquisition_manifest(manifest, path)
        reread = ia.read_acquisition_manifest(path)
        assert reread is not None
        assert reread.granule_revision == "V07B"
        assert reread.retrospective is True
        assert reread.granule_checksums == {"a.HDF5": "deadbeef"}

    def test_read_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert ia.read_acquisition_manifest(tmp_path / "missing.json") is None

    def test_lives_outside_the_disposable_raw_dir(self, tmp_path: Path) -> None:
        """D10 — 'the acquisition manifest is PERMANENT'; the raw granules
        are disposable. The manifest must not live INSIDE the raw dir, or
        discarding the raw dir takes the manifest with it."""
        data_root = tmp_path / "data_root"
        manifest_path = ia.acquisition_manifest_path(data_root)
        raw_dir = ia.imerg_raw_dir(data_root)
        assert raw_dir not in manifest_path.parents


# --- missing-granule accounting (D4's upstream half — a window retrieval
# with some granules absent from the archive), no network ---


class TestRetrieveWindow:
    def test_accounts_for_missing_granules_without_failing_the_run(
        self, tmp_path: Path
    ) -> None:
        starts = [
            datetime(2026, 8, 28, 6, 0, tzinfo=UTC),
            datetime(2026, 8, 28, 6, 30, tzinfo=UTC),
            datetime(2026, 8, 28, 7, 0, tzinfo=UTC),
        ]
        fixture_dir = tmp_path / "fixtures"
        good_granules = {
            starts[0]: fixture_dir / "g0.h5",
            starts[2]: fixture_dir / "g2.h5",
        }
        for start, path in good_granules.items():
            _write_fake_granule(
                path,
                file_header_filename=ia.ImergGranuleId(start=start).filename(
                    revision="V07B"
                ),
            )

        client = FakeImergHttpClient()
        for start in starts:
            granule = ia.ImergGranuleId(start=start)
            directory_url = granule.directory_url()
            # All three starts fall on the same calendar day, so "missing"
            # means "absent from that shared day's listing" — never listing
            # it, rather than marking the whole directory unreachable.
            if start in good_granules:
                filename = granule.filename(revision="V07B")
                client.listings[directory_url] = (
                    client.listings.get(directory_url, "") + filename + "\n"
                )
                client.granule_bytes[f"{directory_url}{filename}"] = good_granules[
                    start
                ]
            else:
                client.listings.setdefault(directory_url, "")

        data_root = tmp_path / "data_root"
        report, frozen, checksums = ia.retrieve_window(
            iter(starts),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        assert report.requested == 3
        assert report.retrieved == 2
        assert report.missing == (starts[1].isoformat(),)
        assert frozen is not None
        assert len(checksums) == 2

    def test_retrieved_granule_files_are_written_under_the_imerg_raw_dir(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        assert (ia.imerg_raw_dir(data_root) / filename).exists()

    def test_a_second_run_does_not_re_download_an_already_acquired_granule(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        # ⚠️ "Retrieve once; never re-download what is on disk" (plan Verify)
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]

    def test_writes_the_permanent_acquisition_manifest(self, tmp_path: Path) -> None:
        """D9/D10 (locking) — `retrieve_window` itself must write the
        acquisition manifest; T2 (and any future bulk-retrieval caller)
        depends on it being written HERE, not merely returned in memory."""
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter([start]),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.requested == 1
        assert manifest.retrieved == 1
        assert manifest.granule_checksums
        assert filename in manifest.granule_retrieved_at
        # A single-granule `retrieve_window` call does not account for the
        # pinned window, so it never DERIVES as complete.
        assert ia.is_complete_acquisition(manifest) is False

    def test_a_two_granule_retrieval_starting_at_the_window_edge_is_partial(
        self, tmp_path: Path
    ) -> None:
        """⛔ Starting at the pinned window's own first granule is NOT enough
        to be COMPLETE: completeness is DERIVED from the record, and two
        granules do not account for the window's 105,216 half-hour starts."""
        starts = [
            ia.FIRST_GRANULE_START,
            ia.FIRST_GRANULE_START + timedelta(minutes=30),
        ]
        client = FakeImergHttpClient()
        for index, start in enumerate(starts):
            granule = ia.ImergGranuleId(start=start)
            filename = granule.filename(revision="V07B")
            fixture = tmp_path / f"fixture-{index}.h5"
            _write_fake_granule(fixture, file_header_filename=filename)
            client.listings[granule.directory_url()] = filename
            client.granule_bytes[f"{granule.directory_url()}{filename}"] = fixture
        data_root = tmp_path / "data_root"
        ia.retrieve_window(
            iter(starts),
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        # requested (2) != EXPECTED_GRANULE_COUNT (105,216) -> not complete,
        # even though every requested granule was retrieved.
        assert ia.is_complete_acquisition(manifest) is False


class TestRunProbe:
    def test_probe_downloads_exactly_one_granule_and_writes_the_manifest(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        granule = ia.ImergGranuleId(start=start)
        filename = granule.filename(revision="V07B")
        fixture = tmp_path / "fixture.h5"
        _write_fake_granule(fixture, file_header_filename=filename)
        client = FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )
        data_root = tmp_path / "data_root"
        projection = ia.run_probe(
            granule_start=start,
            client=client,
            data_root=data_root,
            clock=_clock,
            sleep=_sleep,
            free_disk_bytes=10**12,
        )
        assert client.download_calls == [f"{granule.directory_url()}{filename}"]
        # D10 — the projection is MEASURED from this one granule, never
        # restated: probe size x the pinned window's granule count.
        assert projection.projected_granule_count == ia.EXPECTED_GRANULE_COUNT
        assert projection.projected_total_bytes == (
            projection.probe_granule_bytes * ia.EXPECTED_GRANULE_COUNT
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.granule_revision == "V07B"
        assert manifest.retrospective is True
        # A one-granule probe never derives as a complete acquisition, so T2
        # cannot mistake it for one (D9).
        assert ia.is_complete_acquisition(manifest) is False
        # A probe's requested window is its OWN single granule, never the
        # full pinned D5 window — D9's "a mandated recent probe outside the
        # 2020-2025 window must not [be mistaken for a complete acquisition
        # covering it]".
        assert manifest.requested_window_start == start
        assert manifest.requested_window_end == start


class TestCliBoundaryParsing:
    """Minor findings: naive `--granule-start` values must be rejected, not
    silently interpreted in the host timezone; disk-free must be measured
    against `--data-root`, not `.`."""

    def test_parse_cli_utc_timestamp_rejects_a_naive_value(self) -> None:
        with pytest.raises(ValueError, match="UTC offset"):
            ia.parse_cli_utc_timestamp("2026-08-28T07:00:00")

    def test_parse_cli_utc_timestamp_accepts_an_explicit_offset(self) -> None:
        parsed = ia.parse_cli_utc_timestamp("2026-08-28T07:00:00+00:00")
        assert parsed == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)

    def test_parse_cli_utc_timestamp_accepts_a_trailing_z(self) -> None:
        parsed = ia.parse_cli_utc_timestamp("2026-08-28T07:00:00Z")
        assert parsed == datetime(2026, 8, 28, 7, 0, tzinfo=UTC)

    def test_nearest_existing_ancestor_walks_up_to_an_existing_directory(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "not" / "yet" / "created"
        assert ia._nearest_existing_ancestor(missing) == tmp_path.resolve()

    def test_nearest_existing_ancestor_is_identity_for_an_existing_path(
        self, tmp_path: Path
    ) -> None:
        assert ia._nearest_existing_ancestor(tmp_path) == tmp_path.resolve()


# --- D9/D5 — completeness is DERIVED, never trusted (the blocker) ---


class TestAcquisitionCompletenessIsDerived:
    """⛔ The defect class this track has spent nine review rounds
    eliminating: a value published under an identity SUPPLIED alongside it
    rather than DERIVED from it. A manifest may CLAIM `COMPLETE`; the claim
    is worth nothing unless the record itself accounts for every granule of
    the pinned D5 window."""

    def test_a_fully_accounted_window_derives_complete(self) -> None:
        manifest = _derivation_complete_manifest()
        assert ia.acquisition_completeness_violations(manifest) == ()
        assert ia.is_complete_acquisition(manifest) is True
        ia.assert_acquisition_manifest_complete(manifest)

    def test_a_read_contract_of_placeholder_values_does_not_derive_complete(
        self,
    ) -> None:
        """⛔ D1 (locking). These fixtures recorded every contract value as
        the string "recorded" and the record still derived COMPLETE: the
        check asked whether the D1 field NAMES were present, never whether
        the values satisfied the contract they claim to be."""
        manifest = _derivation_complete_manifest(
            read_contract=dict.fromkeys(
                ia.ImergReadContract.__dataclass_fields__, "recorded"
            )
        )
        assert ia.is_complete_acquisition(manifest) is False
        with pytest.raises(ia.ImergRequestFailedError, match="D1 read contract"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_read_contract_whose_revision_contradicts_the_record_is_rejected(
        self,
    ) -> None:
        contract = dict(_reference_read_contract())
        contract["granule_revision"] = "V06B"
        manifest = _derivation_complete_manifest(read_contract=contract)
        assert ia.is_complete_acquisition(manifest) is False

    def test_gaps_are_stored_chronologically_so_identity_ignores_their_order(
        self,
    ) -> None:
        """D9 (locking) — `missing` is hashed into the extraction identity as
        a TUPLE, so a record whose gaps arrived in a different order would
        publish under a different identity while describing the same
        acquisition."""
        gaps = [
            (ia.FIRST_GRANULE_START + timedelta(minutes=30 * i)).isoformat()
            for i in (5, 1, 3)
        ]
        forward = _derivation_complete_manifest(missing=tuple(gaps))
        reversed_ = _derivation_complete_manifest(missing=tuple(reversed(gaps)))
        assert forward.missing == reversed_.missing == tuple(sorted(gaps))

    def test_a_two_granule_one_hour_manifest_labelled_complete_is_rejected(
        self,
    ) -> None:
        """The EXACT shape the previous locking fixture built: a two-granule,
        one-hour window with `completeness=COMPLETE` — from which the
        end-to-end test then published a full 52,608-hour bundle."""
        starts = [
            ia.FIRST_GRANULE_START,
            ia.FIRST_GRANULE_START + timedelta(minutes=30),
        ]
        names = [ia.ImergGranuleId(start=s).filename(revision="V07B") for s in starts]
        manifest = _derivation_complete_manifest(
            granule_checksums={name: "deadbeef" for name in names},
            requested_window_end=starts[-1],
            requested=2,
            retrieved=2,
            missing=(),
        )
        with pytest.raises(ia.ImergRequestFailedError, match="105216|unaccounted"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_duplicate_plus_missing_sequence_is_rejected(self) -> None:
        """⛔ The counts all add up — retrieved 2 + missing 105,214 ==
        requested 105,216 — but one start is recorded BOTH retrieved and
        missing while another is recorded nowhere. Only comparing the
        accounted-for SET against the pinned window catches that."""
        all_starts = list(ia.all_granule_starts())
        retrieved = all_starts[:2]
        names = {
            ia.ImergGranuleId(start=s).filename(revision="V07B"): "deadbeef"
            for s in retrieved
        }
        manifest = _derivation_complete_manifest(
            granule_checksums=names,
            granule_retrieved_at={name: _clock() for name in names},
            missing=tuple(
                s.isoformat()
                for s in all_starts
                if s not in (all_starts[0], all_starts[2])
            ),
        )
        with pytest.raises(
            ia.ImergRequestFailedError, match="BOTH retrieved and missing"
        ):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_shifted_window_start_is_rejected(self) -> None:
        """D5 — the first hour needs the two granules of 2019-12-31
        23:00-24:00; a window starting an hour later silently shifts the
        whole series."""
        manifest = _derivation_complete_manifest(
            requested_window_start=ia.FIRST_GRANULE_START + timedelta(hours=1)
        )
        with pytest.raises(ia.ImergRequestFailedError, match="window start"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_checksum_retrieval_time_key_mismatch_is_rejected(self) -> None:
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        manifest = _derivation_complete_manifest(
            granule_checksums={name: "deadbeef"}, granule_retrieved_at={}
        )
        with pytest.raises(ia.ImergRequestFailedError, match="same granule filenames"):
            ia.assert_acquisition_manifest_complete(manifest)

    def test_a_gap_outside_the_pinned_window_is_rejected(self) -> None:
        """⛔ The counts still add up, but one recorded gap sits outside the
        pinned D5 window — so the accounting cannot be describing that
        window, however well its numbers balance."""
        all_starts = list(ia.all_granule_starts())
        manifest = _derivation_complete_manifest(
            missing=(
                *(s.isoformat() for s in all_starts[:-1]),
                (ia.LAST_GRANULE_START + timedelta(minutes=30)).isoformat(),
            )
        )
        with pytest.raises(ia.ImergRequestFailedError, match="inside the"):
            ia.assert_acquisition_manifest_complete(manifest)


# --- D10 — the PERMANENT record is not destructively replaceable ---


class TestAcquisitionManifestIsPermanent:
    def test_a_one_granule_record_may_not_overwrite_a_complete_one(
        self, tmp_path: Path
    ) -> None:
        """D10 (locking) — discarding the raw granules is only safe because
        the acquisition record survives. A later probe run writing the same
        fixed path would otherwise replace a 105,216-checksum record with a
        one-granule one, and nothing could ever confirm which bytes produced
        the bundle.

        ⛔ The writer must DERIVE the incoming record's completeness. The
        previous version only refused a record whose stored `completeness`
        LABEL said PROBE/PARTIAL — so this same one-granule record, labelled
        COMPLETE, replaced the permanent one. There is no label any more, and
        the derivation is what refuses it."""
        path = ia.acquisition_manifest_path(tmp_path)
        ia.write_acquisition_manifest(_derivation_complete_manifest(), path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        probe = _derivation_complete_manifest(
            granule_checksums={name: "deadbeef"},
            requested_window_end=ia.FIRST_GRANULE_START,
            requested=1,
            retrieved=1,
            missing=(),
        )
        with pytest.raises(ia.ImergStorageError, match="never be downgraded"):
            ia.write_acquisition_manifest(probe, path)
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert ia.is_complete_acquisition(retained) is True
        assert retained.requested == ia.EXPECTED_GRANULE_COUNT

    def test_a_redownload_whose_checksums_disagree_is_refused(
        self, tmp_path: Path
    ) -> None:
        """D10 — the retained checksums do not PREVENT a GES DISC archive
        revision; they make it DETECTABLE. That only holds if the old
        checksums are still there when the new ones arrive."""
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        ia.write_acquisition_manifest(
            _derivation_complete_manifest(granule_checksums={name: "originalsha"}),
            path,
        )
        with pytest.raises(ia.ImergStorageError, match="revised the archive"):
            ia.write_acquisition_manifest(
                _derivation_complete_manifest(granule_checksums={name: "revisedsha"}),
                path,
            )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.granule_checksums[name] == "originalsha"

    def test_an_identical_complete_rewrite_is_allowed(self, tmp_path: Path) -> None:
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        for _ in range(2):
            ia.write_acquisition_manifest(
                _derivation_complete_manifest(granule_checksums={name: "samesha"}),
                path,
            )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.granule_checksums == {name: "samesha"}

    def test_a_record_a_published_bundle_names_may_not_be_replaced(
        self, tmp_path: Path
    ) -> None:
        """Plan 224 T1 (locking) — a published bundle carries only the DIGEST
        of the acquisition record, so replacing that record with one of
        different identity-bearing content leaves the bundle naming something
        that no longer exists. The writer protected the record from being
        DOWNGRADED but never from being ORPHANED: this replacement derives as
        complete and shares no checksum, so every existing guard passes it."""
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        ia.write_acquisition_manifest(
            _derivation_complete_manifest(granule_checksums={name: "samesha"}), path
        )
        retained_before = ia.read_acquisition_manifest(path)
        assert retained_before is not None
        # ⛔ A REAL bundle, not an empty directory: it carries the digest of the
        # record on disk, which is exactly what the replacement would orphan.
        named_digest = ie.acquisition_record_digest(retained_before)
        replacement = _derivation_complete_manifest()
        assert ie.acquisition_record_digest(replacement) != named_digest
        bundle = ia.imerg_points_root(tmp_path) / f"0000-{named_digest[:12]}"
        bundle.mkdir(parents=True)
        (bundle / "extraction_manifest.json").write_text(
            json.dumps({"acquisition_record_sha256": named_digest})
        )
        with pytest.raises(ia.ImergStorageError, match="published IMERG bundle"):
            ia.write_acquisition_manifest(replacement, path)
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.granule_checksums == {name: "samesha"}
        # the digest the published bundle names still resolves
        assert ie.acquisition_record_digest(retained) == named_digest

    def test_a_rewrite_that_only_moves_the_clock_is_allowed_beside_a_bundle(
        self, tmp_path: Path
    ) -> None:
        """The digest EXCLUDES wall-clock provenance, so a re-run that changes
        only `generated_at` orphans nothing and must still be allowed —
        otherwise the guard would block the idempotent rewrite the writer has
        always permitted."""
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        ia.write_acquisition_manifest(
            _derivation_complete_manifest(granule_checksums={name: "samesha"}), path
        )
        (ia.imerg_points_root(tmp_path) / "0000-abc").mkdir(parents=True)
        later = datetime(2026, 9, 1, tzinfo=UTC)
        ia.write_acquisition_manifest(
            _derivation_complete_manifest(
                granule_checksums={name: "samesha"}, generated_at=later
            ),
            path,
        )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.generated_at == later

    def test_a_staging_directory_is_not_a_published_bundle(
        self, tmp_path: Path
    ) -> None:
        """⛔ Only `NNNN-<identity>` directories are published bundles: a
        staging token left behind by a crashed extraction must not lock the
        permanent record forever."""
        path = ia.acquisition_manifest_path(tmp_path)
        name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
        ia.write_acquisition_manifest(
            _derivation_complete_manifest(granule_checksums={name: "samesha"}), path
        )
        (ia.imerg_points_root(tmp_path) / ".staging" / "deadbeef").mkdir(parents=True)
        ia.write_acquisition_manifest(_derivation_complete_manifest(), path)
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.granule_checksums == {}


# --- Plan 224 T1 — acquisition and publication MUST NOT overlap ---


class TestWriterSerializationLock:
    """The record's writer READS the published bundles and then replaces the
    record; publication VALIDATES the record and then renames a bundle in.
    Interleaved, both pass and the bundle names a replaced digest. The two are
    declared mutually exclusive and the rule is enforced by one non-blocking
    advisory lock, so a violation FAILS LOUDLY instead of racing."""

    def test_a_record_write_refuses_while_the_writer_lock_is_held(
        self, tmp_path: Path
    ) -> None:
        path = ia.acquisition_manifest_path(tmp_path)
        with (
            ia.imerg_writer_lock(ia.imerg_early_root(tmp_path), holder="a publication"),
            pytest.raises(ia.ImergStorageError, match="MUST NOT overlap"),
        ):
            ia.write_acquisition_manifest(_derivation_complete_manifest(), path)
        # released: the same write now succeeds
        ia.write_acquisition_manifest(_derivation_complete_manifest(), path)
        assert ia.read_acquisition_manifest(path) is not None

    def test_publication_refuses_while_the_writer_lock_is_held(
        self, tmp_path: Path
    ) -> None:
        staged = tmp_path / "staged"
        staged.mkdir()
        with (
            ia.imerg_writer_lock(
                ia.imerg_early_root(tmp_path), holder="a record write"
            ),
            pytest.raises(ia.ImergStorageError, match="MUST NOT overlap"),
        ):
            ie.publish_imerg_bundle(staged, data_root=tmp_path, identity="abc")


# --- D5 — the filename's period END is the field that maps an interval ---


class TestPeriodEndIsChecked:
    def test_parse_rejects_a_mislabeled_period_end(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="period end"):
            ia.parse_granule_filename(
                "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E075959.0420.V07B.HDF5"
            )

    def test_resolve_ignores_a_listing_entry_with_a_mislabeled_end(self) -> None:
        granule = ia.ImergGranuleId(start=datetime(2026, 8, 28, 7, 0, tzinfo=UTC))
        listing = "3B-HHR-E.MS.MRG.3IMERG.20260828-S070000-E075959.0420.V07B.HDF5\n"
        with pytest.raises(ia.ImergGranuleMissingError):
            ia.resolve_granule_filename(listing, granule)


# --- the CLI itself (exit codes, and the OSError-unsafe paths) ---


class TestMain:
    def _client_for(self, granule: ia.ImergGranuleId, fixture: Path) -> object:
        filename = granule.filename(revision="V07B")
        _write_fake_granule(fixture, file_header_filename=filename)
        return FakeImergHttpClient(
            listings={granule.directory_url(): filename},
            granule_bytes={f"{granule.directory_url()}{filename}": fixture},
        )

    def test_a_successful_probe_exits_zero_and_records_the_acquisition(
        self, tmp_path: Path
    ) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        client = self._client_for(ia.ImergGranuleId(start=start), tmp_path / "f.h5")
        data_root = tmp_path / "data_root"
        code = ia.main(
            [
                "--data-root",
                str(data_root),
                "--granule-start",
                start.isoformat(),
            ],
            client=client,
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 0
        assert ia.acquisition_manifest_path(data_root).exists()

    def test_a_missing_granule_exits_3(self, tmp_path: Path) -> None:
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--granule-start",
                start.isoformat(),
            ],
            client=FakeImergHttpClient(),  # empty listing -> 404-equivalent
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 3  # noqa: PLR2004

    def test_a_credentials_failure_exits_2_not_3(self, tmp_path: Path) -> None:
        """⛔ `ImergCredentialsError`'s docstring promises exit code 2 and
        `ImergStorageError`'s promises 5; `main()` used to return 3 for
        every `ImergAcquisitionError`, so both promises were fiction. Ops
        tooling can script against an exit code; it cannot script against a
        log field."""

        class RejectingClient:
            def list_directory(self, url: str) -> str:
                raise ia.ImergCredentialsError(f"Earthdata rejected {url}")

            def download_to_path(self, *, url: str, target: Path) -> None:
                raise AssertionError("never reached")

        code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--granule-start",
                "2026-08-28T07:00:00+00:00",
            ],
            client=RejectingClient(),
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 2  # noqa: PLR2004

    def test_an_unwritable_data_root_is_reported_not_raised(
        self, tmp_path: Path
    ) -> None:
        """A storage failure anywhere under the probe — here the acquisition
        manifest's own directory blocked by a FILE — must surface as a typed,
        exit-coded CLI failure, never a raw unhandled traceback."""
        start = datetime(2026, 8, 28, 7, 0, tzinfo=UTC)
        client = self._client_for(ia.ImergGranuleId(start=start), tmp_path / "f.h5")
        data_root = tmp_path / "data_root"
        ia.imerg_raw_dir(data_root).mkdir(parents=True)
        ia.acquisition_manifest_path(data_root).mkdir()  # a DIRECTORY, not a file
        code = ia.main(
            [
                "--data-root",
                str(data_root),
                "--granule-start",
                start.isoformat(),
            ],
            client=client,
            clock=_clock,
            sleep=_sleep,
        )
        assert code == 5  # noqa: PLR2004


# --- Plan 225 (M-A5d) T1/T2 — the OPeNDAP subset route: a second frozen
# contract, route dispatch, and the D5 archive/subset cross-check. Fixtures
# below carry the structure MEASURED on the real GES DISC OPeNDAP subset
# response for 2020-07-15T00:00Z / 80-89E/26-31N (2026-08-31): root-level
# `/precipitation` (not `/Grid/precipitation`), a 1-element-array
# `_FillValue`, unpacked float32, 90x50 cells. ---

_ARCHIVE_LAT = np.round(np.linspace(-89.95, 89.95, 1800), 6).astype(np.float32)
_ARCHIVE_LON = np.round(np.linspace(-179.95, 179.95, 3600), 6).astype(np.float32)
#: The box's own contiguous slice of the real archive grid above — matches
#: the MEASURED 2600:2689 (lon) / 1160:1209 (lat) index range exactly.
_BOX_LON_START, _BOX_LON_STOP = 2600, 2689
_BOX_LAT_START, _BOX_LAT_STOP = 1160, 1209


def _write_fake_archive_granule_for_box(
    path: Path, *, value: float | np.ndarray = 1.5, filename: str | None = None
) -> None:
    """A REAL-shape (1, 3600, 1800) archive granule whose box slice
    (2600:2689 lon, 1160:1209 lat) carries `value` — everything outside the
    box stays at the fill value, so a cross-check against a MATCHING subset
    fixture below is exact by construction."""
    fill_value = -9999.9
    precip = np.full((1, 3600, 1800), fill_value, dtype=np.float32)
    box = (
        value
        if isinstance(value, np.ndarray)
        else np.full((90, 50), value, dtype=np.float32)
    )
    precip[
        0, _BOX_LON_START : _BOX_LON_STOP + 1, _BOX_LAT_START : _BOX_LAT_STOP + 1
    ] = box
    name = filename or path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.attrs["FileHeader"] = (
            f"FileName={name};\nAlgorithmVersion=3IMERGHH;\nProductVersion=V07B;\n"
        ).encode()
        grid = f.create_group("Grid")
        grid.attrs["GridHeader"] = (
            b"BinMethod=ARITHMETIC_MEAN;\nRegistration=CENTER;\n"
            b"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
            b"NorthBoundingCoordinate=90;\nSouthBoundingCoordinate=-90;\n"
            b"EastBoundingCoordinate=180;\nWestBoundingCoordinate=-180;\n"
            b"Origin=SOUTHWEST;\n"
        )
        grid.create_dataset("lat", data=_ARCHIVE_LAT)
        grid.create_dataset("lon", data=_ARCHIVE_LON)
        ds = grid.create_dataset("precipitation", data=precip)
        ds.attrs["DimensionNames"] = b"time,lon,lat"
        ds.attrs["units"] = b"mm/hr"
        ds.attrs["_FillValue"] = fill_value


def _write_fake_subset_granule(
    path: Path,
    *,
    archive_filename: str,
    value: float | np.ndarray = 1.5,
    lat: np.ndarray | None = None,
    lon: np.ndarray | None = None,
    lat_count: int = 50,
    lon_count: int = 90,
) -> None:
    """The MEASURED subset shape: root-level `/precipitation`, `/lat`,
    `/lon` (no `/Grid` group — OPeNDAP flattens it), a 1-element-array
    `_FillValue`. Defaults to the box's own exact slice of `_ARCHIVE_LAT`/
    `_ARCHIVE_LON`, so a fixture pair built with matching `value`s cross-
    checks as exact."""
    lat_vec = (
        lat if lat is not None else _ARCHIVE_LAT[_BOX_LAT_START : _BOX_LAT_STOP + 1]
    )
    lon_vec = (
        lon if lon is not None else _ARCHIVE_LON[_BOX_LON_START : _BOX_LON_STOP + 1]
    )
    precip = (
        value
        if isinstance(value, np.ndarray)
        else np.full((1, lon_count, lat_count), value, dtype=np.float32)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.attrs["FileHeader"] = (
            f"FileName={archive_filename.removesuffix('.HDF5')}.RT-H5;\n"
            "AlgorithmVersion=3IMERGHH;\nProductVersion=V07B;\n"
        ).encode()
        f.create_dataset("lat", data=lat_vec)
        f.create_dataset("lon", data=lon_vec)
        ds = f.create_dataset("precipitation", data=precip)
        ds.attrs["DimensionNames"] = b"time,lon,lat"
        ds.attrs["units"] = b"mm/hr"
        ds.attrs["_FillValue"] = np.array([-9999.9], dtype=np.float32)


@dataclass
class FakeImergSubsetHttpClient:
    """No-network double for `ImergSubsetHttpClient`."""

    content: bytes
    calls: list[tuple[str, str]] = field(default_factory=list)

    def fetch_subset(self, *, url: str, constraint: str) -> bytes:
        self.calls.append((url, constraint))
        return self.content


def _subset_contract_kwargs() -> dict[str, Any]:
    """Every `ImergSubsetReadContract` field except `granule_revision`, so a
    test can vary exactly one of them (mirrors `_contract_kwargs`)."""
    return {
        "variable_path": ia.SUBSET_VARIABLE_PATH,
        "dimension_names": ("time", "lon", "lat"),
        "units": "mm/hr",
        "fill_value": -9999.9,
        "dtype": "float32",
        "scale_factor": None,
        "add_offset": None,
        "longitude_convention": "UNSIGNED_360",
        "grid_shape": ia.EXPECTED_SUBSET_GRID_SHAPE,
        "lat_vector": tuple(float(v) for v in _ARCHIVE_LAT[1160:1210]),
        "lon_vector": tuple(float(v) for v in _ARCHIVE_LON[2600:2690]),
        "file_header_product_version": "V07B",
    }


def _subset_kwargs_with(**overrides: Any) -> dict[str, Any]:
    """`_subset_contract_kwargs()` with exactly the given fields replaced.
    A dedicated helper (rather than an inline `{**kwargs, "field": value}`)
    keeps the merged dict typed as plain `dict[str, Any]`: pyright otherwise
    widens the merge's inferred type per-key, and every keyword the result is
    later spread into reports a spurious `Any | <literal>` mismatch."""
    merged = dict(_subset_contract_kwargs())
    merged.update(overrides)
    return merged


_ARCHIVE_FILENAME = "3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.HDF5"


class TestSubsetReadContract:
    def test_subset_contract_matches_the_real_probe_structure(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(path, archive_filename=_ARCHIVE_FILENAME)
        contract = ia.observe_subset_read_contract(
            path, archive_filename=_ARCHIVE_FILENAME
        )
        assert contract.variable_path == "/precipitation"
        assert contract.dimension_names == ("time", "lon", "lat")
        assert contract.units == "mm/hr"
        assert contract.dtype == "float32"
        assert contract.scale_factor is None
        assert contract.add_offset is None
        assert contract.grid_shape == (1, 90, 50)
        assert contract.granule_revision == "V07B"
        assert len(contract.lat_vector) == 50  # noqa: PLR2004
        assert len(contract.lon_vector) == 90  # noqa: PLR2004

    def test_rejects_a_full_field_grid_shape(self, tmp_path: Path) -> None:
        """T1 verify: "a full-field granule offered against the subset
        contract is refused"."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            value=np.full((1, 3600, 1800), 1.5, dtype=np.float32),
            lat=_ARCHIVE_LAT,
            lon=_ARCHIVE_LON,
            lat_count=1800,
            lon_count=3600,
        )
        with pytest.raises(ia.ImergReadContractError, match="grid shape"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_archive_route_still_refuses_a_subset_shaped_response(
        self, tmp_path: Path
    ) -> None:
        """T1 verify, "vice versa": `contract_from_open_granule`/
        `observe_read_contract` must refuse a subset-shaped file (no
        top-level `Grid` group). (fixer review, finding 4 — locking) — the
        EXACT typed error and message, not a bare `Exception`: a raw h5py
        `KeyError` would silently defeat a caller that only catches
        `ImergReadContractError`, as every other D1 refusal in this module
        does."""
        path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_subset_granule(path, archive_filename=_ARCHIVE_FILENAME)
        with pytest.raises(ia.ImergReadContractError, match="no top-level 'Grid'"):
            ia.observe_read_contract(path)

    def test_a_subset_lat_vector_short_by_one_cell_is_rejected(self) -> None:
        kwargs = _subset_contract_kwargs()
        short_lat = kwargs["lat_vector"][:-1]
        with pytest.raises(ia.ImergReadContractError, match="lat_vector length"):
            ia.ImergSubsetReadContract(
                **{**kwargs, "lat_vector": short_lat}, granule_revision="V07B"
            )

    def test_lat_lon_vectors_match_the_derived_t2_approved_box_constants(
        self,
    ) -> None:
        """The fixture's own coordinates (sliced straight from the real
        archive vectors) and the module's DERIVED `EXPECTED_SUBSET_LAT/LON_
        VECTOR` constants must be the SAME numbers — otherwise the exact-pin
        check below is testing a fixture-only accident, not the real box."""
        kwargs = _subset_contract_kwargs()
        assert kwargs["lat_vector"] == ia.EXPECTED_SUBSET_LAT_VECTOR
        assert kwargs["lon_vector"] == ia.EXPECTED_SUBSET_LON_VECTOR

    def test_a_lat_vector_shifted_by_one_cell_is_rejected(self) -> None:
        """D1 (fixer review, finding 2 — locking) — the length checks alone
        accept ANY same-sized grid; a full-shape, correctly-typed grid whose
        coordinates are one cell off the frozen box must still be refused.
        Mirrors the archive contract's own shifted-vector test: the shifted
        tuple is assigned into the (already `dict[str, Any]`-typed) kwargs
        dict item, not a bare local — matching `_contract_kwargs`'s tests."""
        kwargs = _subset_contract_kwargs()
        lat = kwargs["lat_vector"]
        assert isinstance(lat, tuple)
        kwargs["lat_vector"] = tuple(v + 0.1 for v in lat)
        with pytest.raises(ia.ImergReadContractError, match="lat_vector"):
            ia.ImergSubsetReadContract(**kwargs, granule_revision="V07B")

    def test_a_lon_vector_shifted_by_one_cell_is_rejected(self) -> None:
        kwargs = _subset_contract_kwargs()
        lon = kwargs["lon_vector"]
        assert isinstance(lon, tuple)
        kwargs["lon_vector"] = tuple(v + 0.1 for v in lon)
        with pytest.raises(ia.ImergReadContractError, match="lon_vector"):
            ia.ImergSubsetReadContract(**kwargs, granule_revision="V07B")

    def test_a_lat_vector_perturbed_below_six_decimals_is_rejected(self) -> None:
        """Mirrors the archive contract's own below-6-decimals test: the
        exact-pin check compares the FULL float64 value, not a rounded one."""
        kwargs = _subset_contract_kwargs()
        lat = kwargs["lat_vector"]
        assert isinstance(lat, tuple)
        kwargs["lat_vector"] = (lat[0] + 1e-9, *lat[1:])
        with pytest.raises(ia.ImergReadContractError, match="lat_vector"):
            ia.ImergSubsetReadContract(**kwargs, granule_revision="V07B")

    def test_a_signed_180_longitude_convention_is_rejected(self) -> None:
        """D1 (fixer review, finding 2) — ONE approved convention: the box
        never crosses the sign boundary, so the OTHER convention, even though
        the archive contract still accepts it, must be refused here."""
        with pytest.raises(ia.ImergReadContractError, match="longitude convention"):
            ia.ImergSubsetReadContract(
                **_subset_kwargs_with(longitude_convention="SIGNED_180"),
                granule_revision="V07B",
            )

    def test_a_scale_factor_is_rejected(self) -> None:
        """D1 (fixer review, finding 3 — locking) — packing must be refused
        at CONTRACT CONSTRUCTION, which `read_subset_granule` always goes
        through: this protects every subset read, not only the one granule
        T2's cross-check happens to compare."""
        with pytest.raises(ia.ImergReadContractError, match="PACKED"):
            ia.ImergSubsetReadContract(
                **_subset_kwargs_with(scale_factor=0.01), granule_revision="V07B"
            )

    def test_an_add_offset_is_rejected(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="PACKED"):
            ia.ImergSubsetReadContract(
                **_subset_kwargs_with(add_offset=1.0), granule_revision="V07B"
            )


def _unchecked_subset_contract(**overrides: Any) -> ia.ImergSubsetReadContract:
    """Bypasses `ImergSubsetReadContract.__post_init__` (`object.__new__` +
    `object.__setattr__`), so a locking test can exercise
    `subset_cross_check_tolerance`'s OWN internal guards in isolation — a
    conceptually-invalid contract that D1's constructor would itself now
    refuse to build (finding 2/3's fix) is exactly what proves THIS
    function's guard is not dead code: two independent gates, neither
    substituting for the other (D1 defense in depth)."""
    kwargs: dict[str, Any] = {
        **_subset_contract_kwargs(),
        "granule_revision": "V07B",
        **overrides,
    }
    obj = object.__new__(ia.ImergSubsetReadContract)
    for key, value in kwargs.items():
        object.__setattr__(obj, key, value)
    return obj


class TestSubsetCrossCheckTolerance:
    """D5 (fixer review, finding 6 — locking) — direct unit tests against
    `subset_cross_check_tolerance` itself: the function D5 frames as "the
    stop rule", so a regression that made it always return 0.0 must fail a
    test that calls exactly this function, not merely one further downstream
    that happens to be unreachable once the constructor also guards the same
    invariant."""

    def test_rejects_a_dtype_mismatch(self) -> None:
        contract = _unchecked_subset_contract(dtype="float64")
        with pytest.raises(ia.ImergReadContractError, match="dtype"):
            ia.subset_cross_check_tolerance(subset_contract=contract)

    def test_rejects_a_packed_contract(self) -> None:
        contract = _unchecked_subset_contract(scale_factor=1.0)
        with pytest.raises(ia.ImergReadContractError, match="PACKED"):
            ia.subset_cross_check_tolerance(subset_contract=contract)

    def test_returns_zero_for_a_valid_unpacked_float32_contract(self) -> None:
        contract = ia.ImergSubsetReadContract(
            **_subset_contract_kwargs(), granule_revision="V07B"
        )
        assert ia.subset_cross_check_tolerance(subset_contract=contract) == 0.0


class TestSubsetContractConsistency:
    """D1/D2 (fixer review, finding 1 — locking) — the subset route's own
    counterpart of `TestReadContract`'s `assert_contract_consistent` tests:
    `assert_subset_contract_consistent` must freeze on the first subset
    granule and refuse a drifting one on every subsequent read, exactly as
    strictly as the archive route's own comparator (⛔ neither weakened into
    the other)."""

    def test_passes_for_an_identical_contract(self) -> None:
        contract = ia.ImergSubsetReadContract(
            **_subset_contract_kwargs(), granule_revision="V07B"
        )
        ia.assert_subset_contract_consistent(contract, frozen=contract)

    def test_raises_on_a_revision_mismatch(self) -> None:
        base_kwargs = _subset_contract_kwargs()
        frozen = ia.ImergSubsetReadContract(granule_revision="V07B", **base_kwargs)
        observed = ia.ImergSubsetReadContract(granule_revision="V07C", **base_kwargs)
        with pytest.raises(ia.ImergReadContractError, match="V07C.*V07B|V07B.*V07C"):
            ia.assert_subset_contract_consistent(observed, frozen=frozen)

    def test_raises_on_a_field_mismatch_other_than_revision(self) -> None:
        """`file_header_product_version` is the one subset field NOT already
        pinned to a single value by `__post_init__`, so it is the one field
        that can vary between two otherwise-valid, separately-constructed
        contracts — exactly what this comparator exists to catch."""
        frozen = ia.ImergSubsetReadContract(
            granule_revision="V07B", **_subset_contract_kwargs()
        )
        observed = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version="V07B-different"),
        )
        with pytest.raises(ia.ImergReadContractError, match="other than revision"):
            ia.assert_subset_contract_consistent(observed, frozen=frozen)


class TestSubsetCrossCheck:
    def test_passes_when_the_subset_exactly_matches_the_archive_box(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        report = ia.cross_check_subset_against_archive(
            archive_path=archive_path, data_root=tmp_path / "data_root", client=client
        )
        assert report.lat_exact_match is True
        assert report.lon_exact_match is True
        assert report.tolerance == 0.0
        assert report.max_abs_diff == 0.0
        assert report.passed is True
        ia.assert_subset_cross_check_passed(report)  # must not raise
        assert len(client.calls) == 1

    def test_a_one_cell_lon_shift_is_refused(self, tmp_path: Path) -> None:
        """T1 verify: "a response whose lon/lat vectors differ by one cell
        is refused" (D5's own stop rule — locking). (fixer review, finding 2)
        — a shifted grid is now refused EARLIER, at contract construction:
        `ImergSubsetReadContract` pins the exact T2-approved box coordinates,
        so a same-shaped grid at the wrong location fails before the
        cross-check ever gets to compute a report."""
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        shifted_lon = _ARCHIVE_LON[_BOX_LON_START : _BOX_LON_STOP + 1] + np.float32(0.1)
        _write_fake_subset_granule(
            subset_bytes_path,
            archive_filename=_ARCHIVE_FILENAME,
            value=3.5,
            lon=shifted_lon,
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        with pytest.raises(ia.ImergReadContractError, match="lon_vector"):
            ia.cross_check_subset_against_archive(
                archive_path=archive_path,
                data_root=tmp_path / "data_root",
                client=client,
            )

    def test_assert_subset_cross_check_passed_raises_on_a_failed_report(self) -> None:
        """The FAILURE branch of `assert_subset_cross_check_passed` — the
        happy-path test above only proves it does NOT raise on a passing
        report; this proves it DOES raise, with the "FAILED" message, on one
        that didn't (D5's stop rule), independent of what produced the
        failing report."""
        report = ia.SubsetCrossCheckReport(
            lat_exact_match=True,
            lon_exact_match=False,
            max_abs_diff=0.0,
            tolerance=0.0,
            values_within_tolerance=True,
        )
        with pytest.raises(ia.ImergReadContractError, match="FAILED"):
            ia.assert_subset_cross_check_passed(report)

    def test_a_value_mismatch_exceeding_tolerance_is_refused(
        self, tmp_path: Path
    ) -> None:
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.6
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        report = ia.cross_check_subset_against_archive(
            archive_path=archive_path, data_root=tmp_path / "data_root", client=client
        )
        assert report.lat_exact_match is True
        assert report.lon_exact_match is True
        assert report.values_within_tolerance is False
        assert report.passed is False

    def test_second_run_reuses_the_cached_subset_artifact(self, tmp_path: Path) -> None:
        """D2/D3 — resumability preserved: `acquire_subset_granule` reuses an
        existing artifact rather than re-fetching."""
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        data_root = tmp_path / "data_root"
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        ia.cross_check_subset_against_archive(
            archive_path=archive_path, data_root=data_root, client=client
        )
        assert len(client.calls) == 1

        class PoisonClient:
            def fetch_subset(self, *, url: str, constraint: str) -> bytes:
                raise AssertionError("must not re-fetch a cached artifact")

        report = ia.cross_check_subset_against_archive(
            archive_path=archive_path, data_root=data_root, client=PoisonClient()
        )
        assert report.passed is True

    def test_a_packed_archive_side_is_refused(self, tmp_path: Path) -> None:
        """D1/D5 (fixer review, finding 3 — locking) — the archive contract
        has never RECORDED `scale_factor`/`add_offset` (D1 has no field for
        them), so a packed archive granule would otherwise sail past
        `observe_read_contract` unnoticed; the cross-check must catch it by
        reading the HDF5 attribute directly, before deriving a tolerance."""
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        with h5py.File(archive_path, "a") as f:
            f["Grid/precipitation"].attrs["scale_factor"] = np.float32(0.01)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        with pytest.raises(ia.ImergReadContractError, match="PACKED"):
            ia.cross_check_subset_against_archive(
                archive_path=archive_path,
                data_root=tmp_path / "data_root",
                client=client,
            )

    def test_an_archive_side_dtype_mismatch_is_refused(self, tmp_path: Path) -> None:
        """D1/D5 (fixer review, finding 3 — locking) — the archive contract
        has never recorded a dtype either; a float64 archive dataset (never
        observed, but not impossible) must not be silently compared as if it
        were the expected unpacked float32."""
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        with h5py.File(archive_path, "a") as f:
            precip64 = np.asarray(f["Grid/precipitation"][:], dtype=np.float64)
            del f["Grid/precipitation"]
            ds = f["Grid"].create_dataset("precipitation", data=precip64)
            ds.attrs["DimensionNames"] = b"time,lon,lat"
            ds.attrs["units"] = b"mm/hr"
            ds.attrs["_FillValue"] = -9999.9
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        with pytest.raises(ia.ImergReadContractError, match="dtype"):
            ia.cross_check_subset_against_archive(
                archive_path=archive_path,
                data_root=tmp_path / "data_root",
                client=client,
            )


class TestSubsetRouteDispatch:
    """D2 — "Contract validation must DISPATCH on the recorded route." Tests
    for BOTH mismatched combinations."""

    def test_archive_route_rejects_a_subset_shaped_read_contract(self) -> None:
        subset_dict = ia.ImergSubsetReadContract(
            **_subset_contract_kwargs(), granule_revision="V07B"
        ).as_manifest_dict()
        violations = ia.read_contract_violations(
            subset_dict, granule_revision="V07B", route=ia.ROUTE
        )
        assert violations

    def test_subset_route_rejects_an_archive_shaped_read_contract(self) -> None:
        archive_dict = _reference_read_contract()
        violations = ia.read_contract_violations(
            archive_dict, granule_revision="V07B", route=ia.SUBSET_ROUTE
        )
        assert violations

    def test_subset_route_accepts_a_valid_subset_read_contract(self) -> None:
        subset_dict = ia.ImergSubsetReadContract(
            **_subset_contract_kwargs(), granule_revision="V07B"
        ).as_manifest_dict()
        violations = ia.read_contract_violations(
            subset_dict, granule_revision="V07B", route=ia.SUBSET_ROUTE
        )
        assert violations == ()

    def test_pinned_provenance_accepts_the_subset_route(self) -> None:
        violations = ia.pinned_provenance_violations(
            route=ia.SUBSET_ROUTE,
            collection_short_name=ia.COLLECTION_SHORT_NAME,
            granule_revision=ia.PINNED_GRANULE_REVISION_PER_PLAN,
            box=ia.STUDY_BOX,
            retrospective=True,
        )
        assert violations == ()

    def test_pinned_provenance_rejects_an_unknown_route(self) -> None:
        violations = ia.pinned_provenance_violations(
            route="a made-up route",
            collection_short_name=ia.COLLECTION_SHORT_NAME,
            granule_revision=ia.PINNED_GRANULE_REVISION_PER_PLAN,
            box=ia.STUDY_BOX,
            retrospective=True,
        )
        assert any("route" in v for v in violations)


class TestSubsetFilesystemIdentity:
    """D2 — "Subset artifacts need a route-distinct filename or raw
    directory": the archive/subset artifact paths for the SAME archive
    filename must never collide."""

    def test_subset_and_archive_raw_dirs_differ(self, tmp_path: Path) -> None:
        assert ia.imerg_subset_raw_dir(tmp_path) != ia.imerg_raw_dir(tmp_path)

    def test_subset_and_archive_artifact_paths_never_collide(
        self, tmp_path: Path
    ) -> None:
        archive_path = ia.granule_artifact_path(tmp_path, filename=_ARCHIVE_FILENAME)
        subset_path = ia.subset_granule_artifact_path(
            tmp_path, archive_filename=_ARCHIVE_FILENAME
        )
        assert archive_path != subset_path
        assert archive_path.parent != subset_path.parent


class TestSubsetBoxIndices:
    def test_matches_the_measured_lon_index_range(self) -> None:
        start, stop = ia.subset_box_indices(
            tuple(float(v) for v in _ARCHIVE_LON), low=80, high=89
        )
        assert (start, stop) == (2600, 2689)

    def test_matches_the_measured_lat_index_range(self) -> None:
        start, stop = ia.subset_box_indices(
            tuple(float(v) for v in _ARCHIVE_LAT), low=26, high=31
        )
        assert (start, stop) == (1160, 1209)

    def test_raises_when_the_box_is_not_covered(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="no coordinate values"):
            ia.subset_box_indices((1.0, 2.0, 3.0), low=100.0, high=200.0)


class TestSubsetUrlAndConstraint:
    def test_subset_granule_url_matches_the_measured_endpoint(self) -> None:
        start = datetime(2020, 7, 15, 0, 0, tzinfo=UTC)
        url = ia.subset_granule_url(archive_filename=_ARCHIVE_FILENAME, start=start)
        assert url == (
            "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/"
            f"GPM_3IMERGHHE.07/2020/197/{_ARCHIVE_FILENAME}.dap.nc4"
        )

    def test_subset_constraint_matches_the_measured_shape(self) -> None:
        constraint = ia.subset_constraint(
            lon_start=2600, lon_stop=2689, lat_start=1160, lat_stop=1209
        )
        assert constraint == (
            "/precipitation[0][2600:2689][1160:1209];/lon[2600:2689];/lat[1160:1209]"
        )
