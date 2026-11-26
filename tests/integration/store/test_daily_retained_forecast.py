"""Issue #581: a failed later attempt keeps an earlier same-day result visible."""

from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.api.routes.api_swiss_overview import _cmal
from sapphire_flow.services.evaluation_forecasts import EvaluationStores
from sapphire_flow.services.swiss_overview import (
    CurrentHeadline,
    read_current_headlines,
)
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast import (
    DailyAttemptState,
    ScheduledDailyForecast,
)
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberReason,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_operator_retry import (
    OperatorDailyForecast,
    OperatorRetryRequest,
    roster_sha256,
)
from sapphire_flow.types.daily_output import DailyOutputContract, DailyOutputEvidence
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.evaluation_forecast import EvaluationReadSettings
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import PrincipalId, StationId
from sapphire_flow.types.swiss_overview import HeadlineAbsence
from tests.conftest import make_forecast_ensemble, make_station_config
from tests.integration.store.test_daily_forecast_claim_store import (
    NOW,
    _later,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_forecast_store import _make_forecast

AS_OF = ensure_utc(NOW + timedelta(hours=1, minutes=30))


@dataclass
class Day:
    engine: sa.Engine
    store: PgDailyForecastStore
    key: DailyClaimKey
    frozen: FrozenDailyAssignment
    a: StationId
    b: StationId

    def claim(self, key: DailyClaimKey | None = None) -> DailyClaim:
        claim = self.store.claim(
            key or self.key,
            self.frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        assert isinstance(claim, DailyClaim)
        return claim

    def failed(
        self,
        station: StationId,
        state: DailyMemberState = DailyMemberState.MODEL_FAILED,
    ) -> DailyMemberOutcome:
        member = next(m for m in self.frozen.members if m.station_id == station)
        if state is DailyMemberState.INPUT_EXCLUDED:
            return replace(
                member, state=state, reason=DailyMemberReason.MISSING_REQUIRED_TARGET
            )
        return replace(member, state=state, reason=DailyMemberReason.MODEL_FAILURE)

    def produced(
        self, claim: DailyClaim, station: StationId
    ) -> tuple[DailyMemberOutcome, DailyOutputEvidence]:
        issued = claim.key.context.issue_time
        provenance = DailyInputProvenance(
            nwp_source=NwpCycleSource.PRIMARY,
            nwp_reference_time=issued,
            nwp_age_hours=0.0,
        )
        forecast = _make_forecast(
            station,
            claim.key.model_id,
            claim.frozen.artifact_id,
            issued_at=issued,
            n_steps=2,
            n_members=2,
            nwp_cycle_reference_time=issued,
        )
        ensemble = make_forecast_ensemble(
            station_id=station,
            model_id=claim.key.model_id,
            parameter="discharge",
            n_steps=2,
            n_members=2,
        )
        ensemble = replace(
            ensemble,
            issued_at=issued,
            values=ensemble.values.with_columns(
                pl.col("valid_time") + (issued - ensemble.issued_at)
            ),
        )
        forecast = replace(forecast, ensemble=ensemble, qc_status=QcStatus.QC_PASSED)
        with self.engine.connect() as conn:
            output_id = PgForecastStore(
                conn, transaction_factory=self.engine.begin
            ).store_forecast(forecast)
        reference = DailyOutputReference(
            key=ForecastOutputKey(
                station_id=station,
                model_id=claim.key.model_id,
                issued_at=issued,
                parameter="discharge",
            ),
            source=DailyOutputSource.FORECAST,
            output_id=output_id,
        )
        member = next(m for m in self.frozen.members if m.station_id == station)
        outcome = replace(
            member,
            state=DailyMemberState.PRODUCED,
            references=(reference,),
            provenance=provenance,
        )
        evidence = DailyOutputEvidence(
            reference=reference,
            ensemble=ensemble,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=(),
            provenance=provenance,
        )
        return outcome, evidence

    def finalize(
        self,
        claim: DailyClaim,
        *,
        produce: tuple[StationId, ...] = (),
        fail: tuple[StationId, ...] = (),
    ) -> DailyAttemptState:
        outcomes: list[DailyMemberOutcome] = []
        evidence: list[DailyOutputEvidence] = []
        for station in produce:
            outcome, item = self.produced(claim, station)
            outcomes.append(outcome)
            evidence.append(item)
        outcomes.extend(self.failed(station) for station in fail)
        return self.store.finalize(
            claim,
            outcomes=tuple(outcomes),
            evidence=tuple(evidence),
            finished_at=NOW,
        )

    def headlines(self, as_of: UtcDatetime = AS_OF) -> dict[StationId, CurrentHeadline]:
        settings = EvaluationReadSettings(
            tenant_id=self.key.tenant_id,
            deployment=self.key.deployment,
            group_ids=(self.key.group_id,),
            daily_model_id=self.key.model_id,
            parameters=("discharge",),
        )
        with self.engine.connect() as conn:
            stores = EvaluationStores(
                daily=self.store,
                forecasts=PgForecastStore(conn, transaction_factory=self.engine.begin),
                rejected=PgRejectedForecastStore(
                    conn, transaction_factory=self.engine.begin
                ),
                groups=PgStationGroupStore(conn, transaction_factory=self.engine.begin),
            )
            return dict(
                read_current_headlines(
                    station_ids=[self.a, self.b],
                    settings=settings,
                    stores=stores,
                    as_of=as_of,
                )
            )

    def payload(self, station: StationId, as_of: UtcDatetime = AS_OF) -> dict:
        headline = self.headlines(as_of)[station]
        return _cmal(headline, as_of=as_of, withheld=False).model_dump(mode="json")


@pytest.fixture
def day(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> Day:
    key, frozen = assignment
    second = StationId(uuid4())
    with isolated_committed_db_engine.begin() as conn:
        PgStationStore(conn).store_station(
            make_station_config(
                station_id=second, code=f"ret-{second}", tenant_id=key.tenant_id
            )
        )
    first = frozen.members[0].station_id
    members = tuple(
        replace(frozen.members[0], station_id=sid, required_parameters=("discharge",))
        for sid in (first, second)
    )
    contract = DailyOutputContract(
        parameter="discharge",
        units="m³/s",
        representation=EnsembleRepresentation.MEMBERS,
        time_step=timedelta(hours=1),
        first_lead=timedelta(hours=1),
        horizon_steps=2,
        series_keys=(0.0, 1.0),
    )
    frozen = replace(frozen, members=members, output_contracts=(contract,))
    return Day(
        engine=isolated_committed_db_engine,
        store=PgDailyForecastStore(isolated_committed_db_engine.begin),
        key=key,
        frozen=frozen,
        a=first,
        b=second,
    )


def _label(payload: dict) -> dict:
    assert payload["state"] != "unavailable", payload
    return payload["retained_result"]


class TestRetainedAfterFailedRetry:
    def test_incident_all_failed_retry_keeps_the_morning_forecast(
        self, day: Day
    ) -> None:
        first = day.claim()
        assert (
            day.finalize(first, produce=(day.a,), fail=(day.b,))
            is DailyAttemptState.INCOMPLETE
        )
        retry = day.claim(_later(day.key))
        assert day.finalize(retry, fail=(day.a, day.b)) is DailyAttemptState.INCOMPLETE
        payload = day.payload(day.a)
        label = _label(payload)
        assert payload["state"] == "available"
        snapshot = day.store.read_day(day.key)
        assert snapshot.authoritative is not None
        assert snapshot.authoritative.claim.id == retry.id
        assert tuple(s.claim.id for s in snapshot.earlier) == (first.id,)
        assert label == {
            "kind": "earlier_attempt_after_failed_retry",
            "original_issued_at": NOW.isoformat().replace("+00:00", "Z"),
            "original_attempt_id": str(first.id),
            "superseding_attempt_id": str(retry.id),
            "superseding_state": "model_failed",
            "superseding_reason": "model_failure",
        }
        # The station the morning run never produced stays unavailable.
        assert day.payload(day.b) == {
            "state": "unavailable",
            "reason": HeadlineAbsence.NO_CURRENT_CANDIDATE.value,
        }

    def test_partial_retry_uses_the_retry_where_it_produced(self, day: Day) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        retry = day.claim(_later(day.key))
        day.finalize(retry, produce=(day.b,), fail=(day.a,))
        fresh = day.headlines()
        assert fresh[day.b].candidate is not None and fresh[day.b].retained is None
        retained = fresh[day.a].retained
        assert fresh[day.a].candidate is not None
        assert retained is not None and retained.earlier_attempt_id == first.id

    def test_no_failed_retry_has_no_label(self, day: Day) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        payload = day.payload(day.a)
        assert payload["state"] == "available"
        assert payload["retained_result"] is None
        assert day.store.read_day(day.key).earlier == ()

    def test_expired_earlier_result_is_unavailable_not_retained(self, day: Day) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        day.finalize(day.claim(_later(day.key)), fail=(day.a, day.b))
        # The horizon is two hourly steps from the issue time.
        late = ensure_utc(NOW + timedelta(hours=5))
        headline = day.headlines(late)[day.a]
        assert headline.candidate is None and headline.retained is None
        assert headline.absence is HeadlineAbsence.EXPIRED_HORIZON

    def test_superseded_earlier_record_is_unavailable(self, day: Day) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        day.finalize(day.claim(_later(day.key)), fail=(day.a, day.b))
        with day.engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE forecasts SET status = 'superseded'"),
            )
        headline = day.headlines()[day.a]
        assert headline.candidate is None and headline.retained is None
        assert headline.absence is HeadlineAbsence.NO_CURRENT_CANDIDATE


class TestOperatorChain:
    def _operator_leaf(self, day: Day, parent: DailyClaim) -> object:
        request = OperatorRetryRequest(
            request_id=uuid4(),
            parent_id=parent.id,
            key=replace(day.key, context=OperatorDailyForecast(target_date=NOW.date())),
            artifact_id=day.frozen.artifact_id,
            artifact_sha256="c" * 64,
            configuration_sha256=day.frozen.configuration_sha256,
            roster_sha256=roster_sha256(day.frozen),
            actor=PrincipalId("operator"),
            reason="Deploy repaired runtime",
        )
        return day.store.claim_operator_retry(
            request,
            day.frozen,
            owner_id=uuid4(),
            started_at=NOW,
            software_version="test",
        ).claim

    def test_operator_leaf_with_summary_permits_retention_without_does_not(
        self, day: Day
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        second = day.claim(_later(day.key))
        day.store.fail(second, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
        leaf = self._operator_leaf(day, second)
        # An in-flight operator head has no summary: authority None, no fallback.
        snapshot = day.store.read_day(day.key)
        assert snapshot.authoritative is None and snapshot.earlier == ()
        assert day.headlines()[day.a].candidate is None
        day.store.finalize(
            leaf,  # type: ignore[arg-type]
            outcomes=(day.failed(day.a), day.failed(day.b)),
            evidence=(),
            finished_at=NOW,
        )
        snapshot = day.store.read_day(day.key)
        assert snapshot.authoritative is not None
        assert tuple(s.claim.id for s in snapshot.earlier) == (first.id,)
        assert day.headlines()[day.a].retained is not None


def test_earlier_never_includes_another_issue_day(day: Day) -> None:
    yesterday = replace(
        day.key,
        context=ScheduledDailyForecast(
            scheduled_tick=ensure_utc(NOW - timedelta(days=1))
        ),
    )
    first = day.claim(yesterday)
    day.finalize(first, produce=(day.a,), fail=(day.b,))
    today = day.claim()
    day.finalize(today, fail=(day.a, day.b))
    snapshot = day.store.read_day(day.key)
    assert snapshot.earlier == ()
    headline = day.headlines()[day.a]
    assert headline.candidate is None and headline.retained is None
    assert snapshot.authoritative is not None
    assert snapshot.authoritative.claim.id == today.id
