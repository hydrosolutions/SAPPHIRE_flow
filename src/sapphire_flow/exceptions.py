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


class ConfigurationError(SapphireError):
    """Invalid or missing configuration."""
