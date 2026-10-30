from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa  # noqa: TC002 -- role fixture annotations

from sapphire_flow.flows.bafu_ingest import dispatch_bafu_captures
from sapphire_flow.store.bafu_unit_writer import (
    PgBafuUnitWriter,
    bafu_unit_transaction_factory,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.observation import HydroScraperBatchResult, StationFetchOutcome
from tests.integration.db.test_bafu_unit_guards import batch as batch
from tests.integration.db.test_bafu_unit_guards import bootstrapped as bootstrapped
from tests.integration.db.test_bafu_unit_guards import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)
from tests.integration.db.test_bafu_unit_guards import protected as protected
from tests.integration.db.test_bafu_unit_guards import role_harness as role_harness
from tests.integration.db.test_bafu_unit_guards import worker as worker
from tests.integration.store.test_bafu_unit_writer import QUOTA, later

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch


def test_dispatch_persists_once_and_trusted_reader_sees_canonical(
    worker: sa.Engine, protected: BafuStationCaptureBatch
) -> None:
    factory = bafu_unit_transaction_factory(worker.url)
    writer = PgBafuUnitWriter(factory, quota=QUOTA)
    with worker.connect() as conn:
        stations = [
            s
            for s in PgStationStore(conn).fetch_all_stations()
            if s.id == protected.station_id
        ]
    counts = []
    for candidate in (protected, later(protected)):
        outcome = StationFetchOutcome(
            station_id=candidate.station_id,
            observations=candidate.observations,
            source_captures=candidate.captures,
            failure_cause=None,
            failure_detail=None,
        )
        result = dispatch_bafu_captures(
            HydroScraperBatchResult(outcomes=(outcome,)),
            stations,
            candidate.registry,
            writer,
        )
        assert result.failed_station_ids == frozenset()
        assert result.canonical_observations[0].value == 0.022
        counts.append(result.observations_written)
    assert counts == [1, 0]
    with worker.connect() as conn:
        rows = PgObservationStore(conn).fetch_observations(
            protected.station_id,
            "discharge",
            protected.received_at,
            protected.received_at + timedelta(days=1),
        )
        assert [row.value for row in rows] == [0.022]


def test_factory_does_not_change_shared_autocommit(worker: sa.Engine) -> None:
    with worker.connect().execution_options(isolation_level="AUTOCOMMIT") as shared:
        factory = bafu_unit_transaction_factory(shared.engine.url)
        with factory() as dedicated:
            assert (
                dedicated.connection.driver_connection
                is not shared.connection.driver_connection
            )
            assert dedicated.get_isolation_level() == "READ COMMITTED"
            assert dedicated.connection.driver_connection.autocommit is False
            assert shared.connection.driver_connection.autocommit is True


def test_unknown_unit_dispatch_captures_without_canonical_leak(
    worker: sa.Engine, batch: BafuStationCaptureBatch
) -> None:
    writer = PgBafuUnitWriter(bafu_unit_transaction_factory(worker.url), quota=QUOTA)
    with worker.connect() as conn:
        stations = [
            s
            for s in PgStationStore(conn).fetch_all_stations()
            if s.id == batch.station_id
        ]
    outcome = StationFetchOutcome(
        station_id=batch.station_id,
        observations=(),
        source_captures=batch.captures,
        failure_cause=None,
        failure_detail=None,
    )
    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=(outcome,)), stations, batch.registry, writer
    )
    assert result.results[0].counts.quarantined == 1
    assert result.canonical_observations == ()
    assert result.observations_written == 0
    with worker.connect() as conn:
        store = PgObservationStore(conn)
        assert (
            store.fetch_observations(
                batch.station_id,
                "discharge",
                batch.received_at,
                batch.received_at + timedelta(days=1),
            )
            == []
        )
        # The existing timestamp-only cursor tracks stored canonical rows,
        # independently of trusted visibility; quarantine is not a raw row.
        assert (
            store.fetch_latest_capture_timestamp(batch.station_id, "discharge") is None
        )
