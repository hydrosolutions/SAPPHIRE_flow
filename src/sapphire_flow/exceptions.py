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
    """Local size or file-count guard tripped; not a retriable external-source error."""


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
