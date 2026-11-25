from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.store.test_skill_store import _seed_model, _seed_station


def test_nullable_threshold_context_upgrade_and_preservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgis/postgis:16-3.4") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0093")
        command.downgrade(config, "0092")
        command.upgrade(config, "0093")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                sid, mid = _seed_station(conn), _seed_model(conn)
                conn.execute(
                    sa.text("""
INSERT INTO skill_scores (id, station_id, model_id, parameter, skill_source,
 computation_version, computed_at, lead_time_hours, metric, score, sample_size,
 eval_period_start, eval_period_end, threshold_context)
VALUES (gen_random_uuid(), :sid, :mid, 'discharge', 'hindcast_reanalysis',
 4, now(), 24, 'brier_danger_High', 0.25, 1, now(), now(), '{}')
"""),
                    {"sid": sid, "mid": mid},
                )
            with pytest.raises(
                sa.exc.DBAPIError, match="cannot remove stored threshold skill context"
            ):
                command.downgrade(config, "0092")
        finally:
            engine.dispose()
