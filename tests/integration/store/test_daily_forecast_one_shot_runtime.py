import os
from dataclasses import replace
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    model_artifacts,
    models,
    station_groups,
    stations,
    tenants,
)
from sapphire_flow.flows.daily_forecast_one_shot_runtime import (
    create_local_one_shot_outputs,
)
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptDecision, DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.ids import ForecastId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.integration.store.test_daily_forecast_claim_store import NOW, _later
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare
from tests.integration.store.test_daily_forecast_one_shot import (
    _forecasts,
    one_shot_key,
)


def stage_synthetic_assignment(
    source: sa.Engine,
    target: sa.Engine,
    key: DailyClaimKey,
    frozen: FrozenDailyAssignment,
) -> None:
    # Only these test-generated rows, never a production clone/export facility.
    identities = (
        (tenants, key.tenant_id),
        (stations, frozen.members[0].station_id),
        (station_groups, key.group_id),
        (models, key.model_id),
        (model_artifacts, frozen.artifact_id),
    )
    with source.connect() as src, target.begin() as dst:
        for table, identity in identities:
            row = (
                src.execute(sa.select(table).where(table.c.id == identity))
                .mappings()
                .one()
            )
            dst.execute(sa.insert(table).values(**dict(row)))


@pytest.mark.parametrize(
    "source", [DailyOutputSource.FORECAST, DailyOutputSource.REJECTED]
)
def test_owned_outputs_cannot_change_scheduled_database(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    monkeypatch: pytest.MonkeyPatch,
    source: DailyOutputSource,
) -> None:
    scheduled, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
    scheduled_store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    forecasts = _forecasts(scheduled, evidence)
    if source is DailyOutputSource.FORECAST:
        mapping = PgCommittedForecastWriter(
            isolated_committed_db_engine.begin
        ).write_committed(forecasts)
        refs = tuple(
            DailyOutputReference(key=k, source=source, output_id=v)
            for k, v in mapping.items()
        )
        evidence = tuple(
            replace(e, reference=r, qc_status=f.qc_status, qc_flags=f.qc_flags)
            for e, r, f in zip(evidence, refs, forecasts, strict=True)
        )
        outcome = replace(outcome, references=refs)
    scheduled_store.finalize(
        scheduled, outcomes=(outcome,), evidence=evidence, finished_at=NOW
    )
    before = scheduled_store.read_day(scheduled.key)
    recovery = scheduled_store.recovery_status(scheduled.key)
    with isolated_committed_db_engine.connect() as conn:
        before_rows = (
            conn.execute(
                sa.text("SELECT row_to_json(f)::text FROM forecasts f ORDER BY id")
            )
            .scalars()
            .all()
        )
        before_rejected = (
            conn.execute(
                sa.text(
                    "SELECT row_to_json(f)::text FROM rejected_forecasts f ORDER BY id"
                )
            )
            .scalars()
            .all()
        )
    # The existing fixture sets DATABASE_URL. Prove the factory ignores it and
    # does not overwrite it, even when it names a migrated scheduled database.
    configured = isolated_committed_db_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", configured)
    outputs = create_local_one_shot_outputs()
    try:
        assert outputs.resource_id
        assert os.environ["DATABASE_URL"] == configured
        with outputs.engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text("SELECT count(*) FROM daily_forecast_attempts")
                ).scalar_one()
                == 0
            )
        stage_synthetic_assignment(
            isolated_committed_db_engine,
            outputs.engine,
            scheduled.key,
            scheduled.frozen,
        )
        execution = outputs.ledger.start_one_shot(
            one_shot_key(scheduled.key),
            scheduled.frozen,
            execution_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        changed = tuple(
            replace(
                e,
                ensemble=replace(
                    e.ensemble,
                    values=e.ensemble.values.with_columns(
                        (pl.col("value") + 100).alias("value")
                    ),
                ),
            )
            for e in evidence
        )
        if source is DailyOutputSource.FORECAST:
            changed_forecasts = tuple(
                replace(f, id=ForecastId(uuid4()), ensemble=e.ensemble)
                for f, e in zip(forecasts, changed, strict=True)
            )
            mapping = outputs.accepted_writer.write_committed(changed_forecasts)
            for output_id, forecast in zip(
                mapping.values(), changed_forecasts, strict=True
            ):
                stored = outputs.accepted_reader.fetch_forecast(output_id)
                assert stored is not None
                assert stored.ensemble.values.equals(forecast.ensemble.values)
                with isolated_committed_db_engine.connect() as conn:
                    assert PgForecastStore(conn).fetch_forecast(output_id) is None
        else:
            entry = RejectedForecastEntry(
                attempt_id=execution.id,
                payload=RejectedAssignmentPayload(
                    station_id=outcome.station_id,
                    model_id=scheduled.key.model_id,
                    model_artifact_id=scheduled.frozen.artifact_id,
                    issued_at=NOW,
                    group_id=scheduled.key.group_id,
                    parameters=tuple(
                        RejectedParameterPayload(
                            ensemble=e.ensemble,
                            qc_status=e.qc_status,
                            qc_flags=e.qc_flags,
                        )
                        for e in changed
                    ),
                ),
            )
            mapping = outputs.rejected.write_committed(entry)
            for output_id in mapping.values():
                assert outputs.rejected.fetch_rejected_forecast(output_id) is not None
        refs = tuple(
            DailyOutputReference(key=k, source=source, output_id=v)
            for k, v in mapping.items()
        )
        changed = tuple(
            replace(e, reference=r) for e, r in zip(changed, refs, strict=True)
        )
        assert (
            outputs.ledger.finalize(
                execution,
                outcomes=(replace(outcome, references=refs),),
                evidence=changed,
                finished_at=NOW,
            )
            is DailyAttemptState.COMPLETE
        )
        assert (
            outputs.ledger.read_one_shot(execution.key, execution_id=execution.id).state
            is DailyAttemptState.COMPLETE
        )
        assert outputs.ledger.read_day(scheduled.key).latest is None
        assert (
            scheduled_store.read_one_shot(execution.key, execution_id=execution.id)
            is None
        )
        assert scheduled_store.read_day(scheduled.key) == before
        if source is DailyOutputSource.FORECAST:
            with isolated_committed_db_engine.connect() as conn:
                for item in evidence:
                    stored = PgForecastStore(conn).fetch_forecast(
                        item.reference.output_id
                    )
                    assert stored is not None
                    assert stored.ensemble.values.equals(item.ensemble.values)
        assert scheduled_store.recovery_status(scheduled.key) == recovery
        assert (
            scheduled_store.claim(
                _later(scheduled.key),
                scheduled.frozen,
                attempt_id=uuid4(),
                owner_id=uuid4(),
                started_at=NOW,
            )
            is DailyAttemptDecision.CLOSED
        )
        with isolated_committed_db_engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text("SELECT row_to_json(f)::text FROM forecasts f ORDER BY id")
                )
                .scalars()
                .all()
                == before_rows
            )
            assert (
                conn.execute(
                    sa.text(
                        "SELECT row_to_json(f)::text "
                        "FROM rejected_forecasts f ORDER BY id"
                    )
                )
                .scalars()
                .all()
                == before_rejected
            )
    finally:
        outputs.cleanup()


def test_each_factory_call_owns_a_fresh_resource() -> None:
    first = create_local_one_shot_outputs()
    try:
        second = create_local_one_shot_outputs()
        try:
            assert first.resource_id != second.resource_id
            with first.engine.begin() as conn:
                conn.execute(sa.text("CREATE TABLE owned_marker (id integer)"))
            with second.engine.connect() as conn:
                assert (
                    conn.execute(
                        sa.text("SELECT to_regclass('owned_marker')")
                    ).scalar_one()
                    is None
                )
            first.cleanup()
            with second.engine.connect() as conn:
                assert conn.execute(sa.text("SELECT 1")).scalar_one() == 1
        finally:
            second.cleanup()
    finally:
        first.cleanup()
