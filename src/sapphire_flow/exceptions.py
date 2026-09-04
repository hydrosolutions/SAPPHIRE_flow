from __future__ import annotations

from typing import Literal


class SapphireError(Exception):
    """Base for all SAPPHIRE Flow domain errors."""


class InsufficientDataError(SapphireError):
    """Not enough input data to run a model or service function."""


class InsufficientObservationsError(InsufficientDataError):
    """Not enough recent observations to run an observation-backed model."""


class SanityCheckFailure(SapphireError):  # noqa: N818
    """Model output failed plausibility checks."""


class ModelLoadError(SapphireError):
    """Failed to deserialize or load a model artifact."""


class ModelOutputError(SapphireError):
    """Model ran but produced zero convertible ensembles."""


class ConflictError(SapphireError):
    """Optimistic locking detected a concurrent modification."""


class AdapterError(SapphireError):
    """External data source returned an error or timed out."""


class NoCycleAvailableError(AdapterError):
    """NWP cycle is not published and no fallback succeeded within the fallback cap."""


class BudgetExceededError(AdapterError):
    """Local size or file-count guard tripped; not a retriable external-source error.

    ``kind``/``observed``/``limit`` (Plan 223 D6) let a caller construct a
    short, sanitised failure reason (e.g. ``"nwp_file_count_exceeded: 501 >
    500"``) from values the code itself computed, without ever parsing
    ``str(exc)`` — the message text may still be freeform for logs.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["byte", "file_count"],
        observed: int,
        limit: int,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.observed = observed
        self.limit = limit


class LindasRateLimitExhaustedError(AdapterError):
    """A LINDAS request via ``adapters.lindas_rate_limiter`` could not
    succeed within the retry budget (Plan 175 D7) — either the bounded
    attempt count or the bounded wall-clock deadline was hit first.
    Callers translate this into their own adapter-specific error/outcome
    shape; it is never allowed to propagate raw past an adapter boundary."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_status: int | None,
        last_exc: Exception | None,
        bound: Literal["attempts", "deadline"],
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_status = last_status
        self.last_exc = last_exc
        self.bound = bound


class DiskSoftLimitError(AdapterError):
    """Free disk space below soft threshold; NWP fetch degraded to runoff-only."""

    def __init__(
        self,
        message: str,
        *,
        path: str,
        free_gb: float,
        threshold_gb: float,
        subject: Literal["scratch", "nwp_archive"],
    ) -> None:
        super().__init__(message)
        self.path = path
        self.free_gb = free_gb
        self.threshold_gb = threshold_gb
        self.subject = subject


class DiskHardLimitError(AdapterError):
    """Free disk space below hard threshold; NWP fetch aborted (fail-closed)."""

    def __init__(
        self,
        message: str,
        *,
        path: str,
        free_gb: float,
        threshold_gb: float,
        subject: Literal["scratch", "nwp_archive"],
    ) -> None:
        super().__init__(message)
        self.path = path
        self.free_gb = free_gb
        self.threshold_gb = threshold_gb
        self.subject = subject


class ConfigurationError(SapphireError):
    """Invalid or missing configuration."""


class UnsupportedModelRequirementError(SapphireError):
    """A ForecastInterface model's ``InputRequirement`` cannot be represented
    by SAP3's single-resolution domain types (Plan 156) — e.g. more than one
    ``time_step`` branch declares non-empty ``future_known`` (raised at
    adapter construction), or a requirement with more than one ``time_step``
    branch at all reaches an actual predict/train call (raised there
    instead — SAP3 can only deliver one branch's dynamic inputs per call, so
    the non-active branch(es) would otherwise be silently omitted from
    ``ModelInputs``). Deliberately **not** a ``ConfigurationError``
    subclass: ``discover_models()`` re-raises ``ConfigurationError`` for
    every entry point (a registry-wide blackout), but one unsupported model
    must not darken discovery for the rest — it is skipped per entry point
    instead (``services/model_registry.py::discover_models``)."""


class ModelSmokeTestError(SapphireError):
    """Model failed smoke test during onboarding."""


class ArtifactIntegrityError(SapphireError):
    """SHA-256 hash verification failed on artifact deserialization."""


class ExtractionError(SapphireError):
    """Preprocessing/extraction failure (e.g. GridExtractor)."""


class StoreError(SapphireError):
    """Store data retrieval failure (archive not found, corrupt data)."""


class BasinPackageRejectedError(SapphireError):
    """A basin/static package (``docs/requirements/04-basin-static-artifact-
    contract.md``) fails a WHOLE-PACKAGE acceptance rule (contract §9 first
    list) — an unsupported ``contract_version``, a missing mandatory file, a
    checksum mismatch, schema-nonconformance, or a cross-file ``gauge_id``
    mismatch (Plan 120 Task 1A/1B). The entire package is rejected before any
    write; this is distinct from a per-basin ``onboarding`` hold, which does
    not raise (see ``BasinAcceptanceDecision``)."""


class TenantIsolationError(SapphireError):
    """Plan 147 Slice E: a write's tenant-scoped ``WritePrincipal`` does not
    authorize the target row's ``tenant_id`` (R5/G6 LOCKED). Fail-loud, raised
    BEFORE the write happens (no domain-state change); the rejection is
    additionally recorded as a persisted ``audit_log`` event by the caller
    (see ``services/write_principal.py::enforce_tenant_isolation``)."""


class ForecastCycleAbortedError(SapphireError):
    """Plan 237 T2: the NWP fetch produced zero forecasts and the cycle
    aborted before Phase B. Raised at the FLOW level (never at the task
    site -- ``_fetch_nwp_task`` has already swallowed the underlying
    exception into a returned outcome) so Prefect reports the run as
    failed rather than COMPLETED, agreeing with the watchdog that already
    calls this event critical. Raised AFTER the forced-CRITICAL freshness
    record is written, never before."""
