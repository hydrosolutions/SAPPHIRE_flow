import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    convert,
    permit_fixture,
    seed,
)

TABLES = (
    "measurement_feed_evidence",
    "rating_reference_proofs",
    "provisional_discharges",
    "provisional_discharge_permissions",
)


class TestProtectedSchema:
    def test_relations_exist(self, db_connection: sa.Connection) -> None:
        for table in TABLES:
            assert (
                db_connection.scalar(
                    sa.text("SELECT to_regclass(:name)"), {"name": "public." + table}
                )
                is not None
            )

    @pytest.mark.parametrize("table", TABLES)
    def test_owner_cannot_truncate(
        self, db_connection: sa.Connection, table: str
    ) -> None:
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            db_connection.execute(sa.text(f"TRUNCATE public.{table} CASCADE"))


class TestProtectedMigration:
    def test_empty_roundtrip(self, migration_engine: tuple[sa.Engine, str]) -> None:
        engine, _ = migration_engine
        config = Config("alembic.ini")
        command.upgrade(config, "0067")
        command.upgrade(config, "0068")
        with engine.connect() as conn:
            inspector = sa.inspect(conn)
            for table in TABLES:
                assert table in inspector.get_table_names()
            assert len(inspector.get_foreign_keys("provisional_discharges")) == 5
            assert len(inspector.get_indexes("provisional_discharges")) == 5
        command.downgrade(config, "0067")
        with engine.connect() as conn:
            assert not set(TABLES) & set(sa.inspect(conn).get_table_names())
        command.upgrade(config, "0068")

    def test_nonempty_downgrade_refused(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        config = Config("alembic.ini")
        command.upgrade(config, "0068")
        with engine.begin() as conn:
            seed(conn)
        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(config, "0067")
        with engine.connect() as conn:
            assert (
                conn.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == "0068"
            )

    def test_append_locks_measured_value_qc_and_curve_candidates(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0068")
        with engine.begin() as conn:
            args = seed(conn)
            permit_fixture(conn, args[2].tenant_id)
        result = convert(*args)
        with engine.connect() as writer, writer.begin():
            PgProvisionalDischargeStore(writer).store_provisional_discharge(
                result, captured_at=NOW
            )
            for statement in (
                "UPDATE observations SET value = 1.9",
                "UPDATE observations SET qc_status = 'raw'",
                "UPDATE rating_curves SET version = 2",
                "INSERT INTO rating_curves SELECT * FROM rating_curves",
            ):
                with engine.connect() as contender, contender.begin():
                    contender.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(sa.exc.DBAPIError, match="lock timeout"):
                        contender.execute(sa.text(statement))

    def test_upgrade_revokes_new_table_default_grants(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        config = Config("alembic.ini")
        command.upgrade(config, "0067")
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE ROLE sapphire_api"))
            conn.execute(sa.text("CREATE ROLE sapphire_worker"))
            conn.execute(
                sa.text(
                    "ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES "
                    "TO sapphire_api, sapphire_worker"
                )
            )
        command.upgrade(config, "0068")
        with engine.connect() as conn:
            for role in ("sapphire_api", "sapphire_worker"):
                for table in TABLES:
                    assert not conn.scalar(
                        sa.text(
                            "SELECT has_table_privilege(:role, :table, "
                            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                        ),
                        {"role": role, "table": table},
                    )
