"""Plan 513: the ``[tenants.<code>]`` declaration — which tenants a host hosts.

Read with plain ``tomllib`` and merged per code across the base config and the
overlays; NO ``${...}`` expansion and NO ``load_config()``, so an unrelated
runtime-only placeholder can never break ``init``.
"""

from __future__ import annotations

import re
import tomllib
from typing import TYPE_CHECKING, Annotated, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.tenant import (
    DEFAULT_TENANT_CODE,
    DEFAULT_TENANT_NAME,
    DeclaredTenant,
)

if TYPE_CHECKING:
    from pathlib import Path

_CODE_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
_MAX_NAME_LENGTH = 200


class _TenantEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(max_length=_MAX_NAME_LENGTH)] = Field(
        min_length=1
    )


def parse_declared_tenants(raw: object) -> tuple[DeclaredTenant, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ConfigurationError("[tenants] must be a table of [tenants.<code>]")
    table = cast("dict[str, object]", raw)
    return tuple(_parse_entry(code, table[code]) for code in sorted(table))


def _parse_entry(code: str, entry: object) -> DeclaredTenant:
    if not re.fullmatch(_CODE_PATTERN, code):
        raise ConfigurationError(
            f"[tenants.{code!r}]: code must match {_CODE_PATTERN} "
            "(lowercase letters, digits, _ and -, starting with a letter)"
        )
    if not isinstance(entry, dict):
        raise ConfigurationError(f"[tenants.{code}] must be a table with a name")
    try:
        parsed: _TenantEntry | None = _TenantEntry.model_validate(entry)
        problems = ""
    except ValidationError as exc:
        # Locations and error types only: a message that echoes input values
        # could leak a secret a user mistyped into the declaration.
        problems = ", ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['type']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        parsed = None
    if parsed is None:
        raise ConfigurationError(f"[tenants.{code}]: invalid ({problems})")
    if not parsed.name.strip():
        raise ConfigurationError(f"[tenants.{code}]: name must not be blank")
    if code == DEFAULT_TENANT_CODE and parsed.name != DEFAULT_TENANT_NAME:
        raise ConfigurationError(
            f"[tenants.{code}]: the seeded tenant's name is "
            f"{DEFAULT_TENANT_NAME!r}, not {parsed.name!r}"
        )
    return DeclaredTenant(code=code, name=parsed.name)


def load_declared_tenants(
    config_path: Path, overlay_paths: list[Path]
) -> tuple[DeclaredTenant, ...]:
    merged: dict[str, object] = {}
    for path in (config_path, *overlay_paths):
        tenants = _read_tenants_table(path)
        if tenants is None:
            continue
        if not isinstance(tenants, dict):
            raise ConfigurationError(f"{path}: [tenants] must be a table")
        for code, entry in cast("dict[str, object]", tenants).items():
            current = merged.get(code)
            if isinstance(current, dict) and isinstance(entry, dict):
                merged[code] = {
                    **cast("dict[str, object]", current),
                    **cast("dict[str, object]", entry),
                }
            else:
                merged[code] = entry
    return parse_declared_tenants(merged)


def _read_tenants_table(path: Path) -> object:
    if not path.is_file():
        raise ConfigurationError(f"Config file not found: {path}")
    return tomllib.loads(path.read_text()).get("tenants")
