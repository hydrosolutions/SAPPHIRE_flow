"""M-A4 (Plan 171) — typed errors for the ERA5-Land acquisition/transform
pipeline. One hierarchy, one CLI exit-code mapping (task 4a).

CLAUDE.md: never a bare `except`; every raised error here carries context.
"""

from __future__ import annotations

from sapphire_flow.exceptions import SapphireError


class Era5AcquisitionError(SapphireError):
    """Base for every M-A4 ERA5-Land acquisition/transform error."""


class NonExpressibleWindowError(Era5AcquisitionError, ValueError):
    """A requested date span is not a clean CDS Cartesian unit (D2)."""


class Era5CredentialsError(Era5AcquisitionError):
    """CDS credentials absent or rejected by CDS. CLI exit code 2."""


class Era5TransientError(Era5AcquisitionError):
    """Retryable: transport error, HTTP 5xx, documented CDS queue-transient
    state (2a retry contract). Never surfaces past the retry loop — either a
    later attempt succeeds or it is wrapped into `Era5RequestFailedError`."""


class Era5RequestFailedError(Era5AcquisitionError):
    """CDS rejected the request (malformed, licence not accepted), or a
    retryable failure exhausted its attempts, or a downloaded artifact failed
    post-download validation (2a). CLI exit code 3."""


class Era5RequestTooLargeError(Era5RequestFailedError):
    """CDS refused the request as exceeding its per-request COST LIMIT — a
    ceiling on FIELD COUNT, not on bytes (observed 2026-08-17: one calendar
    year is 8,760 hourly fields and is rejected outright, while one calendar
    month is 744 and succeeds). Distinct from `Era5CredentialsError` because
    the credentials are fine and from the generic
    `Era5RequestFailedError` because the fix is mechanical and known: reduce
    the window granularity (Plan 171 D4, corrected 2026-08-17 — the
    acquisition unit is ONE CALENDAR MONTH).

    CLI exit code 6 — deliberately NOT 2 ("inputs absent"), which is what
    the misclassifying `403`-substring rule used to report and which sent
    the operator hunting a credential problem that did not exist.
    Retrying is pointless: an identical payload is rejected identically, so
    this inherits `Era5RequestFailedError`'s non-retryable disposition."""


class Era5ValidationError(Era5RequestFailedError):
    """A downloaded raw artifact failed post-download validation (2a) —
    wrong variable, wrong units, wrong spatial/temporal coverage."""


class Era5TransformFailedError(Era5AcquisitionError):
    """A D6/D7/D8/D9 transform post-condition failed. CLI exit code 4."""


class Era5MissingBoundaryContextError(Era5TransformFailedError):
    """D6 — boundary context required to deaccumulate a requested stamp is
    unavailable. The transform never emits a partial year for this."""


class Era5PackingPostConditionError(Era5TransformFailedError):
    """D7 — a material negative increment (beyond the packing tolerance)
    means the accumulation-day assumption is wrong."""


class Era5ConservationError(Era5TransformFailedError):
    """D6 post-condition 1 — an accumulation day's unclamped increments do
    not sum to its terminal accumulator value within tolerance."""


class Era5UnitsMismatchError(Era5TransformFailedError):
    """D8 — the source variable is missing, not metres, or already
    converted (guards against double-conversion)."""


class Era5SchemaValidationError(Era5TransformFailedError):
    """D9 — the final product dataset does not satisfy the declared schema."""


class Era5StorageError(Era5AcquisitionError):
    """Storage or manifest write/read failed. CLI exit code 5."""


class Era5StageNotApplicableError(Era5AcquisitionError):
    """The requested stage cannot be run for the requested variable — e.g.
    transforming an INSTANTANEOUS field through the accumulator path, which
    would deaccumulate it into hour-to-hour differences and multiply it by
    the m->mm units factor. CLI exit code 7."""


# --- Plan 174 (M-A5) D9 — point-extraction error hierarchy ---


class Era5ExtractionError(Era5AcquisitionError):
    """Base for every M-A5 point-extraction error."""


class ExtractionInputAbsentError(Era5ExtractionError):
    """D9 — a required extraction input is absent from disk, checked BEFORE
    any read is attempted: the acquired per-year product file is missing
    (Plan 171 Task 4b has not produced it), the coordinate table is missing,
    or the orography spec/route is unavailable. CLI exit code 2 — distinct
    from a storage WRITE failure (exit 5, `Era5StorageError`) and from a
    post-condition failure on data that does exist (exit 4)."""


class Era5OrographyError(Era5ExtractionError):
    """D3a/D3b — orography acquisition, conversion or aggregation failed
    (a magnitude-check failure, a hash mismatch against an existing
    `OrographySourceRecord`, a no-data/grid-vector post-condition). CLI
    exit code 3."""


class StationOutsideGridError(Era5ExtractionError):
    """D11.1 — a station's coordinate lies outside the product's grid range
    (plus the D2 half-spacing registration allowance). The extraction must
    never let `.sel(method="nearest")` silently relocate it to the
    boundary. CLI exit code 4."""


class NonFiniteExtractionError(Era5ExtractionError):
    """D11.2 — the nearest-operator series contains a NaN at some station.
    ERA5-Land over this land box should be complete; a NaN means something
    upstream is wrong. CLI exit code 4."""


class StationSetMismatchError(Era5ExtractionError):
    """D8 — the extracted station set does not equal the expected
    (workbook-derived) inventory exactly, or a table carries a duplicate or
    missing station row. CLI exit code 4."""


class SourceChecksumMismatchError(Era5ExtractionError):
    """D7/D9 — a consumed source product's sha256 disagrees with the
    acquisition manifest, checked before the file's payload is decoded.
    CLI exit code 4."""


class ExtractionPostConditionError(Era5ExtractionError):
    """D9/D11 — any other extraction post-condition failure: axis validity,
    registration, required attrs, bundle-publication invariants. CLI exit
    code 4."""
