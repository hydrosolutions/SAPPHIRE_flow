try:
    from sapphire_flow._version import __version__
except ModuleNotFoundError as exc:
    if exc.name == "sapphire_flow._version":
        raise RuntimeError(
            "sapphire_flow version metadata is missing. Install the package with "
            "its build backend, or set "
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW for no-Git "
            "build contexts."
        ) from exc
    raise

__all__ = ["__version__"]
