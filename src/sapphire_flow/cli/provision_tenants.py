"""Plan 513: create the tenants a host's config declares (``[tenants.<code>]``).

Run by ``init`` after ``alembic upgrade head`` and before the role bootstrap:
``python -m sapphire_flow.cli.provision_tenants``. Idempotent and atomic — every
missing tenant is created in ONE transaction, in sorted code order; an existing
tenant keeps its id; the same code with a different name aborts the whole batch.
An unset ``SAPPHIRE_CONFIG`` is a hard error; a config with no ``[tenants]`` is
a no-op (and never touches the database).
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config._overlay import (
    _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
)
from sapphire_flow.config.declared_tenants import load_declared_tenants
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.ids import TenantId

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sapphire_flow.protocols.stores import TenantStore
    from sapphire_flow.types.tenant import DeclaredTenant, Tenant

log = structlog.get_logger(__name__)


def ensure_declared_tenants(
    store: TenantStore,
    declared: Iterable[DeclaredTenant],
    *,
    new_id: Callable[[], UUID] = uuid4,
) -> list[Tenant]:
    """Sorted by code so two concurrent runs never lock rows in opposite orders."""
    return [
        store.ensure_tenant(
            tenant_id=TenantId(new_id()), code=tenant.code, name=tenant.name
        )
        for tenant in sorted(declared, key=lambda t: t.code)
    ]


def _config_path() -> Path:
    raw = os.environ.get("SAPPHIRE_CONFIG")
    if not raw:
        raise ConfigurationError(
            "SAPPHIRE_CONFIG is not set: the declared-tenant step needs the "
            "host's config path (docker-compose `init` must pass it)"
        )
    return Path(raw)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    configure_cli_logging()
    try:
        declared = load_declared_tenants(_config_path(), _resolve_overlay_paths())
        log.info("tenants_declared", count=len(declared))
        if not declared:
            return 0
        engine = create_engine_from_env()
        with engine.begin() as conn:
            ensured = ensure_declared_tenants(PgTenantStore(conn), declared)
    except (ConfigurationError, tomllib.TOMLDecodeError) as exc:
        log.error(
            "tenant_provisioning_failed", cause=type(exc).__name__, error=str(exc)
        )
        return 1
    except SQLAlchemyError as exc:
        # Class name only: a driver message can echo connection details.
        log.error("tenant_provisioning_failed", cause=type(exc).__name__)
        return 1
    log.info("tenants_ensured", codes=[t.code for t in ensured])
    return 0


if __name__ == "__main__":
    sys.exit(main())
