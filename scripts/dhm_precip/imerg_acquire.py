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
    grid = f["Grid"]  # type: ignore[index]
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
                f"COMPLETE acquisition manifest at {path} — GES DISC revised "
                "the archive; resolve that before replacing the record (D10)"
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


def read_contract_violations(
    raw: dict[str, object], *, granule_revision: str
) -> tuple[str, ...]:
    """D1 — a recorded contract must SATISFY the frozen contract, not merely
    carry its field NAMES: a record whose every value read "recorded" derived
    as COMPLETE under a presence-only check. ⛔ `ImergReadContract` is the
    judge (the JSON round-trip turns its tuples into lists)."""
    if set(raw) != set(_READ_CONTRACT_FIELDS):
        return (
            f"read_contract fields {sorted(raw)} != D1's "
            f"{sorted(_READ_CONTRACT_FIELDS)}",
        )
    try:
        contract = ImergReadContract(
            **{  # type: ignore[arg-type]
                key: tuple(value) if isinstance(value, list) else value
                for key, value in raw.items()
            }
        )
    except (ImergReadContractError, TypeError, ValueError) as exc:
        return (f"read_contract does not satisfy the D1 read contract: {exc}",)
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
    """D1/D7 — the pins every IMERG record must carry, checked identically by
    the record and by the bundle's predicate. ⛔ Constants, never parameters."""
    v: list[str] = []
    if route != ROUTE:
        v.append(f"route {route!r} != {ROUTE!r}")
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
            manifest.read_contract, granule_revision=manifest.granule_revision
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

    @staticmethod
    def _raise_for_status(status: int, *, url: str) -> None:
        """One status ladder: 404 missing (D4 counts it), 401/403 credentials
        (exit 2), 5xx retryable."""
        if status == 404:  # noqa: PLR2004 - HTTP status literal
            raise ImergGranuleMissingError(f"not found: {url}")
        if status in (401, 403):
            raise ImergCredentialsError(
                f"Earthdata Login rejected {url} (status {status})"
            )
        if status >= 500:  # noqa: PLR2004 - HTTP status literal
            raise ImergTransientError(f"{url} returned status {status}")
        if status != 200:  # noqa: PLR2004 - HTTP status literal
            raise ImergRequestFailedError(f"{url} returned unexpected status {status}")

    def list_directory(self, url: str) -> str:
        import requests

        try:
            response = requests.get(url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to list {url}: {exc}") from exc
        self._raise_for_status(response.status_code, url=url)
        return response.text

    def download_to_path(self, *, url: str, target: Path) -> None:
        import requests

        try:
            response = requests.get(url, timeout=self.timeout_seconds, stream=True)
        except requests.RequestException as exc:
            raise ImergTransientError(f"failed to download {url}: {exc}") from exc
        self._raise_for_status(response.status_code, url=url)
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
