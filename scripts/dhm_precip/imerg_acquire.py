"""Plan 211 (M-A5b) task T1 — IMERG Early V07 half-hourly acquisition over
the frozen DHM study box, via the GES DISC HTTPS archive.

D1 — the read contract (HDF5 variable path, dimension order, registration,
longitude convention, units, fill value, exact grid shape and the lat/lon
vectors themselves) is FROZEN on the first granule this run observes and
asserted on every subsequent one; a granule that disagrees stops the run
rather than silently blending mixed structures into one bundle.

⛔ THE REVISION IS PINNED, PER THE PLAN TEXT, NOT MERELY DOCUMENTARY. D1:
"Current Early is V07B; if a granule carries another revision, stop and
report rather than blending." `PINNED_GRANULE_REVISION_PER_PLAN` below is
enforced — not just recorded — by `assert_revision_matches_plan_and_header`,
called on every observed granule (`observe_read_contract` and
`imerg_extract.read_granule`): a granule whose filename revision differs
from `V07B` raises `ImergRevisionMismatchError` and the run stops. The
revision is also NOT inferred from the filename alone: the granule's own
`FileHeader.ProductVersion` global attribute is read and cross-checked
against the filename-parsed revision (normalising GES DISC's `V`-prefix
convention), so a filename that disagrees with the file's own embedded
header is caught rather than silently trusted.

⚠️ **Residual risk, reported rather than resolved unilaterally**: the T1
probe run for THIS implementation (2026-08-28, the same day the plan was
written) observed the LIVE archive serving **V07C** — the granule filename
and its embedded `FileHeader.FileName` both read `V07C`, while
`FileHeader.ProductVersion` confusingly still reads `07B` (GES DISC's NRT
reprocessing-generation letter and the ATBD `ProductVersion` field are
evidently not the same axis, and had already drifted apart the same day the
plan's "Current Early is V07B" was measured under Plan 209 T2). With the pin
now enforced, a live acquisition run against the archive AS OBSERVED
2026-08-28 will legitimately raise `ImergRevisionMismatchError` and halt —
which is D1's own required behaviour ("stop and report rather than
blending"), not a bug. Resolving that halt (confirming V07B has returned, or
re-confirming the READY plan for V07C) is the owner's call, not this
module's.

D9's five per-run scope constraints for THIS `/implement`: build the
pipeline, retrieve exactly ONE granule (the D1 contract probe), report the
disk projection, and STOP — no bulk retrieval. `retrieve_window` below is a
fully tested library function for that later, owner-gated bulk retrieval; it
is never invoked by this module's CLI.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import structlog  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from sapphire_flow.exceptions import SapphireError  # noqa: E402
from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.era5_manifest import checksum_file  # noqa: E402
from scripts.dhm_precip.era5_request import STUDY_AREA, STUDY_YEARS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

log = structlog.get_logger(__name__)


# --- errors (mirrors era5_errors.py's shape: one hierarchy, one CLI
# exit-code mapping) ---


class ImergAcquisitionError(SapphireError):
    """Base for every M-A5b IMERG acquisition error."""


class ImergCredentialsError(ImergAcquisitionError):
    """Earthdata Login credentials absent or rejected by GES DISC/URS. CLI
    exit code 2."""


class ImergTransientError(ImergAcquisitionError):
    """Retryable: transport error or HTTP 5xx. Never surfaces past the
    retry loop — either a later attempt succeeds or it is wrapped into
    `ImergRequestFailedError`."""


class ImergRequestFailedError(ImergAcquisitionError):
    """GES DISC rejected the request, or a retryable failure exhausted its
    attempts, or a downloaded artifact failed post-download validation. CLI
    exit code 3."""


class ImergGranuleMissingError(ImergRequestFailedError):
    """HTTP 404 / absent from the day's directory listing — the granule is
    not (yet) published. Counted, never fatal, for a window retrieval
    (D4); fatal for a single-granule probe."""


class ImergReadContractError(ImergRequestFailedError):
    """D1 — an observed granule's structure fails the frozen read contract
    on a field OTHER than revision."""


class ImergRevisionMismatchError(ImergReadContractError):
    """D1 — an observed granule's revision differs from the frozen one.
    ⛔ Stop and report rather than blending mixed revisions into one
    bundle."""


class ImergStorageError(ImergAcquisitionError):
    """Storage or manifest write/read failed. CLI exit code 5."""


# --- D1 pinned identity ---

COLLECTION_SHORT_NAME = "GPM_3IMERGHHE_07"
GESDISC_BASE_URL = "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGHHE.07"
HDF5_VARIABLE_PATH = "/Grid/precipitation"
ROUTE = "GES DISC HTTPS archive"

#: D1 — pinned and ENFORCED (not merely documentary): `assert_revision_
#: matches_plan_and_header` rejects any granule whose revision differs.
#: See the module docstring's residual-risk note: the live archive observed
#: 2026-08-28 serves V07C, so a live run will halt until the owner resolves
#: that drift.
PINNED_GRANULE_REVISION_PER_PLAN = "V07B"

#: D9 — Plan 209 T2's measured figure, carried into every extraction
#: manifest verbatim (D9: "the measured acquisition latency" is a recorded
#: fact, not re-measured per run).
MEASURED_ACQUISITION_LATENCY = (
    "4h20m-4h50m (Plan 209 T2, GES DISC HTTPS archive, granules publish in "
    "hourly batches of two)"
)

# D1 — reuse the frozen bounding box verbatim (T1 pin). IMERG granules are
# always global; this box is recorded as the extraction's own footprint, not
# used to subset the download (D1's "if a subset route is used" branch does
# not apply to the plain HTTPS archive route this module implements).
STUDY_BOX: tuple[int, int, int, int] = STUDY_AREA

GRID_SPACING_DEG = 0.1
EXPECTED_UNITS = "mm/hr"
EXPECTED_FILL_VALUE = -9999.9
EXPECTED_REGISTRATION = "CENTER"
EXPECTED_DIMENSION_NAMES: tuple[str, ...] = ("time", "lon", "lat")
EXPECTED_GRID_SHAPE: tuple[int, int, int] = (1, 3600, 1800)
_FILL_VALUE_TOLERANCE = 1e-3
"""float32 round-trip tolerance: -9999.9 is stored as float32 and reads back
as -9999.900390625 (a ~4e-4 discrepancy) — observed on the real GES DISC
probe granule, 2026-08-28."""

_GPS_EPOCH = datetime(1980, 1, 6, tzinfo=UTC)


# --- D5 window arithmetic — the axis runs 2020-01-01 00:00 .. 2025-12-31
# 23:00 inclusive (period-ending); the first hour needs the two granules of
# 2019-12-31 23:00-24:00, so retrieval starts one hour before the study
# years. The last hour (2025-12-31 23:00) needs granules starting at 22:00
# and 22:30 that same day — the LAST granule requested is therefore
# 2025-12-31 22:30, not 23:30. ---

FIRST_GRANULE_START: datetime = datetime(STUDY_YEARS[0] - 1, 12, 31, 23, 0, tzinfo=UTC)
LAST_GRANULE_START: datetime = datetime(STUDY_YEARS[-1], 12, 31, 22, 30, tzinfo=UTC)
EXPECTED_GRANULE_COUNT = 105_216
"""Measured (plan 'Measured facts'): 48 granules/day * 2192 days across the
six study years. Verified independently by `granule_count()` below, which
derives it from the half-hour arithmetic rather than restating the literal."""


def all_granule_starts() -> Iterator[datetime]:
    """D5 — every half-hour granule start this acquisition needs, in
    order, from `FIRST_GRANULE_START` through `LAST_GRANULE_START`
    inclusive."""
    current = FIRST_GRANULE_START
    step = timedelta(minutes=30)
    while current <= LAST_GRANULE_START:
        yield current
        current += step


def granule_count() -> int:
    """The window's granule count, derived from the arithmetic — never
    restated as a bare literal (D10 forbids "restating that projection as a
    fact")."""
    return sum(1 for _ in all_granule_starts())


# --- granule identity (D1's filename/URL construction) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class ImergGranuleId:
    """One half-hourly IMERG Early granule, identified by its UTC start
    time. The revision letter is NOT part of the identity — it varies by
    production generation and is only knowable by observing (or listing)
    the archive (see the module docstring's DEVIATION note)."""

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
        """The granule's own `E` timestamp — 29:59 after `start`, per the
        observed naming (`...-S070000-E072959...`)."""
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


_FILENAME_RE = re.compile(
    r"^3B-HHR-E\.MS\.MRG\.3IMERG\."
    r"(?P<date>\d{8})-S(?P<start_time>\d{6})-E(?P<end_time>\d{6})\."
    r"(?P<minute>\d{4})\.(?P<revision>V\d{2}[A-Z])\.HDF5$"
)


def parse_granule_filename(name: str) -> tuple[ImergGranuleId, str]:
    """The inverse of `ImergGranuleId.filename` — given an observed
    filename, recover the granule identity and its revision. Used both to
    resolve a day-directory listing and to re-derive a granule's identity
    from an already-acquired file on disk."""
    match = _FILENAME_RE.fullmatch(name)
    if match is None:
        raise ImergReadContractError(
            f"filename {name!r} does not match the expected IMERG Early "
            "half-hourly naming pattern (D1)"
        )
    start = datetime.strptime(
        match["date"] + match["start_time"], "%Y%m%d%H%M%S"
    ).replace(tzinfo=UTC)
    granule = ImergGranuleId(start=start)
    if granule.minute_of_day != int(match["minute"]):
        raise ImergReadContractError(
            f"filename {name!r} minute-of-day field {match['minute']} does "
            f"not match its own start time (expected {granule.minute_of_day:04d})"
        )
    return granule, match["revision"]


def resolve_granule_filename(directory_listing: str, granule: ImergGranuleId) -> str:
    """D1 — the revision letter cannot be predicted, so the day directory's
    listing is searched for the one filename matching this granule's date
    and start time, whatever revision it carries. Pure string logic, no
    network — the `directory_listing` text is the caller's concern."""
    date_str = granule.start.strftime("%Y%m%d")
    start_str = granule.start.strftime("%H%M%S")
    pattern = re.compile(
        rf"3B-HHR-E\.MS\.MRG\.3IMERG\.{date_str}-S{start_str}-E\d{{6}}\."
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
    """D1 — every field the plan requires be frozen on first observation
    and asserted on every subsequent granule: HDF5 path, dimension order,
    coordinate registration, longitude convention, units, fill value, exact
    grid shape, and the full lat/lon vectors themselves."""

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
    """The granule's own `/FileHeader` global attribute's `ProductVersion`
    field — read and cross-checked against `granule_revision` (filename-
    parsed) by `assert_revision_matches_plan_and_header` so the revision is
    never trusted from the filename alone (D1)."""

    def __post_init__(self) -> None:
        if self.hdf5_variable_path != HDF5_VARIABLE_PATH:
            raise ImergReadContractError(
                f"observed HDF5 variable path {self.hdf5_variable_path!r} != "
                f"expected {HDF5_VARIABLE_PATH!r} (D1)"
            )
        if self.units != EXPECTED_UNITS:
            raise ImergReadContractError(
                f"observed units {self.units!r} != expected {EXPECTED_UNITS!r} (D1)"
            )
        if abs(self.fill_value - EXPECTED_FILL_VALUE) > _FILL_VALUE_TOLERANCE:
            raise ImergReadContractError(
                f"observed fill value {self.fill_value!r} != expected "
                f"{EXPECTED_FILL_VALUE!r} (D1)"
            )
        if self.coordinate_registration != EXPECTED_REGISTRATION:
            raise ImergReadContractError(
                f"observed registration {self.coordinate_registration!r} != "
                f"expected {EXPECTED_REGISTRATION!r} (D1)"
            )
        if self.dimension_names != EXPECTED_DIMENSION_NAMES:
            raise ImergReadContractError(
                f"observed dimension order {self.dimension_names!r} != "
                f"expected {EXPECTED_DIMENSION_NAMES!r} (D1)"
            )
        if self.grid_shape != EXPECTED_GRID_SHAPE:
            raise ImergReadContractError(
                f"observed grid shape {self.grid_shape!r} != expected "
                f"{EXPECTED_GRID_SHAPE!r} (D1)"
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


def observe_read_contract(
    path: Path, *, filename: str | None = None
) -> ImergReadContract:
    """Open a granule's raw bytes and extract every D1 field. Pinned to the
    exact structure observed on the real GES DISC probe granule
    (2026-08-28): `Grid/precipitation` dims `time,lon,lat`, `Grid/lat`/
    `Grid/lon` 1-D vectors, `Registration=CENTER` in `Grid`'s `GridHeader`
    attribute.

    `filename` overrides the name the revision is parsed from — needed while
    `path` still carries a `.tmp` suffix mid-download (`acquire_granule`
    observes the contract before the atomic rename)."""
    import h5py

    revision = parse_granule_filename(filename if filename is not None else path.name)[
        1
    ]
    with h5py.File(path, "r") as f:
        file_header_product_version = read_file_header_product_version(f)
        grid = f["Grid"]
        precip = grid["precipitation"]
        dim_names_raw = precip.attrs["DimensionNames"]
        dim_names = tuple(
            (
                dim_names_raw.decode()
                if isinstance(dim_names_raw, bytes)
                else str(dim_names_raw)
            ).split(",")
        )
        units_raw = precip.attrs["units"]
        units = units_raw.decode() if isinstance(units_raw, bytes) else str(units_raw)
        fill_value = float(precip.attrs["_FillValue"])
        grid_header_raw = grid.attrs["GridHeader"]
        grid_header = (
            grid_header_raw.decode()
            if isinstance(grid_header_raw, bytes)
            else str(grid_header_raw)
        )
        registration = parse_grid_header_field(grid_header, "Registration")
        lat = np.asarray(grid["lat"][:], dtype=np.float64)
        lon = np.asarray(grid["lon"][:], dtype=np.float64)
        grid_shape = tuple(int(n) for n in precip.shape)
    if len(grid_shape) != 3:
        raise ImergReadContractError(
            f"observed 'precipitation' shape {grid_shape} is not 3-D (D1)"
        )
    longitude_convention = "SIGNED_180" if float(lon.min()) < 0.0 else "UNSIGNED_360"
    spacing = round(float(np.median(np.diff(np.sort(lat)))), 6)
    contract = ImergReadContract(
        hdf5_variable_path=HDF5_VARIABLE_PATH,
        dimension_names=dim_names,
        coordinate_registration=registration,
        longitude_convention=longitude_convention,
        units=units,
        fill_value=fill_value,
        grid_shape=(grid_shape[0], grid_shape[1], grid_shape[2]),
        lat_vector=tuple(round(float(v), 6) for v in lat),
        lon_vector=tuple(round(float(v), 6) for v in lon),
        grid_spacing_deg=spacing,
        granule_revision=revision,
        file_header_product_version=file_header_product_version,
    )
    assert_revision_matches_plan_and_header(
        filename_revision=revision,
        file_header_product_version=file_header_product_version,
    )
    return contract


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


def read_file_header_product_version(f: object) -> str:
    """Read the granule's root-level `FileHeader` global attribute
    (`;`-separated `key=value` text, same shape as `Grid`'s own
    `GridHeader`) and return its `ProductVersion` field, raw — never
    normalised here (`assert_revision_matches_plan_and_header` normalises
    for comparison)."""
    raw = f.attrs.get("FileHeader")  # type: ignore[attr-defined]
    if raw is None:
        raise ImergReadContractError(
            "granule is missing the root-level FileHeader global attribute (D1)"
        )
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    return parse_grid_header_field(text, "ProductVersion")


def _normalize_product_version(value: str) -> str:
    """GES DISC's `FileHeader.ProductVersion` omits the `V` prefix the
    filename revision carries (observed: filename `V07C`, header `07B` —
    see the module docstring). Normalise so the two are comparable on the
    same axis."""
    return value if value.upper().startswith("V") else f"V{value}"


def assert_revision_matches_plan_and_header(
    *, filename_revision: str, file_header_product_version: str
) -> None:
    """D1 — 'Current Early is V07B; if a granule carries another revision,
    stop and report rather than blending.' Two checks, in order:

    1. The filename-parsed revision must equal the plan's pinned literal
       (`PINNED_GRANULE_REVISION_PER_PLAN`) — not merely 'whatever the
       first granule happened to show', which `assert_contract_consistent`
       alone would allow.
    2. The filename-parsed revision must independently agree with the
       granule's own embedded `FileHeader.ProductVersion` (normalised) —
       the revision must never be trusted from the filename alone."""
    if filename_revision != PINNED_GRANULE_REVISION_PER_PLAN:
        raise ImergRevisionMismatchError(
            f"observed granule revision {filename_revision!r} (from its "
            f"filename) != the D1-pinned {PINNED_GRANULE_REVISION_PER_PLAN!r} "
            "— stop and report rather than blending (D1)"
        )
    normalized_header = _normalize_product_version(file_header_product_version)
    if normalized_header != filename_revision:
        raise ImergReadContractError(
            f"filename revision {filename_revision!r} disagrees with the "
            f"granule's own embedded FileHeader.ProductVersion "
            f"{file_header_product_version!r} (normalised {normalized_header!r}) "
            "— revision must not be inferred from the filename alone (D1)"
        )


def assert_contract_consistent(
    observed: ImergReadContract, *, frozen: ImergReadContract
) -> None:
    """D1 — 'freeze the whole read contract on first granule and assert it
    on every subsequent one'. The revision mismatch gets its own, more
    specific error (`ImergRevisionMismatchError`) because it is the one
    field this session's own probe already proved is NOT stable across the
    plan's own write-day (see the module docstring)."""
    if observed.granule_revision != frozen.granule_revision:
        raise ImergRevisionMismatchError(
            f"granule revision {observed.granule_revision!r} != frozen "
            f"{frozen.granule_revision!r} — stop and report rather than "
            "blending mixed revisions into one bundle (D1)"
        )
    if observed != replace(frozen, granule_revision=observed.granule_revision):
        raise ImergReadContractError(
            "observed read contract differs from the frozen one on a field "
            "other than revision (D1) — refusing to blend"
        )


# --- storage layout ---


def imerg_early_root(data_root: Path) -> Path:
    return data_root / "imerg_early"


def imerg_raw_dir(data_root: Path) -> Path:
    return imerg_early_root(data_root) / "raw"


def granule_artifact_path(data_root: Path, *, filename: str) -> Path:
    return imerg_raw_dir(data_root) / filename


def acquisition_manifest_path(data_root: Path) -> Path:
    """D10 — the acquisition manifest is PERMANENT; the raw granules are
    disposable. It therefore lives as a SIBLING of `raw/`, never inside it —
    discarding the raw directory must never take the manifest with it."""
    return imerg_early_root(data_root) / "acquisition_manifest.json"


# --- D9 T1->T2 handoff — the acquisition manifest ---


class AcquisitionCompleteness(StrEnum):
    """Distinguishes T1's one-granule D1 contract PROBE from a genuine
    window acquisition — the finding that a probe manifest could be
    mistaken for a complete acquisition (D9). Never a bool: a partial
    (interrupted/resumed) retrieval is a third, distinct state, not
    'complete or not'."""

    PROBE = "PROBE"
    """T1's per-run-scope single-granule D1 contract probe. T2 MUST refuse
    to extract from a PROBE manifest."""
    COMPLETE = "COMPLETE"
    """Every granule of the pinned D5 window (`FIRST_GRANULE_START` ..
    `LAST_GRANULE_START`) was requested and accounted for — retrieved or
    recorded missing. The only completeness T2 may extract from."""
    PARTIAL = "PARTIAL"
    """A retrieval that requested fewer than the full pinned window (an
    interrupted or resumed run, or a deliberately scoped sub-window). Not
    yet usable by T2."""


class ImergAcquisitionManifest(BaseModel):
    """D9 — written by T1, read by T2. T2 must not re-derive any of this by
    re-listing the directory (D9 forbids a second discovery rule)."""

    route: str
    collection_short_name: str
    granule_revision: str
    completeness: AcquisitionCompleteness
    requested_window_start: datetime
    requested_window_end: datetime
    """The window ACTUALLY requested THIS run — a PROBE's is its one
    granule's own start, never the full pinned D5 window. A reader must not
    assume these equal `FIRST_GRANULE_START`/`LAST_GRANULE_START` without
    checking `completeness == COMPLETE` first."""
    box: tuple[int, int, int, int]
    read_contract: dict[str, object]
    requested: int
    retrieved: int
    missing: tuple[str, ...] = ()
    """ISO timestamps of granules requested but absent (404) from the
    archive."""
    granule_checksums: dict[str, str] = {}
    """Filename -> sha256, retained INDEPENDENTLY of the raw granules
    (D10) — this is what survives if the raw archive is discarded."""
    granule_retrieved_at: dict[str, datetime] = {}
    """Filename -> the timestamp this run confirmed the granule present
    (freshly downloaded or already on disk) — D9's 'per-granule retrieval
    timestamps'."""
    retrospective: bool
    generated_at: datetime


def write_acquisition_manifest(manifest: ImergAcquisitionManifest, path: Path) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
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


# --- the injected HTTP seam (mirrors era5_acquire.py's CdsClient) ---


@runtime_checkable
class ImergHttpClient(Protocol):
    """The single injected HTTP seam — GES DISC-specific by construction;
    fake implementations satisfy this Protocol for no-network tests."""

    def list_directory(self, url: str) -> str: ...
    def download_to_path(self, *, url: str, target: Path) -> None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class RealImergHttpClient:
    """`.netrc`-based Earthdata Login auth: `requests` looks up the
    `urs.earthdata.nasa.gov` entry automatically on the redirect GES DISC
    issues, exactly as `curl -n` does — no credential ever appears in this
    module's own code."""

    timeout_seconds: float = 60.0

    def list_directory(self, url: str) -> str:
        import requests

        try:
            response = requests.get(url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to list {url}: {exc}") from exc
        if response.status_code == 404:  # noqa: PLR2004 - HTTP status literal
            raise ImergGranuleMissingError(f"directory not found: {url}")
        if response.status_code in (401, 403):
            raise ImergCredentialsError(
                f"Earthdata Login rejected the request for {url} "
                f"(status {response.status_code})"
            )
        if response.status_code >= 500:  # noqa: PLR2004 - HTTP status literal
            raise ImergTransientError(f"{url} returned status {response.status_code}")
        if response.status_code != 200:  # noqa: PLR2004 - HTTP status literal
            raise ImergRequestFailedError(
                f"{url} returned unexpected status {response.status_code}"
            )
        return response.text

    def download_to_path(self, *, url: str, target: Path) -> None:
        import requests

        try:
            response = requests.get(url, timeout=self.timeout_seconds, stream=True)
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to download {url}: {exc}") from exc
        if response.status_code == 404:  # noqa: PLR2004 - HTTP status literal
            raise ImergGranuleMissingError(f"granule not found: {url}")
        if response.status_code in (401, 403):
            raise ImergCredentialsError(
                f"Earthdata Login rejected the download for {url} "
                f"(status {response.status_code})"
            )
        if response.status_code >= 500:  # noqa: PLR2004 - HTTP status literal
            raise ImergTransientError(f"{url} returned status {response.status_code}")
        if response.status_code != 200:  # noqa: PLR2004 - HTTP status literal
            raise ImergRequestFailedError(
                f"{url} returned unexpected status {response.status_code}"
            )
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
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    frozen_contract: ImergReadContract | None,
    max_attempts: int = 5,
    backoff_base_seconds: float = 2.0,
) -> tuple[Path, str, ImergReadContract]:
    """List the day directory to resolve this granule's actual filename
    (its revision letter is unknowable in advance), download it, observe
    its D1 read contract (asserting consistency against `frozen_contract`
    when one is already established), checksum it, and publish atomically.
    Retries transient failures with exponential backoff; raises
    `ImergGranuleMissingError` immediately (never retried) on a 404."""
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


# --- window retrieval (bulk — a tested library function, never wired to
# this module's CLI this run: per-run scope stops at the projection gate) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class WindowRetrievalReport:
    requested: int
    retrieved: int
    missing: tuple[str, ...]
    """ISO timestamps of granules the archive does not (yet) have."""


def retrieve_window(
    granule_starts: Iterator[datetime],
    *,
    client: ImergHttpClient,
    data_root: Path,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> tuple[WindowRetrievalReport, ImergReadContract | None, dict[str, str]]:
    """D9/D10 — retrieve every granule in `granule_starts`, freezing D1's
    read contract on the first one retrieved and asserting it on every
    subsequent one; missing granules (404) are counted, never fatal (D4:
    they become an hour with fewer than two contributing granules
    downstream, in T2). Writes the PERMANENT acquisition manifest (D9/D10)
    before returning, whenever at least one granule was retrieved — this is
    the ONE place a completed (or partial) window retrieval is recorded;
    `run_probe` below writes its own, distinctly-marked PROBE manifest.
    Returns (report, frozen contract or None if nothing was retrieved,
    filename -> sha256 for every retrieved granule)."""
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
        granule = ImergGranuleId(start=start)
        try:
            path, sha256, contract = acquire_granule(
                granule,
                client=client,
                data_root=data_root,
                clock=clock,
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
        requested=requested, retrieved=retrieved, missing=tuple(missing)
    )
    if frozen is not None and first_start is not None and last_start is not None:
        completeness = (
            AcquisitionCompleteness.COMPLETE
            if (
                requested == EXPECTED_GRANULE_COUNT
                and first_start == FIRST_GRANULE_START
                and last_start == LAST_GRANULE_START
            )
            else AcquisitionCompleteness.PARTIAL
        )
        manifest = ImergAcquisitionManifest(
            route=ROUTE,
            collection_short_name=COLLECTION_SHORT_NAME,
            granule_revision=frozen.granule_revision,
            completeness=completeness,
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
        )
        write_acquisition_manifest(manifest, acquisition_manifest_path(data_root))
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
    """D10 — 'decide by MEASUREMENT': one granule's real size times the
    window's granule count, reported BEFORE any bulk retrieval. Never
    restate the projection as a fact independent of the measured size
    (D10) — this function's only inputs are the measured probe size and the
    measured free-disk figure."""
    total = probe_granule_bytes * projected_granule_count
    return DiskProjection(
        probe_granule_bytes=probe_granule_bytes,
        projected_granule_count=projected_granule_count,
        projected_total_bytes=total,
        free_disk_bytes=free_disk_bytes,
        fits=total < free_disk_bytes,
    )


# --- CLI: the D1 contract probe only (per-run scope; bulk retrieval is the
# owner's call and is not wired here) ---


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
    parser.add_argument("--out", type=Path, default=None)
    return parser


def run_probe(
    *,
    granule_start: datetime,
    client: ImergHttpClient,
    data_root: Path,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    free_disk_bytes: int,
) -> dict[str, object]:
    """T1's one permitted network action this run: acquire exactly ONE
    granule, freeze its read contract, write the acquisition manifest for
    it, and report the D10 disk projection. Never retrieves a second
    granule."""
    granule = ImergGranuleId(start=granule_start)
    path, sha256, contract = acquire_granule(
        granule,
        client=client,
        data_root=data_root,
        clock=clock,
        sleep=sleep,
        frozen_contract=None,
    )
    probe_bytes = path.stat().st_size
    projection = compute_projection(
        probe_granule_bytes=probe_bytes, free_disk_bytes=free_disk_bytes
    )
    probed_at = clock()
    manifest = ImergAcquisitionManifest(
        route=ROUTE,
        collection_short_name=COLLECTION_SHORT_NAME,
        granule_revision=contract.granule_revision,
        completeness=AcquisitionCompleteness.PROBE,
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
    )
    write_acquisition_manifest(manifest, acquisition_manifest_path(data_root))
    log.info(
        "imerg.acquire.probe_complete",
        granule=granule_start.isoformat(),
        filename=path.name,
        bytes=probe_bytes,
        projected_total_bytes=projection.projected_total_bytes,
        fits=projection.fits,
    )
    return {
        "granule_start": granule_start.isoformat(),
        "filename": path.name,
        "sha256": sha256,
        "bytes": probe_bytes,
        "read_contract": contract.as_manifest_dict(),
        "projection": asdict(projection),
    }


def parse_cli_utc_timestamp(value: str) -> datetime:
    """Boundary parsing for `--granule-start`: reject a naive timestamp
    outright rather than silently interpreting it in the host's local
    timezone (which would probe the wrong UTC granule)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(
            f"--granule-start {value!r} has no UTC offset — an explicit "
            "offset (e.g. a trailing 'Z' or '+00:00') is required, never "
            "assumed from the host timezone"
        )
    return parsed.astimezone(UTC)


def _nearest_existing_ancestor(path: Path) -> Path:
    """`os.statvfs` needs an existing path; a not-yet-created `--data-root`
    (common on a first run) must not silently fall back to measuring the
    CWD's filesystem — walk up to the nearest existing ancestor instead."""
    candidate = path.resolve()
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return candidate


def main(argv: list[str] | None = None, **kwargs: object) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    granule_start = (
        parse_cli_utc_timestamp(args.granule_start)
        if args.granule_start
        else (datetime.now(UTC) - timedelta(hours=5)).replace(
            minute=0 if datetime.now(UTC).minute < 30 else 30,
            second=0,
            microsecond=0,
        )
    )
    statvfs_target = _nearest_existing_ancestor(args.data_root)
    statvfs_result = os.statvfs(statvfs_target)
    free_disk_bytes = statvfs_result.f_bavail * statvfs_result.f_frsize
    try:
        result = run_probe(
            granule_start=granule_start,
            client=kwargs.get("client") or RealImergHttpClient(),  # type: ignore[arg-type]
            data_root=args.data_root,
            clock=kwargs.get("clock") or (lambda: datetime.now(UTC)),  # type: ignore[arg-type]
            sleep=kwargs.get("sleep") or __import__("time").sleep,  # type: ignore[arg-type]
            free_disk_bytes=free_disk_bytes,
        )
    except ImergAcquisitionError as exc:
        log.error(
            "imerg.acquire.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return 3
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, default=str))
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
