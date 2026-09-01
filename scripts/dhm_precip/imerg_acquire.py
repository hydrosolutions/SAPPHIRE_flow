"""Plan 211 (M-A5b) T1 — IMERG Early half-hourly acquisition over the frozen
DHM study box, via the GES DISC HTTPS archive (D1, D5, D7, D9, D10).

⚠️ The D1 revision pin is ENFORCED, not documentary: the 2026-08-28 probe saw
the archive serving V07C (filename and `FileHeader.FileName` agreeing) while
`FileHeader.ProductVersion` read `07B` — a different axis. A live run halts on
the pin until the owner resolves that drift; that is D1's required behaviour.

Per-run scope: the CLI probes exactly ONE granule and reports the D10 disk
projection. `retrieve_window` is the tested bulk path, deliberately unwired.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import argparse
import fcntl
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import structlog  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from sapphire_flow.exceptions import SapphireError  # noqa: E402
from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.era5_manifest import checksum_file  # noqa: E402
from scripts.dhm_precip.era5_request import STUDY_AREA, STUDY_YEARS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

log = structlog.get_logger(__name__)


# --- errors: one hierarchy, one CLI exit-code mapping (era5_errors.py's shape) ---


class ImergAcquisitionError(SapphireError):
    """Base for every M-A5b IMERG error. CLI exit 3 unless a subclass says."""


class ImergCredentialsError(ImergAcquisitionError):
    """Earthdata Login credentials absent or rejected. CLI exit code 2."""


class ImergTransientError(ImergAcquisitionError):
    """Retryable transport error or 5xx — never surfaces past the retry loop."""


class ImergRequestFailedError(ImergAcquisitionError):
    """GES DISC rejected the request, retries were exhausted, or an artifact
    failed post-acquisition validation."""


class ImergGranuleMissingError(ImergRequestFailedError):
    """404 / absent from the listing: counted for a window (D4), fatal for a
    probe."""


class ImergReadContractError(ImergRequestFailedError):
    """D1 — an observed granule's structure, revision included, fails the
    frozen read contract. ⛔ Stop and report rather than blending."""


class ImergSubsetCoordinateMismatchError(ImergReadContractError):
    """D5 step 1 (fixer round 3, finding MINOR 2) — the subset response's
    coordinate vectors disagree with the archive route's own slice over the
    frozen box. ⛔ Raised BEFORE any precipitation value is read: the
    coordinate vectors are the station-mapping invariant, so a mismatch here
    means every value comparison downstream would be comparing different
    places. A distinct type so a caller can tell "the two routes disagree
    about WHERE" from "they disagree about HOW MUCH"."""


class ImergStorageError(ImergAcquisitionError):
    """Storage or manifest write/read failed. CLI exit code 5."""


# --- D1 pinned identity ---

COLLECTION_SHORT_NAME = "GPM_3IMERGHHE_07"
GESDISC_BASE_URL = "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07"
HDF5_VARIABLE_PATH = "/Grid/precipitation"
ROUTE = "GES DISC HTTPS archive"

#: D1 — enforced by `assert_identity_matches_plan_and_header` on every granule.
PINNED_GRANULE_REVISION_PER_PLAN = "V07B"

#: D9 — Plan 209 T2's measured figure, carried verbatim (never re-measured).
MEASURED_ACQUISITION_LATENCY = (
    "4h20m-4h50m (Plan 209 T2, GES DISC HTTPS archive, granules publish in "
    "hourly batches of two)"
)

#: The frozen box, imported not restated (T1 pin). IMERG granules are global;
#: this is the recorded footprint, not a download subset.
STUDY_BOX: tuple[int, int, int, int] = STUDY_AREA

EXPECTED_UNITS = "mm/hr"
EXPECTED_FILL_VALUE = -9999.9
EXPECTED_REGISTRATION = "CENTER"
EXPECTED_DIMENSION_NAMES: tuple[str, ...] = ("time", "lon", "lat")
EXPECTED_GRID_SHAPE: tuple[int, int, int] = (1, 3600, 1800)
_FILL_VALUE_TOLERANCE = 1e-3
"""float32 round-trip: -9999.9 reads back as -9999.900390625 (probe granule)."""


# --- D5 window arithmetic. The period-ending output axis runs 2020-01-01
# 00:00 .. 2025-12-31 23:00, so the FIRST hour needs the two granules of
# 2019-12-31 23:00-24:00 and the LAST granule starts at 22:30, not 23:30. ---

FIRST_GRANULE_START: datetime = datetime(STUDY_YEARS[0] - 1, 12, 31, 23, 0, tzinfo=UTC)
LAST_GRANULE_START: datetime = datetime(STUDY_YEARS[-1], 12, 31, 22, 30, tzinfo=UTC)
EXPECTED_GRANULE_COUNT: int = (
    int((LAST_GRANULE_START - FIRST_GRANULE_START) / timedelta(minutes=30)) + 1
)
"""105,216 — DERIVED from the pinned window (48/day x 2192 days), never a bare
literal (D10 forbids restating the projection as a fact)."""


def all_granule_starts() -> Iterator[datetime]:
    """D5 — every half-hour granule start needed, in order."""
    return (
        FIRST_GRANULE_START + i * timedelta(minutes=30)
        for i in range(EXPECTED_GRANULE_COUNT)
    )


def granule_start_in_window(start: datetime) -> bool:
    """Membership in D5's half-hour set, without materialising 105,216."""
    return (
        start.tzinfo is not None
        and start.utcoffset() == timedelta(0)
        and FIRST_GRANULE_START <= start <= LAST_GRANULE_START
        and start.minute in (0, 30)
        and start.second == 0
        and start.microsecond == 0
    )


# --- granule identity (D1's filename/URL construction) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class ImergGranuleId:
    """One half-hourly granule, identified by its UTC start. The revision letter
    is NOT part of the identity: it varies by production generation and is only
    knowable by listing the archive."""

    start: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.start.utcoffset() != timedelta(0):
            raise ValueError(f"granule start {self.start} must be tz-aware UTC")
        if (
            self.start.minute not in (0, 30)
            or self.start.second != 0
            or self.start.microsecond != 0
        ):
            raise ValueError(
                f"granule start {self.start} is not half-hour aligned (D1)"
            )

    @property
    def end(self) -> datetime:
        """The granule's own `E` timestamp — 29:59 after `start`."""
        return self.start + timedelta(minutes=29, seconds=59)

    @property
    def minute_of_day(self) -> int:
        return self.start.hour * 60 + self.start.minute

    def filename(self, *, revision: str) -> str:
        s = self.start.strftime("%Y%m%d-S%H%M%S")
        e = self.end.strftime("E%H%M%S")
        return (
            f"3B-HHR-E.MS.MRG.3IMERG.{s}-{e}.{self.minute_of_day:04d}.{revision}.HDF5"
        )

    def directory_url(self) -> str:
        doy = self.start.timetuple().tm_yday
        return f"{GESDISC_BASE_URL}/{self.start.year:04d}/{doy:03d}/"

    def remote_url(self, *, revision: str) -> str:
        return f"{self.directory_url()}{self.filename(revision=revision)}"


_FILENAME_STEM = (
    r"3B-HHR-E\.MS\.MRG\.3IMERG\."
    r"(?P<date>\d{8})-S(?P<start_time>\d{6})-E(?P<end_time>\d{6})\."
    r"(?P<minute>\d{4})\.(?P<revision>V\d{2}[A-Z])"
)

#: The ARCHIVE's own filename. ⛔ Strict about `.HDF5`: these names are read
#: off the day listing and out of `raw/`, and a differently-stored file must
#: never be mistaken for an acquired granule.
_FILENAME_RE = re.compile(rf"^{_FILENAME_STEM}\.HDF5$")

#: The name the granule carries INSIDE itself, in `FileHeader.FileName`.
#: ⛔ MEASURED 2026-08-31 on the real 2020-07-15T00:00Z granule: IMERG EARLY
#: *is* the real-time run, so its embedded name ends `.RT-H5` while the
#: archive stores the very same bytes as `.HDF5`. Comparing it with the
#: archive pattern rejected every real Early granule for an extension
#: difference, reported as a revision disagreement that did not exist (the
#: revision field read `V07B` on both sides). The EXTENSION is the only field
#: that may differ between the two names.
_EMBEDDED_FILENAME_RE = re.compile(rf"^{_FILENAME_STEM}\.(?:HDF5|RT-H5)$")


def parse_granule_filename(name: str) -> tuple[ImergGranuleId, str]:
    """The inverse of `ImergGranuleId.filename`."""
    match = _FILENAME_RE.fullmatch(name)
    if match is None:
        raise ImergReadContractError(
            f"filename {name!r} does not match the expected IMERG Early "
            "half-hourly naming pattern (D1)"
        )
    try:
        start = datetime.strptime(
            match["date"] + match["start_time"], "%Y%m%d%H%M%S"
        ).replace(tzinfo=UTC)
    except ValueError as exc:
        # ⛔ Typed, not raw: the pattern matches syntactically valid nonsense
        # (a day 32, a month 13), and a raw ValueError aborts callers that skip D1.
        raise ImergReadContractError(
            f"filename {name!r} carries an impossible date/time: {exc} (D1)"
        ) from exc
    granule = ImergGranuleId(start=start)
    if granule.minute_of_day != int(match["minute"]):
        raise ImergReadContractError(
            f"filename {name!r} minute-of-day field {match['minute']} does "
            f"not match its own start time (expected {granule.minute_of_day:04d})"
        )
    expected_end = granule.end.strftime("%H%M%S")
    if match["end_time"] != expected_end:
        raise ImergReadContractError(
            f"filename {name!r} period end E{match['end_time']} != "
            f"E{expected_end} (start + 29:59) — D5 maps an interval by its "
            "END, so a mislabeled end must never be assigned using its start"
        )
    return granule, match["revision"]


def resolve_granule_filename(directory_listing: str, granule: ImergGranuleId) -> str:
    """D1 — the revision letter cannot be predicted, so the day directory's
    listing is searched for this granule's date and start time, whatever
    revision it carries. Pure string logic, no network. ⛔ The END is matched
    EXACTLY (`start + 29:59`), never as "any six digits": D5 maps an interval
    by its end, so a mislabeled one must not be resolved off its start."""
    date_str = granule.start.strftime("%Y%m%d")
    start_str = granule.start.strftime("%H%M%S")
    end_str = granule.end.strftime("%H%M%S")
    pattern = re.compile(
        rf"3B-HHR-E\.MS\.MRG\.3IMERG\.{date_str}-S{start_str}-E{end_str}\."
        rf"{granule.minute_of_day:04d}\.V\d{{2}}[A-Z]\.HDF5"
    )
    matches = sorted(set(pattern.findall(directory_listing)))
    if not matches:
        raise ImergGranuleMissingError(
            f"no granule found for {granule.start.isoformat()} in the "
            f"{granule.directory_url()} listing"
        )
    if len(matches) > 1:
        raise ImergReadContractError(
            f"multiple granule filenames matched {granule.start.isoformat()}: "
            f"{matches} — the day directory is ambiguous"
        )
    return matches[0]


# --- D1 — the frozen read contract ---


@dataclass(frozen=True, kw_only=True, slots=True)
class ImergReadContract:
    """D1 — every field frozen on the first granule and asserted on every
    subsequent one, the full lat/lon vectors included. ⛔
    `file_header_product_version` is frozen in its own right, NOT equated with
    `granule_revision`."""

    hdf5_variable_path: str
    dimension_names: tuple[str, ...]
    coordinate_registration: str
    longitude_convention: str
    units: str
    fill_value: float
    grid_shape: tuple[int, int, int]
    lat_vector: tuple[float, ...]
    lon_vector: tuple[float, ...]
    grid_spacing_deg: float
    granule_revision: str
    file_header_product_version: str

    def __post_init__(self) -> None:
        for label, observed, expected in (
            ("HDF5 variable path", self.hdf5_variable_path, HDF5_VARIABLE_PATH),
            ("units", self.units, EXPECTED_UNITS),
            ("registration", self.coordinate_registration, EXPECTED_REGISTRATION),
            ("dimension order", self.dimension_names, EXPECTED_DIMENSION_NAMES),
            ("grid shape", self.grid_shape, EXPECTED_GRID_SHAPE),
        ):
            if observed != expected:
                raise ImergReadContractError(
                    f"observed {label} {observed!r} != expected {expected!r} (D1)"
                )
        if abs(self.fill_value - EXPECTED_FILL_VALUE) > _FILL_VALUE_TOLERANCE:
            raise ImergReadContractError(
                f"observed fill value {self.fill_value!r} != expected "
                f"{EXPECTED_FILL_VALUE!r} (D1)"
            )
        if self.longitude_convention not in ("SIGNED_180", "UNSIGNED_360"):
            raise ImergReadContractError(
                f"unrecognised longitude convention {self.longitude_convention!r} (D1)"
            )
        if len(self.lat_vector) != self.grid_shape[2]:
            raise ImergReadContractError(
                f"lat_vector length {len(self.lat_vector)} != grid_shape[2] "
                f"{self.grid_shape[2]} (D1)"
            )
        if len(self.lon_vector) != self.grid_shape[1]:
            raise ImergReadContractError(
                f"lon_vector length {len(self.lon_vector)} != grid_shape[1] "
                f"{self.grid_shape[1]} (D1)"
            )

    def as_manifest_dict(self) -> dict[str, object]:
        return asdict(self)


def _attr_text(raw: object) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def contract_from_open_granule(f: object, *, filename: str) -> ImergReadContract:
    """D1's ONE contract parser, called by both read paths: D1 freezes one
    contract, so there is one implementation of it. `f` is an open
    `h5py.File`; `filename` is the name the revision is parsed from — a
    mid-download temp file still carries `.tmp`, so its real name is passed
    in."""
    revision = parse_granule_filename(filename)[1]
    file_header = read_file_header(f)
    assert_identity_matches_plan_and_header(
        archive_filename=filename,
        filename_revision=revision,
        file_header_filename=parse_grid_header_field(file_header, "FileName"),
    )
    try:
        grid = f["Grid"]  # type: ignore[index]
    except KeyError as exc:
        # (fixer review, finding 4) — a subset-shaped response (root-level
        # `/precipitation`, no `/Grid` group) must fail the ARCHIVE parser
        # with a typed, reportable error, not a raw h5py `KeyError`: this is
        # the "vice versa" of T1's own verify clause, and a caller catching
        # only `ImergReadContractError` must not see it slip through.
        raise ImergReadContractError(
            f"granule {filename!r} has no top-level 'Grid' group — the "
            "ARCHIVE route's contract requires /Grid/precipitation, and this "
            f"response is not archive-shaped (D1): {exc}"
        ) from exc
    precip = grid["precipitation"]
    lat = np.asarray(grid["lat"][:], dtype=np.float64)
    lon = np.asarray(grid["lon"][:], dtype=np.float64)
    grid_shape = tuple(int(n) for n in precip.shape)
    if len(grid_shape) != 3:  # noqa: PLR2004 - D1 pins a 3-D grid
        raise ImergReadContractError(
            f"observed 'precipitation' shape {grid_shape} is not 3-D (D1)"
        )
    return ImergReadContract(
        hdf5_variable_path=HDF5_VARIABLE_PATH,
        dimension_names=tuple(_attr_text(precip.attrs["DimensionNames"]).split(",")),
        coordinate_registration=parse_grid_header_field(
            _attr_text(grid.attrs["GridHeader"]), "Registration"
        ),
        longitude_convention=(
            "SIGNED_180" if float(lon.min()) < 0.0 else "UNSIGNED_360"
        ),
        units=_attr_text(precip.attrs["units"]),
        fill_value=float(precip.attrs["_FillValue"]),
        grid_shape=(grid_shape[0], grid_shape[1], grid_shape[2]),
        lat_vector=exact_coordinate_vector(lat),
        lon_vector=exact_coordinate_vector(lon),
        grid_spacing_deg=round(float(np.median(np.diff(np.sort(lat)))), 6),
        granule_revision=revision,
        file_header_product_version=parse_grid_header_field(
            file_header, "ProductVersion"
        ),
    )


def observe_read_contract(
    path: Path, *, filename: str | None = None
) -> ImergReadContract:
    """Open a granule and extract every D1 field."""
    import h5py

    with h5py.File(path, "r") as f:
        return contract_from_open_granule(
            f, filename=filename if filename is not None else path.name
        )


def exact_coordinate_vector(values: np.ndarray) -> tuple[float, ...]:
    """D1 — frozen EXACTLY. ⛔ No rounding: a six-decimal round would let
    sub-6-decimal grid drift (a subset service's own grid, a reprocessed
    archive) pass while mapping stations to different cells."""
    return tuple(float(v) for v in values)


def parse_grid_header_field(grid_header: str, field: str) -> str:
    for entry in grid_header.split(";"):
        entry = entry.strip()
        if "=" in entry:
            key, _, value = entry.partition("=")
            if key.strip() == field:
                return value.strip()
    raise ImergReadContractError(
        f"GridHeader is missing field {field!r}: {grid_header!r} (D1)"
    )


def read_file_header(f: object) -> str:
    """The granule's root-level `FileHeader` global attribute, raw
    (`;`-separated `key=value` text, same shape as `Grid`'s `GridHeader`)."""
    raw = f.attrs.get("FileHeader")  # type: ignore[attr-defined]
    if raw is None:
        raise ImergReadContractError(
            "granule is missing the root-level FileHeader global attribute (D1)"
        )
    return _attr_text(raw)


def normalise_embedded_filename(file_header_filename: str) -> str:
    """The embedded `FileHeader.FileName` reduced to the ARCHIVE spelling of
    the same name: `.RT-H5` -> `.HDF5`. ⛔ The EXTENSION is the only field that
    may differ between the two names, so normalising it is what lets the whole
    name — date, start, end, sequence AND revision — be compared."""
    stripped = file_header_filename.strip()
    if _EMBEDDED_FILENAME_RE.fullmatch(stripped) is None:
        raise ImergReadContractError(
            f"the granule's own embedded FileHeader.FileName {stripped!r} does "
            "not match the expected IMERG Early half-hourly naming pattern (D1)"
        )
    if stripped.endswith(".RT-H5"):
        return stripped.removesuffix(".RT-H5") + ".HDF5"
    return stripped


def assert_identity_matches_plan_and_header(
    *, archive_filename: str, filename_revision: str, file_header_filename: str
) -> None:
    """D1's two identity checks: the filename's revision must equal the plan's
    pinned literal AND the granule's own `FileHeader.FileName` must be the SAME
    NAME as the archive path's, so a renamed file is caught. ⛔ The COMPLETE
    names are compared, not merely their revisions: extraction takes the
    timestamp from the PATH, so a granule whose embedded name carries the right
    revision but a different date/time would silently be filed under a time its
    contents are not from. ⛔ NOT `ProductVersion`: a different axis."""
    if filename_revision != PINNED_GRANULE_REVISION_PER_PLAN:
        raise ImergReadContractError(
            f"observed granule revision {filename_revision!r} (from its "
            f"filename) != the D1-pinned {PINNED_GRANULE_REVISION_PER_PLAN!r} "
            "— stop and report rather than blending (D1)"
        )
    if normalise_embedded_filename(file_header_filename) != archive_filename:
        raise ImergReadContractError(
            f"archive filename {archive_filename!r} disagrees with the "
            f"granule's own embedded FileHeader.FileName "
            f"{file_header_filename.strip()!r} — a granule's identity must not "
            "be inferred from the path alone (D1)"
        )


def assert_contract_consistent(
    observed: ImergReadContract, *, frozen: ImergReadContract
) -> None:
    """D1 — frozen on the first granule, asserted on every subsequent one."""
    if observed.granule_revision != frozen.granule_revision:
        raise ImergReadContractError(
            f"granule revision {observed.granule_revision!r} != frozen "
            f"{frozen.granule_revision!r} — stop and report rather than "
            "blending mixed revisions into one bundle (D1)"
        )
    if observed != replace(frozen, granule_revision=observed.granule_revision):
        raise ImergReadContractError(
            "observed read contract differs from the frozen one on a field "
            "other than revision (D1) — refusing to blend"
        )


# --- Plan 225 (M-A5d) D1 — the SUBSET route's own, SEPARATELY-frozen read
# contract. `ImergReadContract`/`contract_from_open_granule` above stay
# untouched: GES DISC's OPeNDAP subset service flattens the HDF5 `/Grid`
# group into root-level variables (`/precipitation`, not
# `/Grid/precipitation`) and slices to a box-local grid, so the archive
# parser cannot read it and must not be parameterised to try (D1 warns in so
# many words against a shared/relaxed contract). ---

SUBSET_ROUTE = "GES DISC OPeNDAP subset"
OPENDAP_BASE_URL = (
    "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGHHE.07"
)
SUBSET_VARIABLE_PATH = "/precipitation"

#: D4 — measured 2026-08-31: the OPeNDAP response carries the archive's own
#: 0.1 deg grid, so the frozen STUDY_BOX maps to an EXACT cell count —
#: DERIVED, never a bare literal (matches EXPECTED_GRANULE_COUNT's own rule).
_GRID_SPACING_DEG = 0.1
EXPECTED_SUBSET_LON_COUNT: int = round(
    (STUDY_BOX[3] - STUDY_BOX[1]) / _GRID_SPACING_DEG
)
EXPECTED_SUBSET_LAT_COUNT: int = round(
    (STUDY_BOX[0] - STUDY_BOX[2]) / _GRID_SPACING_DEG
)
EXPECTED_SUBSET_GRID_SHAPE: tuple[int, int, int] = (
    1,
    EXPECTED_SUBSET_LON_COUNT,
    EXPECTED_SUBSET_LAT_COUNT,
)
EXPECTED_SUBSET_DTYPE = "float32"
"""Measured 2026-08-31 on the live probe granule (2020-07-15T00:00Z,
80-89E/26-31N): the subset response is UNPACKED float32 — no
`scale_factor`/`add_offset` — the same encoding the archive route's own
`/Grid/precipitation` has always used (D1's dtype/packing warning)."""

#: The root-level global attribute the subset response retains for the
#: archive route's `/Grid` group attribute of the same name — MEASURED on
#: the live probe granule 2026-08-31: OPeNDAP's flattening renames it
#: `Grid.GridHeader` (a literal dot) rather than dropping it, exactly as
#: `FileHeader` (already a root attribute) survives unprefixed.
SUBSET_GRID_HEADER_ATTR = "Grid.GridHeader"

#: D1 (fixer review round 2, finding 1 — MAJOR) — the box is entirely in the
#: eastern, northern hemisphere (80-89E/26-31N), so a LOCAL `lon.min() < 0`
#: rule can only ever derive "UNSIGNED_360" for a granule honestly covering
#: this box, regardless of what the GLOBAL grid's own convention is. The real
#: probe response's retained `Grid.GridHeader` carries
#: `WestBoundingCoordinate=-180;EastBoundingCoordinate=180` — the same
#: -180..180 grid the archive contract's own fixtures pin — and `/lon`'s
#: `LongName` attribute independently confirms it ("...from -180 to 180.").
#: ⇒ the box can only ever be cut from a SIGNED_180 global grid; pinned to
#: exactly that ONE value, DERIVED from the retained header (not the box's
#: own sign) in `contract_from_open_subset_granule` — never a bare literal
#: restating what was measured.
EXPECTED_SUBSET_LONGITUDE_CONVENTION = "SIGNED_180"

#: D1 (fixer round 3 — BLOCKER) — the T2-approved EXACT cell centres,
#: MEASURED, not derived. ⛔ No arithmetic defines this pin any more: the
#: previous derivation (`float(np.float32(round(edge + spacing/2 + i*spacing,
#: 6)))`) was MEASURED WRONG against the real OPeNDAP response held at
#: `data/dhm_precip/imerg_early/raw_subset/3B-HHR-E.MS.MRG.3IMERG.20200715-
#: S000000-E002959.0000.V07B.HDF5.dap.nc4` — 20 of 50 lat values differed (max
#: 1.907e-06) and 18 of 90 lon values differed (max 7.629e-06), so the contract
#: rejected the very granule T2 is built on, and would have rejected EVERY
#: subset granule: IMERG's grid is fixed and global. The `round(..., 6)` before
#: the float32 cast is the defect — six-decimal rounding lands on a DIFFERENT
#: float32 than the one NASA's own grid carries.
#:
#: ⇒ These 50 + 90 values are TRANSCRIBED from observed float32 data: the
#: archive granule's own `/Grid/lat[1160:1210]` and `/Grid/lon[2600:2690]`
#: (float32, 1800 / 3600), which is exactly D4's published constraint
#: `/precipitation[0][2600:2689][1160:1209]`. Measured 2026-09-01:
#: `np.array_equal` against the real subset response is True for BOTH — bit
#: for bit, no tolerance. `exact_coordinate_vector`'s own rule ("⛔ No
#: rounding") is what D1 cites; this is that rule applied to the pin itself.
#: ⛔ Do NOT re-derive these arithmetically and do NOT widen the comparison to
#: a tolerance: a tolerance would let a subset service's own grid pass, which
#: is the precise hazard this contract exists to prevent.
#: `tests/unit/scripts/test_imerg_acquire.py::TestSubsetContractAgainstTheRealArtifact`
#: locks them against the committed artifacts (skipped where `data/` is absent).
EXPECTED_SUBSET_LAT_VECTOR: tuple[float, ...] = (
    26.049999237060547,
    26.149999618530273,
    26.25,
    26.349998474121094,
    26.44999885559082,
    26.549999237060547,
    26.649999618530273,
    26.75,
    26.849998474121094,
    26.94999885559082,
    27.049999237060547,
    27.149999618530273,
    27.25,
    27.349998474121094,
    27.44999885559082,
    27.549999237060547,
    27.649999618530273,
    27.75,
    27.849998474121094,
    27.94999885559082,
    28.049999237060547,
    28.149999618530273,
    28.25,
    28.349998474121094,
    28.44999885559082,
    28.549999237060547,
    28.649999618530273,
    28.75,
    28.849998474121094,
    28.94999885559082,
    29.049999237060547,
    29.149999618530273,
    29.25,
    29.349998474121094,
    29.44999885559082,
    29.549999237060547,
    29.649999618530273,
    29.75,
    29.849998474121094,
    29.94999885559082,
    30.049999237060547,
    30.149999618530273,
    30.25,
    30.349998474121094,
    30.44999885559082,
    30.549999237060547,
    30.649999618530273,
    30.75,
    30.849998474121094,
    30.94999885559082,
)

EXPECTED_SUBSET_LON_VECTOR: tuple[float, ...] = (
    80.04999542236328,
    80.1500015258789,
    80.25,
    80.3499984741211,
    80.44999694824219,
    80.54999542236328,
    80.6500015258789,
    80.75,
    80.8499984741211,
    80.94999694824219,
    81.04999542236328,
    81.1500015258789,
    81.25,
    81.3499984741211,
    81.44999694824219,
    81.54999542236328,
    81.6500015258789,
    81.75,
    81.8499984741211,
    81.94999694824219,
    82.04999542236328,
    82.1500015258789,
    82.25,
    82.3499984741211,
    82.44999694824219,
    82.54999542236328,
    82.6500015258789,
    82.75,
    82.8499984741211,
    82.94999694824219,
    83.04999542236328,
    83.1500015258789,
    83.25,
    83.3499984741211,
    83.44999694824219,
    83.54999542236328,
    83.6500015258789,
    83.75,
    83.8499984741211,
    83.94999694824219,
    84.04999542236328,
    84.1500015258789,
    84.25,
    84.3499984741211,
    84.44999694824219,
    84.54999542236328,
    84.6500015258789,
    84.75,
    84.8499984741211,
    84.94999694824219,
    85.04999542236328,
    85.1500015258789,
    85.25,
    85.3499984741211,
    85.44999694824219,
    85.54999542236328,
    85.6500015258789,
    85.75,
    85.8499984741211,
    85.94999694824219,
    86.04999542236328,
    86.1500015258789,
    86.25,
    86.3499984741211,
    86.44999694824219,
    86.54999542236328,
    86.6500015258789,
    86.75,
    86.8499984741211,
    86.94999694824219,
    87.04999542236328,
    87.1500015258789,
    87.25,
    87.3499984741211,
    87.44999694824219,
    87.54999542236328,
    87.6500015258789,
    87.75,
    87.8499984741211,
    87.94999694824219,
    88.04999542236328,
    88.1500015258789,
    88.25,
    88.3499984741211,
    88.44999694824219,
    88.54999542236328,
    88.6500015258789,
    88.75,
    88.8499984741211,
    88.94999694824219,
)

#: D1 (fixer round 3 — MAJOR) — the retained GLOBAL grid's own bounding box,
#: (north, west, south, east) in `STUDY_BOX`'s own order. MEASURED on the real
#: probe response's `Grid.GridHeader` 2026-09-01: `NorthBoundingCoordinate=90;
#: SouthBoundingCoordinate=-90; EastBoundingCoordinate=180;
#: WestBoundingCoordinate=-180`. ⛔ Pinned EXACTLY rather than reduced to a
#: single `west < 0` sign test: a changed east bound, or another malformed
#: negative-west convention, would otherwise pass while extraction still
#: treats the coordinates as cell centres of a -180..180 grid.
EXPECTED_SUBSET_GLOBAL_BOUNDS: tuple[float, float, float, float] = (
    90.0,
    -180.0,
    -90.0,
    180.0,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class ImergSubsetReadContract:
    """D1 — the SECOND frozen read contract, one field frozen at a time just
    as strictly as `ImergReadContract`, but on a DIFFERENT shape: the
    box-local 90x50 grid, a root-level `/precipitation` variable, and the
    dtype/packing fields the archive contract has never needed (⛔ two
    contracts, neither weakened into the other)."""

    variable_path: str
    dimension_names: tuple[str, ...]
    units: str
    fill_value: float
    dtype: str
    scale_factor: float | None
    add_offset: float | None
    coordinate_registration: str
    global_bounds: tuple[float, float, float, float]
    longitude_convention: str
    grid_shape: tuple[int, int, int]
    lat_vector: tuple[float, ...]
    lon_vector: tuple[float, ...]
    granule_revision: str
    file_header_product_version: str

    def __post_init__(self) -> None:
        for label, observed, expected in (
            ("variable path", self.variable_path, SUBSET_VARIABLE_PATH),
            ("units", self.units, EXPECTED_UNITS),
            ("dimension order", self.dimension_names, EXPECTED_DIMENSION_NAMES),
            ("dtype", self.dtype, EXPECTED_SUBSET_DTYPE),
            ("grid shape", self.grid_shape, EXPECTED_SUBSET_GRID_SHAPE),
            # (fixer round 3, finding MAJOR 1) — the frozen cell centres above
            # are only meaningful under CENTER registration: under CORNER the
            # same numbers name cell EDGES and every station maps half a cell
            # away. Pinned explicitly rather than assumed.
            (
                "registration",
                self.coordinate_registration,
                EXPECTED_REGISTRATION,
            ),
            # (fixer round 3, finding MAJOR 1) — the EXACT global bounds, not a
            # single `west < 0` sign test: a changed east bound or another
            # malformed negative-west convention must not pass.
            (
                "global bounds",
                self.global_bounds,
                EXPECTED_SUBSET_GLOBAL_BOUNDS,
            ),
        ):
            if observed != expected:
                raise ImergReadContractError(
                    f"observed subset {label} {observed!r} != expected "
                    f"{expected!r} (D1)"
                )
        if abs(self.fill_value - EXPECTED_FILL_VALUE) > _FILL_VALUE_TOLERANCE:
            raise ImergReadContractError(
                f"observed subset fill value {self.fill_value!r} != expected "
                f"{EXPECTED_FILL_VALUE!r} (D1)"
            )
        # (fixer review, finding 2/3) — ⛔ ONE approved longitude convention,
        # not a two-value membership check: the subset never crosses the sign
        # boundary, so admitting the OTHER convention would silently accept a
        # response the box could never honestly produce.
        if self.longitude_convention != EXPECTED_SUBSET_LONGITUDE_CONVENTION:
            raise ImergReadContractError(
                f"observed subset longitude convention "
                f"{self.longitude_convention!r} != the box-pinned "
                f"{EXPECTED_SUBSET_LONGITUDE_CONVENTION!r} (D1)"
            )
        # (fixer review, finding 3) — packing DECIDES what a decoded value
        # IS, and D5's cross-check tolerance is derived assuming there is
        # none. Rejecting it HERE — at contract construction, which
        # `read_subset_granule` always goes through before it ever touches a
        # raw value — protects every subset read this run performs, not only
        # the one granule T2's cross-check happens to compare.
        if self.scale_factor is not None or self.add_offset is not None:
            raise ImergReadContractError(
                "the subset response is PACKED (scale_factor/add_offset "
                "present) — this contract's decoded values assume none; "
                "refusing to accept a packed subset granule (D1)"
            )
        if len(self.lat_vector) != self.grid_shape[2]:
            raise ImergReadContractError(
                f"subset lat_vector length {len(self.lat_vector)} != "
                f"grid_shape[2] {self.grid_shape[2]} (D1)"
            )
        if len(self.lon_vector) != self.grid_shape[1]:
            raise ImergReadContractError(
                f"subset lon_vector length {len(self.lon_vector)} != "
                f"grid_shape[1] {self.grid_shape[1]} (D1)"
            )
        # (fixer review, finding 2) — the LENGTH checks above accept ANY
        # same-sized grid; only an EXACT match against the T2-approved,
        # box-derived cell centres proves this response actually covers the
        # frozen box rather than some other same-shaped patch of the globe.
        if self.lat_vector != EXPECTED_SUBSET_LAT_VECTOR:
            raise ImergReadContractError(
                "observed subset lat_vector does not exactly match the "
                "T2-approved frozen box coordinates (D1) — a same-sized grid "
                "at the wrong location must not be accepted"
            )
        if self.lon_vector != EXPECTED_SUBSET_LON_VECTOR:
            raise ImergReadContractError(
                "observed subset lon_vector does not exactly match the "
                "T2-approved frozen box coordinates (D1) — a same-sized grid "
                "at the wrong location must not be accepted"
            )

    def as_manifest_dict(self) -> dict[str, object]:
        return asdict(self)


def _attr_scalar_float(raw: object) -> float:
    """The subset response's `_FillValue` was measured as a 1-element ARRAY
    (unlike the archive's scalar) — OPeNDAP's own re-encoding, not ours to
    normalise away silently. Reads either shape the same way."""
    array = np.asarray(raw, dtype=np.float64).reshape(-1)
    if array.size != 1:
        raise ImergReadContractError(
            f"expected a scalar (or 1-element) attribute, got shape {array.shape}"
        )
    return float(array[0])


def read_subset_grid_header_global(f: object) -> str:
    """The subset response's own retained `Grid.GridHeader` global attribute
    (D1, fixer review round 2, finding 1) — read the SAME way
    `read_file_header` reads `FileHeader`, since OPeNDAP's flattening treats
    both as root-level attributes now. This is the only field that can carry
    the GLOBAL grid's longitude convention: the box-local `/lon` slice is
    entirely positive by construction, so its own min/max can never reveal
    whether the grid it was cut from is signed."""
    raw = f.attrs.get(SUBSET_GRID_HEADER_ATTR)  # type: ignore[attr-defined]
    if raw is None:
        raise ImergReadContractError(
            f"subset granule is missing the root-level {SUBSET_GRID_HEADER_ATTR!r} "
            "global attribute (D1) — cannot derive the longitude convention "
            "from a box-local slice alone"
        )
    return _attr_text(raw)


def subset_global_bounds(grid_header: str) -> tuple[float, float, float, float]:
    """D1 (fixer round 3, finding MAJOR 1) — the RETAINED global grid's own
    bounding box as `(north, west, south, east)`, `STUDY_BOX`'s own order.
    ⛔ Every one of the four is read and pinned: reducing the header to a
    single `west < 0` sign test let `Registration=CORNER`, a changed east
    bound, or another malformed negative-west convention through while
    extraction still treated the coordinates as cell centres of a -180..180
    grid."""
    try:
        return (
            float(parse_grid_header_field(grid_header, "NorthBoundingCoordinate")),
            float(parse_grid_header_field(grid_header, "WestBoundingCoordinate")),
            float(parse_grid_header_field(grid_header, "SouthBoundingCoordinate")),
            float(parse_grid_header_field(grid_header, "EastBoundingCoordinate")),
        )
    except ValueError as exc:
        raise ImergReadContractError(
            f"a bounding coordinate in the retained {SUBSET_GRID_HEADER_ATTR!r} "
            f"is not a number: {grid_header!r} (D1): {exc}"
        ) from exc


def contract_from_open_subset_granule(
    f: object, *, archive_filename: str
) -> ImergSubsetReadContract:
    """D1's subset contract parser — the ONE implementation, mirroring
    `contract_from_open_granule`'s role for the archive route. `f` is an open
    `h5py.File` on the OPeNDAP `.dap.nc4` response; `archive_filename` is the
    ARCHIVE spelling this subset granule corresponds to — D2's identity check
    is against that same name, since the embedded `FileHeader` (a root-level
    attribute, unaffected by the `/Grid` flattening) is unchanged by
    server-side subsetting."""
    revision = parse_granule_filename(archive_filename)[1]
    file_header = read_file_header(f)
    assert_identity_matches_plan_and_header(
        archive_filename=archive_filename,
        filename_revision=revision,
        file_header_filename=parse_grid_header_field(file_header, "FileName"),
    )
    try:
        precip = f["precipitation"]  # type: ignore[index]  # OPeNDAP flattens /Grid
        lat = np.asarray(f["lat"][:], dtype=np.float64)  # type: ignore[index]
        lon = np.asarray(f["lon"][:], dtype=np.float64)  # type: ignore[index]
    except KeyError as exc:
        # (fixer review round 2, finding 4 — minor) — an ARCHIVE-shaped
        # response (root has no `/precipitation`, only a `/Grid` GROUP) must
        # fail the SUBSET parser with a typed, reportable error, not a raw
        # h5py `KeyError` — the "vice versa" of the archive parser's own
        # subset-shaped refusal (D1, finding 4 of round 1).
        raise ImergReadContractError(
            f"granule {archive_filename!r} has no root-level 'precipitation' "
            "variable — the SUBSET route's contract requires OPeNDAP's "
            f"flattened /precipitation, and this response is not "
            f"subset-shaped (D1): {exc}"
        ) from exc
    grid_shape = tuple(int(n) for n in precip.shape)
    if len(grid_shape) != 3:  # noqa: PLR2004 - D1 pins a 3-D grid
        raise ImergReadContractError(
            f"observed subset 'precipitation' shape {grid_shape} is not 3-D (D1)"
        )
    attrs = precip.attrs
    grid_header = read_subset_grid_header_global(f)
    bounds = subset_global_bounds(grid_header)
    # (fixer round 3, finding MAJOR 1) — derived from the RETAINED global
    # grid's own bounds, never the box-local `lon` slice's min/max (that is
    # always positive for this box and would silently derive "UNSIGNED_360"
    # regardless of the true convention). The bounds themselves are pinned
    # EXACTLY by the contract, so this sign test can no longer be the only
    # thing standing between a malformed header and acceptance.
    west_bound = bounds[1]
    # (fixer round 3, finding MINOR 1) — a valid-but-malformed HDF5 (openable,
    # but missing `units` / `DimensionNames` / `_FillValue`) must fail with the
    # typed contract error every other D1 refusal raises, not a raw `KeyError`:
    # `acquire_subset_granule`'s validate-and-reuse only catches
    # `ImergReadContractError`, so a raw `KeyError` would escape the recovery
    # path and a poisoned cache entry could never be refetched.
    try:
        dimension_names = tuple(_attr_text(attrs["DimensionNames"]).split(","))
        units = _attr_text(attrs["units"])
        fill_value = _attr_scalar_float(attrs["_FillValue"])
    except KeyError as exc:
        raise ImergReadContractError(
            f"subset granule {archive_filename!r} is missing the required "
            f"'precipitation' attribute {exc} — the response is openable but "
            "does not carry the D1 subset contract's fields (D1)"
        ) from exc
    return ImergSubsetReadContract(
        variable_path=SUBSET_VARIABLE_PATH,
        dimension_names=dimension_names,
        units=units,
        fill_value=fill_value,
        dtype=str(precip.dtype),
        scale_factor=(
            float(attrs["scale_factor"]) if "scale_factor" in attrs else None
        ),
        add_offset=(float(attrs["add_offset"]) if "add_offset" in attrs else None),
        coordinate_registration=parse_grid_header_field(grid_header, "Registration"),
        global_bounds=bounds,
        longitude_convention=("SIGNED_180" if west_bound < 0.0 else "UNSIGNED_360"),
        grid_shape=(grid_shape[0], grid_shape[1], grid_shape[2]),
        lat_vector=exact_coordinate_vector(lat),
        lon_vector=exact_coordinate_vector(lon),
        granule_revision=revision,
        file_header_product_version=parse_grid_header_field(
            file_header, "ProductVersion"
        ),
    )


def observe_subset_read_contract(
    path: Path, *, archive_filename: str
) -> ImergSubsetReadContract:
    """Open an OPeNDAP subset artifact and extract every D1 subset field —
    the subset-route counterpart of `observe_read_contract`."""
    import h5py

    with h5py.File(path, "r") as f:
        return contract_from_open_subset_granule(f, archive_filename=archive_filename)


def assert_subset_contract_consistent(
    observed: ImergSubsetReadContract, *, frozen: ImergSubsetReadContract
) -> None:
    """D1/D2 (fixer review, finding 1) — the subset route's own counterpart
    of `assert_contract_consistent`, frozen on the first subset granule a run
    reads and asserted on every subsequent one. ⛔ A separate function, not a
    shared/parameterised one: the two contracts are separately frozen and
    must stay that way (D1)."""
    if observed.granule_revision != frozen.granule_revision:
        raise ImergReadContractError(
            f"subset granule revision {observed.granule_revision!r} != frozen "
            f"{frozen.granule_revision!r} — stop and report rather than "
            "blending mixed revisions into one bundle (D1)"
        )
    if observed != replace(frozen, granule_revision=observed.granule_revision):
        raise ImergReadContractError(
            "observed subset read contract differs from the frozen one on a "
            "field other than revision (D1) — refusing to blend"
        )


def subset_box_indices(
    vector: Sequence[float], *, low: float, high: float
) -> tuple[int, int]:
    """D4 — the CONTIGUOUS, inclusive `(start, stop)` index range (the
    `dap4.ce` slice convention) a global coordinate vector covers within
    `[low, high]`. DERIVED from the archive's own frozen vector, never a bare
    literal restating the box (D10's rule) — the box is fixed, so the range
    is fixed, but it is computed from data, not hand-copied from a probe."""
    matches = [i for i, v in enumerate(vector) if low <= v <= high]
    if not matches:
        raise ImergReadContractError(
            f"no coordinate values fall within [{low}, {high}] (D4)"
        )
    start, stop = matches[0], matches[-1]
    if matches != list(range(start, stop + 1)):
        raise ImergReadContractError(
            f"coordinate values within [{low}, {high}] are not contiguous (D4)"
        )
    return start, stop


def subset_granule_url(*, archive_filename: str, start: datetime) -> str:
    """D4 — measured 2026-08-31: OPeNDAP mirrors the archive's own
    year/day-of-year layout, with `.dap.nc4` appended to the ARCHIVE
    filename (never a differently-named product)."""
    doy = start.timetuple().tm_yday
    return f"{OPENDAP_BASE_URL}/{start.year:04d}/{doy:03d}/{archive_filename}.dap.nc4"


def subset_constraint(
    *, lon_start: int, lon_stop: int, lat_start: int, lat_stop: int
) -> str:
    """D4's measured `dap4.ce` constraint shape, parameterised over the box
    indices `subset_box_indices` derives — never a magic literal restating
    the box."""
    return (
        f"{SUBSET_VARIABLE_PATH}[0][{lon_start}:{lon_stop}][{lat_start}:{lat_stop}];"
        f"/lon[{lon_start}:{lon_stop}];/lat[{lat_start}:{lat_stop}]"
    )


def _raise_for_http_status(status: int, *, url: str) -> None:
    """One status ladder, shared by BOTH real HTTP clients (fixer review,
    finding 5): 404 missing (D4 counts it), 401/403 credentials (exit 2),
    5xx retryable. ⛔ A MODULE-level function, not a method on either client
    class — `RealImergSubsetHttpClient` needs the identical ladder and
    reaching into a sibling class's underscore-named method would be a
    private-boundary violation, not a shared helper."""
    if status == 404:  # noqa: PLR2004 - HTTP status literal
        raise ImergGranuleMissingError(f"not found: {url}")
    if status in (401, 403):
        raise ImergCredentialsError(f"Earthdata Login rejected {url} (status {status})")
    if status >= 500:  # noqa: PLR2004 - HTTP status literal
        raise ImergTransientError(f"{url} returned status {status}")
    if status != 200:  # noqa: PLR2004 - HTTP status literal
        raise ImergRequestFailedError(f"{url} returned unexpected status {status}")


@runtime_checkable
class ImergSubsetHttpClient(Protocol):
    """D4's injected OPeNDAP seam — fetch, not list/download-to-path: the
    subset response is small enough to hold in memory (~25 KB measured), and
    there is no day-directory listing step (the archive filename this subset
    corresponds to is already known — D2)."""

    def fetch_subset(self, *, url: str, constraint: str) -> bytes: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class RealImergSubsetHttpClient:
    """D4 — the SAME `.netrc`-based Earthdata Login auth as the archive
    route, over `requests`' own redirect+cookie handling (measured
    2026-08-31: a bare `requests.get` with `params={"dap4.ce": ...}`
    succeeds end to end — no extra session/cookie plumbing was needed to
    reproduce curl's `--location-trusted` behaviour). Window-scale
    reuse/throttling (D3) is T3's concern, not built here."""

    timeout_seconds: float = 60.0

    def fetch_subset(self, *, url: str, constraint: str) -> bytes:
        import requests

        try:
            response = requests.get(
                url, params={"dap4.ce": constraint}, timeout=self.timeout_seconds
            )
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to fetch subset {url}: {exc}") from exc
        _raise_for_http_status(response.status_code, url=url)
        return response.content


def imerg_subset_raw_dir(data_root: Path) -> Path:
    """D2 — a route-DISTINCT directory: `acquire_granule`'s existing-path
    reuse is keyed only by filename, so the two routes must never share a
    directory or a same-named archive/subset pair could collide."""
    return imerg_early_root(data_root) / "raw_subset"


def subset_granule_artifact_path(data_root: Path, *, archive_filename: str) -> Path:
    return imerg_subset_raw_dir(data_root) / f"{archive_filename}.dap.nc4"


def _validate_subset_artifact(
    path: Path, *, archive_filename: str
) -> ImergSubsetReadContract:
    """D2/D3 (fixer review round 2, finding 2 — MAJOR) — the ONE place
    `acquire_subset_granule` trusts bytes on disk, existing or freshly
    downloaded. An HTTP-200 login page or a truncated download is not valid
    HDF5/NetCDF4 and would otherwise raise a raw `OSError` from `h5py.File`;
    this converts that into the same typed `ImergReadContractError` a
    structural mismatch already raises, so a caller catching only the typed
    hierarchy still sees it, matching `ImergReadContractError`'s own
    docstring ("...or an artifact failed post-acquisition validation")."""
    try:
        return observe_subset_read_contract(path, archive_filename=archive_filename)
    except ImergReadContractError:
        raise
    except OSError as exc:
        raise ImergReadContractError(
            f"subset artifact {path} could not be opened as HDF5/NetCDF4 (D1): {exc}"
        ) from exc
    except (KeyError, ValueError) as exc:
        # (fixer round 3, finding MINOR 1) — a BACKSTOP behind the parser's own
        # typed translation: a file that OPENS as HDF5 but is missing a
        # variable or attribute the contract needs must still reach the
        # validate-and-reuse recovery path, which catches only the typed
        # hierarchy. ⛔ Not a bare `except Exception`: only the two shapes a
        # malformed-but-openable file can produce.
        raise ImergReadContractError(
            f"subset artifact {path} opened but does not carry the D1 subset "
            f"contract's structure (D1): {exc!r}"
        ) from exc


def acquire_subset_granule(
    granule: ImergGranuleId,
    *,
    archive_filename: str,
    client: ImergSubsetHttpClient,
    data_root: Path,
    lon_bounds: tuple[int, int],
    lat_bounds: tuple[int, int],
) -> Path:
    """D2/D4 — fetch ONE OPeNDAP subset granule, reusing an existing cached
    artifact rather than re-fetching (preserves `acquire_granule`'s own
    resumability for the subset filename, per D3). No retry loop, no
    day-listing: T3's window-scale concerns (D3 cadence/backoff/session
    reuse) are out of this run's scope.
    ⛔ (fixer review round 2, finding 2 — MAJOR) — VALIDATE-and-reuse, not
    merely exists-and-reuse: an HTTP-200 login page, a truncated download, or
    a stale malformed cache entry must not be trusted just because a file
    sits at this path. Only an artifact that PASSES the D1 subset contract
    suppresses the HTTP call; an invalid cache falls through to a fresh
    download rather than either serving bad bytes or hard-failing on a
    corrupt cache a re-fetch would repair. A malformed fresh download is
    never installed: it must pass the same validation before `os.replace`."""
    target = subset_granule_artifact_path(data_root, archive_filename=archive_filename)
    if target.exists():
        try:
            _validate_subset_artifact(target, archive_filename=archive_filename)
        except ImergReadContractError as exc:
            log.warning(
                "imerg.acquire.subset_cache_invalid",
                path=str(target),
                error=str(exc),
            )
        else:
            return target
    lon_start, lon_stop = lon_bounds
    lat_start, lat_stop = lat_bounds
    url = subset_granule_url(archive_filename=archive_filename, start=granule.start)
    constraint = subset_constraint(
        lon_start=lon_start, lon_stop=lon_stop, lat_start=lat_start, lat_stop=lat_stop
    )
    content = client.fetch_subset(url=url, constraint=constraint)
    imerg_subset_raw_dir(data_root).mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + ".tmp")
    try:
        tmp_path.write_bytes(content)
        _validate_subset_artifact(tmp_path, archive_filename=archive_filename)
        os.replace(tmp_path, target)
    except OSError as exc:
        raise ImergStorageError(f"failed to write {target}: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    return target


@dataclass(frozen=True, kw_only=True, slots=True)
class SubsetCrossCheckReport:
    """D5 — the T2 gate: exact coordinate agreement first (the actual
    station-mapping invariant D1 exists to protect), THEN decoded values
    within a tolerance frozen from the observed dtype/packing BEFORE the
    comparison runs."""

    lat_exact_match: bool
    lon_exact_match: bool
    max_abs_diff: float
    tolerance: float
    values_within_tolerance: bool

    @property
    def passed(self) -> bool:
        return (
            self.lat_exact_match
            and self.lon_exact_match
            and self.values_within_tolerance
        )


def subset_cross_check_tolerance(*, subset_contract: ImergSubsetReadContract) -> float:
    """D5 — frozen BEFORE the comparison runs, from the observed dtype and
    packing. The archive route's `/Grid/precipitation` is UNPACKED float32
    (D1's own contract has never had `scale_factor`/`add_offset` fields
    because none has ever been observed there). If the subset side matches
    that exactly, the two grids hold bit-identical values and the tolerance
    is zero; any packing or dtype drift on the subset side means this
    function has no data to derive a tolerance from, so it refuses rather
    than guessing one (⛔ "within float tolerance" left unnamed is
    adjustable, which would defeat D5's stop rule)."""
    if subset_contract.dtype != EXPECTED_SUBSET_DTYPE:
        raise ImergReadContractError(
            f"subset dtype {subset_contract.dtype!r} != the archive route's "
            f"unpacked {EXPECTED_SUBSET_DTYPE!r} — D5's tolerance must be "
            "derived from the dtype mismatch before comparing, not assumed "
            "zero (D1)"
        )
    if (
        subset_contract.scale_factor is not None
        or subset_contract.add_offset is not None
    ):
        raise ImergReadContractError(
            "the subset response is PACKED (scale_factor/add_offset present) "
            "— D5's tolerance must be derived from the packing before "
            "comparing, not assumed zero (D1)"
        )
    return 0.0


def _first_disagreement(a: Sequence[float], b: Sequence[float]) -> int:
    """The index of the first differing element, or the shorter length."""
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return i
    return min(len(a), len(b))


def assert_subset_coordinates_match(
    *,
    archive_lat: tuple[float, ...],
    archive_lon: tuple[float, ...],
    subset_contract: ImergSubsetReadContract,
) -> None:
    """D5 step 1 — the coordinate vectors, compared EXACTLY and in order,
    before anything reads a precipitation value. ⛔ No tolerance: D1's whole
    point is that a subset service's own grid must not pass."""
    for label, archive_vector, subset_vector in (
        ("lat", archive_lat, subset_contract.lat_vector),
        ("lon", archive_lon, subset_contract.lon_vector),
    ):
        if archive_vector != subset_vector:
            raise ImergSubsetCoordinateMismatchError(
                f"the subset response's {label} vector does not match the "
                f"archive route's own slice over the frozen box (D5): "
                f"{len(subset_vector)} subset value(s) vs "
                f"{len(archive_vector)} archive value(s), first disagreement "
                f"at index {_first_disagreement(archive_vector, subset_vector)} "
                "— stop and report rather than comparing values across "
                "different places"
            )


def cross_check_subset_against_archive(
    *,
    archive_path: Path,
    data_root: Path,
    client: ImergSubsetHttpClient,
) -> SubsetCrossCheckReport:
    """D5 — T2: fetch (or reuse) the OPeNDAP subset for the granule already
    held via the archive route, and compare cell for cell over the frozen
    `STUDY_BOX`. ⛔ Not parameterised over an arbitrary box (fixer review,
    finding 2): D1 pins ONE box, and the T2 gate this function IS must
    compare against exactly that box, never a caller-supplied one. ⛔ The
    coordinate VECTORS are compared first and exactly — "all cells match" is
    only indirect evidence of alignment."""
    import h5py

    box = STUDY_BOX
    archive_filename = archive_path.name
    archive_contract = observe_read_contract(archive_path, filename=archive_filename)
    granule, _revision = parse_granule_filename(archive_filename)
    lon_start, lon_stop = subset_box_indices(
        archive_contract.lon_vector, low=box[1], high=box[3]
    )
    lat_start, lat_stop = subset_box_indices(
        archive_contract.lat_vector, low=box[2], high=box[0]
    )
    subset_path = acquire_subset_granule(
        granule,
        archive_filename=archive_filename,
        client=client,
        data_root=data_root,
        lon_bounds=(lon_start, lon_stop),
        lat_bounds=(lat_start, lat_stop),
    )
    subset_contract = observe_subset_read_contract(
        subset_path, archive_filename=archive_filename
    )
    archive_lat = tuple(archive_contract.lat_vector[lat_start : lat_stop + 1])
    archive_lon = tuple(archive_contract.lon_vector[lon_start : lon_stop + 1])
    # (fixer round 3, finding MINOR 2) — D5 step 1, BEFORE a single
    # precipitation value is read: the coordinate vectors ARE the
    # station-mapping invariant, values are only indirect evidence. Comparing
    # them last let a mismatched grid reach the value comparison — or raise a
    # raw numpy broadcasting error — before the gate that exists to catch it
    # ever fired.
    assert_subset_coordinates_match(
        archive_lat=archive_lat,
        archive_lon=archive_lon,
        subset_contract=subset_contract,
    )
    with h5py.File(archive_path, "r") as f:
        archive_precip = f["Grid"]["precipitation"]  # type: ignore[index]
        archive_attrs = archive_precip.attrs
        # (fixer review, finding 3) — verify the ARCHIVE side's own
        # dtype/packing too, not only the subset side: D5's tolerance is
        # derived assuming BOTH grids are unpacked float32, and the archive
        # contract has never recorded these attributes to check them itself.
        archive_is_packed = (
            "scale_factor" in archive_attrs or "add_offset" in archive_attrs
        )
        if archive_is_packed:
            raise ImergReadContractError(
                "the archive route's precipitation is PACKED (scale_factor/"
                "add_offset present) — D5's tolerance assumes an unpacked "
                "archive side; refusing to compare without a tolerance "
                "derived for this encoding (D1)"
            )
        archive_dtype = str(archive_precip.dtype)  # type: ignore[attr-defined]
        if archive_dtype != EXPECTED_SUBSET_DTYPE:
            raise ImergReadContractError(
                f"the archive route's precipitation dtype {archive_dtype!r} != "
                f"the expected unpacked {EXPECTED_SUBSET_DTYPE!r} — D5's "
                "tolerance assumes matching unpacked encodings on both sides (D1)"
            )
        lon_slice = slice(lon_start, lon_stop + 1)
        lat_slice = slice(lat_start, lat_stop + 1)
        archive_slice = archive_precip[0, lon_slice, lat_slice]  # type: ignore[index]
        archive_box = np.asarray(archive_slice, dtype=np.float64)
    with h5py.File(subset_path, "r") as f:
        subset_values = np.asarray(f["precipitation"][:], dtype=np.float64)[0]  # type: ignore[index]

    tolerance = subset_cross_check_tolerance(subset_contract=subset_contract)
    max_abs_diff = float(np.max(np.abs(archive_box - subset_values)))
    return SubsetCrossCheckReport(
        lat_exact_match=archive_lat == subset_contract.lat_vector,
        lon_exact_match=archive_lon == subset_contract.lon_vector,
        max_abs_diff=max_abs_diff,
        tolerance=tolerance,
        values_within_tolerance=max_abs_diff <= tolerance,
    )


def assert_subset_cross_check_passed(report: SubsetCrossCheckReport) -> None:
    """D5 — ⛔ a mismatch STOPS the plan; report it rather than adjusting the
    tolerance to pass, which is precisely why the tolerance is frozen before
    the comparison runs."""
    if not report.passed:
        raise ImergReadContractError(
            "D5 subset/archive cross-check FAILED — stop and report rather "
            f"than adjusting the tolerance to pass: {report}"
        )


def run_subset_cross_check(
    *,
    archive_path: Path,
    data_root: Path,
    client: ImergSubsetHttpClient,
) -> SubsetCrossCheckReport:
    """D5 — T2's ONE production entry point (fixer review round 2, finding 3
    — MAJOR). `cross_check_subset_against_archive` alone RETURNS a failed
    report on a mismatch rather than raising — a caller that forgets to check
    `.passed` would silently continue past D5's hard stop, which is exactly
    what the plan forbids ("a mismatch ends the plan rather than starting
    T3"). This is the only function any T2 caller should use: it computes the
    report and enforces the gate itself before ever returning one, so the gate
    cannot be bypassed by calling the lower-level function directly.
    ⛔ D5's required ORDERING (coordinates before values) is now EXECUTED,
    not merely implied: `cross_check_subset_against_archive` calls
    `assert_subset_coordinates_match` immediately after reading both
    contracts and before it opens either precipitation array (fixer round 3,
    finding MINOR 2). `ImergSubsetReadContract.__post_init__`'s own exact
    lat/lon pin is a second, independent gate against the FROZEN box; the
    cross-check gate is against the ARCHIVE's own slice."""
    report = cross_check_subset_against_archive(
        archive_path=archive_path, data_root=data_root, client=client
    )
    assert_subset_cross_check_passed(report)
    return report


# --- storage layout ---


def imerg_early_root(data_root: Path) -> Path:
    return data_root / "imerg_early"


def imerg_raw_dir(data_root: Path) -> Path:
    return imerg_early_root(data_root) / "raw"


def granule_artifact_path(data_root: Path, *, filename: str) -> Path:
    return imerg_raw_dir(data_root) / filename


def acquisition_manifest_path(data_root: Path) -> Path:
    """D10 — PERMANENT while the raw granules are disposable, so it lives as a
    SIBLING of `raw/`, never inside it."""
    return imerg_early_root(data_root) / "acquisition_manifest.json"


def imerg_points_root(data_root: Path) -> Path:
    """D9 — IMERG's OWN published-bundle root, never `era5_extract_manifest.
    points_root()`. Defined HERE, beside the permanent record, because the
    record's WRITER has to find the bundles that name it (Plan 224 T1);
    `imerg_extract` imports it rather than keeping a second copy."""
    return imerg_early_root(data_root) / "points"


_PUBLISHED_BUNDLE_DIR_RE = re.compile(r"^\d+-")


def published_bundle_dirs(points_root: Path) -> list[Path]:
    """The published `NNNN-<identity>` bundles under `points_root`. ⛔ Only
    those: `.staging/<token>` is a crashed run's leftover, not a bundle that
    anything can name."""
    if not points_root.is_dir():
        return []
    return sorted(
        child
        for child in points_root.iterdir()
        if child.is_dir() and _PUBLISHED_BUNDLE_DIR_RE.match(child.name)
    )


#: Plan 224 T1 — the SERIALIZATION CONTRACT. The writer's orphan guard reads
#: the published bundles and then replaces the record; publication separately
#: validates the record and then renames a bundle in. Interleaved, both can
#: pass and leave a bundle naming a replaced digest. ⛔ Rather than coordinate
#: them, acquisition-record writes and bundle publication are declared MUTUALLY
#: EXCLUSIVE and the rule is ENFORCED here: one advisory `flock` over the whole
#: read-then-act sequence on each side, taken NON-BLOCKING so a violation fails
#: loudly instead of queueing. Both operations are minutes-scale, run from the
#: same host and are never concurrent by design — this only makes that explicit.
WRITER_LOCK_FILENAME = ".imerg-writer.lock"


@contextmanager
def imerg_writer_lock(early_root: Path, *, holder: str) -> Iterator[None]:
    """Hold the exclusive IMERG writer lock for `holder`, or fail loudly."""
    lock_path = early_root / WRITER_LOCK_FILENAME
    try:
        early_root.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise ImergStorageError(
            f"{holder} could not open the IMERG writer lock at {lock_path}: {exc}"
        ) from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ImergStorageError(
                f"{holder} could not take the exclusive IMERG writer lock at "
                f"{lock_path}: another IMERG acquisition-record write or bundle "
                "publication holds it. The two MUST NOT overlap — a published "
                "bundle carries only the record's digest, so an interleaved run "
                "can leave it naming a record that no longer exists. Re-run once "
                "the other finishes (Plan 224 T1)"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def acquisition_record_identity_content(
    manifest: ImergAcquisitionManifest,
) -> dict[str, object]:
    """Exactly the content a published bundle's `acquisition_record_sha256`
    digests — wall-clock provenance excluded (D9 never hashes time). ⛔ ONE
    definition, shared with `imerg_extract.acquisition_record_digest`: a
    second copy could only ever drift, and the writer's orphan guard below
    would then protect a digest nobody computes."""
    return manifest.model_dump(
        mode="json", exclude={"generated_at", "granule_retrieved_at"}
    )


# --- D9 T1->T2 handoff — the permanent acquisition manifest ---


class ImergAcquisitionManifest(BaseModel):
    """D9 — written by T1, read by T2, the SOLE owner of the full D1 read
    contract and the per-granule checksums (D10: what survives if the raw
    granules go). ⛔ No `completeness` field: it is DERIVED, never trusted."""

    route: str
    collection_short_name: str
    granule_revision: str
    requested_window_start: datetime
    requested_window_end: datetime
    """The window ACTUALLY requested this run — a probe's is its one granule's
    own start, so a reader must derive completeness rather than assume these
    are the pinned D5 endpoints."""
    box: tuple[int, int, int, int]
    read_contract: dict[str, object]
    requested: int
    retrieved: int
    missing: tuple[str, ...] = ()
    """ISO timestamps of granules requested but absent (404) from the archive."""
    granule_checksums: dict[str, str] = {}
    granule_retrieved_at: dict[str, datetime] = {}
    retrospective: bool
    generated_at: datetime

    @field_validator("missing")
    @classmethod
    def _chronological(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """D9 — the gaps are hashed into the extraction identity, so they are
        stored chronologically: two records differing only in the ORDER their
        gaps were discovered must not carry different identities."""
        return tuple(sorted(value))


def write_acquisition_manifest(manifest: ImergAcquisitionManifest, path: Path) -> None:
    """D10's writer, under the Plan 224 T1 serialization lock: the guard below
    READS the published bundles and then ACTS on the record, so the whole
    sequence is held exclusive against a concurrent publication."""
    with imerg_writer_lock(path.parent, holder="the IMERG acquisition-record write"):
        _write_acquisition_manifest_locked(manifest, path)


def _write_acquisition_manifest_locked(
    manifest: ImergAcquisitionManifest, path: Path
) -> None:
    """D10 — the record is PERMANENT, and that is the only reason discarding
    the raw granules is safe. ⛔ Completeness is DERIVED here, never read off a
    label: a malformed record must not replace a complete one by *claiming* to
    be complete. A re-download's checksums are compared against the retained
    ones rather than replacing them, so a revision stays DETECTABLE."""
    existing = read_acquisition_manifest(path)
    if existing is not None and is_complete_acquisition(existing):
        violations = acquisition_completeness_violations(manifest)
        if violations:
            raise ImergStorageError(
                f"refusing to overwrite the COMPLETE acquisition manifest at "
                f"{path} with one that does not DERIVE as complete "
                f"({'; '.join(violations[:3])}) — the permanent record must "
                "never be downgraded (D10)"
            )
        # D2/D10 (fixer round 3, finding MAJOR 2) — the revision guard is
        # SAME-ROUTE ONLY. Raw storage is keyed by the ARCHIVE filename on both
        # routes, so an archive record and a subset record of the same granule
        # share every key while NECESSARILY carrying different bytes: an
        # 8 MB global HDF5 against a 25 KB OPeNDAP `.dap.nc4`. Comparing them
        # made a legitimate route switch look exactly like a GES DISC archive
        # revision and refused it. ⛔ A real route change is not waved through:
        # `route` is part of the identity content, so the orphan guard below
        # still refuses to strand any published bundle, and the completeness
        # check above still refuses a downgrade.
        if existing.route == manifest.route:
            revised = sorted(
                name
                for name, sha in manifest.granule_checksums.items()
                if name in existing.granule_checksums
                and existing.granule_checksums[name] != sha
            )
            if revised:
                raise ImergStorageError(
                    f"re-download checksum(s) for {revised[:5]} "
                    f"({len(revised)} granule(s)) disagree with the retained "
                    f"COMPLETE acquisition manifest at {path} — GES DISC "
                    "revised the archive; resolve that before replacing the "
                    "record (D10)"
                )
    if existing is not None and acquisition_record_identity_content(
        manifest
    ) != acquisition_record_identity_content(existing):
        # Plan 224 T1 — a published bundle carries only the record's DIGEST,
        # and `validate_imerg_bundle` resolves it at exactly this path. So
        # every bundle under the sibling points root was validated against the
        # record being replaced here: changing its identity-bearing content
        # leaves their `acquisition_record_sha256` addressing nothing. ⛔ The
        # writer refuses rather than orphaning them; the operator moves the
        # bundles aside deliberately. (A rewrite that only moves the clock is
        # excluded above and stays allowed.)
        points_root = imerg_points_root(path.parent.parent)
        orphaned = published_bundle_dirs(points_root)
        if orphaned:
            raise ImergStorageError(
                f"refusing to replace the permanent acquisition record at "
                f"{path}: {len(orphaned)} published IMERG bundle(s) under "
                f"{points_root} (first {orphaned[0].name}) were validated "
                "against it, and the replacement's identity-bearing content "
                "differs — their acquisition_record_sha256 would address a "
                "record that no longer exists (D9/D10)"
            )
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(manifest.model_dump_json(indent=2))
        os.replace(tmp_path, path)
    except OSError as exc:
        raise ImergStorageError(
            f"failed to write acquisition manifest to {path}: {exc}"
        ) from exc


def read_acquisition_manifest(path: Path) -> ImergAcquisitionManifest | None:
    if not path.exists():
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        raise ImergStorageError(
            f"failed to read acquisition manifest at {path}: {exc}"
        ) from exc
    try:
        return ImergAcquisitionManifest.model_validate_json(text)
    except ValueError as exc:
        raise ImergStorageError(
            f"acquisition manifest at {path} is unreadable: {exc}"
        ) from exc


# --- D9/D5 — completeness and pinned provenance, DERIVED not labelled ---

_READ_CONTRACT_FIELDS: frozenset[str] = frozenset(
    ImergReadContract.__dataclass_fields__
)
_SUBSET_READ_CONTRACT_FIELDS: frozenset[str] = frozenset(
    ImergSubsetReadContract.__dataclass_fields__
)

#: D2 — route -> the frozen contract class it must satisfy. ⛔ Route DISPATCH,
#: not a shared/relaxed contract: a route absent here is unrecognised, never
#: silently accepted.
_KNOWN_ROUTES: frozenset[str] = frozenset((ROUTE, SUBSET_ROUTE))
_CONTRACT_FIELDS_BY_ROUTE: dict[str, frozenset[str]] = {
    ROUTE: _READ_CONTRACT_FIELDS,
    SUBSET_ROUTE: _SUBSET_READ_CONTRACT_FIELDS,
}
_CONTRACT_CLASS_BY_ROUTE: dict[
    str, type[ImergReadContract] | type[ImergSubsetReadContract]
] = {
    ROUTE: ImergReadContract,
    SUBSET_ROUTE: ImergSubsetReadContract,
}


def read_contract_violations(
    raw: dict[str, object], *, granule_revision: str, route: str
) -> tuple[str, ...]:
    """D1/D2 — a recorded contract must SATISFY the frozen contract for ITS
    OWN recorded route, not merely carry SOME contract's field names: a
    record whose every value read "recorded" derived as COMPLETE under a
    presence-only check, and a record naming one route while carrying the
    OTHER route's contract shape must not pass either. The contract class for
    `route` is the judge (the JSON round-trip turns its tuples into lists)."""
    expected_fields = _CONTRACT_FIELDS_BY_ROUTE.get(route)
    if expected_fields is None:
        return (f"route {route!r} is not a recognised IMERG route (D2)",)
    if set(raw) != set(expected_fields):
        return (
            f"read_contract fields {sorted(raw)} != the {route!r} route's "
            f"{sorted(expected_fields)}",
        )
    contract_cls = _CONTRACT_CLASS_BY_ROUTE[route]
    try:
        contract = contract_cls(
            **{  # type: ignore[arg-type]
                key: tuple(value) if isinstance(value, list) else value
                for key, value in raw.items()
            }
        )
    except (ImergReadContractError, TypeError, ValueError) as exc:
        return (
            f"read_contract does not satisfy the D1 read contract for the "
            f"{route!r} route: {exc}",
        )
    if contract.granule_revision != granule_revision:
        return (
            f"read_contract granule_revision {contract.granule_revision!r} != "
            f"the record's own {granule_revision!r}",
        )
    return ()


def pinned_provenance_violations(
    *,
    route: str,
    collection_short_name: str,
    granule_revision: str,
    box: tuple[int, ...],
    retrospective: bool,
) -> tuple[str, ...]:
    """D1/D2/D7 — the pins every IMERG record must carry, checked identically
    by the record and by the bundle's predicate. ⛔ Constants, never
    parameters — except `route`, which is now one of TWO known constants
    (D2), never an arbitrary string."""
    v: list[str] = []
    if route not in _KNOWN_ROUTES:
        v.append(
            f"route {route!r} is not one of the known IMERG routes "
            f"{sorted(_KNOWN_ROUTES)}"
        )
    if collection_short_name != COLLECTION_SHORT_NAME:
        v.append(f"collection {collection_short_name!r} != {COLLECTION_SHORT_NAME!r}")
    if granule_revision != PINNED_GRANULE_REVISION_PER_PLAN:
        v.append(
            f"revision {granule_revision!r} != the D1-pinned "
            f"{PINNED_GRANULE_REVISION_PER_PLAN!r}"
        )
    if tuple(box) != tuple(STUDY_BOX):
        v.append(f"box {tuple(box)} != the frozen {tuple(STUDY_BOX)}")
    if retrospective is not True:
        v.append("the record is not marked RETROSPECTIVE (D7)")
    return tuple(v)


def _parse_missing_starts(missing: Sequence[str]) -> tuple[set[datetime], list[str]]:
    v: list[str] = []
    parsed: list[datetime] = []
    for iso in missing:
        try:
            start = datetime.fromisoformat(iso)
        except ValueError:
            v.append(f"missing entry {iso!r} is not an ISO timestamp")
            continue
        if not granule_start_in_window(start):
            v.append(
                f"missing entry {iso!r} is not a half-hour start inside the "
                "pinned D5 window"
            )
            continue
        parsed.append(start)
    starts = set(parsed)
    if len(starts) != len(parsed):
        v.append("missing contains duplicate granule start times")
    return starts, v


def window_accounting_violations(
    *,
    requested: int,
    retrieved: int,
    missing: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[str, ...]:
    """D5/D9 — every reason an acquisition ACCOUNTING fails to describe the
    WHOLE pinned window. ⛔ Shared by the permanent record and by the bundle's
    publish/discovery predicate, so an under-complete accounting cannot reach
    publication by bypassing acquisition: with the requested count pinned, the
    gaps unique and inside the window, and `retrieved + missing == requested`,
    the retrieved set can only be the exact complement of the gaps."""
    v: list[str] = []
    if window_start != FIRST_GRANULE_START:
        v.append(
            f"window start {window_start.isoformat()} != "
            f"{FIRST_GRANULE_START.isoformat()} (D5 boundary granules)"
        )
    if window_end != LAST_GRANULE_START:
        v.append(
            f"window end {window_end.isoformat()} != "
            f"{LAST_GRANULE_START.isoformat()} (D5)"
        )
    if requested != EXPECTED_GRANULE_COUNT:
        v.append(
            f"requested {requested} != the pinned window's "
            f"{EXPECTED_GRANULE_COUNT} half-hour granules"
        )
    if retrieved + len(missing) != requested:
        v.append(
            f"retrieved {retrieved} + missing {len(missing)} != requested {requested}"
        )
    v.extend(_parse_missing_starts(missing)[1])
    return tuple(v)


def acquisition_completeness_violations(
    manifest: ImergAcquisitionManifest,
) -> tuple[str, ...]:
    """D9/D5 — every reason this record does not describe a complete
    acquisition of the pinned window, derived from its own contents. A consumer
    trusting a claim it could have computed publishes an unverified identity."""
    v = list(
        window_accounting_violations(
            requested=manifest.requested,
            retrieved=manifest.retrieved,
            missing=manifest.missing,
            window_start=manifest.requested_window_start,
            window_end=manifest.requested_window_end,
        )
    )
    v.extend(
        pinned_provenance_violations(
            route=manifest.route,
            collection_short_name=manifest.collection_short_name,
            granule_revision=manifest.granule_revision,
            box=manifest.box,
            retrospective=manifest.retrospective,
        )
    )
    v.extend(
        read_contract_violations(
            manifest.read_contract,
            granule_revision=manifest.granule_revision,
            route=manifest.route,
        )
    )
    if manifest.retrieved != len(manifest.granule_checksums):
        v.append(
            f"retrieved {manifest.retrieved} != the "
            f"{len(manifest.granule_checksums)} granule_checksums entries recorded"
        )
    if set(manifest.granule_retrieved_at) != set(manifest.granule_checksums):
        v.append(
            "granule_retrieved_at and granule_checksums do not cover the same "
            "granule filenames"
        )
    retrieved_starts: set[datetime] = set()
    for name in manifest.granule_checksums:
        try:
            granule, revision = parse_granule_filename(name)
        except ImergReadContractError as exc:
            v.append(f"granule filename {name!r} is unparseable: {exc}")
            continue
        if revision != PINNED_GRANULE_REVISION_PER_PLAN:
            v.append(
                f"granule {name!r} carries revision {revision!r}, not the "
                f"D1-pinned {PINNED_GRANULE_REVISION_PER_PLAN!r}"
            )
        if not granule_start_in_window(granule.start):
            v.append(f"granule {name!r} starts outside the pinned D5 window")
        retrieved_starts.add(granule.start)
    if len(retrieved_starts) != len(manifest.granule_checksums):
        v.append("granule_checksums contains duplicate granule start times")
    overlap = retrieved_starts & _parse_missing_starts(manifest.missing)[0]
    if overlap:
        v.append(f"{len(overlap)} granule(s) are recorded BOTH retrieved and missing")
    return tuple(v)


def is_complete_acquisition(manifest: ImergAcquisitionManifest) -> bool:
    """The one place COMPLETE is decided, from the record's own contents."""
    return not acquisition_completeness_violations(manifest)


def assert_acquisition_manifest_complete(manifest: ImergAcquisitionManifest) -> None:
    """D9 — the gate T2 must pass BEFORE reading any granule file."""
    violations = acquisition_completeness_violations(manifest)
    if violations:
        raise ImergRequestFailedError(
            "the IMERG acquisition manifest does not derive as a COMPLETE "
            "acquisition of the pinned D5 window (D9): " + "; ".join(violations)
        )


# --- the injected HTTP seam (mirrors era5_acquire.py's CdsClient) ---


@runtime_checkable
class ImergHttpClient(Protocol):
    """The single injected HTTP seam; fakes satisfy it for no-network tests."""

    def list_directory(self, url: str) -> str: ...
    def download_to_path(self, *, url: str, target: Path) -> None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class RealImergHttpClient:
    """`.netrc`-based Earthdata Login auth: `requests` looks up the
    `urs.earthdata.nasa.gov` entry on GES DISC's redirect, as `curl -n` does —
    no credential appears in this module."""

    timeout_seconds: float = 60.0

    def list_directory(self, url: str) -> str:
        import requests

        try:
            response = requests.get(url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to list {url}: {exc}") from exc
        _raise_for_http_status(response.status_code, url=url)
        return response.text

    def download_to_path(self, *, url: str, target: Path) -> None:
        import requests

        try:
            response = requests.get(url, timeout=self.timeout_seconds, stream=True)
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to download {url}: {exc}") from exc
        _raise_for_http_status(response.status_code, url=url)
        try:
            with target.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    fh.write(chunk)
        except OSError as exc:
            raise ImergStorageError(f"failed to write {target}: {exc}") from exc


# --- per-granule acquisition ---


def acquire_granule(
    granule: ImergGranuleId,
    *,
    client: ImergHttpClient,
    data_root: Path,
    sleep: Callable[[float], None],
    frozen_contract: ImergReadContract | None,
    max_attempts: int = 5,
    backoff_base_seconds: float = 2.0,
) -> tuple[Path, str, ImergReadContract]:
    """List the day directory to resolve this granule's filename (its revision
    letter is unknowable in advance), download it, observe its D1 contract
    (asserted against `frozen_contract` once established), checksum it and
    publish atomically. Transient failures retry; a 404 never does."""
    listing = client.list_directory(granule.directory_url())
    filename = resolve_granule_filename(listing, granule)
    final_path = granule_artifact_path(data_root, filename=filename)
    if final_path.exists():
        contract = observe_read_contract(final_path)
        if frozen_contract is not None:
            assert_contract_consistent(contract, frozen=frozen_contract)
        return final_path, checksum_file(final_path), contract

    imerg_raw_dir(data_root).mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(final_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)
    url = f"{granule.directory_url()}{filename}"
    last_exc: ImergTransientError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            client.download_to_path(url=url, target=tmp_path)
            break
        except ImergTransientError as exc:
            last_exc = exc
            tmp_path.unlink(missing_ok=True)
            log.warning(
                "imerg.acquire.transient_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                granule=granule.start.isoformat(),
                error=str(exc),
            )
            if attempt < max_attempts:
                sleep(backoff_base_seconds * (2 ** (attempt - 1)))
        except (ImergCredentialsError, ImergRequestFailedError, OSError) as exc:
            tmp_path.unlink(missing_ok=True)
            if isinstance(exc, OSError):
                raise ImergStorageError(str(exc)) from exc
            raise
    else:
        tmp_path.unlink(missing_ok=True)
        raise ImergRequestFailedError(
            f"exhausted {max_attempts} attempts downloading {url}"
        ) from last_exc

    try:
        contract = observe_read_contract(tmp_path, filename=filename)
        if frozen_contract is not None:
            assert_contract_consistent(contract, frozen=frozen_contract)
        sha256 = checksum_file(tmp_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, final_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    log.info(
        "imerg.acquire.granule_complete",
        granule=granule.start.isoformat(),
        filename=filename,
        sha256=sha256,
    )
    return final_path, sha256, contract


# --- window retrieval: a tested library function, never wired to this CLI
# (the per-run scope stops at the projection gate) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class WindowRetrievalReport:
    requested: int
    retrieved: int
    missing: tuple[str, ...]
    """ISO timestamps of granules the archive does not (yet) have. When
    nothing at all was retrieved no acquisition manifest is written, so this
    report carries the only record of the attempt."""


def retrieve_window(
    granule_starts: Iterator[datetime],
    *,
    client: ImergHttpClient,
    data_root: Path,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> tuple[WindowRetrievalReport, ImergReadContract | None, dict[str, str]]:
    """D9/D10 — retrieve every granule in `granule_starts`, freezing D1's read
    contract on the first and asserting it on the rest; a 404 is counted,
    never fatal (D4 turns it into a partial hour). Writes the PERMANENT
    acquisition manifest whenever at least one granule was retrieved."""
    requested = 0
    retrieved = 0
    missing: list[str] = []
    checksums: dict[str, str] = {}
    retrieved_at: dict[str, datetime] = {}
    frozen: ImergReadContract | None = None
    first_start: datetime | None = None
    last_start: datetime | None = None
    for start in granule_starts:
        requested += 1
        first_start = start if first_start is None else min(first_start, start)
        last_start = start if last_start is None else max(last_start, start)
        try:
            path, sha256, contract = acquire_granule(
                ImergGranuleId(start=start),
                client=client,
                data_root=data_root,
                sleep=sleep,
                frozen_contract=frozen,
            )
        except ImergGranuleMissingError:
            missing.append(start.isoformat())
            continue
        if frozen is None:
            frozen = contract
        checksums[path.name] = sha256
        retrieved_at[path.name] = clock()
        retrieved += 1
    report = WindowRetrievalReport(
        requested=requested, retrieved=retrieved, missing=tuple(sorted(missing))
    )
    if frozen is not None and first_start is not None and last_start is not None:
        write_acquisition_manifest(
            ImergAcquisitionManifest(
                route=ROUTE,
                collection_short_name=COLLECTION_SHORT_NAME,
                granule_revision=frozen.granule_revision,
                requested_window_start=first_start,
                requested_window_end=last_start,
                box=STUDY_BOX,
                read_contract=frozen.as_manifest_dict(),
                requested=requested,
                retrieved=retrieved,
                missing=tuple(missing),
                granule_checksums=checksums,
                granule_retrieved_at=retrieved_at,
                retrospective=True,
                generated_at=clock(),
            ),
            acquisition_manifest_path(data_root),
        )
    return report, frozen, checksums


# --- D10 disk projection ---


@dataclass(frozen=True, kw_only=True, slots=True)
class DiskProjection:
    probe_granule_bytes: int
    projected_granule_count: int
    projected_total_bytes: int
    free_disk_bytes: int
    fits: bool


def compute_projection(
    *,
    probe_granule_bytes: int,
    free_disk_bytes: int,
    projected_granule_count: int = EXPECTED_GRANULE_COUNT,
) -> DiskProjection:
    """D10 — decide by MEASUREMENT: one granule's size x the window's count,
    reported BEFORE any bulk retrieval."""
    total = probe_granule_bytes * projected_granule_count
    return DiskProjection(
        probe_granule_bytes=probe_granule_bytes,
        projected_granule_count=projected_granule_count,
        projected_total_bytes=total,
        free_disk_bytes=free_disk_bytes,
        fits=total < free_disk_bytes,
    )


# --- CLI: the D1 contract probe only; bulk retrieval is the owner's call ---


DEFAULT_DATA_ROOT = Path("data/dhm_precip")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imerg_acquire", description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--granule-start",
        type=str,
        default=None,
        help=(
            "ISO 8601 UTC half-hour timestamp to probe (default: the most "
            "recently likely-available granule, now minus 5 hours)."
        ),
    )
    return parser


def run_probe(
    *,
    granule_start: datetime,
    client: ImergHttpClient,
    data_root: Path,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    free_disk_bytes: int,
) -> DiskProjection:
    """T1's one permitted network action this run: acquire exactly ONE granule,
    freeze its read contract, record it, report the D10 disk projection."""
    path, sha256, contract = acquire_granule(
        ImergGranuleId(start=granule_start),
        client=client,
        data_root=data_root,
        sleep=sleep,
        frozen_contract=None,
    )
    probe_bytes = path.stat().st_size
    projection = compute_projection(
        probe_granule_bytes=probe_bytes, free_disk_bytes=free_disk_bytes
    )
    probed_at = clock()
    write_acquisition_manifest(
        ImergAcquisitionManifest(
            route=ROUTE,
            collection_short_name=COLLECTION_SHORT_NAME,
            granule_revision=contract.granule_revision,
            requested_window_start=granule_start,
            requested_window_end=granule_start,
            box=STUDY_BOX,
            read_contract=contract.as_manifest_dict(),
            requested=1,
            retrieved=1,
            missing=(),
            granule_checksums={path.name: sha256},
            granule_retrieved_at={path.name: probed_at},
            retrospective=True,
            generated_at=probed_at,
        ),
        acquisition_manifest_path(data_root),
    )
    log.info(
        "imerg.acquire.probe_complete",
        granule=granule_start.isoformat(),
        filename=path.name,
        bytes=probe_bytes,
        projected_granule_count=projection.projected_granule_count,
        projected_total_bytes=projection.projected_total_bytes,
        free_disk_bytes=free_disk_bytes,
        fits=projection.fits,
    )
    return projection


def parse_cli_utc_timestamp(value: str) -> datetime:
    """Boundary parsing for `--granule-start`: reject a naive timestamp rather
    than reading it in the host's timezone (the wrong granule)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(
            f"--granule-start {value!r} has no UTC offset — an explicit "
            "offset (e.g. a trailing 'Z' or '+00:00') is required, never "
            "assumed from the host timezone"
        )
    return parsed.astimezone(UTC)


def _nearest_existing_ancestor(path: Path) -> Path:
    """`os.statvfs` needs an existing path; a not-yet-created `--data-root` must
    not fall back to measuring the CWD's filesystem."""
    candidate = path.resolve()
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return candidate


#: Ordered subclass-first, mirroring `imerg_extract._EXIT_BY_ERROR`: the
#: per-subclass exit codes the error docstrings promise are HONORED, not
#: merely documented. A raw `OSError` never reaches the typed hierarchy.
_EXIT_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (ImergCredentialsError, 2),
    (ImergStorageError, 5),
    (ImergAcquisitionError, 3),
    (OSError, 5),
)


def _exit_code_for(exc: Exception) -> int:
    for exc_type, code in _EXIT_BY_ERROR:
        if isinstance(exc, exc_type):
            return code
    return 1


def main(argv: list[str] | None = None, **kwargs: object) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    granule_start = (
        parse_cli_utc_timestamp(args.granule_start)
        if args.granule_start
        else (datetime.now(UTC) - timedelta(hours=5)).replace(
            minute=0 if datetime.now(UTC).minute < 30 else 30,  # noqa: PLR2004
            second=0,
            microsecond=0,
        )
    )
    # ⛔ The statvfs probe is INSIDE the guard: an unreadable --data-root
    # ancestor is a typed, reported storage failure, never a raw traceback.
    try:
        statvfs_result = os.statvfs(_nearest_existing_ancestor(args.data_root))
        run_probe(
            granule_start=granule_start,
            client=kwargs.get("client") or RealImergHttpClient(),  # type: ignore[arg-type]
            data_root=args.data_root,
            clock=kwargs.get("clock") or (lambda: datetime.now(UTC)),  # type: ignore[arg-type]
            sleep=kwargs.get("sleep") or __import__("time").sleep,  # type: ignore[arg-type]
            free_disk_bytes=statvfs_result.f_bavail * statvfs_result.f_frsize,
        )
    except (ImergAcquisitionError, OSError) as exc:
        log.error(
            "imerg.acquire.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return _exit_code_for(exc)
    log.warning(
        "imerg.acquire.cli.stop_after_projection",
        message=(
            "T1 per-run scope: probe complete, projection reported. Bulk "
            "retrieval is the owner's call — this CLI does not perform it."
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
