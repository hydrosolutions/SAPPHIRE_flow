from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

import pytest

from tests.integration.services import skill_persistence_fixture as fixture

if TYPE_CHECKING:
    from alembic.config import Config


class TestMigrateSkillDatabase:
    @pytest.mark.parametrize("prior_state", ["present", "absent"])
    @pytest.mark.parametrize("outcome", ["success", "error"])
    def test_restores_environment_at_migration_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        prior_state: Literal["present", "absent"],
        outcome: Literal["success", "error"],
    ) -> None:
        prior = "postgresql+psycopg://unused.invalid/prior"
        target = "postgresql+psycopg://unused.invalid/migration"
        if prior_state == "present":
            monkeypatch.setenv("DATABASE_URL", prior)
        else:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        expected = os.environ.get("DATABASE_URL")
        observed: list[bool] = []

        def upgrade(config: Config, revision: str) -> None:
            observed.append(os.environ.get("DATABASE_URL") == target)
            assert config.config_file_name == "alembic.ini"
            assert revision == "head"
            if outcome == "error":
                raise RuntimeError("migration boundary failure")

        monkeypatch.setattr(fixture.command, "upgrade", upgrade)
        if outcome == "error":
            with pytest.raises(RuntimeError, match="migration boundary failure"):
                fixture.migrate_skill_database(target)
        else:
            fixture.migrate_skill_database(target)

        assert observed == [True]
        restored = os.environ.get("DATABASE_URL") == expected
        assert restored
