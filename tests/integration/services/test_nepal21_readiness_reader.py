from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.services.nepal_onboarding import DeliveryObservationReader
from sapphire_flow.services.qc_datum import obs_qc_rule_version
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import StationId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_nepal21_concurrency import (
    race_generation as race_generation,
)
from tests.integration.db.test_role_bootstrap import role_harness as role_harness

if TYPE_CHECKING:
    from tests.integration.db.test_nepal21_concurrency import RaceGeneration


def test_readiness_reader_uses_accepted_generation_and_respects_policy_pin(
    race_generation: RaceGeneration,
) -> None:
    with race_generation.engine.connect() as conn:
        station_id = StationId(
            conn.scalar(
                sa.text("SELECT id FROM stations WHERE code='225' AND network='dhm'")
            )
        )
        policy = conn.scalar(
            sa.text(
                "SELECT qc_policy_sha256 FROM restricted_delivery_generations "
                "WHERE id=:id"
            ),
            {"id": race_generation.generation_id},
        )
        assert isinstance(policy, str)
        reader = DeliveryObservationReader(conn)
        start = ensure_utc(datetime(1991, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(1991, 1, 4, tzinfo=UTC))
        rows = reader.fetch_accepted_historical_observations(
            station_id,
            "discharge",
            start,
            end,
            qc_status=QcStatus.QC_PASSED,
            expected_qc_policy_sha256=policy,
        )
        assert [row.value for row in rows] == [2.0, 3.0]
        assert (
            reader.fetch_accepted_historical_observations(
                station_id,
                "discharge",
                start,
                end,
                qc_status=QcStatus.QC_PASSED,
                expected_qc_policy_sha256="f" * 64,
            )
            == []
        )


@pytest.mark.parametrize("delivery_id", [None, "unrelated-delivery"])
def test_missing_and_unrelated_tags_cannot_join_valid_accepted_neighbors(
    race_generation: RaceGeneration, delivery_id: str | None
) -> None:
    with race_generation.engine.connect() as conn:
        conn.begin()
        station_id = StationId(
            conn.scalar(
                sa.text("SELECT id FROM stations WHERE code='225' AND network='dhm'")
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO observations (id,station_id,timestamp,parameter,value,"
                "source,qc_status,qc_flags,qc_rule_version,delivery_id) VALUES "
                "(:id,:station,:at,'discharge',99.0,'manual_import','qc_passed',"
                "'[]'::jsonb,'irrelevant',:delivery)"
            ),
            {
                "id": uuid4(),
                "station": station_id,
                "at": datetime(1991, 1, 3, 12, tzinfo=UTC),
                "delivery": delivery_id,
            },
        )
        start = ensure_utc(datetime(1991, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(1991, 1, 4, tzinfo=UTC))
        base = PgObservationStore(conn).fetch_accepted_historical_observations(
            station_id,
            "discharge",
            start,
            end,
            qc_status=QcStatus.QC_PASSED,
        )
        assert [row.value for row in base] == [2.0, 3.0, 99.0]
        reader = DeliveryObservationReader(conn)
        assert [
            row.value
            for row in reader.fetch_accepted_historical_observations(
                station_id,
                "discharge",
                start,
                end,
                qc_status=QcStatus.QC_PASSED,
            )
        ] == [2.0, 3.0]
        assert [
            row.value
            for row in reader.fetch_observations(
                station_id,
                "discharge",
                start,
                end,
                qc_status=QcStatus.QC_PASSED,
            )
        ] == [2.0, 3.0]


@pytest.mark.parametrize("variant", ["nonmanual", "non_discharge", "stale_qc"])
def test_legacy_readiness_filters_have_valid_neighbor_controls(
    db_connection: sa.Connection,
    variant: Literal["nonmanual", "non_discharge", "stale_qc"],
) -> None:
    tenant_id = _tenant(db_connection)
    station_id = StationId(_station(db_connection, tenant_id))
    current_version = obs_qc_rule_version("discharge", None)
    parameter = "water_level" if variant == "non_discharge" else "discharge"
    statement = sa.text(
        "INSERT INTO observations (id,station_id,timestamp,parameter,value,"
        "source,qc_status,qc_flags,qc_rule_version,delivery_id) VALUES "
        "(:id,:station,:at,:parameter,:value,:source,'qc_passed',"
        "'[]'::jsonb,:version,'dhm-barkhk-2026-09-08')"
    )
    for invalid in (False, True):
        db_connection.execute(
            statement,
            {
                "id": uuid4(),
                "station": station_id,
                "at": datetime(1991, 1, 2 if invalid else 1, tzinfo=UTC),
                "parameter": parameter if invalid else "discharge",
                "value": 99.0 if invalid else 2.0,
                "source": "measured"
                if invalid and variant == "nonmanual"
                else "manual_import",
                "version": "stale"
                if invalid and variant == "stale_qc"
                else current_version,
            },
        )
    start = ensure_utc(datetime(1991, 1, 1, tzinfo=UTC))
    end = ensure_utc(datetime(1991, 1, 4, tzinfo=UTC))
    base = PgObservationStore(db_connection).fetch_accepted_historical_observations(
        station_id,
        parameter,
        start,
        end,
        qc_status=QcStatus.QC_PASSED,
    )
    assert 99.0 in [row.value for row in base]
    reader = DeliveryObservationReader(db_connection)
    assert [
        row.value
        for row in reader.fetch_accepted_historical_observations(
            station_id,
            "discharge",
            start,
            end,
            qc_status=QcStatus.QC_PASSED,
        )
    ] == [2.0]
    if variant == "non_discharge":
        assert (
            reader.fetch_accepted_historical_observations(
                station_id,
                "water_level",
                start,
                end,
                qc_status=QcStatus.QC_PASSED,
            )
            == []
        )
