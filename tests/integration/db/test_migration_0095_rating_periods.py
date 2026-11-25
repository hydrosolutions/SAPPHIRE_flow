from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.store.test_native_rating_forecasts import native_pair


def test_old_rating_evidence_registered_without_heap_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgis/postgis:16-3.4") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0094")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                native_pair(conn)
                node = conn.scalar(
                    sa.text("SELECT pg_relation_filenode('rating_derived_discharge')")
                )
                before = conn.scalar(sa.select(sa.func.clock_timestamp()))
                evidence = conn.execute(
                    sa.text("SELECT fingerprint,content FROM rating_derived_discharge")
                ).all()
            command.upgrade(config, "0095")
            with engine.connect() as conn:
                after = conn.scalar(sa.select(sa.func.clock_timestamp()))
                assert (
                    conn.scalar(
                        sa.text(
                            "SELECT pg_relation_filenode('rating_derived_discharge')"
                        )
                    )
                    == node
                )
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT fingerprint,content FROM rating_derived_discharge"
                        )
                    ).all()
                    == evidence
                )
                recorded = conn.scalar(
                    sa.text("SELECT recorded_at FROM rating_derived_discharge")
                )
                assert before <= recorded <= after
            with pytest.raises(RuntimeError, match="downgrade refused"):
                command.downgrade(config, "0094")
        finally:
            engine.dispose()
