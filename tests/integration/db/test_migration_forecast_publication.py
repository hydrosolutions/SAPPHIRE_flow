from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_publication_migration_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            yield engine, url
        finally:
            engine.dispose()
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous


def _legacy_forecast_content(
    conn: sa.Connection, forecast_id: UUID
) -> dict[str, list[dict[str, object]]]:
    queries = {
        "header": "SELECT * FROM forecasts WHERE id=:id",
        "values": "SELECT * FROM forecast_values WHERE forecast_id=:id "
        "ORDER BY valid_time, member_id",
        "evidence": "SELECT * FROM forecast_evidence WHERE forecast_id=:id",
    }
    return {
        kind: [
            dict(row)
            for row in conn.execute(sa.text(query), {"id": forecast_id}).mappings()
        ]
        for kind, query in queries.items()
    }


def test_additive_publication_migration_and_rollback(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, url = migration_engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0062")
    with engine.begin() as conn:
        station_id = _seed_station(conn)
        model_id = _seed_model(conn)
        artifact_id = _seed_artifact(conn, station_id, model_id)
        forecast = _make_forecast(station_id, model_id, artifact_id)
        # Seed the historical contract, not a current store/metadata projection.
        conn.execute(
            sa.text(
                "INSERT INTO forecasts (id, station_id, model_id, model_artifact_id, "
                "issued_at, nwp_cycle_reference_time, nwp_cycle_source, "
                "representation, "
                "status, version, parameter, units, created_at, updated_at) VALUES "
                "(:id, :station, :model, :artifact, :issued, :cycle, "
                "'primary', 'members', "
                "'raw', 1, :parameter, :units, :created, :updated)"
            ),
            dict(
                id=forecast.id,
                station=station_id,
                model=model_id,
                artifact=artifact_id,
                issued=forecast.issued_at,
                cycle=forecast.nwp_cycle_reference_time,
                parameter=forecast.ensemble.parameter,
                units=forecast.ensemble.units,
                created=forecast.created_at,
                updated=forecast.updated_at,
            ),
        )
        conn.execute(
            sa.text(
                "INSERT INTO forecast_values (id, forecast_id, issued_at, valid_time, "
                "lead_time_hours, member_id, value) VALUES "
                "(:id, :forecast, :issued, :valid, :lead, :member, :value)"
            ),
            [
                dict(
                    id=uuid4(),
                    forecast=forecast.id,
                    issued=forecast.issued_at,
                    valid=row["valid_time"],
                    lead=int(
                        (row["valid_time"] - forecast.issued_at).total_seconds() // 3600
                    ),
                    member=row["member_id"],
                    value=row["value"],
                )
                for row in forecast.ensemble.values.iter_rows(named=True)
            ],
        )
        conn.execute(
            sa.text(
                "INSERT INTO forecast_evidence (forecast_id, status, "
                "manifest_json, reason) "
                "VALUES (:id, 'evidence_incomplete', :manifest, :reason)"
            ),
            {
                "id": forecast.id,
                "manifest": '{"schema_version":1}',
                "reason": "prediction_capture_unavailable;thresholds_unavailable",
            },
        )
        original = _legacy_forecast_content(conn, forecast.id)
        assert len(original["header"]) == 1
        assert original["values"]
        assert len(original["values"]) == forecast.ensemble.values.height
        assert original["evidence"][0]["status"] == "evidence_incomplete"
    with engine.connect() as conn:
        old_forecast_columns = {
            column["name"] for column in sa.inspect(conn).get_columns("forecasts")
        }
        assert (
            "forecast_publication_decisions" not in sa.inspect(conn).get_table_names()
        )

    command.upgrade(config, "0063")
    with engine.connect() as conn:
        assert old_forecast_columns == {
            column["name"] for column in sa.inspect(conn).get_columns("forecasts")
        }
        assert _legacy_forecast_content(conn, forecast.id) == original
        assert {
            "forecast_publication_decisions",
            "forecast_publication_selections",
            "forecast_publication_events",
            "forecast_publication_sequence",
            "protected_backup_health",
            "protected_backup_forecast_proofs",
        }.issubset(sa.inspect(conn).get_table_names())
        assert (
            conn.scalar(
                sa.text(
                    "SELECT next_value FROM forecast_publication_sequence WHERE id=1"
                )
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname LIKE "
                    "'trg_forecast_publication_%'"
                )
            )
            == 5
        )

    command.downgrade(config, "0062")
    with engine.connect() as conn:
        assert (
            "forecast_publication_decisions" not in sa.inspect(conn).get_table_names()
        )
        assert old_forecast_columns == {
            column["name"] for column in sa.inspect(conn).get_columns("forecasts")
        }

        assert _legacy_forecast_content(conn, forecast.id) == original
