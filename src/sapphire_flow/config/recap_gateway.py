# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
"""Nepal v1 recap Data Gateway config + API-key secret plumbing (Plan 082 Task 2A).

Security posture (docs/standards/security.md § Secrets management): application
code reads secrets from file paths, never from environment variables in
production. ``load_recap_api_key`` mirrors the existing ``db_password``
Docker-secret pattern (``docker/entrypoint.sh``) — read the mounted secret
file, falling back to the ``RECAP_API_KEY`` env var for local dev only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sapphire_flow.adapters.recap_gateway import DEFAULT_MAX_CYCLE_AGE_HOURS
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from recap_client import ApiClientConfig

# Docker secret mount point (docker-compose.yml `secrets.sapphire_dg_api_key`).
DEFAULT_RECAP_API_KEY_SECRET_PATH = Path("/run/secrets/sapphire_dg_api_key")

_ENV_VAR = "RECAP_API_KEY"  # noqa: S105 - env var NAME, not a secret value


@dataclass(frozen=True, kw_only=True, slots=True)
class RecapGatewayConfig:
    """Nepal v1 recap Gateway connection config (``[adapters.recap_gateway]``).

    Never carries the API key itself (that is threaded separately via
    :func:`load_recap_api_key` / :func:`build_recap_client_config`, kept out of
    any object that might be logged).
    """

    base_url: str
    timeout_s: int
    verify_tls: bool
    # SAP3-side staleness threshold for the NWP_DELIVERY watchdog (Task 2G) —
    # the Gateway itself exposes no cycle-freshness metadata.
    staleness_threshold_hours: float
    # Documents where the Gateway HRU metadata (polygon names) came from —
    # informational only; the actual mapping lives in the §5a table (Task 2D).
    hru_metadata_source: str
    # SAP3-side retry policy. The recap-dg-client issues a bare
    # requests.Session.get with no retry/backoff of its own (client
    # http.py:167,192) — retries are a Prefect task-level concern
    # (@task(retries=...)), this field documents the configured count.
    max_retries: int
    # Plan 082 Task 2B/2D (Codex review Finding 1): fallback bound for
    # `resolve_latest_cycle` when the nominal IFS cycle is not yet published.
    # Optional in TOML (defaults to `DEFAULT_MAX_CYCLE_AGE_HOURS`) so existing
    # `[adapters.recap_gateway]` sections keep working unchanged.
    max_cycle_age_hours: float = DEFAULT_MAX_CYCLE_AGE_HOURS


def load_recap_api_key(*, secret_path: Path | None = None) -> str:
    """Read the recap Gateway API key from a Docker secret file.

    Falls back to the ``RECAP_API_KEY`` env var when the secret file is
    absent (local dev, matching the existing ``db_password`` convention).
    Raises ``ConfigurationError`` — never returns an empty/placeholder key —
    when neither source is available. Never logs the returned value.
    """
    import os

    path = secret_path if secret_path is not None else DEFAULT_RECAP_API_KEY_SECRET_PATH
    if path.is_file():
        key = path.read_text().strip()
        if key:
            return key
    env_key = os.environ.get(_ENV_VAR)
    if env_key:
        return env_key
    raise ConfigurationError(
        f"recap Gateway API key not found: neither {path} nor {_ENV_VAR} is set"
    )


def build_recap_client_config(
    *, api_key: str, config: RecapGatewayConfig
) -> ApiClientConfig:
    """Thread the loaded API key into the client's ``ApiClientConfig``."""
    from recap_client import ApiClientConfig

    return ApiClientConfig(
        base_url=config.base_url,
        api_key=api_key,
        timeout_s=config.timeout_s,
        verify_tls=config.verify_tls,
    )


def load_recap_gateway_config(config_path: Path) -> RecapGatewayConfig:
    """Read ``[adapters.recap_gateway]`` from the MERGED SAPPHIRE_CONFIG TOML.

    Uses the same ``load_merged_toml`` + ``SAPPHIRE_CONFIG_OVERLAY`` resolution
    as the Flow-1 forecast selector (``run_forecast_cycle.py``'s
    ``_load_weather_forecast_adapter_config``/``_build_recap_forecast_adapter``)
    — Codex review Finding 4. A Nepal overlay-driven deployment supplies
    ``type = "recap_gateway"`` and this whole section from an overlay layered
    on the base (Swiss) config; reading the base file alone would raise
    "missing section" even though the selector picked Recap. No-overlay
    behavior (``SAPPHIRE_CONFIG_OVERLAY`` unset) is unchanged.
    """
    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    data = cast(
        "dict[str, Any]",
        load_merged_toml(config_path, _resolve_overlay_paths()),
    )

    adapters = data.get("adapters", {})
    section = cast(
        "dict[str, Any]",
        adapters.get("recap_gateway", {}) if isinstance(adapters, dict) else {},
    )
    if not section:
        raise ConfigurationError(
            "[adapters.recap_gateway] section is required to build the recap "
            "Gateway config but is missing or empty"
        )

    required = (
        "base_url",
        "timeout_s",
        "verify_tls",
        "staleness_threshold_hours",
        "hru_metadata_source",
        "max_retries",
    )
    missing = [key for key in required if key not in section]
    if missing:
        raise ConfigurationError(
            f"[adapters.recap_gateway] missing required field(s): {', '.join(missing)}"
        )

    max_cycle_age_hours = cast(
        "float",
        section.get("max_cycle_age_hours", DEFAULT_MAX_CYCLE_AGE_HOURS),
    )

    return RecapGatewayConfig(
        base_url=cast("str", section["base_url"]),
        timeout_s=cast("int", section["timeout_s"]),
        verify_tls=cast("bool", section["verify_tls"]),
        staleness_threshold_hours=cast("float", section["staleness_threshold_hours"]),
        hru_metadata_source=cast("str", section["hru_metadata_source"]),
        max_retries=cast("int", section["max_retries"]),
        max_cycle_age_hours=max_cycle_age_hours,
    )
