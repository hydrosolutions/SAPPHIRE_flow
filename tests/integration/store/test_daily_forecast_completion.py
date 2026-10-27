from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.exceptions import (
    DailyOutputRolledBackError,
    ModelOutputError,
    StoreError,
)
from sapphire_flow.services.daily_forecast_completion import complete_daily_group
from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
from sapphire_flow.services.run_station_forecast import StationForecastResult
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ForecastId, StationId
from tests.integration.store.test_daily_forecast_claim_store import NOW
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare
from tests.integration.store.test_daily_forecast_persistence import _forecasts


class TestDailyGroupCompletion:
    @pytest.mark.parametrize("case", ["accepted", "invalid", "missing", "post_state"])
    def test_finalizes_whole_summary(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        case: str,
    ) -> None:
        claim, member, prior = _prepare(isolated_committed_db_engine, assignment)
        forecasts = _forecasts(claim, prior)
        assert claim.frozen.artifact_id is not None
        if case == "invalid":
            forecasts = tuple(
                replace(f, ensemble=replace(f.ensemble, units="wrong"))
                for f in forecasts
            )
        station = StationForecastResult(
            station_id=member.station_id,
            model_id=claim.key.model_id,
            artifact_id=claim.frozen.artifact_id,
            forecasts=list(forecasts),
            new_state=b"bad" if case == "post_state" else None,
            ensembles={f.ensemble.parameter: f.ensemble for f in forecasts},
        )
        result = GroupForecastOutcome(
            results={} if case == "missing" else {member.station_id: station}
        )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with isolated_committed_db_engine.connect() as conn:
            rejected = PgRejectedForecastStore(
                conn, transaction_factory=isolated_committed_db_engine.begin
            )
            if case == "post_state":
                with pytest.raises(ModelOutputError, match="stateless.*post-state"):
                    complete_daily_group(
                        claim=claim,
                        result=result,
                        exclusions=(),
                        provenance={member.station_id: member.provenance},
                        forecast_writer=PgCommittedForecastWriter(
                            isolated_committed_db_engine.begin
                        ),
                        rejected_writer=rejected,
                        store=store,
                        clock=lambda: NOW,
                    )
            else:
                state = complete_daily_group(
                    claim=claim,
                    result=result,
                    exclusions=(),
                    provenance={member.station_id: member.provenance},
                    forecast_writer=PgCommittedForecastWriter(
                        isolated_committed_db_engine.begin
                    ),
                    rejected_writer=rejected,
                    store=store,
                    clock=lambda: NOW,
                )
                assert state.state is (
                    DailyAttemptState.COMPLETE
                    if case == "accepted"
                    else DailyAttemptState.INCOMPLETE
                )
        snapshot = store.read_day(claim.key)
        assert snapshot.latest is not None
        assert snapshot.latest.state is (
            DailyAttemptState.ACTIVE
            if case == "post_state"
            else DailyAttemptState.COMPLETE
            if case == "accepted"
            else DailyAttemptState.INCOMPLETE
        )

    def test_finish_clock_is_read_after_outputs_commit(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, member, prior = _prepare(isolated_committed_db_engine, assignment)
        forecasts = _forecasts(claim, prior)
        assert claim.frozen.artifact_id is not None
        station = StationForecastResult(
            station_id=member.station_id,
            model_id=claim.key.model_id,
            artifact_id=claim.frozen.artifact_id,
            forecasts=list(forecasts),
            new_state=None,
            ensembles={f.ensemble.parameter: f.ensemble for f in forecasts},
        )
        calls: list[UtcDatetime] = []
        finish = NOW + timedelta(seconds=10)

        def finish_clock() -> UtcDatetime:
            with isolated_committed_db_engine.connect() as observer:
                assert observer.execute(
                    sa.text("SELECT count(*) FROM forecasts WHERE station_id = :sid"),
                    {"sid": member.station_id},
                ).scalar_one() == len(forecasts)
            calls.append(finish)
            return finish

        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with isolated_committed_db_engine.connect() as conn:
            complete_daily_group(
                claim=claim,
                result=GroupForecastOutcome(results={member.station_id: station}),
                exclusions=(),
                provenance={member.station_id: member.provenance},
                forecast_writer=PgCommittedForecastWriter(
                    isolated_committed_db_engine.begin
                ),
                rejected_writer=PgRejectedForecastStore(
                    conn, transaction_factory=isolated_committed_db_engine.begin
                ),
                store=store,
                clock=finish_clock,
            )
        assert calls == [finish]
        snapshot = store.read_day(claim.key)
        assert snapshot.authoritative is not None
        assert snapshot.authoritative.finished_at == finish


class TestCompletionRollbackBoundary:
    def test_later_rollback_does_not_release_earlier_committed_outputs(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, member, prior = _prepare(isolated_committed_db_engine, assignment)
        second_id = StationId(uuid4())
        second = replace(claim.frozen.members[0], station_id=second_id)
        claim = replace(
            claim, frozen=replace(claim.frozen, members=(*claim.frozen.members, second))
        )
        assert claim.frozen.artifact_id is not None
        first_forecasts = _forecasts(claim, prior)
        second_forecasts = tuple(
            replace(
                f,
                station_id=second_id,
                ensemble=replace(f.ensemble, station_id=second_id),
            )
            for f in first_forecasts
        )
        results = {
            sid: StationForecastResult(
                station_id=sid,
                model_id=claim.key.model_id,
                artifact_id=claim.frozen.artifact_id,
                forecasts=list(forecasts),
                new_state=None,
                ensembles={f.ensemble.parameter: f.ensemble for f in forecasts},
            )
            for sid, forecasts in (
                (member.station_id, first_forecasts),
                (second_id, second_forecasts),
            )
        }

        class FirstCommittedThenRolledBack:
            def write_committed(
                self, forecasts: tuple[OperationalForecast, ...]
            ) -> dict[ForecastOutputKey, ForecastId]:
                if forecasts[0].station_id == second_id:
                    raise DailyOutputRolledBackError("second transaction rolled back")
                return {
                    ForecastOutputKey(
                        station_id=f.station_id,
                        model_id=f.model_id,
                        issued_at=f.issued_at,
                        parameter=f.ensemble.parameter,
                    ): ForecastId(uuid4())
                    for f in forecasts
                }

        with (
            isolated_committed_db_engine.connect() as conn,
            pytest.raises(StoreError, match="earlier daily output") as failure,
        ):
            complete_daily_group(
                claim=claim,
                result=GroupForecastOutcome(results=results),
                exclusions=(),
                provenance={
                    member.station_id: member.provenance,
                    second_id: member.provenance,
                },
                forecast_writer=FirstCommittedThenRolledBack(),
                rejected_writer=PgRejectedForecastStore(
                    conn, transaction_factory=isolated_committed_db_engine.begin
                ),
                store=PgDailyForecastStore(isolated_committed_db_engine.begin),
                clock=lambda: NOW,
            )
        assert not isinstance(failure.value, DailyOutputRolledBackError)
