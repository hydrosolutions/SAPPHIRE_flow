from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_nepal21_concurrency import (
    race_generation as race_generation,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness

if TYPE_CHECKING:
    from tests.integration.db.test_nepal21_concurrency import RaceGeneration


@pytest.mark.parametrize("table", ["observations", "rating_curves"])
@pytest.mark.parametrize("operation", ["INSERT", "UPDATE", "DELETE"])
def test_writer_refuses_unrelated_native_dml(
    race_generation: RaceGeneration,
    table: Literal["observations", "rating_curves"],
    operation: Literal["INSERT", "UPDATE", "DELETE"],
) -> None:
    with race_generation.engine.begin() as conn:
        station_id = _station(
            conn, race_generation.tenant_id, code="unrelated-" + str(uuid.uuid4())
        )
        conn.execute(
            sa.text("UPDATE stations SET network='bafu' WHERE id=:id"),
            {"id": station_id},
        )
        row_id = uuid.uuid4()
        common = {
            "id": row_id,
            "station": station_id,
            "at": datetime(1990, 1, 1, tzinfo=UTC),
            "version": 1,
            "points": json.dumps(
                [{"water_level": 0, "discharge": 1}, {"water_level": 1, "discharge": 2}]
            ),
        }
        insert = (
            "INSERT INTO observations (id,station_id,timestamp,parameter,value,source) "
            "VALUES (:id,:station,:at,'discharge',1.0,'manual_import')"
            if table == "observations"
            else "INSERT INTO rating_curves (id,station_id,version,valid_from,"
            "points,interpolation) "
            "VALUES (:id,:station,:version,:at,CAST(:points AS jsonb),'linear')"
        )
        conn.execute(sa.text(insert), common)
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        assert (
            conn.scalar(sa.text("SELECT session_user"))
            == "sapphire_dhm21_delivery_writer"
        )
        if operation == "INSERT":
            statement = insert
            common = {
                **common,
                "id": uuid.uuid4(),
                "version": 2,
                "at": datetime(1991, 1, 1, tzinfo=UTC),
            }
        elif operation == "UPDATE":
            statement = (
                "UPDATE observations SET value=3.0 WHERE id=:id"
                if table == "observations"
                else "UPDATE rating_curves SET version=3 WHERE id=:id"
            )
        else:
            statement = f"DELETE FROM {table} WHERE id=:id"
        with (
            pytest.raises(DBAPIError, match="release-bound writer scope"),
            conn.begin_nested(),
        ):
            conn.execute(sa.text(statement), common)


def test_writer_refuses_append_after_qc_complete(
    race_generation: RaceGeneration,
) -> None:
    with race_generation.engine.begin() as conn:
        station_id = conn.scalar(
            sa.text("SELECT id FROM stations WHERE tenant_id=:tenant AND code='447'"),
            {"tenant": race_generation.tenant_id},
        )
        delivery = conn.scalar(
            sa.text(
                "SELECT delivery_id FROM restricted_delivery_generations WHERE id=:id"
            ),
            {"id": race_generation.generation_id},
        )
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_dhm21_delivery_writer")
        )
        with (
            pytest.raises(DBAPIError, match="release-bound writer scope"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO observations (id,station_id,timestamp,parameter,"
                    "value,source,delivery_id) VALUES (:id,:station,:at,'discharge',"
                    "1.0,'manual_import',:delivery)"
                ),
                {
                    "id": uuid.uuid4(),
                    "station": station_id,
                    "at": datetime(1993, 1, 1, tzinfo=UTC),
                    "delivery": delivery,
                },
            )


@pytest.mark.parametrize("principal", ["sapphire_operator", "test"])
def test_predecessor_resurrection_refused_after_selected_generation(
    race_generation: RaceGeneration,
    principal: Literal["sapphire_operator", "test"],
) -> None:
    with race_generation.engine.begin() as conn:
        station_id = conn.scalar(
            sa.text("SELECT id FROM stations WHERE tenant_id=:tenant AND code='447'"),
            {"tenant": race_generation.tenant_id},
        )
        conn.execute(sa.text(f"SET LOCAL SESSION AUTHORIZATION {principal}"))
        assert conn.scalar(sa.text("SELECT session_user")) == principal
        with (
            pytest.raises(DBAPIError, match="predecessor resurrection"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO observations (id,station_id,timestamp,parameter,"
                    "value,source,delivery_id) VALUES (:id,:station,:at,'discharge',"
                    "1.0,'manual_import','dhm-barkhk-2026-09-08')"
                ),
                {
                    "id": uuid.uuid4(),
                    "station": station_id,
                    "at": datetime(1994, 1, 1, tzinfo=UTC),
                },
            )
