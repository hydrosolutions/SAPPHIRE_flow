"""Plan 174 (M-A5) task 4a — D7's two identities, the identity-addressed
bundle publication, and the extraction manifest.

**Publication model — P1-P6 (redesigned 2026-08-17, grill-me).** Four
independent review rounds each found a blocker in an "adopt if the manifest
reconciles" / `CURRENT`-pointer / quarantine design, and every fix relocated
the defect one layer down instead of removing it. The redesign removes
possibilities instead of adding checks:

- **P1/P1a** — the published directory is per-run UNIQUE:
  `<NNNN>-<extraction_identity>/`, with `NNNN` allocated by
  `mkdir(exist_ok=False)` (never scan-then-create, which races). `os.replace`
  therefore never faces a non-empty target — there is no rename, no
  quarantine, no `.orphan-<n>`, no window.
- **P2** — there is no `CURRENT` pointer. Nothing in production reads one.
  Discovery is "the highest `NNNN` whose manifest validates" — a documented
  convention (P6), not code here.
- **P3** — `extraction_identity` is a LABEL (directory name + manifest
  provenance), never a lookup key.
- **P4/P4a** — validation moves INSIDE `publish_bundle`, applying EXACTLY
  the predicate discovery would apply (including `payload_sha256s`
  reconciliation), so publication and discovery can never drift apart.
- **P5** — the manifest hashes the payload artefacts only, never itself.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - pydantic must resolve this at runtime
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from scripts.dhm_precip.domain_types import (
    DatumReconciliationStatus,
    ExtractionOperator,
    OrographySource,
    SensitivityDeltaUnit,
    SensitivityScope,
    SensitivityStatistic,
    VerticalDatum,
)
from scripts.dhm_precip.era5_errors import (
    Era5StorageError,
    ExtractionPostConditionError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import numpy as np

log = structlog.get_logger(__name__)

# D7: the SET of delta statistics D1a computes — part of the identity so a
# future change to WHAT is computed forces regeneration even without a
# version bump, mirroring `transform_identity`'s own inputs.
DELTA_STATISTICS: tuple[str, ...] = ("absolute", "ratio", "sign_agreement")
EXTRACTION_CODE_VERSION = "1"

# D9's payload set, EXACTLY — the artefacts the extraction manifest hashes
# beside itself (P5: never its own hash).
D9_PAYLOAD_FILES: tuple[str, ...] = (
    "series_nearest.nc",
    "series_bilinear.nc",
    "station_grid_elevation.csv",
    "operator_sensitivity.csv",
)

# --- D9/D7 — the frozen on-disk encoding spec for the points bundle. ---
#
# MAJOR (2026-08-17 review) — this used to live in `extract_era5.py`, where
# the WRITER (`_write_series_netcdf`/`_xarray_encoding`) partly duplicated it
# as separate hard-coded literals (`_FillValue`, the semantic timezone attr)
# instead of reading them from here, so an identity field could change
# without the on-disk bytes changing to match (violating P7: "an identity
# may hash only inputs that are actually read"). Moved HERE — the one module
# that also validates the reopened bundle against it (`_assert_series_schema`
# below) — so the writer, the identity and the validator all read the SAME
# spec; nothing can drift between them.
_POINTS_CHUNK_STATIONS = 1
_POINTS_CHUNK_HOURS = 8760
POINTS_SEMANTIC_TIMEZONE = "UTC"
POINTS_TIME_UNITS = "hours since 1970-01-01 00:00:00"

POINTS_OUTPUT_ENCODING_SPEC: dict[str, dict[str, object]] = {
    "precipitation_mm_per_h": {
        "dtype": "float32",
        "zlib": True,
        "complevel": 4,
        "chunk_stations": _POINTS_CHUNK_STATIONS,
        "chunk_hours": _POINTS_CHUNK_HOURS,
        "_FillValue": "NaN",
    },
    "valid_time": {
        "units": POINTS_TIME_UNITS,
        "dtype": "int64",
        "semantic_timezone_attr": POINTS_SEMANTIC_TIMEZONE,
    },
    "station": {"dtype": "S1", "fixed_length": True},
}


def points_xarray_encoding(shape: tuple[int, int]) -> dict[str, dict[str, object]]:
    """Translate the frozen `POINTS_OUTPUT_ENCODING_SPEC` into the encoding
    dict the pinned xarray/h5netcdf encoder takes, clamping the declared
    chunk policy to the array actually being written. The ONE place that
    reads the spec to produce on-disk bytes — `extraction_identity`'s
    `output_encoding` input and this function must never diverge, because
    they now read the same dict."""
    n_stations, n_hours = shape
    precip_spec = POINTS_OUTPUT_ENCODING_SPEC["precipitation_mm_per_h"]
    time_spec = POINTS_OUTPUT_ENCODING_SPEC["valid_time"]
    station_spec = POINTS_OUTPUT_ENCODING_SPEC["station"]
    if not station_spec.get("fixed_length"):
        # P7 — a declared-but-unread identity input is exactly the drift
        # this move exists to remove; `fixed_length=False` has no
        # implementation here, so refuse to write under a spec claiming it.
        raise ExtractionPostConditionError(
            "POINTS_OUTPUT_ENCODING_SPEC['station']['fixed_length'] is "
            "False, but only a fixed-length station encoding (dtype='S1') "
            "is implemented (D9)"
        )
    fill_value = float(precip_spec["_FillValue"])  # type: ignore[arg-type]
    return {
        "precipitation_mm_per_h": {
            "dtype": precip_spec["dtype"],
            "zlib": precip_spec["zlib"],
            "complevel": precip_spec["complevel"],
            "chunksizes": (
                min(precip_spec["chunk_stations"], n_stations),  # type: ignore[arg-type]
                min(precip_spec["chunk_hours"], n_hours),  # type: ignore[arg-type]
            ),
            "_FillValue": fill_value,
        },
        "valid_time": {"units": time_spec["units"], "dtype": time_spec["dtype"]},
        "station": {"dtype": station_spec["dtype"]},
    }


_RUN_NUMBER_WIDTH = 4
_MAX_RUN_NUMBER = 10**_RUN_NUMBER_WIDTH - 1


def points_root(data_root: Path) -> Path:
    return data_root / "era5_land" / "points"


def staging_dir(data_root: Path, *, identity: str, token: str) -> Path:
    """MAJOR (2026-08-17 review) — a shared `.staging/<identity>` path let
    two concurrent runs under the SAME identity destructively `rmtree` and
    interleave each other's staged payload. `token` makes the directory
    unique PER INVOCATION, not merely per identity, so no two runs — same
    identity or not — ever share one."""
    return points_root(data_root) / ".staging" / f"{identity}--{token}"


def published_dir(data_root: Path, *, run_number: int, identity: str) -> Path:
    """P1 — the per-run unique published directory name: a zero-padded run
    number joined to the (label-only, P3) identity. Two different inputs
    always sort by run order; the SAME inputs re-run still get a fresh
    number (P1a), so this can never collide with an existing directory."""
    return points_root(data_root) / f"{run_number:0{_RUN_NUMBER_WIDTH}d}-{identity}"


def _reservation_path(data_root: Path, *, run_number: int) -> Path:
    return points_root(data_root) / f".run-{run_number:0{_RUN_NUMBER_WIDTH}d}.reserve"


def _taken_run_numbers(data_root: Path) -> list[int]:
    """Both PUBLISHED directories and outstanding `.run-<NNNN>.reserve`
    markers claim a run number. A `.reserve` left by a crashed run (the
    process died between reserving the number and `mkdir`ing the
    identity-labelled directory) permanently retires that number rather
    than leaving it free for a future scan to reclaim — consistent with
    P1's own rule that a number, once claimed, is never reused."""
    root = points_root(data_root)
    if not root.exists():
        return []
    numbers: list[int] = []
    for child in root.iterdir():
        name = child.name
        if child.is_dir():
            if name == ".staging":
                continue
            prefix = name.split("-", 1)[0]
            if prefix.isdigit():
                numbers.append(int(prefix))
        elif name.startswith(".run-") and name.endswith(".reserve"):
            middle = name[len(".run-") : -len(".reserve")]
            if middle.isdigit():
                numbers.append(int(middle))
    return numbers


def _reserve_run_number(data_root: Path) -> int:
    """BLOCKER (2026-08-17 review) — the previous allocator reserved a
    number by `mkdir`ing the IDENTITY-LABELLED target directory
    (`<NNNN>-<identity>`) directly. Two concurrent runs racing for the SAME
    `NNNN` under DIFFERENT identities then both succeeded — `mkdir` on
    `0000-a` and `mkdir` on `0000-b` never contend, so both were assigned
    run number 0000: an ambiguous, identity-dependent order, exactly what
    P1a promises never happens.

    Reservation must therefore be IDENTITY-INDEPENDENT: an exclusively
    created `.run-<NNNN>.reserve` marker, keyed on the number alone, is the
    one atomic object every racing identity contends for regardless of what
    it will eventually publish under. `os.open(..., O_CREAT | O_EXCL)` is
    atomic at the OS level; the loser of a race hits `FileExistsError` on
    the reservation file itself and simply retries the next candidate — the
    scan below is only an optimisation (start just past the highest number
    seen), never the source of truth for which numbers are taken."""
    root = points_root(data_root)
    root.mkdir(parents=True, exist_ok=True)
    candidate = max(_taken_run_numbers(data_root), default=-1) + 1
    while candidate <= _MAX_RUN_NUMBER:
        reservation = _reservation_path(data_root, run_number=candidate)
        try:
            fd = os.open(str(reservation), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            candidate += 1
            continue
        except OSError as exc:
            raise Era5StorageError(
                f"failed to reserve run number {candidate} at {reservation}: {exc}"
            ) from exc
        os.close(fd)
        return candidate
    raise Era5StorageError(f"no free run number <= {_MAX_RUN_NUMBER} under {root}")


def allocate_published_dir(data_root: Path, *, identity: str) -> Path:
    """P1a — allocate the next free run number through the
    IDENTITY-INDEPENDENT atomic reservation above, then `mkdir(exist_ok=
    False)` the identity-labelled directory that number publishes at.
    Because the NUMBER itself is exclusively reserved first, two different
    identities can never be allocated the same one — the defect an
    identity-scoped `mkdir` race left open (2026-08-17 review).

    The reservation marker is deliberately left in place forever, even
    after the directory is created: removing it would re-open the exact
    same race for a LATE-arriving competitor for the same number (its
    `mkdir` target differs by identity, so it would not collide with the
    now-published directory either — the marker, not the directory, is the
    one thing every identity contends for, so it must stay live for as long
    as the number could ever be asked for again)."""
    run_number = _reserve_run_number(data_root)
    target = published_dir(data_root, run_number=run_number, identity=identity)
    try:
        target.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to create reserved published directory {target}: {exc}"
        ) from exc
    return target


def manifest_filename() -> str:
    return "extraction_manifest.json"


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


_EXPECTED_VALUE_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "operator_id",
        "coordinate_table_sha256",
        "source_sha256s",
        "orography_identity",
        "seasons",
        "wet_threshold_mm_per_h",
        "wet_threshold_side",
        "zero_policy",
        "quantile_definition",
        "quantile_grid",
        "station_elevation_datum",
        "orography_elevation_datum",
        "output_dtype",
        "output_encoding",
    }
)
# P7 (MAJOR, 2026-08-17 review) — `output_format` and `delta_statistics`
# moved OUT of `_EXPECTED_VALUE_INPUT_KEYS`. Neither is actually READ by any
# writer or computation: `output_format` is a descriptive label (the writer
# always calls `ds.to_netcdf(..., engine="h5netcdf", ...)` regardless of its
# value — there is no second implemented format to branch on), and
# `delta_statistics` is `era5_extract.build_operator_sensitivity_table`'s
# own fixed, hardcoded output shape, never read back from anywhere to
# decide what gets computed. P7's standing obligation ("a test must change
# the input AND change an output, or the field does not belong in the
# identity") cannot be satisfied for either — exactly P7a's own escape
# hatch: they force regeneration on a declared-but-code-level change
# (mirroring `output_schema_version`/`extraction_code_version`), which is a
# legitimate reason to hash them, just not as VALUE inputs.
_EXPECTED_INVALIDATION_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "output_schema_version",
        "extraction_code_version",
        "output_format",
        "delta_statistics",
    }
)


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractionIdentityInputs:
    """D7/P7a — the ONE canonical, typed snapshot of every input
    `extraction_identity` hashes.

    MAJOR (2026-08-17 review) — the identity used to disappear once
    computed: `extraction_identity` built its canonical JSON inline from an
    untyped local dict and threw it away, so nothing downstream (including
    `ExtractionManifest`, which never recorded these fields at all) could
    recompute or even INTERPRET the digest — seasons, thresholds,
    `zero_policy`, the quantile grid, output format/encoding and both datum
    inputs were hashed but nowhere else visible. This type is now the ONE
    source both `extraction_identity` (to compute the digest) and
    `ExtractionManifest.identity_inputs` (to record the inputs alongside
    it) read, via `canonical_payload()`/`digest()`, so a manifest reader can
    recompute (`recompute_extraction_identity`) and interpret the digest
    without having been the writer."""

    operator_id: str
    coordinate_table_sha256: str
    source_sha256s: tuple[str, ...]
    orography_identity: str
    jjas_months: tuple[int, ...]
    djf_months: tuple[int, ...]
    mam_months: tuple[int, ...]
    on_months: tuple[int, ...]
    wet_threshold_mm_per_h: float
    wet_threshold_side: str
    zero_policy: str
    quantile_definition: str
    quantile_grid: tuple[float, ...]
    station_elevation_datum: str
    orography_elevation_datum: str
    output_schema_version: str
    output_format: str
    output_dtype: str
    output_encoding: Mapping[str, object]
    extraction_code_version: str = EXTRACTION_CODE_VERSION

    def canonical_payload(self) -> dict[str, object]:
        """P7a — split into two explicitly labelled halves. `value_inputs`
        are inputs the computation actually reads (P7's standing
        obligation: a test must change the input AND change an output);
        `invalidation_inputs` (`output_schema_version`/
        `extraction_code_version`/`output_format`/`delta_statistics`) exist
        ONLY to force regeneration on a behaviour-neutral bump, so "changing
        this input must change an output" is unsatisfiable for them by
        design — labelling them makes that exemption visible instead of
        silently smuggling a value-determining field past P7's rule.
        `output_format` and `delta_statistics` moved here (MAJOR,
        2026-08-17 review): neither is read by any writer or computation
        (the writer always emits `h5netcdf`; the sensitivity table always
        computes exactly `DELTA_STATISTICS`'s fixed set), so keeping them
        under `value_inputs` asserted a dependency the code does not have."""
        value_inputs: dict[str, object] = {
            "operator_id": self.operator_id,
            "coordinate_table_sha256": self.coordinate_table_sha256,
            "source_sha256s": sorted(self.source_sha256s),
            "orography_identity": self.orography_identity,
            "seasons": {
                "jjas_months": list(self.jjas_months),
                "djf_months": list(self.djf_months),
                "mam_months": list(self.mam_months),
                "on_months": list(self.on_months),
            },
            "wet_threshold_mm_per_h": self.wet_threshold_mm_per_h,
            "wet_threshold_side": self.wet_threshold_side,
            "zero_policy": self.zero_policy,
            "quantile_definition": self.quantile_definition,
            "quantile_grid": list(self.quantile_grid),
            "station_elevation_datum": self.station_elevation_datum,
            "orography_elevation_datum": self.orography_elevation_datum,
            "output_dtype": self.output_dtype,
            "output_encoding": dict(self.output_encoding),
        }
        invalidation_inputs: dict[str, object] = {
            "output_schema_version": self.output_schema_version,
            "extraction_code_version": self.extraction_code_version,
            "output_format": self.output_format,
            "delta_statistics": list(DELTA_STATISTICS),
        }
        return {
            "value_inputs": value_inputs,
            "invalidation_inputs": invalidation_inputs,
        }

    def digest(self) -> str:
        canonical = _canonical_json(self.canonical_payload())
        return hashlib.sha256(canonical.encode()).hexdigest()


def recompute_extraction_identity(identity_inputs: Mapping[str, object]) -> str:
    """The recomputation half of D7's identity: given the canonical
    `{"value_inputs": ..., "invalidation_inputs": ...}` payload a manifest
    records (`ExtractionManifest.identity_inputs`), recompute the digest a
    reader can compare against `ExtractionManifest.extraction_identity`
    WITHOUT needing to have been the writer — the gap the previous
    implementation left open (2026-08-17 review)."""
    return hashlib.sha256(_canonical_json(identity_inputs).encode()).hexdigest()


def assert_identity_inputs_complete(identity_inputs: Mapping[str, object]) -> None:
    """MAJOR (2026-08-17 review) — a published bundle must carry the
    COMPLETE canonical snapshot its `extraction_identity` was computed from,
    not merely the digest string. Structural completeness (every declared
    input key present) is enforced on every publication (`publish_bundle`);
    digest-equality against `extraction_identity` is left to callers that
    want it (`recompute_extraction_identity`) rather than forced here,
    because `extraction_identity` is deliberately a LABEL (P3) — some
    callers (tests exercising publication MECHANICS, not identity fidelity)
    legitimately use a short human-readable label that was never meant to
    recompute from anything."""
    if set(identity_inputs) != {"value_inputs", "invalidation_inputs"}:
        raise ExtractionPostConditionError(
            "extraction manifest's identity_inputs has top-level key(s) "
            f"{sorted(identity_inputs)}, expected exactly "
            "{'value_inputs', 'invalidation_inputs'} (D7/P7a)"
        )
    value_inputs = identity_inputs["value_inputs"]
    invalidation_inputs = identity_inputs["invalidation_inputs"]
    if not isinstance(value_inputs, dict) or not isinstance(invalidation_inputs, dict):
        raise ExtractionPostConditionError(
            "extraction manifest's identity_inputs.value_inputs/"
            "invalidation_inputs must both be objects (D7/P7a)"
        )
    missing_value = _EXPECTED_VALUE_INPUT_KEYS - set(value_inputs)
    if missing_value:
        raise ExtractionPostConditionError(
            "extraction manifest's identity_inputs.value_inputs is missing "
            f"key(s) {sorted(missing_value)} (D7/P7a) — the digest cannot "
            "be interpreted downstream without them"
        )
    missing_invalidation = _EXPECTED_INVALIDATION_INPUT_KEYS - set(invalidation_inputs)
    if missing_invalidation:
        raise ExtractionPostConditionError(
            "extraction manifest's identity_inputs.invalidation_inputs is "
            f"missing key(s) {sorted(missing_invalidation)} (D7/P7a)"
        )


def extraction_identity(
    *,
    operator_id: str,
    coordinate_table_sha256: str,
    source_sha256s: Sequence[str],
    orography_identity: str,
    jjas_months: Sequence[int],
    djf_months: Sequence[int],
    mam_months: Sequence[int],
    on_months: Sequence[int],
    wet_threshold_mm_per_h: float,
    wet_threshold_side: str,
    zero_policy: str,
    quantile_definition: str,
    quantile_grid: Sequence[float],
    station_elevation_datum: str,
    orography_elevation_datum: str,
    output_schema_version: str,
    output_format: str,
    output_dtype: str,
    output_encoding: Mapping[str, object],
    extraction_code_version: str = EXTRACTION_CODE_VERSION,
) -> str:
    """D7 — sha256(canonical-JSON of every VALUE-AFFECTING input), via
    `ExtractionIdentityInputs.digest()`. A version bump alone
    (`extraction_code_version`) forces regeneration, mirroring
    `era5_manifest.transform_identity`."""
    return ExtractionIdentityInputs(
        operator_id=operator_id,
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s=tuple(source_sha256s),
        orography_identity=orography_identity,
        jjas_months=tuple(jjas_months),
        djf_months=tuple(djf_months),
        mam_months=tuple(mam_months),
        on_months=tuple(on_months),
        wet_threshold_mm_per_h=wet_threshold_mm_per_h,
        wet_threshold_side=wet_threshold_side,
        zero_policy=zero_policy,
        quantile_definition=quantile_definition,
        quantile_grid=tuple(quantile_grid),
        station_elevation_datum=station_elevation_datum,
        orography_elevation_datum=orography_elevation_datum,
        output_schema_version=output_schema_version,
        output_format=output_format,
        output_dtype=output_dtype,
        output_encoding=output_encoding,
        extraction_code_version=extraction_code_version,
    ).digest()


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractionManifest:
    """D9's `extraction_manifest.json` — the payload artefacts' sha256s
    ONLY, never its own hash (P5)."""

    orography_identity: str
    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    source_sha256s: tuple[str, ...]
    payload_sha256s: dict[str, str]
    """Filename (relative to this identity directory) -> sha256."""
    orography_spec: dict[str, object]
    """EVERY `OrographySpec` field (D3a), not a four-field excerpt — the
    route is provenance, and a partially serialised route cannot be
    reconstructed."""
    orography_source_record: dict[str, object]
    """The materialised record: every downloaded file's path, sha256 and
    byte size, the derived raster's path/sha256, and both identities."""
    accumulation_diagnostic: dict[str, object]
    """The whole cited record (D5.2), including `terminal_hour`,
    `sample_size_days` and the injected-clock `recorded_at`."""
    station_accounting: dict[str, dict[str, dict[str, object]]] = field(
        default_factory=dict[str, dict[str, dict[str, object]]]
    )
    """D11 — operator id -> station -> {n_hours, n_finite, n_nan,
    first_nan_valid_time, last_nan_valid_time}. "The counts are emitted in
    the manifest either way" (D11.2), so they are reported for BOTH
    operators, including bilinear's counted missing-neighbour NaNs
    (D11.3)."""
    identity_inputs: dict[str, object] = field(default_factory=dict)
    """MAJOR (2026-08-17 review) — `ExtractionIdentityInputs.canonical_
    payload()`: the complete `{"value_inputs": ..., "invalidation_inputs":
    ...}` snapshot `extraction_identity` was computed from. Without this the
    digest hashed seasons, thresholds, `zero_policy`, the quantile grid,
    output format/encoding and both datum inputs that disappeared once
    computed — nothing downstream could recompute or even interpret them.
    Structural completeness is enforced on every publication
    (`assert_identity_inputs_complete`, called from `publish_bundle`)."""
    generated_at: datetime


class _ExtractionManifestModel(BaseModel):
    orography_identity: str
    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    source_sha256s: list[str]
    payload_sha256s: dict[str, str]
    orography_spec: dict[str, object]
    orography_source_record: dict[str, object]
    accumulation_diagnostic: dict[str, object]
    station_accounting: dict[str, dict[str, dict[str, object]]] = {}
    identity_inputs: dict[str, object] = {}
    generated_at: datetime


def write_extraction_manifest(manifest: ExtractionManifest, path: Path) -> None:
    model = _ExtractionManifestModel(
        orography_identity=manifest.orography_identity,
        extraction_identity=manifest.extraction_identity,
        operator_id=manifest.operator_id,
        coordinate_table_sha256=manifest.coordinate_table_sha256,
        source_sha256s=list(manifest.source_sha256s),
        payload_sha256s=dict(manifest.payload_sha256s),
        orography_spec=manifest.orography_spec,
        orography_source_record=manifest.orography_source_record,
        accumulation_diagnostic=manifest.accumulation_diagnostic,
        station_accounting=manifest.station_accounting,
        identity_inputs=manifest.identity_inputs,
        generated_at=manifest.generated_at,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2))


def read_extraction_manifest(path: Path) -> ExtractionManifest | None:
    try:
        exists = path.exists()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to stat extraction manifest at {path}: {exc}"
        ) from exc
    if not exists:
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to read extraction manifest at {path}: {exc}"
        ) from exc
    try:
        model = _ExtractionManifestModel.model_validate_json(text)
    except ValueError as exc:
        raise Era5StorageError(
            f"extraction manifest at {path} is unreadable: {exc}"
        ) from exc
    return ExtractionManifest(
        orography_identity=model.orography_identity,
        extraction_identity=model.extraction_identity,
        operator_id=model.operator_id,
        coordinate_table_sha256=model.coordinate_table_sha256,
        source_sha256s=tuple(model.source_sha256s),
        payload_sha256s=dict(model.payload_sha256s),
        orography_spec=model.orography_spec,
        orography_source_record=model.orography_source_record,
        accumulation_diagnostic=model.accumulation_diagnostic,
        station_accounting=model.station_accounting,
        identity_inputs=model.identity_inputs,
        generated_at=model.generated_at,
    )


def checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Era5StorageError(f"failed to checksum {path}: {exc}") from exc
    return digest.hexdigest()


def prepare_staging_dir(data_root: Path, *, identity: str) -> Path:
    """MAJOR (2026-08-17 review) — every call allocates a FRESH,
    per-invocation-unique staging directory (`staging_dir`'s `token`), never
    the old shared `.staging/<identity>` path: two concurrent SAME-identity
    runs could previously `rmtree` and interleave each other's staged
    payload. Because the name is unique, there is nothing stale to delete
    first: `mkdir(exist_ok=False)` fails loudly on the astronomically
    unlikely token collision rather than silently reusing another run's
    directory. A staging directory left by a crashed prior run is
    unreferenced garbage under its own unique name — nothing ever looks it
    up again, so (as before) it is never resumed or published, only now it
    is also never deleted by a LATER run (there is no shared name left to
    collide with)."""
    token = uuid.uuid4().hex[:16]
    staging = staging_dir(data_root, identity=identity, token=token)
    try:
        staging.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to create staging directory {staging}: {exc}"
        ) from exc
    return staging


def publish_bundle(
    staged_dir: Path,
    *,
    data_root: Path,
    identity: str,
    expected_station_count: int,
    expected_hour_count: int,
) -> Path:
    """P1/P4 — validate the staged bundle FIRST, refusing to publish on
    failure (P4/P4a: this call applies EXACTLY the predicate discovery
    would apply, including `payload_sha256s` reconciliation — publication
    and discovery share one predicate, never two that can drift). Only on a
    passing validation does it allocate a per-run-unique numbered directory
    (P1a) and move the validated content into it.

    `os.replace` here never faces a non-empty target: `allocate_published_dir`
    reserves the target via `mkdir(exist_ok=False)` immediately before the
    move, so the directory is freshly created and empty, owned by this call
    alone. There is no `CURRENT` pointer (P2), no quarantine and no
    `.orphan-<n>` (P1 — a fresh numbered directory can never collide with a
    prior bundle, so there is nothing to displace), and no adoption of an
    existing bundle (D7.3, cut before this redesign for the same reason:
    three review rounds each found a blocker inside that one branch).
    Every previous bundle is left untouched.
    """
    reopen_and_validate_bundle(
        staged_dir,
        expected_station_count=expected_station_count,
        expected_hour_count=expected_hour_count,
        identity=identity,
    )
    final_dir = allocate_published_dir(data_root, identity=identity)
    try:
        os.replace(staged_dir, final_dir)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to publish bundle from {staged_dir} to {final_dir}: {exc}"
        ) from exc
    log.info(
        "era5_extract.publish.published",
        identity=identity,
        published_dir=str(final_dir),
    )
    return final_dir


# --- D9 payload schema — required columns and legal enum values, shared by
# publication (P4a) and any future discovery reader (P6), so the two can
# never apply a different predicate to the same bundle. ---
#
# BLOCKER (2026-08-17) — the previous validator checked only variable
# presence, station COUNTS and CSV readability: it never checked station
# uniqueness/equality across the two series and the elevation table, the
# elevation/sensitivity CSVs' required columns or enum values, the series'
# dims/dtype/time-axis/encoding/attributes, or that the manifest's own
# `extraction_identity` matches the identity it is being published under. A
# writer that emitted only `{station, marker}` columns still published
# successfully, hashing a malformed payload rather than refusing it.

ELEVATION_REQUIRED_COLUMNS: tuple[str, ...] = (
    "station",
    "lat",
    "lon",
    "grid_lat",
    "grid_lon",
    "grid_i",
    "grid_j",
    "offset_km",
    "station_elev_m",
    "station_elevation_datum",
    "orography_elev_m",
    "orography_elevation_datum",
    "orography_source",
    "orography_product_id",
    "orography_product_version",
    "elev_mismatch_m",
    "datum_reconciled",
    "shared_cell_id",
    "stations_in_cell",
)

SENSITIVITY_REQUIRED_COLUMNS: tuple[str, ...] = (
    "scope",
    "station",
    "season",
    "statistic",
    "quantile",
    "nearest_value",
    "bilinear_value",
    "delta_absolute",
    "delta_unit",
    "ratio",
    "n_hours_common_finite",
    "n_hours_excluded",
    "n_wet_nearest",
    "n_wet_bilinear",
    "sign_agreement_fraction",
)

_VALID_OROGRAPHY_SOURCES = {m.value for m in OrographySource}
_VALID_VERTICAL_DATA = {m.value for m in VerticalDatum}
_VALID_DATUM_RECONCILED = {m.value for m in DatumReconciliationStatus}
_VALID_SENSITIVITY_SCOPES = {m.value for m in SensitivityScope}
_VALID_SENSITIVITY_STATISTICS = {m.value for m in SensitivityStatistic}
_VALID_SENSITIVITY_DELTA_UNITS = {m.value for m in SensitivityDeltaUnit}


# BLOCKER (2026-08-17 review) — publication validated only the manifest's
# `extraction_identity` STRING, never that the provenance/accounting
# sections it is supposed to carry were actually populated: an empty `{}`
# for `orography_spec`/`orography_source_record`/`accumulation_diagnostic`,
# or an empty `station_accounting`, used to publish successfully. These key
# sets mirror exactly what `extract_era5.py`'s real writer populates.
_REQUIRED_OROGRAPHY_SPEC_KEYS: frozenset[str] = frozenset(
    {
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
    }
)
_REQUIRED_OROGRAPHY_SOURCE_RECORD_KEYS: frozenset[str] = frozenset(
    {
        "orography_route_identity",
        "orography_identity",
        "fetched_at",
        "downloaded_files",
        "raster_path",
        "raster_sha256",
        "raster_schema_version",
    }
)
_REQUIRED_ACCUMULATION_DIAGNOSTIC_KEYS: frozenset[str] = frozenset(
    {
        "window_id",
        "source_sha256",
        "reset_hour",
        "terminal_hour",
        "monotone_within_day",
        "sample_size_days",
        "recorded_at",
    }
)
_REQUIRED_STATION_ACCOUNTING_ENTRY_KEYS: frozenset[str] = frozenset(
    {"n_hours", "n_finite", "n_nan", "first_nan_valid_time", "last_nan_valid_time"}
)


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _assert_valid_sha256(value: object, *, label: str) -> None:
    if not isinstance(value, str) or not _SHA256_HEX_RE.match(value):
        raise ExtractionPostConditionError(
            f"{label} is not a valid 64-character hex sha256: {value!r} (D9)"
        )


def _assert_required_sections_present(
    manifest: ExtractionManifest, *, expected_stations: set[str]
) -> None:
    """MAJOR (2026-08-17 review) — this used to check KEY PRESENCE only:
    an `orography_spec` that omitted P7a's `provenance` labels or
    `rejected_candidates`, a `downloaded_files` list that was empty or held
    malformed entries, an `accumulation_diagnostic` recording a FAILING
    result, or a `station_accounting` with an arbitrary operator/station map
    or counts that did not reconcile to `n_hours` — all still published
    with the OLD, presence-only check. A matching `payload_sha256s` hash
    proves the bytes were not tampered AFTER the manifest was written; it
    proves nothing about whether the manifest's own claims were true in the
    first place."""
    missing_spec = _REQUIRED_OROGRAPHY_SPEC_KEYS - set(manifest.orography_spec)
    if missing_spec:
        raise ExtractionPostConditionError(
            f"extraction manifest's orography_spec is missing key(s) "
            f"{sorted(missing_spec)} (D9)"
        )
    # D7 P7a — the machine-verified/operator-attested provenance split must
    # actually be published, not merely computable by the writer.
    provenance = manifest.orography_spec.get("provenance")
    if not isinstance(provenance, dict):
        raise ExtractionPostConditionError(
            "extraction manifest's orography_spec is missing the P7a "
            "'provenance' object (machine_verified_fields/"
            "operator_attested_fields) (D9/P7a)"
        )
    machine_verified = provenance.get("machine_verified_fields")
    operator_attested = provenance.get("operator_attested_fields")
    if not isinstance(machine_verified, list) or not machine_verified:
        raise ExtractionPostConditionError(
            "extraction manifest's orography_spec.provenance."
            "machine_verified_fields must be a non-empty list (D9/P7a)"
        )
    if not isinstance(operator_attested, list) or not operator_attested:
        raise ExtractionPostConditionError(
            "extraction manifest's orography_spec.provenance."
            "operator_attested_fields must be a non-empty list (D9/P7a)"
        )
    if set(machine_verified) & set(operator_attested):
        raise ExtractionPostConditionError(
            "extraction manifest's orography_spec.provenance fields are not "
            "disjoint between machine_verified_fields and "
            "operator_attested_fields (D9/P7a)"
        )
    if "rejected_candidates" not in manifest.orography_spec:
        raise ExtractionPostConditionError(
            "extraction manifest's orography_spec is missing "
            "'rejected_candidates' (D3a)"
        )

    missing_record = _REQUIRED_OROGRAPHY_SOURCE_RECORD_KEYS - set(
        manifest.orography_source_record
    )
    if missing_record:
        raise ExtractionPostConditionError(
            "extraction manifest's orography_source_record is missing "
            f"key(s) {sorted(missing_record)} (D9)"
        )
    # D7/D3a — the derived raster must trace to at least one VALIDATED
    # downloaded source file, not an empty or malformed list.
    downloaded_files = manifest.orography_source_record.get("downloaded_files")
    if not isinstance(downloaded_files, list) or not downloaded_files:
        raise ExtractionPostConditionError(
            "extraction manifest's orography_source_record.downloaded_files "
            "must be a non-empty list — the raster must trace to at least "
            "one downloaded source file (D3a/D7)"
        )
    for entry in downloaded_files:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ExtractionPostConditionError(
                "extraction manifest's orography_source_record."
                f"downloaded_files has a malformed entry: {entry!r} (D7)"
            )
        _assert_valid_sha256(
            entry.get("sha256"),
            label=(
                "extraction manifest's orography_source_record."
                f"downloaded_files[{entry.get('path')!r}].sha256"
            ),
        )
        size_bytes = entry.get("size_bytes")
        if not isinstance(size_bytes, int) or size_bytes <= 0:
            raise ExtractionPostConditionError(
                "extraction manifest's orography_source_record."
                f"downloaded_files[{entry.get('path')!r}].size_bytes must "
                f"be a positive integer, got {size_bytes!r} (D7)"
            )
    _assert_valid_sha256(
        manifest.orography_source_record.get("raster_sha256"),
        label="extraction manifest's orography_source_record.raster_sha256",
    )

    missing_diag = _REQUIRED_ACCUMULATION_DIAGNOSTIC_KEYS - set(
        manifest.accumulation_diagnostic
    )
    if missing_diag:
        raise ExtractionPostConditionError(
            "extraction manifest's accumulation_diagnostic is missing "
            f"key(s) {sorted(missing_diag)} (D9)"
        )
    # D5.2 — publication requires a PASSING diagnostic; the OLD check only
    # required the KEYS to be present, so a recorded FAILING diagnostic
    # (monotone_within_day=False, or an implausible sample size/hour) still
    # published.
    diagnostic = manifest.accumulation_diagnostic
    if diagnostic.get("monotone_within_day") is not True:
        raise ExtractionPostConditionError(
            "extraction manifest's accumulation_diagnostic."
            "monotone_within_day must be True — publication requires a "
            "PASSING diagnostic (D5.2)"
        )
    sample_size_days = diagnostic.get("sample_size_days")
    if not isinstance(sample_size_days, int) or sample_size_days < 1:
        raise ExtractionPostConditionError(
            "extraction manifest's accumulation_diagnostic.sample_size_days "
            f"must be a positive integer, got {sample_size_days!r} (D5.2)"
        )
    for hour_field in ("reset_hour", "terminal_hour"):
        hour_value = diagnostic.get(hour_field)
        if not isinstance(hour_value, int) or not (0 <= hour_value <= 23):
            raise ExtractionPostConditionError(
                f"extraction manifest's accumulation_diagnostic.{hour_field} "
                f"must be an hour-of-day (0-23), got {hour_value!r} (D5.2)"
            )
    _assert_valid_sha256(
        diagnostic.get("source_sha256"),
        label="extraction manifest's accumulation_diagnostic.source_sha256",
    )

    if not manifest.station_accounting:
        raise ExtractionPostConditionError(
            "extraction manifest's station_accounting is empty — D11 "
            "requires per-operator, per-station accounting for BOTH "
            "operators"
        )
    # D11 — the OLD check accepted ANY non-empty operator/station map. The
    # operator keys must be EXACTLY the two D1/D1a operators, and each
    # operator's station set must equal the series' OWN station set exactly
    # (an accounting entry for a station that was never extracted, or a
    # missing entry for one that was, must be refused).
    expected_operator_ids = {str(op) for op in ExtractionOperator}
    if set(manifest.station_accounting) != expected_operator_ids:
        raise ExtractionPostConditionError(
            "extraction manifest's station_accounting operator keys "
            f"{sorted(manifest.station_accounting)} do not equal exactly "
            f"{sorted(expected_operator_ids)} (D11)"
        )
    for operator_id, by_station in manifest.station_accounting.items():
        if set(by_station) != expected_stations:
            raise ExtractionPostConditionError(
                f"extraction manifest's station_accounting[{operator_id!r}] "
                f"station set {sorted(by_station)} does not equal the "
                f"series' station set {sorted(expected_stations)} (D11)"
            )
        for station, entry in by_station.items():
            missing_entry = _REQUIRED_STATION_ACCOUNTING_ENTRY_KEYS - set(entry)
            if missing_entry:
                raise ExtractionPostConditionError(
                    "extraction manifest's station_accounting"
                    f"[{operator_id!r}][{station!r}] is missing key(s) "
                    f"{sorted(missing_entry)} (D11)"
                )
            # D11 — the counted fields must actually reconcile to
            # `n_hours`; an accounting entry that merely carried the
            # required KEYS with inconsistent values previously published
            # undetected. `n_inf` defaults to 0 for a manifest written
            # before it existed (P4a: additive, never a required key here).
            n_hours = entry.get("n_hours")
            n_finite = entry.get("n_finite")
            n_nan = entry.get("n_nan")
            n_inf = entry.get("n_inf", 0)
            if not all(isinstance(v, int) for v in (n_hours, n_finite, n_nan, n_inf)):
                raise ExtractionPostConditionError(
                    "extraction manifest's station_accounting"
                    f"[{operator_id!r}][{station!r}] has non-integer "
                    f"count(s): n_hours={n_hours!r}, n_finite={n_finite!r}, "
                    f"n_nan={n_nan!r}, n_inf={n_inf!r} (D11)"
                )
            if n_finite + n_nan + n_inf != n_hours:  # type: ignore[operator]
                raise ExtractionPostConditionError(
                    "extraction manifest's station_accounting"
                    f"[{operator_id!r}][{station!r}] counts do not "
                    f"reconcile: n_finite({n_finite}) + n_nan({n_nan}) + "
                    f"n_inf({n_inf}) != n_hours({n_hours}) (D11)"
                )


def _assert_columns_present(
    frame: object, *, required: tuple[str, ...], label: str
) -> None:
    missing = [c for c in required if c not in frame.columns]  # type: ignore[attr-defined]
    if missing:
        raise ExtractionPostConditionError(
            f"{label} is missing required column(s) {missing} (D9)"
        )


def _assert_enum_column(
    frame: object, *, column: str, valid: set[str], label: str
) -> None:
    """BLOCKER (2026-08-17 review) — this used to `drop_nulls()` before
    comparing, so a column that was entirely `null` (or partially so) passed
    silently: an enum column is required precisely because every row must
    declare which of a fixed set of values applies, and a null is not one of
    them."""
    values = frame[column].to_list()  # type: ignore[index]
    if any(v is None for v in values):
        raise ExtractionPostConditionError(
            f"{label}.{column} has null value(s) — every row must declare "
            f"one of the enum values {sorted(valid)} (D9)"
        )
    bad = set(values) - valid
    if bad:
        raise ExtractionPostConditionError(
            f"{label}.{column} has value(s) {sorted(bad)} outside the "
            f"declared enum {sorted(valid)} (D9)"
        )


def _assert_series_schema(
    path: Path,
    *,
    expected_station_count: int,
    expected_hour_count: int,
    require_finite: bool,
) -> tuple[set[str], np.ndarray]:
    """Reopen a D9 series file and validate its dims/dtype/time-axis/
    encoding/attributes; returns the declared station set and `valid_time`
    array for the cross-file checks in `reopen_and_validate_bundle`
    (station-set equality, valid_time-axis equality, and — for the PRIMARY
    nearest series only, via `require_finite` — D11.2 completeness).

    BLOCKER (2026-08-17 review, P4) — this used to check only that adjacent
    stamps were exactly hourly; it never checked the axis actually spans the
    declared coverage (`expected_hour_count`), never compared the two
    series' axes for equality, never re-verified the PRIMARY series carries
    no non-finite value on reopen (D11.2 was only ever checked in memory,
    before writing), and never checked the pinned `valid_time`
    units/dtype or the fixed-length `station` encoding on reopen — so a
    severely truncated, mismatched-axis, non-finite-primary or
    variable-length-station bundle still published. The suite's own
    `_write_payload_files` fixture (3 hours) proved the truncation gap: it
    published as a 'valid' bundle under the OLD validator."""
    import numpy as np
    import xarray as xr

    if not path.exists():
        raise ExtractionPostConditionError(
            f"staged bundle missing {path.name} at {path}"
        )
    with xr.open_dataset(path, engine="h5netcdf") as reopened:
        loaded = reopened.load()
        if "precipitation_mm_per_h" not in loaded:
            raise ExtractionPostConditionError(
                f"{path.name} missing 'precipitation_mm_per_h' on reopen"
            )
        var = loaded["precipitation_mm_per_h"]
        if tuple(var.dims) != ("station", "valid_time"):
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has dims {tuple(var.dims)}, "
                "expected ('station', 'valid_time') (D9)"
            )
        if str(var.dtype) != "float32":
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has dtype {var.dtype}, "
                "expected float32 (D9)"
            )
        stations = [str(s) for s in loaded["station"].values]
        if len(stations) != expected_station_count:
            raise ExtractionPostConditionError(
                f"{path.name} has {len(stations)} stations on reopen, "
                f"expected {expected_station_count}"
            )
        if len(set(stations)) != len(stations):
            raise ExtractionPostConditionError(
                f"{path.name} has duplicate station entries: {stations} (D8/D9)"
            )
        valid_time = loaded["valid_time"].values
        # BLOCKER (2026-08-17 review) — this only checked the axis was
        # STRICTLY INCREASING (any positive gap passed). D5.0/D9 declare the
        # axis "exactly hourly with no gaps"; check the actual spacing, not
        # merely its sign.
        if valid_time.size > 1:
            diffs = np.diff(valid_time.astype("datetime64[s]"))
            if not bool(np.all(diffs == np.timedelta64(3600, "s"))):
                raise ExtractionPostConditionError(
                    f"{path.name} 'valid_time' is not exactly hourly with "
                    "no gaps (D9/D5.0)"
                )
        # P7 (MAJOR, 2026-08-17 review) — read the expected value from the
        # SAME frozen spec `output_encoding` hashes, rather than a
        # hardcoded "UTC" literal: `semantic_timezone_attr` is a real
        # identity input now that the writer also reads it (extract_era5.py
        # `_write_series_netcdf`), so the validator must check against it
        # too, not a duplicated constant that could drift from the spec.
        expected_timezone_attr = POINTS_OUTPUT_ENCODING_SPEC["valid_time"][
            "semantic_timezone_attr"
        ]
        if loaded["valid_time"].attrs.get("timezone") != expected_timezone_attr:
            raise ExtractionPostConditionError(
                f"{path.name} 'valid_time' is missing the semantic "
                f"{expected_timezone_attr!r} attribute (D9/D5.0)"
            )
        # BLOCKER (2026-08-17 review) — the frozen `POINTS_OUTPUT_ENCODING_
        # SPEC` is hashed whole into `extraction_identity`, but only `zlib`
        # was ever checked on reopen: a rehashed series written with the
        # wrong compression level, fill value or chunking still published.
        precip_spec = POINTS_OUTPUT_ENCODING_SPEC["precipitation_mm_per_h"]
        if bool(var.encoding.get("zlib")) is not True:
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' is not zlib-compressed "
                "on reopen (D9's frozen encoding spec)"
            )
        if var.encoding.get("complevel") != precip_spec["complevel"]:
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has complevel="
                f"{var.encoding.get('complevel')!r} on reopen, expected "
                f"{precip_spec['complevel']!r} (D9's frozen encoding spec)"
            )
        fill = var.encoding.get("_FillValue")
        if fill is None or not np.isnan(float(fill)):
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has _FillValue="
                f"{fill!r} on reopen, expected NaN (D9's frozen encoding spec)"
            )
        expected_chunks = (
            min(precip_spec["chunk_stations"], len(stations)),  # type: ignore[arg-type]
            min(precip_spec["chunk_hours"], int(valid_time.size)),  # type: ignore[arg-type]
        )
        actual_chunks = var.encoding.get("chunksizes")
        if actual_chunks is None or tuple(actual_chunks) != expected_chunks:
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has chunksizes="
                f"{actual_chunks!r} on reopen, expected {expected_chunks!r} "
                "(D9's frozen encoding spec)"
            )
        # BLOCKER (2026-08-17 review, P4) — coverage: the axis being
        # hourly-with-no-gaps says nothing about its LENGTH. A 3-stamp
        # series is exactly hourly and still a severely truncated bundle.
        if int(valid_time.size) != expected_hour_count:
            raise ExtractionPostConditionError(
                f"{path.name} has {int(valid_time.size)} 'valid_time' "
                f"stamp(s) on reopen, expected exactly {expected_hour_count} "
                "(D9 coverage)"
            )
        # BLOCKER (2026-08-17 review, P4) — the pinned `valid_time` on-disk
        # encoding (units/dtype) is hashed whole into `extraction_identity`
        # via `output_encoding`, but was never checked on reopen.
        time_spec = POINTS_OUTPUT_ENCODING_SPEC["valid_time"]
        time_units = str(loaded["valid_time"].encoding.get("units", ""))
        expected_time_units = str(time_spec["units"])
        if not expected_time_units.startswith(time_units) or not time_units:
            raise ExtractionPostConditionError(
                f"{path.name} 'valid_time' has on-disk units {time_units!r}, "
                f"expected a UTC-epoch prefix of {expected_time_units!r} "
                "(D9's frozen encoding spec)"
            )
        if str(loaded["valid_time"].encoding.get("dtype")) != str(time_spec["dtype"]):
            raise ExtractionPostConditionError(
                f"{path.name} 'valid_time' has on-disk dtype "
                f"{loaded['valid_time'].encoding.get('dtype')!r} on reopen, "
                f"expected {time_spec['dtype']!r} (D9's frozen encoding spec)"
            )
        # BLOCKER (2026-08-17 review, P4) — the fixed-length `station`
        # encoding (`dtype="S1"`) is likewise hashed but was never checked:
        # a writer emitting xarray's variable-length default still
        # published, silently violating D9's fixed-length contract.
        station_spec = POINTS_OUTPUT_ENCODING_SPEC["station"]
        actual_station_dtype = loaded["station"].encoding.get("dtype")
        expected_station_dtype = np.dtype(station_spec["dtype"])  # type: ignore[arg-type]
        if actual_station_dtype is None or (
            np.dtype(actual_station_dtype) != expected_station_dtype
        ):
            raise ExtractionPostConditionError(
                f"{path.name} 'station' has on-disk encoding dtype "
                f"{actual_station_dtype!r} on reopen, expected fixed-length "
                f"{station_spec['dtype']!r} (D9's frozen encoding spec)"
            )
        # BLOCKER (2026-08-17 review, P4) — D11.2 requires the PRIMARY
        # (nearest) series to carry NO non-finite value; this was only ever
        # checked in memory before writing (`assert_no_missing_primary`),
        # never re-verified on the REOPENED bundle P4a is supposed to trust
        # instead of the writer's own word.
        if require_finite:
            values = np.asarray(var.values)
            n_non_finite = int((~np.isfinite(values)).sum())
            if n_non_finite > 0:
                raise ExtractionPostConditionError(
                    f"{path.name} 'precipitation_mm_per_h' has "
                    f"{n_non_finite} non-finite value(s) on reopen — the "
                    "PRIMARY series must be complete (D11.2)"
                )
    return set(stations), np.asarray(valid_time)


def reopen_and_validate_bundle(
    directory: Path,
    *,
    expected_station_count: int,
    expected_hour_count: int,
    identity: str,
) -> None:
    """4a/P4a — reopen-and-validate every staged file before it is trusted
    (mirrors M-A4's own reopen-after-write discipline): station
    uniqueness/equality across both series and the elevation table, every
    D9-required CSV column and enum value, each series' dims/dtype/
    time-axis/encoding/attributes/COVERAGE, the two series' valid_time axes
    being IDENTICAL, the PRIMARY (nearest) series carrying no non-finite
    value, the sensitivity schema, the manifest's own `extraction_identity`
    matching the identity this bundle is being published under, and (P4a) a
    full `payload_sha256s` reconciliation — a payload modified after its
    hash was computed must fail HERE, not silently pass publication and only
    fail a later discovery read."""
    import numpy as np
    import polars as pl

    nearest_stations, nearest_valid_time = _assert_series_schema(
        directory / "series_nearest.nc",
        expected_station_count=expected_station_count,
        expected_hour_count=expected_hour_count,
        require_finite=True,
    )
    bilinear_stations, bilinear_valid_time = _assert_series_schema(
        directory / "series_bilinear.nc",
        expected_station_count=expected_station_count,
        expected_hour_count=expected_hour_count,
        require_finite=False,
    )
    if nearest_stations != bilinear_stations:
        raise ExtractionPostConditionError(
            "series_nearest.nc and series_bilinear.nc declare different "
            f"station sets: {sorted(nearest_stations)} vs "
            f"{sorted(bilinear_stations)} (D8/D9)"
        )
    # BLOCKER (2026-08-17 review, P4) — the two series must share ONE
    # valid_time axis; nothing previously compared them, so a bilinear file
    # written against a shifted or reordered axis still published.
    if nearest_valid_time.shape != bilinear_valid_time.shape or not np.array_equal(
        nearest_valid_time, bilinear_valid_time
    ):
        raise ExtractionPostConditionError(
            "series_nearest.nc and series_bilinear.nc declare different "
            "'valid_time' axes (D9)"
        )

    elevation_path = directory / "station_grid_elevation.csv"
    if not elevation_path.exists():
        raise ExtractionPostConditionError(
            "staged bundle missing station_grid_elevation.csv"
        )
    elevation = pl.read_csv(elevation_path)
    _assert_columns_present(
        elevation,
        required=ELEVATION_REQUIRED_COLUMNS,
        label="station_grid_elevation.csv",
    )
    if elevation.height != expected_station_count:
        raise ExtractionPostConditionError(
            f"station_grid_elevation.csv has {elevation.height} rows on reopen, "
            f"expected {expected_station_count}"
        )
    elevation_stations = elevation["station"].to_list()
    if len(set(elevation_stations)) != len(elevation_stations):
        raise ExtractionPostConditionError(
            "station_grid_elevation.csv has duplicate station rows: "
            f"{elevation_stations} (D8/D9)"
        )
    if set(elevation_stations) != nearest_stations:
        raise ExtractionPostConditionError(
            "station_grid_elevation.csv's station set does not equal the "
            f"series' station set: {sorted(set(elevation_stations))} vs "
            f"{sorted(nearest_stations)} (D8/D9)"
        )
    _assert_enum_column(
        elevation,
        column="orography_source",
        valid=_VALID_OROGRAPHY_SOURCES,
        label="station_grid_elevation.csv",
    )
    _assert_enum_column(
        elevation,
        column="station_elevation_datum",
        valid=_VALID_VERTICAL_DATA,
        label="station_grid_elevation.csv",
    )
    _assert_enum_column(
        elevation,
        column="orography_elevation_datum",
        valid=_VALID_VERTICAL_DATA,
        label="station_grid_elevation.csv",
    )
    _assert_enum_column(
        elevation,
        column="datum_reconciled",
        valid=_VALID_DATUM_RECONCILED,
        label="station_grid_elevation.csv",
    )

    sensitivity_path = directory / "operator_sensitivity.csv"
    if not sensitivity_path.exists():
        raise ExtractionPostConditionError(
            "staged bundle missing operator_sensitivity.csv"
        )
    sensitivity = pl.read_csv(sensitivity_path)
    _assert_columns_present(
        sensitivity,
        required=SENSITIVITY_REQUIRED_COLUMNS,
        label="operator_sensitivity.csv",
    )
    _assert_enum_column(
        sensitivity,
        column="scope",
        valid=_VALID_SENSITIVITY_SCOPES,
        label="operator_sensitivity.csv",
    )
    _assert_enum_column(
        sensitivity,
        column="statistic",
        valid=_VALID_SENSITIVITY_STATISTICS,
        label="operator_sensitivity.csv",
    )
    _assert_enum_column(
        sensitivity,
        column="delta_unit",
        valid=_VALID_SENSITIVITY_DELTA_UNITS,
        label="operator_sensitivity.csv",
    )
    station_rows = sensitivity.filter(pl.col("scope") == "STATION")
    sensitivity_stations = {v for v in station_rows["station"].drop_nulls().to_list()}
    # BLOCKER (2026-08-17 review) — this used to accept a MERELY-SUBSET
    # station set (`<=`), so a writer that silently dropped a station's rows
    # still published. The real writer (`build_operator_sensitivity_table`)
    # always emits STATION-scope rows for every extracted station, so exact
    # equality is the correct — and achievable — invariant.
    if sensitivity_stations != nearest_stations:
        raise ExtractionPostConditionError(
            "operator_sensitivity.csv's STATION-scope station set does not "
            f"equal the series' station set: {sorted(sensitivity_stations)} "
            f"vs {sorted(nearest_stations)} (D8/D9)"
        )
    # MAJOR (2026-08-17 review) — "the complete station/season/statistic/
    # quantile matrix": the previous check compared only ROW COUNTS per
    # station, which one arbitrary q0.5 row plus one summary row satisfies
    # trivially, and proves nothing about which (season, statistic,
    # quantile) combinations each station actually carries — two stations
    # with EQUAL counts can cover entirely DIFFERENT tail quantiles,
    # seasons, or statistics. Derive the exact composite-key SET per
    # station (also catching within-station DUPLICATE keys, which equal
    # counts cannot distinguish from a genuinely complete matrix either),
    # and require every station's key set to be IDENTICAL — not merely
    # equally sized.
    keys_by_station: dict[str, set[tuple[str, str, float | None]]] = {}
    for row in station_rows.iter_rows(named=True):
        key: tuple[str, str, float | None] = (
            row["season"],
            row["statistic"],
            row["quantile"],
        )
        keys = keys_by_station.setdefault(row["station"], set())
        if key in keys:
            raise ExtractionPostConditionError(
                "operator_sensitivity.csv has a duplicate STATION-scope row "
                f"for station {row['station']!r} at (season, statistic, "
                f"quantile)={key} (D9)"
            )
        keys.add(key)

    reference_station = min(keys_by_station)
    reference_keys = keys_by_station[reference_station]
    mismatched_stations = {
        station: keys
        for station, keys in keys_by_station.items()
        if keys != reference_keys
    }
    if mismatched_stations:
        raise ExtractionPostConditionError(
            "operator_sensitivity.csv's STATION-scope rows are not "
            "complete for every station — the exact (season, statistic, "
            f"quantile) key set differs by station (reference station "
            f"{reference_station!r} has {len(reference_keys)} keys): "
            f"{sorted(mismatched_stations)} diverge (D9)"
        )

    # MAJOR (2026-08-17 review) — publication used to validate NOTHING about
    # ACROSS_STATION rows beyond enum values: an empty or partial
    # ACROSS_STATION block (missing seasons, statistics or quantiles the
    # STATION-scope rows do carry) still published.
    across_rows = sensitivity.filter(pl.col("scope") == "ACROSS_STATION")
    across_keys: set[tuple[str, str, float | None]] = set()
    for row in across_rows.iter_rows(named=True):
        key = (row["season"], row["statistic"], row["quantile"])
        if key in across_keys:
            raise ExtractionPostConditionError(
                "operator_sensitivity.csv has a duplicate ACROSS_STATION "
                f"row at (season, statistic, quantile)={key} (D9)"
            )
        across_keys.add(key)
    if across_keys != reference_keys:
        missing = sorted(reference_keys - across_keys, key=str)
        extra = sorted(across_keys - reference_keys, key=str)
        raise ExtractionPostConditionError(
            "operator_sensitivity.csv's ACROSS_STATION rows do not cover "
            "the same (season, statistic, quantile) key set as the "
            f"STATION-scope rows — missing={missing}, extra={extra} (D9)"
        )

    manifest_path = directory / manifest_filename()
    manifest = read_extraction_manifest(manifest_path)
    if manifest is None:
        raise ExtractionPostConditionError(
            f"staged bundle missing {manifest_filename()}"
        )
    if manifest.extraction_identity != identity:
        raise ExtractionPostConditionError(
            "extraction_manifest.json's extraction_identity "
            f"{manifest.extraction_identity!r} does not match the identity "
            f"this bundle is being published under {identity!r} (manifest "
            "identity consistency)"
        )
    # BLOCKER (2026-08-17 review) — publication used to validate only the
    # identity STRING, never that the provenance/accounting sections the
    # manifest is supposed to carry were actually populated.
    _assert_required_sections_present(manifest, expected_stations=nearest_stations)
    assert_identity_inputs_complete(manifest.identity_inputs)

    # P4a — the same predicate discovery would apply: every D9 payload
    # file's sha256 must reconcile against what the manifest recorded.
    for name in D9_PAYLOAD_FILES:
        expected = manifest.payload_sha256s.get(name)
        if expected is None:
            raise ExtractionPostConditionError(
                f"extraction manifest at {manifest_path} records no sha256 "
                f"for {name!r} (P4a)"
            )
        actual = checksum_file(directory / name)
        if actual != expected:
            raise ExtractionPostConditionError(
                f"{name} sha256 {actual} does not match the manifest's "
                f"recorded {expected} — payload was modified after its "
                "hash was computed (P4a)"
            )
