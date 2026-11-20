from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.store.test_model_artifact_store import _seed_model, _seed_station


def test_unknown_provenance_migration_and_downgrade_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgis/postgis:16-3.4") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0088")
        command.downgrade(config, "0087")
        command.upgrade(config, "0088")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                mid, sid = _seed_model(conn), _seed_station(conn)
                conn.execute(
                    sa.text("""
INSERT INTO model_artifacts
 (id, model_id, station_id, status, artifact_path, training_provenance, sha256_hash)
VALUES (gen_random_uuid(), :mid, :sid, 'training', '/test/unknown', 'unknown',
 repeat('a', 64))
"""),
                    {"mid": str(mid), "sid": sid},
                )
            with pytest.raises(sa.exc.DBAPIError, match="unknown training provenance"):
                command.downgrade(config, "0087")
            with (
                engine.begin() as conn,
                pytest.raises(sa.exc.IntegrityError, match="training_provenance"),
            ):
                conn.execute(
                    sa.text("""
INSERT INTO model_artifacts
 (id, model_id, station_id, status, artifact_path, training_provenance,
  trained_at, sha256_hash)
VALUES (gen_random_uuid(), :mid, :sid, 'training', '/test/bad', 'unknown',
 now(), repeat('a', 64))
"""),
                    {"mid": str(mid), "sid": sid},
                )
        finally:
            engine.dispose()
