from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command


def test_model_label_backfill_constraints_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgis/postgis:16-3.4") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "0091")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text("""
                    INSERT INTO models (id, display_name, artifact_scope, description)
                    VALUES ('cmal_nepal_static_encoder_20261123',
                            'CMAL Full', 'group', ''),
                           ('other', 'Other', 'station', '')
                """)
                )
            command.upgrade(config, "0092")
            with engine.connect() as conn:
                assert conn.execute(
                    sa.text(
                        "SELECT id, display_name_short FROM models "
                        "WHERE id IN ('cmal_nepal_static_encoder_20261123', 'other') "
                        "ORDER BY id"
                    )
                ).all() == [
                    ("cmal_nepal_static_encoder_20261123", "CMAL SE"),
                    ("other", None),
                ]
            for invalid in ["", " CMAL", "CMAL ", "x" * 33]:
                with (
                    engine.begin() as conn,
                    pytest.raises(
                        sa.exc.IntegrityError, match="ck_models_display_name_short"
                    ),
                ):
                    conn.execute(
                        sa.text(
                            "UPDATE models SET display_name_short=:label "
                            "WHERE id='other'"
                        ),
                        {"label": invalid},
                    )
            command.downgrade(config, "0091")
            with engine.connect() as conn:
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT display_name FROM models "
                            "WHERE id='cmal_nepal_static_encoder_20261123'"
                        )
                    ).scalar_one()
                    == "CMAL Full"
                )
            command.upgrade(config, "0092")
        finally:
            engine.dispose()
