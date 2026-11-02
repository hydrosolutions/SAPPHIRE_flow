from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.store.test_native_rating_forecasts import native_pair


def test_native_migration_roundtrip_and_evidence_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer(
        "postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="native_migration",
    ) as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0078")
        command.upgrade(config, "0079")
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                assert (
                    conn.scalar(
                        sa.text("SELECT to_regclass('rating_derived_discharge')")
                    )
                    is not None
                )
            command.downgrade(config, "0078")
            with engine.connect() as conn:
                assert (
                    conn.scalar(
                        sa.text("SELECT to_regclass('rating_derived_discharge')")
                    )
                    is None
                )
                assert "provisional_rating" not in conn.scalar(
                    sa.text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname='ck_forecasts_data_use'"
                    )
                )
            command.upgrade(config, "0079")
            with engine.begin() as conn:
                native_pair(conn)
            with pytest.raises(
                sa.exc.DBAPIError, match="surviving native rating evidence"
            ):
                command.downgrade(config, "0078")
            with engine.connect() as conn:
                assert (
                    conn.scalar(sa.text("SELECT version_num FROM alembic_version"))
                    == "0079"
                )
                assert (
                    conn.scalar(
                        sa.text("SELECT count(*) FROM rating_derived_discharge")
                    )
                    == 1
                )
        finally:
            engine.dispose()
