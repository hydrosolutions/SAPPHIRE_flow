class SapphireError(Exception):
    """Base for all SAPPHIRE Flow domain errors."""


class InsufficientDataError(SapphireError):
    """Not enough input data to run a model or service function."""


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
