from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.store.test_skill_store import _seed_model, _seed_station


def test_rated_evidence_nullable_upgrade_and_guarded_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgis/postgis:16-3.4") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0098")
        command.downgrade(config, "0097")
        command.upgrade(config, "0098")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                # A stored payload must survive even when no score rows remain.
                sid, mid = _seed_station(conn), _seed_model(conn)
                conn.execute(
                    sa.text("""
INSERT INTO skill_generations (id,station_id,model_id,parameter,skill_source,
computation_version,published_at,score_count,diagram_count,threshold_evaluation_manifest)
VALUES (gen_random_uuid(),:sid,:mid,'discharge',
'hindcast_reanalysis',4,now(),0,0,'{}')
"""),
                    {"sid": sid, "mid": mid},
                )
            with pytest.raises(
                RuntimeError, match="cannot remove retained rated-threshold"
            ):
                command.downgrade(config, "0097")
        finally:
            engine.dispose()
