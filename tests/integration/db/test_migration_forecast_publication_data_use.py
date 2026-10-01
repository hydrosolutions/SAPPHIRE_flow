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
                "(id,attempt_id,station_id,model_id,issued_at,parameter,units,"
                "representation,time_step_seconds,values,qc_status)"
                " VALUES "
                "(:id,:attempt,:sid,:mid,:issued,'discharge','m3/s','members',3600,"
                "'{}','qc_failed')"
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
                f"(id,station_id,model_id,issued_at,parameter,units,representation,"
                f"data_use,input_lineage{extra_cols})"
                f" VALUES "
                f"(:id,:sid,:mid,:issued,'discharge','m3/s','members',"
                f"'expired_rating_test',:lineage{extra_values})"
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


def _legacy_rows(conn: sa.Connection) -> dict[str, list[str]]:
    """Snapshot all legacy tables without latest-schema metadata or stores."""
    tables = sa.inspect(conn).get_table_names(schema="public")
    return {
        table: list(
            conn.execute(
                sa.text(
                    "SELECT row_to_json(t)::text FROM public."
                    + conn.dialect.identifier_preparer.quote(table)
                    + " t ORDER BY row_to_json(t)::text"
                )
            ).scalars()
        )
        for table in tables
    }


@pytest.mark.parametrize(
    "contradiction",
    [
        "reviewed",
        "published",
        "selection",
        "decision_current",
        "decision_replaced",
        "event_current",
        "event_replaced",
    ],
)
def test_upgrade_refuses_legacy_test_publication_without_rewriting_history(
    migration_engine: tuple[sa.Engine, str],
    contradiction: str,
) -> None:
    import json

    from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

    engine, url = migration_engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0069")
    sid, uid, test_id, ordinary_id, decision_id = (uuid4() for _ in range(5))
    args = dict(
        sid=sid,
        uid=uid,
        tenant=DEFAULT_TENANT_ID,
        test_id=test_id,
        ordinary_id=ordinary_id,
        decision_id=decision_id,
        issued=_ISSUED_A,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO stations (id,code,name,location,station_kind,timezone,"
                "measured_parameters,network,tenant_id) VALUES "
                "(:sid,'preflight','Preflight fixture',ST_SetSRID(ST_MakePoint(7,46),"
                "4326),"
                "'river','UTC',ARRAY['discharge'],'fixture',:tenant)"
            ),
            args,
        )
        conn.execute(
            sa.text(
                "INSERT INTO models (id,display_name,artifact_scope,description) "
                "VALUES ('preflight','Preflight','station','synthetic')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO users (id,tenant_id,display_name) VALUES (:uid,:tenant,"
                "'Fixture')"
            ),
            args,
        )
        # Only the disposable fixture's exact dormant INSERT guard is removed.
        # All immutable/lineage/FK/append-only guards remain active and it is restored.
        conn.execute(
            sa.text("DROP TRIGGER trg_forecast_test_write_refused ON forecasts")
        )
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
        conn.execute(
            sa.text(
                "INSERT INTO forecasts (id,station_id,model_id,issued_at,parameter,"
                "units,representation,status,data_use,input_lineage) "
                "VALUES (:test_id,:sid,'preflight',:issued,'discharge','m3/s',"
                "'members',:status,'expired_rating_test',:lineage),"
                "(:ordinary_id,:sid,'preflight',:issued,'discharge','m3/s','members',"
                "'raw','standard',NULL)"
            ),
            {
                **args,
                "lineage": lineage,
                "status": contradiction
                if contradiction in {"reviewed", "published"}
                else "raw",
            },
        )
        conn.execute(
            sa.text(
                "CREATE TRIGGER trg_forecast_test_write_refused BEFORE INSERT ON "
                "forecasts "
                "FOR EACH ROW EXECUTE FUNCTION public.forecast_test_write_refused()"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO forecast_publication_selections (tenant_id,station_id,"
                "parameter,issued_at,selected_forecast_id,version) "
                "VALUES (:tenant,:sid,'discharge',:issued,:selected,1)"
            ),
            {
                **args,
                "selected": test_id if contradiction == "selection" else ordinary_id,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO forecast_publication_decisions (id,tenant_id,station_id,"
                "parameter,issued_at,forecast_id,actor_user_id,action,"
                "selection_version,forecast_version,preservation_at_publish,"
                "replaced_forecast_id,idempotency_key,request_sha256,created_at) "
                "VALUES (:decision_id,:tenant,:sid,'discharge',:issued,:current,:uid,"
                "'publish',1,1,'backup_pending',:replaced,'fixture',:digest,:issued)"
            ),
            {
                **args,
                "current": test_id
                if contradiction == "decision_current"
                else ordinary_id,
                "replaced": test_id if contradiction == "decision_replaced" else None,
                "digest": "a" * 64,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO forecast_publication_events (sequence,decision_id,"
                "event_type,forecast_id,replaced_forecast_id,actor_user_id,created_at) "
                "VALUES (1,:decision_id,'published',:current,:replaced,:uid,:issued)"
            ),
            {
                **args,
                "current": test_id if contradiction == "event_current" else ordinary_id,
                "replaced": test_id if contradiction == "event_replaced" else None,
            },
        )
        before = _legacy_rows(conn)
    with pytest.raises(
        RuntimeError, match="test forecast publication exists; migration refused"
    ):
        command.upgrade(config, "0070")
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0069"
        assert _legacy_rows(conn) == before
