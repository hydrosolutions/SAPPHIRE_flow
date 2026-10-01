from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _seed_model,
    _seed_station,
)


def test_legacy_rejection_defaults_and_roundtrip(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, url = migration_engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0069")
    rid = uuid4()
    with engine.begin() as conn:
        sid = _seed_station(conn)
        mid = _seed_model(conn)
        conn.execute(
            sa.text(
                "INSERT INTO rejected_forecasts "
                "(id,attempt_id,station_id,model_id,issued_at,parameter,units,representation,time_step_seconds,values,qc_status)"
                " VALUES "
                "(:id,:attempt,:sid,:mid,:issued,'discharge','m3/s','members',3600,'{}','qc_failed')"
            ),
            dict(id=rid, attempt=uuid4(), sid=sid, mid=mid, issued=_ISSUED_A),
        )
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(
            sa.text(
                "SELECT data_use,input_lineage FROM rejected_forecasts WHERE id=:id"
            ),
            {"id": rid},
        ).one() == ("standard", None)
    command.downgrade(config, "0069")
    with engine.connect() as conn:
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM rejected_forecasts WHERE id=:id"),
                {"id": rid},
            )
            == 1
        )
    command.upgrade(config, "head")


@pytest.mark.parametrize("relation", ["forecasts", "rejected_forecasts"])
def test_downgrade_refuses_existing_test_data(
    migration_engine: tuple[sa.Engine, str], relation: str
) -> None:
    import json

    engine, url = migration_engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    with engine.begin() as conn:
        sid = _seed_station(conn)
        mid = _seed_model(conn)
        lineage = json.dumps(
            {
                "snapshots": [],
                "static_attributes": [
                    {
                        "station_id": str(sid),
                        "source": "fixture",
                        "version": "v1",
                        "values": {"area": 1.0},
                    }
                ],
                "provisional_discharge_fingerprints": [],
                "contributor_forecast_ids": [],
                "transformation_versions": ["v1"],
            }
        )
        trigger = (
            "trg_forecast_test_write_refused"
            if relation == "forecasts"
            else "trg_rejected_forecast_test_write_refused"
        )
        function = (
            "forecast_test_write_refused"
            if relation == "forecasts"
            else "rejected_forecast_test_write_refused"
        )
        conn.execute(sa.text(f"DROP TRIGGER {trigger} ON {relation}"))
        extra_cols = (
            ",attempt_id,time_step_seconds,values,qc_status"
            if relation == "rejected_forecasts"
            else ""
        )
        extra_values = (
            ",:attempt,3600,'{}','qc_failed'"
            if relation == "rejected_forecasts"
            else ""
        )
        conn.execute(
            sa.text(
                f"INSERT INTO {relation} "
                f"(id,station_id,model_id,issued_at,parameter,units,representation,data_use,input_lineage{extra_cols})"
                f" VALUES "
                f"(:id,:sid,:mid,:issued,'discharge','m3/s','members','expired_rating_test',:lineage{extra_values})"
            ),
            dict(
                id=uuid4(),
                sid=sid,
                mid=mid,
                issued=_ISSUED_A,
                lineage=lineage,
                attempt=uuid4(),
            ),
        )
        conn.execute(
            sa.text(
                f"CREATE TRIGGER {trigger} BEFORE INSERT ON {relation} FOR EACH "
                f"ROW EXECUTE FUNCTION public.{function}()"
            )
        )
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(config, "0069")
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0070"
