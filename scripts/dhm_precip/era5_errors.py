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
