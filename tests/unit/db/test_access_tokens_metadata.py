"""Plan 401 — metadata/migration parity for the access_tokens CHECK constraints.

The integration schema is built by ``alembic upgrade head``, so no database test
ever sees ``sapphire_flow.db.metadata``. This pins each ``access_tokens`` CHECK
in metadata to the SQL migration 0061 creates, so the two cannot drift silently.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import access_tokens

if TYPE_CHECKING:
    from types import ModuleType

_MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "0061_access_tokens_reviewer_role.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0061", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks_by_name() -> dict[str, str]:
    constraints = [*access_tokens.constraints, *access_tokens.c.role.constraints]
    return {
        str(c.name): str(c.sqltext)
        for c in constraints
        if isinstance(c, sa.CheckConstraint)
    }


class TestAccessTokensCheckConstraintParity:
    @pytest.mark.parametrize(
        ("name", "migration_constant"),
        [
            ("ck_access_tokens_role", "_ROLE_AFTER"),
            ("ck_access_tokens_role_tenant", "_ROLE_TENANT_AFTER"),
            ("ck_access_tokens_tenant_mode_is_consumer", "_TENANT_MODE_AFTER"),
        ],
    )
    def test_metadata_matches_migration_0061(
        self, name: str, migration_constant: str
    ) -> None:
        expected = getattr(_load_migration(), migration_constant)
        assert _checks_by_name()[name] == expected
