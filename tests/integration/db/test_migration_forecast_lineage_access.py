from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)


def test_acl_upgrade_downgrade_does_not_restore_lineage(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, _ = migration_engine
    config = Config("alembic.ini")
    command.upgrade(config, "0070")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE sapphire_api"))
        conn.execute(sa.text("GRANT SELECT ON forecasts, rejected_forecasts TO PUBLIC"))
        conn.execute(
            sa.text(
                "GRANT SELECT (input_lineage) ON forecasts, "
                "rejected_forecasts TO sapphire_api"
            )
        )
    command.upgrade(config, "0071")
    command.downgrade(config, "0070")
    with engine.connect() as conn:
        for table in ["forecasts", "rejected_forecasts"]:
            assert not conn.scalar(
                sa.text(
                    "SELECT has_column_privilege("
                    "'sapphire_api', :table, 'input_lineage', 'SELECT')"
                ),
                {"table": table},
            )
            assert conn.scalar(
                sa.text(
                    "SELECT has_column_privilege("
                    "'sapphire_api', :table, 'id', 'SELECT')"
                ),
                {"table": table},
            )
    command.upgrade(config, "0071")


def test_failed_migration_rolls_back_early_revokes(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, _ = migration_engine
    config = Config("alembic.ini")
    command.upgrade(config, "0070")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE ROLE sapphire_api NOINHERIT"))
        conn.execute(sa.text("CREATE ROLE lineage_reader"))
        conn.execute(sa.text("GRANT lineage_reader TO sapphire_api"))
        conn.execute(
            sa.text("GRANT SELECT ON forecasts TO lineage_reader, sapphire_api")
        )
    with pytest.raises(sa.exc.DBAPIError, match="reachable read authority"):
        command.upgrade(config, "0071")
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0070"
        assert conn.scalar(
            sa.text("SELECT has_table_privilege('sapphire_api', 'forecasts', 'SELECT')")
        )
