"""Plan 211 (M-A5b) task T1 — granule-name construction, window/box
arithmetic and missing-granule accounting, exercised entirely against a fake
HTTP client (no network in CI). The one real network probe permitted by the
plan (the D1 contract observation) was run manually against the live GES
DISC archive during implementation — see the module docstring's residual-
risk note in `imerg_acquire.py`; it is not repeated here.
"""

from __future__ import annotations

import json
import pathlib
import threading
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
    from collections.abc import Callable, Sequence
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


def _kwargs_with(**overrides: Any) -> dict[str, Any]:
    """`_contract_kwargs()` with exactly the given fields replaced — the
    archive-route mirror of `_subset_kwargs_with`, and for the same reason:
    an inline `{**kwargs, "field": value}` merge makes pyright widen the
    result per-key and every spread keyword then reports a spurious
    `Any | <literal>` mismatch."""
    merged = dict(_contract_kwargs())
    merged.update(overrides)
    return merged


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

#: The box's own contiguous slice of the real archive grid — the MEASURED
#: 2600:2689 (lon) / 1160:1209 (lat) index range, which is exactly D4's
#: published `dap4.ce` constraint.
_BOX_LON_START, _BOX_LON_STOP = 2600, 2689
_BOX_LAT_START, _BOX_LAT_STOP = 1160, 1209

#: 🔴 Plan 225 fixer round 3 (BLOCKER, locking) — OUTSIDE the box these are a
#: convenient `linspace`; INSIDE it they are the MEASURED float32 values,
#: spliced in from the module's own pinned constants. Measured 2026-09-01: a
#: bare `np.round(np.linspace(...), 6).astype(np.float32)` disagrees with the
#: real archive granule's `/Grid/lat` on 464 of 1800 values and `/Grid/lon` on
#: 925 of 3600 — 20 and 18 of them inside the box. Because the OLD production
#: constants were generated by the SAME idealised arithmetic, every fixture
#: here coincided with the formula under test and the whole subset suite was
#: circular: it passed while the contract rejected the real OPeNDAP response.
#: ⇒ The box slice now comes from `ia.EXPECTED_SUBSET_*_VECTOR`, and
#: `TestSubsetContractAgainstTheRealArtifact` is what proves THOSE against the
#: committed real artifacts. ⛔ Do not regenerate this slice arithmetically.
_ARCHIVE_LAT = np.round(np.linspace(-89.95, 89.95, 1800), 6).astype(np.float32)
_ARCHIVE_LON = np.round(np.linspace(-179.95, 179.95, 3600), 6).astype(np.float32)
_ARCHIVE_LAT[_BOX_LAT_START : _BOX_LAT_STOP + 1] = np.asarray(
    ia.EXPECTED_SUBSET_LAT_VECTOR, dtype=np.float32
)
_ARCHIVE_LON[_BOX_LON_START : _BOX_LON_STOP + 1] = np.asarray(
    ia.EXPECTED_SUBSET_LON_VECTOR, dtype=np.float32
)


def _write_fake_archive_granule_for_box(
    path: Path,
    *,
    value: float | np.ndarray = 1.5,
    filename: str | None = None,
    lat: np.ndarray | None = None,
    lon: np.ndarray | None = None,
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
        grid.create_dataset("lat", data=_ARCHIVE_LAT if lat is None else lat)
        grid.create_dataset("lon", data=_ARCHIVE_LON if lon is None else lon)
        ds = grid.create_dataset("precipitation", data=precip)
        ds.attrs["DimensionNames"] = b"time,lon,lat"
        ds.attrs["units"] = b"mm/hr"
        ds.attrs["_FillValue"] = fill_value


#: The root-level `Grid.GridHeader` global attribute the REAL live probe
#: response retains (measured 2026-08-31, `data/dhm_precip/imerg_early/
#: raw_subset/3B-HHR-E.MS.MRG.3IMERG.20200715-S000000-E002959.0000.V07B.
#: HDF5.dap.nc4`) — bit-for-bit the same text the archive fixture's `/Grid`
#: group attribute above carries, since it is the SAME global grid.
_MEASURED_SUBSET_GRID_HEADER = (
    b"BinMethod=ARITHMETIC_MEAN;\nRegistration=CENTER;\n"
    b"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
    b"NorthBoundingCoordinate=90;\nSouthBoundingCoordinate=-90;\n"
    b"EastBoundingCoordinate=180;\nWestBoundingCoordinate=-180;\n"
    b"Origin=SOUTHWEST;\n"
)


def _write_fake_subset_granule(
    path: Path,
    *,
    archive_filename: str,
    value: float | np.ndarray = 1.5,
    lat: np.ndarray | None = None,
    lon: np.ndarray | None = None,
    lat_count: int = 50,
    lon_count: int = 90,
    grid_header: bytes | None = None,
    omit_grid_header: bool = False,
    product_version: str = "V07B",
) -> None:
    """The MEASURED subset shape: root-level `/precipitation`, `/lat`,
    `/lon` (no `/Grid` group — OPeNDAP flattens it), a 1-element-array
    `_FillValue`, and a root-level `Grid.GridHeader` global attribute (fixer
    review round 2, finding 1) carrying the retained GLOBAL grid's own
    bounding coordinates. Defaults to the box's own exact slice of
    `_ARCHIVE_LAT`/`_ARCHIVE_LON`, so a fixture pair built with matching
    `value`s cross-checks as exact. `grid_header` overrides the default
    -180/180 header text (for a fixture exercising a contradictory one);
    `omit_grid_header` drops the attribute entirely."""
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
            f"AlgorithmVersion=3IMERGHH;\nProductVersion={product_version};\n"
        ).encode()
        if not omit_grid_header:
            f.attrs["Grid.GridHeader"] = (
                grid_header if grid_header is not None else _MEASURED_SUBSET_GRID_HEADER
            )
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
        # (fixer review round 2, finding 1) — SIGNED_180 is the retained
        # GLOBAL grid's own convention (the real probe's `Grid.GridHeader`:
        # WestBoundingCoordinate=-180), not derived from the box's sign.
        "longitude_convention": "SIGNED_180",
        # (fixer round 3, MAJOR 1) — the cell centres above are only meaningful
        # under CENTER registration, and the retained header's four bounding
        # coordinates are pinned exactly rather than reduced to a sign test.
        "coordinate_registration": ia.EXPECTED_REGISTRATION,
        "global_bounds": ia.EXPECTED_SUBSET_GLOBAL_BOUNDS,
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

    def test_an_unsigned_360_longitude_convention_is_rejected(self) -> None:
        """D1 (fixer review round 2, finding 1 — MAJOR, locking) — ONE
        approved convention, and it is SIGNED_180: the real probe's own
        retained `Grid.GridHeader` carries `WestBoundingCoordinate=-180`, so
        UNSIGNED_360 is the value that must now be refused (this test used
        to pin the OPPOSITE, wrong expectation — refusing SIGNED_180, the
        actually-correct value — which is exactly the bug finding 1 caught)."""
        with pytest.raises(ia.ImergReadContractError, match="longitude convention"):
            ia.ImergSubsetReadContract(
                **_subset_kwargs_with(longitude_convention="UNSIGNED_360"),
                granule_revision="V07B",
            )

    def test_derives_signed_180_from_the_retained_grid_header(
        self, tmp_path: Path
    ) -> None:
        """D1 (fixer review round 2, finding 1 — MAJOR, locking) — the
        fixture's own `/lon` slice is entirely positive (the box never
        crosses the sign boundary), so a derivation from `lon.min()` alone
        would read "UNSIGNED_360" no matter what. The retained
        `Grid.GridHeader` (`WestBoundingCoordinate=-180`) is the only field
        that can reveal the box was cut from a SIGNED_180 global grid — this
        fails against the pre-fix `lon.min()` derivation."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(path, archive_filename=_ARCHIVE_FILENAME)
        contract = ia.observe_subset_read_contract(
            path, archive_filename=_ARCHIVE_FILENAME
        )
        assert contract.longitude_convention == "SIGNED_180"

    def test_a_grid_header_implying_unsigned_360_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """D1 (fixer review round 2, finding 1 — MAJOR, locking) — a subset
        response whose retained header contradicts the SIGNED_180 grid the
        box can only honestly be cut from (e.g. a hypothetical 0/360-bounded
        header) must be refused, not silently accepted as a same-shaped grid
        from the wrong global convention. (fixer round 3, MAJOR 1) — since the
        four bounding coordinates are now pinned EXACTLY, this header is
        refused on the BOUNDS before the derived convention is even reached;
        that is strictly stronger, so the assertion follows the earlier gate."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            grid_header=(
                b"BinMethod=ARITHMETIC_MEAN;\nRegistration=CENTER;\n"
                b"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
                b"NorthBoundingCoordinate=90;\nSouthBoundingCoordinate=-90;\n"
                b"EastBoundingCoordinate=360;\nWestBoundingCoordinate=0;\n"
                b"Origin=SOUTHWEST;\n"
            ),
        )
        with pytest.raises(ia.ImergReadContractError, match="global bounds"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_a_missing_grid_header_is_rejected(self, tmp_path: Path) -> None:
        """D1 (fixer review round 2, finding 1) — without the retained
        header there is no honest way to derive the longitude convention;
        refuse rather than silently falling back to a `lon.min()` guess."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path, archive_filename=_ARCHIVE_FILENAME, omit_grid_header=True
        )
        with pytest.raises(ia.ImergReadContractError, match="Grid.GridHeader"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_subset_route_refuses_an_archive_shaped_response(
        self, tmp_path: Path
    ) -> None:
        """D1 (fixer review round 2, finding 4 — minor, locking) — the
        "vice versa" of `test_archive_route_still_refuses_a_subset_shaped_
        response`: an ARCHIVE-shaped granule (a real `/Grid` group, no
        root-level `/precipitation`) offered to the SUBSET parser must raise
        the typed `ImergReadContractError`, not a raw h5py `KeyError`. The
        prior "full-field" test built a root-level layout with a full-field
        SHAPE, which never exercised this route-mismatch KeyError at all."""
        path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(path, value=3.5)
        with pytest.raises(ia.ImergReadContractError, match="no root-level"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

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


class TestProductVersionComparisonTolerance:
    """(2026-09-02, locking) — MEASURED across the banked window: NASA writes
    `FileHeader.ProductVersion` as `V07B` up to 2024-05 and as `07B` from
    2024-06, while every other contract field and the authoritative
    filename-derived `granule_revision` (`V07B` on both sides) are unchanged.
    A full-window run froze on a 2020 granule and aborted at 2024-06-02 on
    that ONE cosmetic character.

    ⛔ Both halves are locked here: the `V` prefix is tolerated, and a REAL
    version change still trips the gate."""

    def test_the_normaliser_removes_exactly_one_leading_v(self) -> None:
        assert ia.product_version_for_comparison("V07B") == "07B"
        assert ia.product_version_for_comparison("07B") == "07B"
        # ⛔ ONE `V`, and only a LEADING one: nothing else is absorbed.
        assert ia.product_version_for_comparison("VV07B") == "V07B"
        assert ia.product_version_for_comparison("07BV") == "07BV"
        assert ia.product_version_for_comparison("07C") == "07C"

    def test_a_dropped_v_prefix_compares_equal_on_the_subset_route(self) -> None:
        frozen = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version="V07B"),
        )
        observed = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version="07B"),
        )
        ia.assert_subset_contract_consistent(observed, frozen=frozen)

    def test_a_dropped_v_prefix_compares_equal_on_the_archive_route(self) -> None:
        """⚠️ The archive route reads the SAME `FileHeader.ProductVersion` of
        the SAME NASA product into the SAME field name, so it carries the
        identical exposure: the tolerance is applied to both."""
        frozen = ia.ImergReadContract(
            granule_revision="V07B",
            **_kwargs_with(file_header_product_version="V07B"),
        )
        observed = ia.ImergReadContract(
            granule_revision="V07B",
            **_kwargs_with(file_header_product_version="07B"),
        )
        ia.assert_contract_consistent(observed, frozen=frozen)

    @pytest.mark.parametrize(
        ("frozen_version", "observed_version"),
        [("07B", "07C"), ("V07B", "V07C"), ("V07B", "07C"), ("07B", "V07C")],
    )
    def test_a_real_product_version_change_still_raises(
        self, frozen_version: str, observed_version: str
    ) -> None:
        """🔴 Plan 211's own probe MEASURED a live `V07C` drift, so this is not
        hypothetical: the tolerance must absorb the prefix and NOTHING else."""
        frozen = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version=frozen_version),
        )
        observed = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version=observed_version),
        )
        with pytest.raises(
            ia.ImergReadContractError, match="file_header_product_version"
        ):
            ia.assert_subset_contract_consistent(observed, frozen=frozen)

    def test_a_real_product_version_change_still_raises_on_the_archive_route(
        self,
    ) -> None:
        frozen = ia.ImergReadContract(
            granule_revision="V07B",
            **_kwargs_with(file_header_product_version="V07B"),
        )
        observed = ia.ImergReadContract(
            granule_revision="V07B",
            **_kwargs_with(file_header_product_version="07C"),
        )
        with pytest.raises(
            ia.ImergReadContractError, match="file_header_product_version"
        ):
            ia.assert_contract_consistent(observed, frozen=frozen)

    def test_the_observed_product_version_is_recorded_verbatim(
        self, tmp_path: Path
    ) -> None:
        """⛔ The tolerance is a COMPARISON rule, never a parse rule. The
        observed string is identity-bearing — it enters the acquisition
        manifest and therefore the digest — and it is the evidence NASA
        changed the string, so it is stored exactly as read."""
        for product_version in ("V07B", "07B"):
            path = tmp_path / f"subset-{product_version}.dap.nc4"
            _write_fake_subset_granule(
                path,
                archive_filename=_ARCHIVE_FILENAME,
                product_version=product_version,
            )
            contract = ia.observe_subset_read_contract(
                path, archive_filename=_ARCHIVE_FILENAME
            )
            assert contract.file_header_product_version == product_version
            assert contract.granule_revision == "V07B"
            assert (
                contract.as_manifest_dict()["file_header_product_version"]
                == product_version
            )


class TestContractDifferenceMessage:
    """(2026-09-02, locking) — the whole-dataclass `observed != replace(...)`
    comparison said only that SOMETHING differed, which cost hours of
    bisecting granules to find a one-field, cosmetic difference. ⇒ The message
    must NAME every offending field and show BOTH values."""

    def test_the_message_names_the_offending_field_and_both_values(self) -> None:
        frozen = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version="V07B"),
        )
        observed = ia.ImergSubsetReadContract(
            granule_revision="V07B",
            **_subset_kwargs_with(file_header_product_version="07C"),
        )
        with pytest.raises(ia.ImergReadContractError) as excinfo:
            ia.assert_subset_contract_consistent(observed, frozen=frozen)
        message = str(excinfo.value)
        assert "file_header_product_version" in message
        assert "observed='07C'" in message
        assert "frozen='V07B'" in message

    def test_every_differing_field_is_named_not_only_the_first(self) -> None:
        """⛔ Two fields differ; naming only the first would still leave the
        reader bisecting for the second."""
        frozen = ia.ImergReadContract(granule_revision="V07B", **_contract_kwargs())
        observed = ia.ImergReadContract(
            granule_revision="V07B",
            **_kwargs_with(grid_spacing_deg=0.2, file_header_product_version="07C"),
        )
        with pytest.raises(ia.ImergReadContractError) as excinfo:
            ia.assert_contract_consistent(observed, frozen=frozen)
        message = str(excinfo.value)
        assert "grid_spacing_deg: observed=0.2 frozen=0.1" in message
        assert "file_header_product_version: observed='07C' frozen='07B'" in message

    def test_a_differing_coordinate_vector_is_named_and_truncated(self) -> None:
        """⛔ Named, but not dumped: the archive vectors run to 3600 floats."""
        lat: tuple[float, ...] = _contract_kwargs()["lat_vector"]
        frozen = ia.ImergReadContract(granule_revision="V07B", **_contract_kwargs())
        observed = ia.ImergReadContract(
            granule_revision="V07B",
            **_kwargs_with(lat_vector=(lat[0] + 1e-9, *lat[1:])),
        )
        with pytest.raises(ia.ImergReadContractError) as excinfo:
            ia.assert_contract_consistent(observed, frozen=frozen)
        message = str(excinfo.value)
        assert "lat_vector" in message
        assert "more..." in message
        # ⛔ 1800 floats twice over would bury the field name it just reported.
        assert len(message) < 1000
        assert repr(lat[0]) in message


class TestExitCodeSeparatesPermanentFromTransient:
    """(2026-09-02, locking) — a supervising retry loop MUST be able to tell a
    permanent failure from a transient one FROM THE EXIT CODE. Exit 3 cannot
    carry that: measured over one 308-attempt run it covered a frozen-contract
    violation and an out-of-window bound (both permanent — 307 attempts burned
    at 60 s each) AND an `ImergRetriesExhaustedError` (transient; the re-run
    succeeded). ⇒ The two PERMANENT classes exit 6; everything else keeps 3.

    ⛔ Every case is driven through `main()`, so it locks the code the
    SUPERVISOR actually observes, not an internal mapping table."""

    def test_a_transient_listing_exhaustion_keeps_exit_three(
        self, tmp_path: Path
    ) -> None:
        """🔴 The case that forbids simply treating 3 as fatal: this failure IS
        worth another attempt, and the supervisor must still make it."""

        class AlwaysTransientClient:
            def list_directory(self, url: str) -> str:
                raise ia.ImergTransientError(f"connection reset for {url}")

            def download_to_path(self, *, url: str, target: Path) -> None:
                raise AssertionError("never reached")

        starts = _starts_from(_TRIAL_DAY_START, 2)
        exit_code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--subset-window-start",
                starts[0].isoformat(),
                "--subset-window-end",
                starts[-1].isoformat(),
                "--request-interval-seconds",
                "0",
            ],
            client=AlwaysTransientClient(),
            subset_client=FakeSubsetWindowClient(content_by_url={}),
            clock=_clock,
            sleep=_sleep,
        )
        assert exit_code == 3  # noqa: PLR2004


def _box_granule() -> ia.ImergGranuleId:
    return ia.parse_granule_filename(_ARCHIVE_FILENAME)[0]


class TestAcquireSubsetGranule:
    """D2/D3 (fixer review round 2, finding 2 — MAJOR, locking) —
    validate-and-reuse, exercised directly against `acquire_subset_granule`
    rather than only through the cross-check: a malformed artifact is a
    concern independent of any archive comparison."""

    def test_a_malformed_new_download_is_never_installed(self, tmp_path: Path) -> None:
        """An HTTP-200 login page (garbage bytes, not HDF5) must fail
        validation and must NOT be atomically installed — the pre-fix code
        wrote it straight to disk via `os.replace` and returned it as if it
        were valid."""
        data_root = tmp_path / "data_root"
        client = FakeImergSubsetHttpClient(content=b"<html>login required</html>")
        with pytest.raises(ia.ImergReadContractError):
            ia.acquire_subset_granule(
                _box_granule(),
                archive_filename=_ARCHIVE_FILENAME,
                client=client,
                data_root=data_root,
                lon_bounds=(_BOX_LON_START, _BOX_LON_STOP),
                lat_bounds=(_BOX_LAT_START, _BOX_LAT_STOP),
            )
        target = ia.subset_granule_artifact_path(
            data_root, archive_filename=_ARCHIVE_FILENAME
        )
        assert not target.exists()
        assert not target.with_name(target.name + ".tmp").exists()

    def test_a_malformed_existing_cache_does_not_suppress_the_http_call(
        self, tmp_path: Path
    ) -> None:
        """A stale/corrupt cache entry must not be trusted merely because it
        exists at the path — the pre-fix code returned it unread on the
        `target.exists()` check alone. Only a VALID cache suppresses the
        HTTP call; an invalid one falls through to a fresh download, which
        here succeeds and replaces the bad cache."""
        data_root = tmp_path / "data_root"
        target = ia.subset_granule_artifact_path(
            data_root, archive_filename=_ARCHIVE_FILENAME
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not a real HDF5 file")
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        result = ia.acquire_subset_granule(
            _box_granule(),
            archive_filename=_ARCHIVE_FILENAME,
            client=client,
            data_root=data_root,
            lon_bounds=(_BOX_LON_START, _BOX_LON_STOP),
            lat_bounds=(_BOX_LAT_START, _BOX_LAT_STOP),
        )
        assert result == target
        assert len(client.calls) == 1  # the HTTP call was NOT suppressed
        # the bad cache was actually replaced with the valid download
        ia.observe_subset_read_contract(target, archive_filename=_ARCHIVE_FILENAME)

    def test_a_valid_existing_cache_suppresses_the_http_call(
        self, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "data_root"
        target = ia.subset_granule_artifact_path(
            data_root, archive_filename=_ARCHIVE_FILENAME
        )
        _write_fake_subset_granule(
            target, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )

        class PoisonClient:
            def fetch_subset(self, *, url: str, constraint: str) -> bytes:
                raise AssertionError("must not re-fetch a valid cached artifact")

        result = ia.acquire_subset_granule(
            _box_granule(),
            archive_filename=_ARCHIVE_FILENAME,
            client=PoisonClient(),  # type: ignore[arg-type]
            data_root=data_root,
            lon_bounds=(_BOX_LON_START, _BOX_LON_STOP),
            lat_bounds=(_BOX_LAT_START, _BOX_LAT_STOP),
        )
        assert result == target


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

    def test_run_subset_cross_check_returns_the_passing_report(
        self, tmp_path: Path
    ) -> None:
        """The happy path of the T2 production entry point (fixer review
        round 2, finding 3): it must not raise, and must return the same
        passing report `cross_check_subset_against_archive` would."""
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        report = ia.run_subset_cross_check(
            archive_path=archive_path, data_root=tmp_path / "data_root", client=client
        )
        assert report.passed is True

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
        """T2 verify: "a mismatch stops the plan". (fixer review round 2,
        finding 3 — MAJOR, locking) — `cross_check_subset_against_archive`
        alone RETURNS a failed report rather than raising, so a caller that
        forgets `report.passed` would silently continue past D5's hard stop.
        `run_subset_cross_check` is the ONE production entry point that
        enforces the gate; this exercises IT, not the lower-level report
        function, and fails against pre-fix code where no caller raises."""
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.6
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        with pytest.raises(ia.ImergReadContractError, match="FAILED"):
            ia.run_subset_cross_check(
                archive_path=archive_path,
                data_root=tmp_path / "data_root",
                client=client,
            )

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


# --- 🔴 Plan 225 fixer round 3 — the REAL committed artifacts. Every fixture
# above is synthetic; the frozen coordinate pin was generated by the same
# idealised arithmetic the production constants used, so the whole subset
# suite passed while the contract rejected the real OPeNDAP response (20 of 50
# lat and 18 of 90 lon values differ, max 1.9e-06 / 7.6e-06). ⛔ A locking test
# for an EXACT pin must compare against MEASURED data, never another
# derivation. `data/` is a symlink outside git and CI cannot see it, so these
# skip cleanly there — and genuinely run on the development host. ---

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_REAL_ARCHIVE_GRANULE = (
    _REPO_ROOT / "data/dhm_precip/imerg_early/raw" / _ARCHIVE_FILENAME
)
_REAL_SUBSET_ARTIFACT = (
    _REPO_ROOT
    / "data/dhm_precip/imerg_early/raw_subset"
    / f"{_ARCHIVE_FILENAME}.dap.nc4"
)
_REAL_ARTIFACTS_PRESENT = (
    _REAL_ARCHIVE_GRANULE.is_file() and _REAL_SUBSET_ARTIFACT.is_file()
)


def _real_archive_coordinate_slices() -> tuple[Any, Any]:
    """The real archive granule's own float32 `/Grid/lat` and `/Grid/lon` over
    the box, read whole and sliced with numpy (h5py's own element type is
    `Group | Dataset | Datatype` to a type checker, so the slice is taken after
    `np.asarray`, not on the h5py object)."""
    with h5py.File(_REAL_ARCHIVE_GRANULE, "r") as f:
        lat = np.asarray(f["Grid/lat"][:])  # type: ignore[index]
        lon = np.asarray(f["Grid/lon"][:])  # type: ignore[index]
    return (
        lat[_BOX_LAT_START : _BOX_LAT_STOP + 1],
        lon[_BOX_LON_START : _BOX_LON_STOP + 1],
    )


@pytest.mark.skipif(
    not _REAL_ARTIFACTS_PRESENT,
    reason=(
        "the real IMERG artifacts are not on this host — "
        f"{_REAL_ARCHIVE_GRANULE} and/or {_REAL_SUBSET_ARTIFACT} absent. "
        "They live under `data/`, which is a symlink outside git, so CI never "
        "has them; this test must run on a host that does."
    ),
)
class TestSubsetContractAgainstTheRealArtifact:
    """D1/D5 — the frozen pin, validated through the PRODUCTION path against
    the real GES DISC OPeNDAP response and the real archive granule."""

    def test_the_real_opendap_response_satisfies_the_frozen_subset_contract(
        self,
    ) -> None:
        """🔴 The regression this round exists for: against the derived
        constants `observe_subset_read_contract` RAISED on this very file
        ("observed subset lat_vector does not exactly match the T2-approved
        frozen box coordinates"), so the route could not ingest a single real
        granule — IMERG's grid is fixed, so every one would have failed."""
        contract = ia.observe_subset_read_contract(
            _REAL_SUBSET_ARTIFACT, archive_filename=_ARCHIVE_FILENAME
        )
        assert contract.grid_shape == ia.EXPECTED_SUBSET_GRID_SHAPE
        assert contract.dtype == "float32"
        assert contract.coordinate_registration == ia.EXPECTED_REGISTRATION
        assert contract.global_bounds == ia.EXPECTED_SUBSET_GLOBAL_BOUNDS
        assert contract.lat_vector == ia.EXPECTED_SUBSET_LAT_VECTOR
        assert contract.lon_vector == ia.EXPECTED_SUBSET_LON_VECTOR

    def test_the_pin_is_the_archive_granules_own_float32_slice_bit_for_bit(
        self,
    ) -> None:
        """⛔ The pin must come from OBSERVED float32 data, not arithmetic:
        D4's published constraint is `/precipitation[0][2600:2689]
        [1160:1209]`, so the archive granule's own `/Grid/lat[1160:1210]` and
        `/Grid/lon[2600:2690]` ARE the subset's coordinates. `np.array_equal`,
        no tolerance — a tolerance here would admit a subset service's own
        grid, the precise hazard D1 exists to prevent."""
        archive_lat, archive_lon = _real_archive_coordinate_slices()
        assert archive_lat.dtype == np.float32
        assert archive_lon.dtype == np.float32
        assert np.array_equal(
            archive_lat, np.asarray(ia.EXPECTED_SUBSET_LAT_VECTOR, dtype=np.float32)
        )
        assert np.array_equal(
            archive_lon, np.asarray(ia.EXPECTED_SUBSET_LON_VECTOR, dtype=np.float32)
        )

    def test_the_t2_cross_check_passes_bit_for_bit_on_the_real_pair(self) -> None:
        """D5/T2 on the two artifacts actually held — the cached subset
        artifact is validated and reused, so ⛔ no network call is made (the
        client raises if reached)."""

        class NoNetworkClient:
            def fetch_subset(self, *, url: str, constraint: str) -> bytes:
                raise AssertionError(f"a network call was attempted: {url}")

        report = ia.run_subset_cross_check(
            archive_path=_REAL_ARCHIVE_GRANULE,
            data_root=_REPO_ROOT / "data/dhm_precip",
            client=NoNetworkClient(),  # type: ignore[arg-type]
        )
        assert report.lat_exact_match is True
        assert report.lon_exact_match is True
        assert report.tolerance == 0.0
        assert report.max_abs_diff == 0.0
        assert report.passed is True


# --- fixer round 3, MAJOR 1 — registration and the global bounds are pinned,
# not inferred from a single sign test. Independent mutations, one per field. ---


def _grid_header(
    *,
    registration: str = "CENTER",
    north: str = "90",
    south: str = "-90",
    east: str = "180",
    west: str = "-180",
) -> bytes:
    return (
        f"BinMethod=ARITHMETIC_MEAN;\nRegistration={registration};\n"
        f"LatitudeResolution=0.1;\nLongitudeResolution=0.1;\n"
        f"NorthBoundingCoordinate={north};\nSouthBoundingCoordinate={south};\n"
        f"EastBoundingCoordinate={east};\nWestBoundingCoordinate={west};\n"
        "Origin=SOUTHWEST;\n"
    ).encode()


class TestSubsetRegistrationAndBoundsArePinned:
    def test_the_default_fixture_header_is_the_measured_one(self) -> None:
        """⛔ Guard on the mutations below: each varies ONE field of this
        builder, so the builder's own output must be the header the real probe
        response carries — otherwise every mutation test would be varying a
        field of something the service never sends."""
        assert _grid_header() == _MEASURED_SUBSET_GRID_HEADER

    def test_corner_registration_is_rejected(self, tmp_path: Path) -> None:
        """🔴 The contract had NO registration field at all, although its
        frozen cell centres assume CENTER: under CORNER the identical numbers
        name cell EDGES and every station maps half a cell away, while
        extraction still treats them as centres."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            grid_header=_grid_header(registration="CORNER"),
        )
        with pytest.raises(ia.ImergReadContractError, match="registration"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_a_shifted_west_bound_is_rejected(self, tmp_path: Path) -> None:
        """⛔ `-179` is still NEGATIVE, so the old `west_bound < 0` sign test
        derived SIGNED_180 and waved it through — a global grid one degree off
        the one these cell centres were cut from."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            grid_header=_grid_header(west="-179"),
        )
        with pytest.raises(ia.ImergReadContractError, match="global bounds"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_a_shifted_east_bound_is_rejected(self, tmp_path: Path) -> None:
        """⛔ The east bound was never read at all: only `west` was, and only
        for its sign."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            grid_header=_grid_header(east="179"),
        )
        with pytest.raises(ia.ImergReadContractError, match="global bounds"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_a_shifted_north_bound_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            grid_header=_grid_header(north="89"),
        )
        with pytest.raises(ia.ImergReadContractError, match="global bounds"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_a_non_numeric_bound_is_a_typed_contract_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(
            path,
            archive_filename=_ARCHIVE_FILENAME,
            grid_header=_grid_header(west="west"),
        )
        with pytest.raises(ia.ImergReadContractError, match="is not a number"):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_the_contract_itself_refuses_corner_registration(self) -> None:
        """The field-level mutation, independent of any file: a RECORDED
        contract (manifest round-trip, D2 dispatch) must be refused too."""
        with pytest.raises(ia.ImergReadContractError, match="registration"):
            ia.ImergSubsetReadContract(
                **_subset_kwargs_with(coordinate_registration="CORNER"),
                granule_revision="V07B",
            )

    def test_the_contract_itself_refuses_shifted_global_bounds(self) -> None:
        with pytest.raises(ia.ImergReadContractError, match="global bounds"):
            ia.ImergSubsetReadContract(
                **_subset_kwargs_with(global_bounds=(90.0, -180.0, -90.0, 179.0)),
                granule_revision="V07B",
            )


# --- fixer round 3, MINOR 1 — a malformed-but-OPENABLE cached artifact must
# reach the validate-and-reuse recovery path, not escape it as a raw KeyError. ---


def _strip_precipitation_attribute(path: Path, attribute: str) -> None:
    with h5py.File(path, "r+") as f:
        del f["precipitation"].attrs[attribute]


class TestMalformedButOpenableSubsetArtifact:
    @pytest.mark.parametrize("attribute", ["units", "DimensionNames", "_FillValue"])
    def test_a_missing_attribute_is_a_typed_contract_error(
        self, tmp_path: Path, attribute: str
    ) -> None:
        """🔴 The file OPENS as valid HDF5 — the existing tests only covered
        garbage bytes, which fail at `h5py.File` with `OSError`. A missing
        contract attribute raised a raw `KeyError` straight through
        `_validate_subset_artifact`, which wrapped only `OSError`."""
        path = tmp_path / "subset.dap.nc4"
        _write_fake_subset_granule(path, archive_filename=_ARCHIVE_FILENAME)
        _strip_precipitation_attribute(path, attribute)
        with pytest.raises(ia.ImergReadContractError, match=attribute):
            ia.observe_subset_read_contract(path, archive_filename=_ARCHIVE_FILENAME)

    def test_a_malformed_but_openable_cache_is_refetched(self, tmp_path: Path) -> None:
        """The consequence that matters: validate-and-reuse catches only the
        typed hierarchy, so before the fix a poisoned-but-openable cache entry
        could NEVER be repaired by a re-fetch — the raw `KeyError` escaped
        `acquire_subset_granule` entirely."""
        data_root = tmp_path / "data_root"
        target = ia.subset_granule_artifact_path(
            data_root, archive_filename=_ARCHIVE_FILENAME
        )
        _write_fake_subset_granule(
            target, archive_filename=_ARCHIVE_FILENAME, value=1.0
        )
        _strip_precipitation_attribute(target, "units")
        good = tmp_path / "good.dap.nc4"
        _write_fake_subset_granule(good, archive_filename=_ARCHIVE_FILENAME, value=3.5)
        client = FakeImergSubsetHttpClient(content=good.read_bytes())
        result = ia.acquire_subset_granule(
            _box_granule(),
            archive_filename=_ARCHIVE_FILENAME,
            client=client,
            data_root=data_root,
            lon_bounds=(_BOX_LON_START, _BOX_LON_STOP),
            lat_bounds=(_BOX_LAT_START, _BOX_LAT_STOP),
        )
        assert result == target
        assert len(client.calls) == 1  # the poisoned cache did NOT suppress it
        ia.observe_subset_read_contract(target, archive_filename=_ARCHIVE_FILENAME)


# --- fixer round 3, MINOR 2 — D5's mandated ORDER: the coordinate vectors are
# the station-mapping invariant, so they are compared FIRST. ---


class TestCrossCheckComparesCoordinatesFirst:
    def test_a_coordinate_disagreement_raises_before_any_value_is_read(
        self, tmp_path: Path
    ) -> None:
        """🔴 The archive here covers the box with NINETY-ONE lon cells (its
        cell 2599 also falls inside 80..89), so the two sides' value arrays
        are (91, 50) against (90, 50). Before the fix the archive slice was
        read and subtracted BEFORE the coordinate comparison, so this raised a
        raw numpy broadcasting `ValueError` — an untyped crash instead of D5's
        coordinate gate. The typed error is the assertion: a `ValueError` that
        is not an `ImergSubsetCoordinateMismatchError` fails this test."""
        lon = _ARCHIVE_LON.copy()
        lon[_BOX_LON_START - 1] = np.float32(80.0)
        archive_path = tmp_path / _ARCHIVE_FILENAME
        _write_fake_archive_granule_for_box(archive_path, value=3.5, lon=lon)
        subset_bytes_path = tmp_path / "subset_source.dap.nc4"
        _write_fake_subset_granule(
            subset_bytes_path, archive_filename=_ARCHIVE_FILENAME, value=3.5
        )
        client = FakeImergSubsetHttpClient(content=subset_bytes_path.read_bytes())
        with pytest.raises(
            ia.ImergSubsetCoordinateMismatchError, match="lon vector does not match"
        ):
            ia.cross_check_subset_against_archive(
                archive_path=archive_path,
                data_root=tmp_path / "data_root",
                client=client,
            )

    def test_the_coordinate_gate_is_a_read_contract_error_subclass(self) -> None:
        """A caller that already catches `ImergReadContractError` — every
        production caller does — must not be broken by the new type."""
        assert issubclass(
            ia.ImergSubsetCoordinateMismatchError, ia.ImergReadContractError
        )


# --- fixer round 3, MAJOR 2 — the permanent record's revision guard is
# ROUTE-AWARE: raw storage is keyed by the ARCHIVE filename on both routes, so
# an archive record and a subset record of the same granule necessarily
# disagree on every checksum (8 MB global HDF5 vs 25 KB OPeNDAP response). ---


def _reference_subset_read_contract() -> dict[str, object]:
    return ia.ImergSubsetReadContract(
        **_subset_contract_kwargs(),
        granule_revision=ia.PINNED_GRANULE_REVISION_PER_PLAN,
    ).as_manifest_dict()


def _complete_record_for_route(route: str, *, checksum: str) -> Any:
    name = ia.ImergGranuleId(start=ia.FIRST_GRANULE_START).filename(revision="V07B")
    contract = (
        _reference_subset_read_contract()
        if route == ia.SUBSET_ROUTE
        else _reference_read_contract()
    )
    return _derivation_complete_manifest(
        granule_checksums={name: checksum}, route=route, read_contract=contract
    )


class TestRouteSwitchIsNotAnArchiveRevision:
    def test_a_subset_record_may_replace_a_complete_archive_record(
        self, tmp_path: Path
    ) -> None:
        """🔴 Both records are COMPLETE and both key their checksums by the
        ARCHIVE filename, so the checksums MUST differ — the bytes are a
        different product. The guard ran before route was considered and
        rejected a legitimate route switch as "GES DISC revised the archive"."""
        path = ia.acquisition_manifest_path(tmp_path)
        ia.write_acquisition_manifest(
            _complete_record_for_route(ia.ROUTE, checksum="archivesha"), path
        )
        ia.write_acquisition_manifest(
            _complete_record_for_route(ia.SUBSET_ROUTE, checksum="subsetsha"), path
        )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.route == ia.SUBSET_ROUTE
        assert ia.is_complete_acquisition(retained) is True

    def test_an_archive_record_may_replace_a_complete_subset_record(
        self, tmp_path: Path
    ) -> None:
        """The reverse direction — a route switch is symmetric."""
        path = ia.acquisition_manifest_path(tmp_path)
        ia.write_acquisition_manifest(
            _complete_record_for_route(ia.SUBSET_ROUTE, checksum="subsetsha"), path
        )
        ia.write_acquisition_manifest(
            _complete_record_for_route(ia.ROUTE, checksum="archivesha"), path
        )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.route == ia.ROUTE

    def test_a_same_route_checksum_disagreement_is_still_refused(
        self, tmp_path: Path
    ) -> None:
        """⛔ The guard is narrowed, not removed: WITHIN one route a changed
        checksum still means GES DISC revised the archive."""
        path = ia.acquisition_manifest_path(tmp_path)
        ia.write_acquisition_manifest(
            _complete_record_for_route(ia.SUBSET_ROUTE, checksum="subsetsha"), path
        )
        with pytest.raises(ia.ImergStorageError, match="revised the archive"):
            ia.write_acquisition_manifest(
                _complete_record_for_route(ia.SUBSET_ROUTE, checksum="othersha"), path
            )

    def test_a_route_switch_still_may_not_orphan_a_published_bundle(
        self, tmp_path: Path
    ) -> None:
        """⛔ A route change is not waved through: `route` is part of the
        identity content, so the orphan guard — not the revision guard — is
        what refuses it when a published bundle names the retained record."""
        path = ia.acquisition_manifest_path(tmp_path)
        ia.write_acquisition_manifest(
            _complete_record_for_route(ia.ROUTE, checksum="archivesha"), path
        )
        retained_before = ia.read_acquisition_manifest(path)
        assert retained_before is not None
        named_digest = ie.acquisition_record_digest(retained_before)
        bundle = ia.imerg_points_root(tmp_path) / f"0000-{named_digest[:12]}"
        bundle.mkdir(parents=True)
        (bundle / "extraction_manifest.json").write_text(
            json.dumps({"acquisition_record_sha256": named_digest})
        )
        with pytest.raises(ia.ImergStorageError, match="published IMERG bundle"):
            ia.write_acquisition_manifest(
                _complete_record_for_route(ia.SUBSET_ROUTE, checksum="subsetsha"), path
            )
        retained = ia.read_acquisition_manifest(path)
        assert retained is not None
        assert retained.route == ia.ROUTE


# --- 🔴 Plan 225 T3 — D3's four behaviours and the subset-route window
# runner. Measured on the pre-T3 HEAD: `grep` returned ZERO matches for `429`,
# `Retry-After`, `Session` or any cadence, and `acquire_granule` listed the day
# directory once per GRANULE. Each class below fails against that code. ---


_TRIAL_DAY_START = datetime(2020, 7, 15, 0, 0, tzinfo=UTC)
_TRIAL_DAY_END = datetime(2020, 7, 15, 23, 30, tzinfo=UTC)


def _archive_name(start: datetime) -> str:
    return ia.ImergGranuleId(start=start).filename(revision="V07B")


def _starts_from(first: datetime, count: int) -> list[datetime]:
    return [first + i * timedelta(minutes=30) for i in range(count)]


def _day_listing(starts: Sequence[datetime]) -> str:
    """A GES DISC day-directory page, in the only shape the resolver reads:
    the granule names appearing somewhere in the text."""
    return "\n".join(
        f'<a href="{_archive_name(s)}">{_archive_name(s)}</a>' for s in starts
    )


def _listing_client(
    all_starts: Sequence[datetime], *, present: Sequence[datetime] | None = None
) -> FakeImergHttpClient:
    """One listing per DAY covering `all_starts`, advertising only `present`."""
    listed = all_starts if present is None else present
    by_day: dict[str, list[datetime]] = {
        ia.ImergGranuleId(start=s).directory_url(): [] for s in all_starts
    }
    for start in listed:
        by_day[ia.ImergGranuleId(start=start).directory_url()].append(start)
    return FakeImergHttpClient(
        listings={url: _day_listing(starts) for url, starts in by_day.items()}
    )


def _subset_fixture_bytes(tmp_path: Path, start: datetime, *, value: float) -> bytes:
    name = _archive_name(start)
    path = tmp_path / f"source-{name}.dap.nc4"
    _write_fake_subset_granule(path, archive_filename=name, value=value)
    return path.read_bytes()


def _subset_bytes_with_product_version(
    tmp_path: Path, start: datetime, *, product_version: str
) -> bytes:
    """A subset fixture identical to `_subset_fixture_bytes`' except for the
    `FileHeader.ProductVersion` spelling — the ONE thing that changed across
    the measured 2024-06 boundary."""
    name = _archive_name(start)
    path = tmp_path / f"pv-{product_version}-{name}.dap.nc4"
    _write_fake_subset_granule(
        path, archive_filename=name, product_version=product_version
    )
    return path.read_bytes()


@dataclass
class FakeSubsetWindowClient:
    """No-network double for the T3 runner: one fixture per OPeNDAP URL, plus
    an optional queue of exceptions to raise before the next success. An
    unknown URL is a 404, exactly as GES DISC would answer for a granule the
    listing advertised but the subset service does not hold."""

    content_by_url: dict[str, bytes] = field(default_factory=dict)
    failures: list[Exception] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def fetch_subset(self, *, url: str, constraint: str) -> bytes:
        self.calls.append(url)
        if self.failures:
            raise self.failures.pop(0)
        try:
            return self.content_by_url[url]
        except KeyError:
            raise ia.ImergGranuleMissingError(f"not found: {url}") from None


def _subset_client(
    tmp_path: Path, starts: Sequence[datetime], *, value: float = 1.5
) -> FakeSubsetWindowClient:
    return FakeSubsetWindowClient(
        content_by_url={
            ia.subset_granule_url(archive_filename=_archive_name(s), start=s): (
                _subset_fixture_bytes(tmp_path, s, value=value)
            )
            for s in starts
        }
    )


@dataclass
class RecordingSleep:
    """The injected `sleep`, and a run's ONLY source of elapsed time: it
    RECORDS each pause and advances a virtual clock by it, which `monotonic`
    reads. ⛔ Virtual on purpose — the shared limiter states a 429 backoff as a
    DEADLINE all workers wait out, and proving that must neither cost seven
    real seconds nor turn an exact 7.0 into "about 7.0". Lock-guarded because
    with `--subset-workers` > 1 several threads pause through it."""

    calls: list[float] = field(default_factory=list)
    now: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, seconds: float) -> None:
        with self.lock:
            self.calls.append(seconds)
            self.now += seconds

    def monotonic(self) -> float:
        with self.lock:
            return self.now


def _run_window(
    tmp_path: Path,
    starts: Sequence[datetime],
    *,
    client: FakeImergHttpClient | None = None,
    subset_client: ia.ImergSubsetHttpClient | None = None,
    sleep: RecordingSleep | None = None,
    data_root: Path | None = None,
    interval_seconds: float = 0.25,
    workers: int = 1,
) -> ia.SubsetWindowRetrievalReport:
    pauses = sleep if sleep is not None else RecordingSleep()
    return ia.retrieve_subset_window(
        starts,
        client=client if client is not None else _listing_client(starts),
        subset_client=(
            subset_client
            if subset_client is not None
            else _subset_client(tmp_path, starts)
        ),
        data_root=data_root if data_root is not None else tmp_path / "data_root",
        clock=_clock,
        sleep=pauses,
        interval_seconds=interval_seconds,
        workers=workers,
        # ⛔ The pauses ARE the clock: see `RecordingSleep`.
        monotonic=pauses.monotonic,
    )


class TestSubsetWindowCadence:
    """D3.1 — "a fixed cadence between successful requests". ⛔ The pre-T3
    loop had no delay at all: `RecordingSleep.calls` was empty."""

    def test_a_fixed_interval_separates_successive_requests(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 4)
        sleep = RecordingSleep()
        report = _run_window(tmp_path, starts, sleep=sleep, interval_seconds=0.25)
        assert report.retrieved == 4
        # one day listing + four fetches = five requests, four gaps between them
        assert report.requests_issued == 5
        assert sleep.calls == [0.25] * 4

    def test_a_reused_artifact_costs_neither_a_request_nor_a_pause(
        self, tmp_path: Path
    ) -> None:
        """D3's resumability, measured in REQUESTS: the cached granule must
        not consume cadence either, which is why the pacer wraps the client
        rather than the loop."""
        starts = _starts_from(_TRIAL_DAY_START, 4)
        data_root = tmp_path / "data_root"
        cached_name = _archive_name(starts[0])
        _write_fake_subset_granule(
            ia.subset_granule_artifact_path(data_root, archive_filename=cached_name),
            archive_filename=cached_name,
            value=1.5,
        )
        sleep = RecordingSleep()
        subset_client = _subset_client(tmp_path, starts)
        report = _run_window(
            tmp_path,
            starts,
            sleep=sleep,
            subset_client=subset_client,
            data_root=data_root,
            interval_seconds=0.25,
        )
        assert report.retrieved == 4
        assert report.reused == 1
        assert len(subset_client.calls) == 3
        assert report.requests_issued == 4  # one listing + three fetches
        assert sleep.calls == [0.25] * 3


def _ladder_429(*, retry_after: str | None) -> Exception:
    """The exception the REAL status ladder raises for a 429 — ⛔ never a
    hand-written stand-in: the retry tests below must exercise the ladder's
    own CLASSIFICATION (pre-T3 it was the non-retryable
    `ImergRequestFailedError`), not merely the pacer's handling of a type the
    test itself chose."""
    try:
        ia._raise_for_http_status(429, url="https://gpm1/x", retry_after=retry_after)
    except ia.ImergTransientError as exc:
        return exc
    except ia.ImergAcquisitionError as exc:  # pragma: no cover - the pre-T3 branch
        raise AssertionError(
            f"429 must classify as a RETRYABLE transient failure, got {exc!r}"
        ) from exc
    raise AssertionError("429 did not raise at all")


class TestSubsetWindowRateLimiting:
    """D3.2 — "retry 429 honouring Retry-After, with backoff". ⛔ On pre-T3
    HEAD a 429 fell into the status ladder's non-retryable tail, so the FIRST
    throttle aborted the run."""

    def test_the_status_ladder_makes_429_retryable_and_reads_retry_after(
        self,
    ) -> None:
        with pytest.raises(ia.ImergRateLimitedError) as excinfo:
            ia._raise_for_http_status(429, url="https://x/y", retry_after="12")
        assert isinstance(excinfo.value, ia.ImergTransientError)
        assert not isinstance(excinfo.value, ia.ImergRequestFailedError)
        assert excinfo.value.retry_after_seconds == 12.0

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("soon", None),
            ("5", 5.0),
            ("-3", 0.0),
            (" 7 ", 7.0),
        ],
    )
    def test_retry_after_parsing(self, raw: str | None, expected: float | None) -> None:
        assert ia.parse_retry_after(raw) == expected

    def test_a_429_carrying_retry_after_is_retried_after_the_servers_own_delay(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 2)
        subset_client = _subset_client(tmp_path, starts)
        subset_client.failures = [_ladder_429(retry_after="7")]
        sleep = RecordingSleep()
        report = _run_window(
            tmp_path,
            starts,
            subset_client=subset_client,
            sleep=sleep,
            interval_seconds=0.25,
        )
        assert report.retrieved == 2  # the run did NOT abort
        assert report.rate_limited == 1
        assert 7.0 in sleep.calls  # the SERVER's number, not our backoff curve
        assert 2.0 not in sleep.calls

    def test_a_429_without_retry_after_falls_back_to_backoff(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 2)
        subset_client = _subset_client(tmp_path, starts)
        subset_client.failures = [_ladder_429(retry_after=None)]
        sleep = RecordingSleep()
        report = _run_window(
            tmp_path,
            starts,
            subset_client=subset_client,
            sleep=sleep,
            interval_seconds=0.25,
        )
        assert report.retrieved == 2
        assert report.rate_limited == 1
        assert 2.0 in sleep.calls  # backoff_base * 2**0

    def test_a_404_is_never_retried(self, tmp_path: Path) -> None:
        """⛔ The retry is for TRANSIENT failures only: a missing granule is
        data (a gap), and retrying it five times would be five wasted
        requests per gap over a 105,216-granule window."""
        starts = _starts_from(_TRIAL_DAY_START, 2)
        subset_client = _subset_client(tmp_path, starts)
        del subset_client.content_by_url[
            ia.subset_granule_url(
                archive_filename=_archive_name(starts[1]), start=starts[1]
            )
        ]
        report = _run_window(tmp_path, starts, subset_client=subset_client)
        assert report.missing == (starts[1].isoformat(),)
        assert len(subset_client.calls) == 2  # one attempt for the gap, not five


@dataclass
class _FakeResponse:
    status_code: int = 200
    text: str = ""
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    history: list[object] = field(default_factory=list)
    trickle_chunk_bytes: int | None = None
    """When set, the body arrives in chunks of this size rather than at once —
    the shape of the measured 2026-08-31 stall. ⚠️ Bytes KEEP ARRIVING, so the
    per-recv read timeout keeps resetting and never fires; only a total-elapsed
    deadline can end it."""
    closed: bool = False

    def iter_content(self, chunk_size: int = 1) -> Any:
        if self.trickle_chunk_bytes is None:
            yield self.content
            return
        # ⛔ FINITE on purpose: with the deadline reverted this test must FAIL,
        # not hang, or the revert-proof proves nothing.
        for i in range(0, len(self.content), self.trickle_chunk_bytes):
            yield self.content[i : i + self.trickle_chunk_bytes]

    def close(self) -> None:
        self.closed = True


@dataclass
class _RecordingSession:
    responses: list[_FakeResponse]
    calls: list[str] = field(default_factory=list)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        return self.responses.pop(0)


class TestOneReusedSession:
    """D3.3/D4 — "one authenticated, cookie-bearing session, reused". ⛔ Every
    call on pre-T3 HEAD was a bare `requests.get`, which builds a throwaway
    session and discards its cookie jar, so each request re-ran the whole
    Earthdata redirect chain."""

    def test_every_client_call_goes_through_the_one_injected_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import requests

        def boom(*args: Any, **kwargs: Any) -> None:
            raise AssertionError(
                "a bare requests.get bypasses the shared cookie-bearing session (D3.3)"
            )

        monkeypatch.setattr(requests, "get", boom)
        session = _RecordingSession(
            responses=[
                _FakeResponse(text="a listing"),
                _FakeResponse(content=b"granule bytes"),
                _FakeResponse(content=b"subset bytes"),
            ]
        )
        archive = ia.RealImergHttpClient(session=session)  # type: ignore[arg-type]
        subset = ia.RealImergSubsetHttpClient(session=session)  # type: ignore[arg-type]
        assert archive.list_directory("https://gpm1/2020/197/") == "a listing"
        archive.download_to_path(url="https://gpm1/g.HDF5", target=tmp_path / "g.HDF5")
        assert (
            subset.fetch_subset(
                url="https://gpm1/g.dap.nc4", constraint="/precipitation"
            )
            == b"subset bytes"
        )
        assert len(session.calls) == 3

    def test_the_shared_session_is_one_object_per_thread(self) -> None:
        """⛔ D3.3's literal "one session" is now "one per WORKER THREAD":
        `requests.Session` is not documented thread-safe, and putting one
        behind a lock would serialise exactly the I/O `--subset-workers`
        exists to overlap. The property D3.3 is FOR is unchanged and is what
        this asserts — a thread authenticates once and keeps its cookie jar,
        so a run pays `workers` cold Earthdata redirect chains, not 105,216."""
        assert ia.earthdata_session() is ia.earthdata_session()
        seen: list[object] = []
        for _ in range(2):
            worker = threading.Thread(
                target=lambda: seen.extend(
                    [ia.earthdata_session(), ia.earthdata_session()]
                )
            )
            worker.start()
            worker.join()
        assert seen[0] is seen[1]  # reused WITHIN the thread
        assert seen[2] is seen[3]
        assert seen[0] is not seen[2]  # a different thread, a different jar
        assert seen[0] is not ia.earthdata_session()

    def test_both_real_clients_default_to_that_same_session(self) -> None:
        assert ia.RealImergHttpClient().session is None
        assert ia.RealImergSubsetHttpClient().session is None
        assert ia._session_for(None) is ia.earthdata_session()


class TestPerDayFilenameResolution:
    """D3.4 — "resolve each day's filenames ONCE". ⛔ `acquire_granule` lists
    the day directory once per GRANULE (and before checking the disk), so the
    archive route would issue 105,216 listings on top of its downloads."""

    def test_one_listing_serves_a_whole_day(self, tmp_path: Path) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 6)
        client = _listing_client(starts)
        report = _run_window(tmp_path, starts, client=client)
        assert report.retrieved == 6
        assert len(client.list_calls) == 1

    def test_each_day_is_listed_exactly_once(self, tmp_path: Path) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 2) + _starts_from(
            _TRIAL_DAY_START + timedelta(days=1), 2
        )
        client = _listing_client(starts)
        report = _run_window(tmp_path, starts, client=client)
        assert report.retrieved == 4
        assert len(client.list_calls) == 2
        assert len(set(client.list_calls)) == 2

    def test_a_reused_artifact_still_costs_no_extra_listing(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 3)
        client = _listing_client(starts)
        _run_window(tmp_path, starts, client=client, data_root=tmp_path / "dr")
        _run_window(tmp_path, starts, client=client, data_root=tmp_path / "dr")
        assert len(client.list_calls) == 2  # one per RUN, not one per granule


class TestSubsetWindowGapsAreRecordedAsGaps:
    """Plan 220's rule — a gap is data, a wrong retrieval is not. ⛔ Nothing
    here scales, fills or interpolates a missing granule."""

    def test_a_granule_absent_from_the_listing_is_a_gap_and_is_never_fetched(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 4)
        present = [s for s in starts if s != starts[2]]
        subset_client = _subset_client(tmp_path, starts)
        report = _run_window(
            tmp_path,
            starts,
            client=_listing_client(starts, present=present),
            subset_client=subset_client,
        )
        assert report.requested == 4
        assert report.retrieved == 3
        assert report.missing == (starts[2].isoformat(),)
        assert len(subset_client.calls) == 3

    def test_the_gap_is_carried_into_the_permanent_record(self, tmp_path: Path) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 4)
        data_root = tmp_path / "data_root"
        _run_window(
            tmp_path,
            starts,
            client=_listing_client(starts, present=starts[:3]),
            data_root=data_root,
        )
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.missing == (starts[3].isoformat(),)
        assert manifest.requested == 4
        assert manifest.retrieved == 3


@dataclass
class _SteppingClock:
    """A `time.monotonic` stand-in that advances a fixed step on every read.
    ⛔ No real time passes: proving a 120-second deadline must not cost 120
    seconds, and `sleep` is injected for the same reason."""

    step: float
    now: float = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@dataclass
class _TricklingSubsetSession:
    """A no-network `requests.Session` double for the SUBSET route: one body
    per URL, and a set of URLs whose body TRICKLES a byte at a time."""

    content_by_url: dict[str, bytes] = field(default_factory=dict[str, bytes])
    stalling: set[str] = field(default_factory=set[str])
    calls: list[str] = field(default_factory=list[str])

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        if url in self.stalling:
            return _FakeResponse(content=b"\x00" * 4096, trickle_chunk_bytes=1)
        try:
            return _FakeResponse(content=self.content_by_url[url])
        except KeyError:
            return _FakeResponse(status_code=404)


class TestSubsetRequestDeadline:
    """The 2026-08-31 trial measured ONE fetch held open 1,971 s (32.9 min)
    against 2.394 s for the other 47. GES DISC never answered 429 — it
    TRICKLED, so `timeout_seconds` reset on every recv and never fired.
    ⇒ The per-recv timeout bounds a DEAD connection; the deadline bounds a
    live-but-useless one. Correctness was never at risk here; wall clock was
    (~51 days over the 105,216-granule window at the observed 1-in-47 rate)."""

    def _client(
        self, session: _TricklingSubsetSession | _RecordingSession
    ) -> ia.RealImergSubsetHttpClient:
        return ia.RealImergSubsetHttpClient(
            session=session,  # type: ignore[arg-type]
            monotonic=_SteppingClock(step=10.0),
            deadline_seconds=120.0,
        )

    def test_the_default_deadline_is_the_measured_outlier_killer(self) -> None:
        """50x the 2.394 s healthy mean and 6.1 % of the 1,971 s stall — a
        bound in the gap between them, biased generous because a premature
        abandon on all five attempts would record a gap that is not one."""
        assert ia.DEFAULT_SUBSET_REQUEST_DEADLINE_SECONDS == 120.0
        assert ia.RealImergSubsetHttpClient().deadline_seconds == (
            ia.DEFAULT_SUBSET_REQUEST_DEADLINE_SECONDS
        )
        # ⛔ The per-recv timeout is NOT the bound and must stay beside it.
        assert ia.RealImergSubsetHttpClient().timeout_seconds == 60.0

    def test_a_trickling_response_is_abandoned_and_the_retry_then_succeeds(
        self,
    ) -> None:
        """The locking test: a body that keeps arriving past the deadline is
        abandoned as a TRANSIENT failure, so `RequestPacer` retries it on
        exactly the path a fired read timeout already takes, and the next
        healthy response wins."""
        session = _RecordingSession(
            responses=[
                _FakeResponse(content=b"\x00" * 4096, trickle_chunk_bytes=1),
                _FakeResponse(content=b"subset bytes"),
            ]
        )
        sleep = RecordingSleep()
        pacer = ia.RequestPacer(sleep=sleep, interval_seconds=0.0)
        paced = ia.PacedSubsetClient(client=self._client(session), pacer=pacer)
        got = paced.fetch_subset(
            url="https://gpm1/g.dap.nc4", constraint="/precipitation"
        )
        assert got == b"subset bytes"
        assert len(session.calls) == 2  # abandoned, then retried
        assert pacer.requests_issued == 2
        assert 2.0 in sleep.calls  # the existing backoff, unchanged
        assert session.responses == []

    def test_the_abandoned_stream_is_closed(self) -> None:
        """⛔ An abandoned stream must release its connection, or the retry
        competes with the attempt it replaced."""
        trickler = _FakeResponse(content=b"\x00" * 4096, trickle_chunk_bytes=1)
        session = _RecordingSession(responses=[trickler])
        with pytest.raises(ia.ImergTransientError, match="trickling"):
            self._client(session).fetch_subset(
                url="https://gpm1/g.dap.nc4", constraint="/precipitation"
            )
        assert trickler.closed

    def test_a_healthy_fetch_well_inside_the_deadline_is_untouched(self) -> None:
        session = _RecordingSession(responses=[_FakeResponse(content=b"subset bytes")])
        assert (
            self._client(session).fetch_subset(
                url="https://gpm1/g.dap.nc4", constraint="/precipitation"
            )
            == b"subset bytes"
        )


class TestAGranuleStallingOnEveryAttempt:
    """⛔ The retry budget must not turn one stalled granule into an aborted
    window. Pre-deadline the pacer's exhaustion raised straight through
    `retrieve_subset_window`, so granule N+1..105,216 would never be
    attempted. It is a GAP instead — Plan 220: a gap is data, a wrong
    retrieval is not — and ⛔ nothing fills, scales or interpolates it."""

    def _run(self, tmp_path: Path) -> tuple[ia.SubsetWindowRetrievalReport, Path, str]:
        starts = _starts_from(_TRIAL_DAY_START, 4)
        stalled = starts[2]
        stalled_url = ia.subset_granule_url(
            archive_filename=_archive_name(stalled), start=stalled
        )
        session = _TricklingSubsetSession(
            content_by_url={
                ia.subset_granule_url(archive_filename=_archive_name(s), start=s): (
                    _subset_fixture_bytes(tmp_path, s, value=1.5)
                )
                for s in starts
            },
            stalling={stalled_url},
        )
        data_root = tmp_path / "data_root"
        report = _run_window(
            tmp_path,
            starts,
            subset_client=ia.RealImergSubsetHttpClient(
                session=session,  # type: ignore[arg-type]
                monotonic=_SteppingClock(step=10.0),
                deadline_seconds=120.0,
            ),
            data_root=data_root,
        )
        assert session.calls.count(stalled_url) == 5  # every attempt, then a gap
        return report, data_root, stalled.isoformat()

    def test_the_run_continues_and_the_stalled_granule_becomes_a_gap(
        self, tmp_path: Path
    ) -> None:
        report, _, stalled = self._run(tmp_path)
        assert report.requested == 4
        assert report.retrieved == 3  # the run did NOT abort
        assert report.missing == (stalled,)
        # ⛔ A gap for a DIFFERENT reason than a 404, and the report says so:
        # these gaps deserve a re-run before anyone reads them as absent data.
        assert report.failed == 1

    def test_the_gap_reaches_the_permanent_record(self, tmp_path: Path) -> None:
        _, data_root, stalled = self._run(tmp_path)
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.missing == (stalled,)
        assert manifest.requested == 4
        assert manifest.retrieved == 3

    def test_a_contract_violation_is_still_fatal(self, tmp_path: Path) -> None:
        """⛔ The gap path catches `ImergRetriesExhaustedError` ONLY. Catching
        its parent would swallow `ImergReadContractError` — the same parent —
        and turn a route/grid disagreement into a silent gap, which is exactly
        the wrong retrieval D1 and D5 exist to stop."""
        assert issubclass(ia.ImergReadContractError, ia.ImergRequestFailedError)
        assert issubclass(ia.ImergRetriesExhaustedError, ia.ImergRequestFailedError)
        assert not issubclass(ia.ImergReadContractError, ia.ImergRetriesExhaustedError)
        starts = _starts_from(_TRIAL_DAY_START, 2)
        session = _TricklingSubsetSession(
            content_by_url={
                ia.subset_granule_url(archive_filename=_archive_name(s), start=s): (
                    b"not an HDF5 file at all"
                )
                for s in starts
            }
        )
        with pytest.raises(ia.ImergReadContractError):
            _run_window(
                tmp_path,
                starts,
                subset_client=ia.RealImergSubsetHttpClient(
                    session=session,  # type: ignore[arg-type]
                    monotonic=_SteppingClock(step=10.0),
                ),
                data_root=tmp_path / "data_root",
            )


class TestSubsetWindowManifest:
    """D2 — the ROUTE is recorded, and the recorded contract must satisfy that
    route's own frozen contract."""

    def test_records_the_subset_route_and_a_contract_that_dispatches_to_it(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 3)
        data_root = tmp_path / "data_root"
        report = _run_window(tmp_path, starts, data_root=data_root)
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.route == ia.SUBSET_ROUTE
        assert (
            ia.read_contract_violations(
                manifest.read_contract,
                granule_revision=manifest.granule_revision,
                route=manifest.route,
            )
            == ()
        )
        # ⛔ the ARCHIVE spelling keys the record on BOTH routes
        assert set(manifest.granule_checksums) == {_archive_name(s) for s in starts}
        assert manifest.requested_window_start == starts[0]
        assert manifest.requested_window_end == starts[-1]
        assert report.total_bytes > 0

    def test_no_record_is_written_when_nothing_was_retrieved(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 2)
        data_root = tmp_path / "data_root"
        report = _run_window(
            tmp_path,
            starts,
            client=_listing_client(starts, present=[]),
            data_root=data_root,
        )
        assert report.retrieved == 0
        assert report.missing == tuple(s.isoformat() for s in starts)
        assert not ia.acquisition_manifest_path(data_root).exists()


# --- 🔴 bounded concurrency: the four things N workers break. Every test
# below is driven by injected fakes — no network, no `sleep`-based timing
# race, no wall-clock assertion. `RecordingSleep` is the only clock. ---


@dataclass
class _GateWatchingSleep(RecordingSleep):
    """A `RecordingSleep` that also records whether the shared limiter's gate
    was HELD for each pause. ⛔ The one instrument that separates ONE global
    rate limit from N per-worker ones: both sleep once per request, so
    counting pauses cannot tell them apart — whether the pauses OVERLAP can,
    and a pause taken under the gate cannot overlap another."""

    pacer: ia.RequestPacer | None = None
    gated: list[tuple[float, bool]] = field(default_factory=list[tuple[float, bool]])

    def __call__(self, seconds: float) -> None:
        assert self.pacer is not None
        held = self.pacer.gate_locked()
        super().__call__(seconds)
        with self.lock:
            self.gated.append((seconds, held))


def _pacer_with_gate_watch(
    *, interval_seconds: float, max_attempts: int = 5
) -> tuple[ia.RequestPacer, _GateWatchingSleep]:
    sleep = _GateWatchingSleep()
    pacer = ia.RequestPacer(
        sleep=sleep,
        interval_seconds=interval_seconds,
        max_attempts=max_attempts,
        monotonic=sleep.monotonic,
    )
    sleep.pacer = pacer
    return pacer, sleep


def _run_on_threads(target: Callable[[], object], *, count: int) -> None:
    """Run `target` on `count` real threads and join them. ⛔ No timeout and no
    sleep: every thread below either completes or blocks on state another
    thread is guaranteed to set, so the test cannot pass by winning a race."""
    threads = [threading.Thread(target=target) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


class TestTheCadenceIsOneGlobalRateLimit:
    """🔴 N workers each honouring a 1 s interval is N requests/second — the
    politeness property D3.1 exists for, silently multiplied. The interval
    must separate any two requests WHATEVER worker issues them."""

    def test_every_cadence_pause_is_taken_under_the_shared_gate(self) -> None:
        """⛔ The revert-proof assertion, and the only one that can tell ONE
        global limiter from N per-worker ones: both sleep once per request, so
        counting pauses cannot separate them — whether the pauses may OVERLAP
        can. Move the pause out of `_take_slot`'s `with self._gate` and four
        workers pause concurrently: one interval of wall clock, four
        requests."""
        pacer, sleep = _pacer_with_gate_watch(interval_seconds=0.25)
        _run_on_threads(lambda: [pacer.run(lambda: "ok") for _ in range(2)], count=4)
        assert pacer.requests_issued == 8
        assert sleep.gated  # the pacer really did pace
        assert all(held for _, held in sleep.gated)
        # every request after the first paid the FULL interval, exactly once
        assert sleep.calls == [0.25] * 7
        assert pacer.min_issue_gap_seconds == 0.25  # exact: the clock is the pauses

    def test_four_workers_never_start_two_requests_inside_the_interval(
        self, tmp_path: Path
    ) -> None:
        """The whole-run form: ONE limiter for the listings and every worker's
        fetches, so the rate the run reached is the rate it was configured
        with. ⛔ `requests_issued` counts the day listing too — a per-worker
        limiter would each report only its own share."""
        starts = _starts_from(_TRIAL_DAY_START, 8)
        sleep = RecordingSleep()
        report = _run_window(
            tmp_path, starts, sleep=sleep, interval_seconds=0.25, workers=4
        )
        assert report.retrieved == 8
        assert report.requests_issued == 9  # one listing + eight fetches
        assert report.min_request_gap_seconds == 0.25
        assert sleep.calls == [0.25] * 8


class TestA429BacksOffEveryWorker:
    """🔴 If only the receiving worker honours `Retry-After` while the other
    three keep firing, we answer NASA's "slow down" by continuing at 3/4
    speed."""

    def test_the_delay_is_registered_on_the_shared_limiter_not_the_caller(
        self,
    ) -> None:
        """⛔ Single-threaded on purpose: the property is that the pause
        outlives the CALL that received the 429. A later request that never
        saw a 429 — any other worker, in a run — waits out the remainder.
        Revert to sleeping the delay inside the failing call and the second
        request below pays only the cadence."""
        pacer, sleep = _pacer_with_gate_watch(interval_seconds=0.25, max_attempts=1)

        def throttled() -> str:
            raise _ladder_429(retry_after="7")

        with pytest.raises(ia.ImergRetriesExhaustedError):
            pacer.run(throttled)
        assert sleep.calls == []  # the receiving call did NOT serve it alone
        assert pacer.run(lambda: "second worker") == "second worker"
        # ⛔ the server's 7 s FIRST, then the ordinary cadence on top
        assert sleep.calls == [7.0, 0.25]
        assert pacer.rate_limited == 1
        assert pacer.retry_after_honoured == 1

    def test_the_429_one_worker_received_pauses_the_others(self) -> None:
        """The threaded form, made deterministic: the worker that receives the
        429 has EXHAUSTED its one attempt and is gone before the others start,
        so the 7 s they wait cannot be its own retry. It is served ONCE, by
        whoever reaches the gate first, and the rest resume with it — not each
        in turn."""
        pacer, sleep = _pacer_with_gate_watch(interval_seconds=0.25, max_attempts=1)
        registered = threading.Event()

        def throttled() -> str:
            raise _ladder_429(retry_after="7")

        def receive_the_429() -> None:
            with pytest.raises(ia.ImergRetriesExhaustedError):
                pacer.run(throttled)
            registered.set()

        def other_worker() -> None:
            registered.wait()
            pacer.run(lambda: "ok")

        receiver = threading.Thread(target=receive_the_429)
        receiver.start()
        _run_on_threads(other_worker, count=3)
        receiver.join()
        assert pacer.rate_limited == 1
        # ⛔ ONE 7 s pause for all three, not three of them, and taken under
        # the gate so none of them could issue inside it.
        assert sleep.calls == [7.0, 0.25, 0.25, 0.25]
        assert (7.0, True) in sleep.gated
        assert all(held for _, held in sleep.gated)

    def test_a_throttled_run_with_four_workers_waits_the_delay_out(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 6)
        subset_client = _subset_client(tmp_path, starts)
        subset_client.failures = [_ladder_429(retry_after="7")]
        sleep = RecordingSleep()
        report = _run_window(
            tmp_path,
            starts,
            subset_client=subset_client,
            sleep=sleep,
            interval_seconds=0.25,
            workers=4,
        )
        assert report.retrieved == 6  # the run did NOT abort
        assert report.rate_limited == 1
        # the server's own delay was served in full before the run finished
        assert sleep.now >= 7.0


@dataclass
class _OutOfOrderSubsetClient:
    """Forces the day's FIRST granule to complete LAST: its fetch blocks until
    the last granule's fetch has returned. ⛔ Event-driven, never a sleep —
    the ordering is FORCED, so the test cannot pass by winning a race. It also
    cannot deadlock: the blocking granule is the only one that waits, and
    nothing waits on it."""

    content_by_url: dict[str, bytes]
    first_url: str
    last_url: str
    released: threading.Event = field(default_factory=threading.Event)
    completed: list[str] = field(default_factory=list[str])
    lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch_subset(self, *, url: str, constraint: str) -> bytes:
        if url == self.first_url:
            self.released.wait()
        body = self.content_by_url[url]
        with self.lock:
            self.completed.append(url)
        if url == self.last_url:
            self.released.set()
        return body


class TestTheManifestIsIndependentOfCompletionOrder:
    """🔴 The acquisition record is IDENTITY-BEARING and its digest is
    compared. If `granule_checksums` / `granule_retrieved_at` serialised in
    COMPLETION order, two identical runs would write different bytes."""

    def _urls(self, tmp_path: Path, starts: Sequence[datetime]) -> dict[str, bytes]:
        return {
            ia.subset_granule_url(archive_filename=_archive_name(s), start=s): (
                _subset_fixture_bytes(tmp_path, s, value=1.5)
            )
            for s in starts
        }

    def test_out_of_order_completion_writes_the_byte_identical_manifest(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 4)
        urls = list(self._urls(tmp_path, starts))
        serial_root = tmp_path / "serial"
        _run_window(tmp_path, starts, data_root=serial_root, workers=1)

        shuffled = _OutOfOrderSubsetClient(
            content_by_url=self._urls(tmp_path, starts),
            first_url=urls[0],
            last_url=urls[-1],
        )
        concurrent_root = tmp_path / "concurrent"
        _run_window(
            tmp_path,
            starts,
            subset_client=shuffled,
            data_root=concurrent_root,
            workers=2,
        )
        # the run really did complete out of order
        assert shuffled.completed[-1] == urls[0]
        assert shuffled.completed[0] != urls[0]

        serial = ia.acquisition_manifest_path(serial_root).read_text()
        concurrent = ia.acquisition_manifest_path(concurrent_root).read_text()
        assert concurrent == serial
        assert list(json.loads(concurrent)["granule_checksums"]) == [
            _archive_name(s) for s in starts
        ]

    def test_the_digest_a_published_bundle_names_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        """⚠️ NOT a second lock on the ordering, and it must not be read as
        one: `imerg_extract._canonical_json` dumps with `sort_keys=True`, so
        the digest survives a reordering ON ITS OWN — measured, by reverting
        the fold to completion order, which leaves this test green and fails
        only the byte test above. What it does lock is the CONSEQUENCE a
        published bundle depends on: if the canonicalisation ever stopped
        sorting, the byte ordering above would become identity-bearing here
        too."""
        starts = _starts_from(_TRIAL_DAY_START, 4)
        urls = list(self._urls(tmp_path, starts))
        serial_root = tmp_path / "serial"
        _run_window(tmp_path, starts, data_root=serial_root, workers=1)
        concurrent_root = tmp_path / "concurrent"
        _run_window(
            tmp_path,
            starts,
            subset_client=_OutOfOrderSubsetClient(
                content_by_url=self._urls(tmp_path, starts),
                first_url=urls[0],
                last_url=urls[-1],
            ),
            data_root=concurrent_root,
            workers=2,
        )
        records = [
            ia.read_acquisition_manifest(ia.acquisition_manifest_path(root))
            for root in (serial_root, concurrent_root)
        ]
        assert records[0] is not None
        assert records[1] is not None
        assert ie.acquisition_record_digest(records[0]) == (
            ie.acquisition_record_digest(records[1])
        )


class TestTheExistingGuaranteesHoldUnderConcurrency:
    """Resumability, gap-on-exhaustion and contract-violation-is-fatal are not
    weakened by `--subset-workers`. ⛔ Each is the same behaviour the serial
    tests above lock, re-asserted with four workers."""

    def test_an_artifact_already_on_disk_is_not_re_fetched(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 6)
        data_root = tmp_path / "data_root"
        # ⛔ the LAST granule of six, against four workers: it cannot start
        # until a worker has freed itself by COMPLETING a fetch, so "some
        # other worker has already issued a request" is guaranteed rather
        # than likely — which is exactly the state a shared request counter
        # would misread as a fetch.
        cached_name = _archive_name(starts[5])
        _write_fake_subset_granule(
            ia.subset_granule_artifact_path(data_root, archive_filename=cached_name),
            archive_filename=cached_name,
            value=1.5,
        )
        subset_client = _subset_client(tmp_path, starts)
        report = _run_window(
            tmp_path,
            starts,
            subset_client=subset_client,
            data_root=data_root,
            workers=4,
        )
        assert report.retrieved == 6
        # ⛔ attributed to the RIGHT granule: a before/after delta on the
        # shared pacer counter would charge this granule other workers'
        # requests and report a fetch as a reuse.
        assert report.reused == 1
        assert len(subset_client.calls) == 5
        assert report.requests_issued == 6  # one listing + five fetches

    def test_a_granule_stalling_on_every_attempt_is_still_only_a_gap(
        self, tmp_path: Path
    ) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 4)
        stalled = starts[2]
        stalled_url = ia.subset_granule_url(
            archive_filename=_archive_name(stalled), start=stalled
        )
        session = _TricklingSubsetSession(
            content_by_url={
                ia.subset_granule_url(archive_filename=_archive_name(s), start=s): (
                    _subset_fixture_bytes(tmp_path, s, value=1.5)
                )
                for s in starts
            },
            stalling={stalled_url},
        )
        report = _run_window(
            tmp_path,
            starts,
            subset_client=ia.RealImergSubsetHttpClient(
                session=session,  # type: ignore[arg-type]
                monotonic=_SteppingClock(step=10.0),
                deadline_seconds=120.0,
            ),
            data_root=tmp_path / "data_root",
            workers=4,
        )
        assert report.retrieved == 3  # the run did NOT abort
        assert report.missing == (stalled.isoformat(),)
        assert report.failed == 1

    def test_a_contract_violation_is_still_fatal(self, tmp_path: Path) -> None:
        """⛔ A worker catches ONLY the two typed failures that are gaps;
        anything else escapes into its `Future` and is re-raised — in
        granule-start order, so WHICH violation aborts the run is
        deterministic too."""
        starts = _starts_from(_TRIAL_DAY_START, 4)
        session = _TricklingSubsetSession(
            content_by_url={
                ia.subset_granule_url(archive_filename=_archive_name(s), start=s): (
                    b"not an HDF5 file at all"
                )
                for s in starts
            }
        )
        with pytest.raises(ia.ImergReadContractError):
            _run_window(
                tmp_path,
                starts,
                subset_client=ia.RealImergSubsetHttpClient(
                    session=session,  # type: ignore[arg-type]
                    monotonic=_SteppingClock(step=10.0),
                ),
                data_root=tmp_path / "data_root",
                workers=4,
            )


class TestTheWorkerCountIsExplicit:
    def test_the_default_is_serial(self) -> None:
        """⛔ Conservative: the default is the behaviour the 48-granule trial
        actually measured, so concurrency is something a run ASKS for."""
        assert ia.DEFAULT_SUBSET_WORKERS == 1
        assert ia.build_parser().parse_args([]).subset_workers == 1

    def test_the_flag_is_read_from_the_command_line(self) -> None:
        args = ia.build_parser().parse_args(["--subset-workers", "4"])
        assert args.subset_workers == 4

    def test_a_run_with_no_worker_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ia.ImergRequestFailedError, match="at least 1"):
            _run_window(tmp_path, _starts_from(_TRIAL_DAY_START, 2), workers=0)


class TestSubsetBoxIndexBounds:
    def test_derives_the_measured_index_range_without_restating_it(self) -> None:
        assert ia.subset_box_index_bounds() == (
            (_BOX_LON_START, _BOX_LON_STOP),
            (_BOX_LAT_START, _BOX_LAT_STOP),
        )

    def test_the_derived_bounds_agree_with_the_archive_contracts_own_vectors(
        self,
    ) -> None:
        """The bounds T2 reads off a granule and the bounds a window run
        derives must be the same numbers — otherwise the two routes would ask
        for different boxes."""
        lon_bounds, lat_bounds = ia.subset_box_index_bounds()
        assert lon_bounds == ia.subset_box_indices(
            tuple(float(v) for v in _ARCHIVE_LON),
            low=ia.STUDY_BOX[1],
            high=ia.STUDY_BOX[3],
        )
        assert lat_bounds == ia.subset_box_indices(
            tuple(float(v) for v in _ARCHIVE_LAT),
            low=ia.STUDY_BOX[2],
            high=ia.STUDY_BOX[0],
        )


class TestExplicitWindowBound:
    """⛔ The bound is always stated: there is no default, and a run stops at
    the end it was given."""

    def test_the_trial_day_is_forty_eight_half_hour_granules(self) -> None:
        starts = ia.granule_starts_between(_TRIAL_DAY_START, _TRIAL_DAY_END)
        assert len(starts) == 48
        assert starts[0] == _TRIAL_DAY_START
        assert starts[-1] == _TRIAL_DAY_END

    def test_a_reversed_bound_is_refused(self) -> None:
        with pytest.raises(ia.ImergRequestFailedError, match="is after its end"):
            ia.granule_starts_between(_TRIAL_DAY_END, _TRIAL_DAY_START)

    def test_a_start_outside_the_pinned_window_is_refused(self) -> None:
        with pytest.raises(ia.ImergRequestFailedError, match="pinned D5 window"):
            ia.granule_starts_between(datetime(2019, 1, 1, tzinfo=UTC), _TRIAL_DAY_END)

    def test_a_non_half_hour_bound_is_refused(self) -> None:
        with pytest.raises(ia.ImergRequestFailedError, match="half-hour"):
            ia.granule_starts_between(
                datetime(2020, 7, 15, 0, 7, tzinfo=UTC), _TRIAL_DAY_END
            )


class TestSubsetWindowCli:
    def test_a_half_stated_bound_is_refused_rather_than_defaulted(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            ia.main(
                [
                    "--data-root",
                    str(tmp_path),
                    "--subset-window-start",
                    "2020-07-15T00:00:00Z",
                ]
            )
        assert excinfo.value.code == 2

    def test_the_cli_retrieves_exactly_the_stated_bound(self, tmp_path: Path) -> None:
        starts = _starts_from(_TRIAL_DAY_START, 3)
        data_root = tmp_path / "data_root"
        subset_client = _subset_client(tmp_path, starts)
        exit_code = ia.main(
            [
                "--data-root",
                str(data_root),
                "--subset-window-start",
                starts[0].isoformat(),
                "--subset-window-end",
                starts[-1].isoformat(),
                "--request-interval-seconds",
                "0",
            ],
            client=_listing_client(starts),
            subset_client=subset_client,
            clock=_clock,
            sleep=_sleep,
        )
        assert exit_code == 0
        assert len(subset_client.calls) == 3  # ⛔ not one granule more
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.route == ia.SUBSET_ROUTE

    def test_the_run_survives_the_measured_v_prefix_boundary(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE regression (2026-09-02, locking). MEASURED: NASA's
        `FileHeader.ProductVersion` reads `V07B` up to 2024-05 and `07B` from
        2024-06 on, everything else identical. A run that froze its contract on
        the early spelling aborted at the boundary — here it must cross it,
        retrieve every granule, and write ONE manifest."""
        starts = _starts_from(_TRIAL_DAY_START, 3)
        data_root = tmp_path / "data_root"
        subset_client = FakeSubsetWindowClient(
            content_by_url={
                ia.subset_granule_url(
                    archive_filename=_archive_name(start), start=start
                ): _subset_bytes_with_product_version(
                    tmp_path, start, product_version=version
                )
                # the frozen contract comes from the FIRST granule
                for start, version in zip(starts, ("V07B", "07B", "07B"), strict=True)
            }
        )
        exit_code = ia.main(
            [
                "--data-root",
                str(data_root),
                "--subset-window-start",
                starts[0].isoformat(),
                "--subset-window-end",
                starts[-1].isoformat(),
                "--request-interval-seconds",
                "0",
            ],
            client=_listing_client(starts),
            subset_client=subset_client,
            clock=_clock,
            sleep=_sleep,
        )
        assert exit_code == 0
        manifest = ia.read_acquisition_manifest(ia.acquisition_manifest_path(data_root))
        assert manifest is not None
        assert manifest.retrieved == 3  # ⛔ not aborted at the boundary
        # ⛔ VERBATIM: the manifest records the FIRST granule's own string.
        assert manifest.read_contract["file_header_product_version"] == "V07B"

    def test_a_real_contract_drift_still_aborts_the_run_and_exits_six(
        self, tmp_path: Path
    ) -> None:
        """⛔ The tolerance must not have opened the gate: a granule on a
        DIFFERENT grid still stops the run — now with a PERMANENT exit code, so
        a supervising loop reports instead of retrying it 200 times."""
        starts = _starts_from(_TRIAL_DAY_START, 2)
        drifted = tmp_path / "drifted.dap.nc4"
        _write_fake_subset_granule(
            drifted,
            archive_filename=_archive_name(starts[1]),
            lat=_ARCHIVE_LAT[_BOX_LAT_START : _BOX_LAT_STOP + 1] + 0.1,
        )
        subset_client = FakeSubsetWindowClient(
            content_by_url={
                ia.subset_granule_url(
                    archive_filename=_archive_name(starts[0]), start=starts[0]
                ): _subset_fixture_bytes(tmp_path, starts[0], value=1.5),
                ia.subset_granule_url(
                    archive_filename=_archive_name(starts[1]), start=starts[1]
                ): drifted.read_bytes(),
            }
        )
        exit_code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--subset-window-start",
                starts[0].isoformat(),
                "--subset-window-end",
                starts[-1].isoformat(),
                "--request-interval-seconds",
                "0",
            ],
            client=_listing_client(starts),
            subset_client=subset_client,
            clock=_clock,
            sleep=_sleep,
        )
        assert exit_code == 6  # noqa: PLR2004

    def test_an_out_of_window_bound_exits_six_rather_than_three(
        self, tmp_path: Path
    ) -> None:
        """⛔ Deterministic caller misuse: the same arguments fail the same way
        for ever, so the supervisor must stop rather than retry."""
        exit_code = ia.main(
            [
                "--data-root",
                str(tmp_path / "data_root"),
                "--subset-window-start",
                "2019-01-01T00:00:00Z",
                "--subset-window-end",
                "2019-01-01T00:30:00Z",
            ],
            client=_listing_client([]),
            subset_client=FakeSubsetWindowClient(content_by_url={}),
            clock=_clock,
            sleep=_sleep,
        )
        assert exit_code == 6  # noqa: PLR2004
