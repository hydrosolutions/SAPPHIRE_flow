"""Plan 147 Slice E (G6 LOCKED): the ``[deployment]`` config block that
declares which tenant(s) THIS host may write to. Read at write-principal
resolution time (``services/write_principal.py::resolve_run_principal``) —
NOT folded into the large ``config.deployment.DeploymentConfig`` (that class
is the operational-tuning config; this is the write-authority identity,
mirroring how ``config/recap_gateway.py`` keeps its own adapter section
separate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, kw_only=True, slots=True)
class DeploymentIdentityConfig:
    """``[deployment]`` in ``config.toml``.

    Exactly one of ``global_admin=True`` (an unscoped host) or a non-empty
    ``writable_tenants`` (one or more tenant CODEs this host may write to)
    must hold — enforced in ``__post_init__`` so an ambiguous or empty
    declaration fails at load time, not at first write.
    """

    writable_tenants: frozenset[str]
    global_admin: bool
    operator: str | None = None

    def __post_init__(self) -> None:
        if self.global_admin and self.writable_tenants:
            raise ConfigurationError(
                "[deployment]: global_admin=true and writable_tenants are "
                "mutually exclusive — a global-admin host is unscoped, it "
                "does not also declare a tenant list"
            )
        if not self.global_admin and not self.writable_tenants:
            raise ConfigurationError(
                "[deployment]: must declare either writable_tenants = "
                '["<code>", ...] or global_admin = true'
            )


def load_deployment_identity_config(
    config_path: Path | str,
) -> DeploymentIdentityConfig:
    """Read ``[deployment]`` from the MERGED SAPPHIRE_CONFIG TOML (same
    ``load_merged_toml`` + ``SAPPHIRE_CONFIG_OVERLAY`` resolution as
    ``config/recap_gateway.py``)."""
    from pathlib import Path as _Path

    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    data = cast(
        "dict[str, Any]",
        load_merged_toml(_Path(config_path), _resolve_overlay_paths()),
    )
    section_raw = data.get("deployment", {})
    section = cast(
        "dict[str, Any]", section_raw if isinstance(section_raw, dict) else {}
    )

    writable_tenants = frozenset(cast("list[str]", section.get("writable_tenants", [])))
    global_admin = bool(section.get("global_admin", False))
    operator = cast("str | None", section.get("operator"))

    return DeploymentIdentityConfig(
        writable_tenants=writable_tenants,
        global_admin=global_admin,
        operator=operator,
    )
