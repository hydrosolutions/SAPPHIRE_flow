# ruff: noqa: F811 -- imported pytest fixture.
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_output import DailyOutputContract
from sapphire_flow.types.enums import EnsembleRepresentation
from tests.integration.store.test_daily_forecast_claim_store import (  # noqa: F401
    NOW,
    assignment,
)


class TestDailyForecastRuntime:
    def test_runtime_bundle_commits_claim_independently(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        with isolated_committed_db_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            stores = make_pg_stores(conn)
            store = stores["daily_forecast_store"]
            assert isinstance(store, PgDailyForecastStore)
            claim = store.claim(
                key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
            )
            assert claim is not None
        observed = PgDailyForecastStore(isolated_committed_db_engine.begin).read_day(
            key
        )
        assert observed.latest is not None
        assert observed.latest.claim == claim

    def test_transaction_factory_rolls_back_failed_work(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        from sapphire_flow.store.daily_forecast_store import (
            daily_forecast_transaction_factory,
        )

        key, _ = assignment
        query = sa.text("SELECT description FROM station_groups WHERE id = :id")
        with isolated_committed_db_engine.connect() as conn:
            before = conn.execute(query, {"id": key.group_id}).scalar_one()
        begin = daily_forecast_transaction_factory(isolated_committed_db_engine.url)
        with pytest.raises(RuntimeError, match="rollback test"), begin() as txn:
            txn.execute(
                sa.text(
                    "UPDATE station_groups SET description = :description "
                    "WHERE id = :id"
                ),
                {"description": "uncommitted daily test", "id": key.group_id},
            )
            raise RuntimeError("rollback test")
        with isolated_committed_db_engine.connect() as conn:
            assert conn.execute(query, {"id": key.group_id}).scalar_one() == before

    def test_horizon_bounds_survive_committed_read(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        contract = DailyOutputContract(
            parameter="discharge",
            units="m³/s",
            representation=EnsembleRepresentation.MEMBERS,
            time_step=timedelta(days=1),
            first_lead=timedelta(days=1),
            horizon_steps=10,
            minimum_horizon_steps=1,
            series_keys=(0.0, 1.0),
        )
        frozen = replace(
            frozen,
            output_contracts=(contract,),
            members=(replace(frozen.members[0], required_parameters=("discharge",)),),
        )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.claim(key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW)
        observed = store.read_day(key)
        assert observed.latest is not None
        assert observed.latest.claim.frozen.output_contracts == (contract,)
