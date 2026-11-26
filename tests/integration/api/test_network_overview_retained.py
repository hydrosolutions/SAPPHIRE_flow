"""Issue #581 T2: the network overview keeps an earlier same-day daily result."""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

import sapphire_flow.store.daily_forecast_store as store_module
from sapphire_flow.api.network_overview import NetworkOverviewReader
from sapphire_flow.db.metadata import daily_forecast_attempts
from sapphire_flow.exceptions import DailyAuthorityIntegrityError
from sapphire_flow.store.daily_forecast_documents import outcomes_json
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_operator_retry import (
    OperatorDailyForecast,
    OperatorRetryRequest,
    roster_sha256,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.evaluation_forecast import EvaluationReadSettings
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import PrincipalId, StationId
from tests.integration.store.test_daily_forecast_claim_store import NOW, _later
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_retained_forecast import AS_OF, Day
from tests.integration.store.test_daily_retained_forecast import day as day

if TYPE_CHECKING:
    from sapphire_flow.types.daily_forecast_claim import DailyClaim, DailyClaimKey


class Reader:
    """`_daily_references` on the real store, counting the earlier-attempt reads."""

    def __init__(
        self,
        day: Day,
        monkeypatch: pytest.MonkeyPatch,
        group_ids: tuple[UUID, ...] | None = None,
    ) -> None:
        self.day = day
        self.calls: list[tuple[DailyClaimKey, ...]] = []
        original = PgDailyForecastStore.read_earlier_attempts

        def counting(store: PgDailyForecastStore, keys: Any, ids: Any) -> Any:
            self.calls.append(tuple(keys))
            return original(store, keys, ids)

        monkeypatch.setattr(PgDailyForecastStore, "read_earlier_attempts", counting)
        self.group_ids = group_ids or (day.key.group_id,)

    def run(self) -> tuple[dict, dict]:
        key = self.day.key
        settings = EvaluationReadSettings(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            group_ids=self.group_ids,  # type: ignore[arg-type]
            daily_model_id=key.model_id,
            parameters=("discharge",),
        )
        config = SimpleNamespace(
            evaluation_forecasts=SimpleNamespace(to_domain=lambda: settings)
        )
        sids = [self.day.a, self.day.b]
        assignments = {(sid, key.model_id) for sid in sids}
        with self.day.engine.connect() as conn:
            reader = NetworkOverviewReader(
                conn,
                None,  # type: ignore[arg-type]
                config,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
            )
            result, _, labels = reader._daily_references(
                sids,
                {sid: key.tenant_id for sid in sids},
                assignments,
                (ForecastDataUse.STANDARD,),
                AS_OF,
            )
        return result, labels

    @staticmethod
    def of(station: StationId, result: dict) -> tuple[Any, Any] | None:
        return next((v for k, v in result.items() if k[0] == station), None)


def _yesterday(day: Day) -> DailyClaimKey:
    return replace(
        day.key,
        context=ScheduledDailyForecast(
            scheduled_tick=ensure_utc(NOW - timedelta(days=1))
        ),
    )


def operator_leaf(day: Day, parent: DailyClaim) -> Any:
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
        request, day.frozen, owner_id=uuid4(), started_at=NOW, software_version="test"
    ).claim


def _fail_leaf(day: Day, leaf: Any) -> None:
    day.store.finalize(
        leaf,
        outcomes=(day.failed(day.a), day.failed(day.b)),
        evidence=(),
        finished_at=NOW,
    )


def _rejected(day: Day, parameter: str = "discharge") -> Any:
    return replace(
        day.failed(day.a),
        state=DailyMemberState.INTEGRITY_REJECTED,
        reason=DailyMemberReason.OUTPUT_INTEGRITY,
        references=(
            DailyOutputReference(
                key=ForecastOutputKey(
                    station_id=day.a,
                    model_id=day.key.model_id,
                    issued_at=day.key.context.issue_time,
                    parameter=parameter,
                ),
                source=DailyOutputSource.REJECTED,
                output_id=uuid4(),
            ),
        ),
    )


def _insert_attempt(day: Day, parent: Any, *members: Any) -> SimpleNamespace:
    """A scheduled attempt (ordinal 2) over `parent`, written with the given summary."""
    with day.engine.begin() as conn:
        row = (
            conn.execute(
                sa.text("SELECT * FROM daily_forecast_attempts WHERE id = :id"),
                {"id": parent.id},
            )
            .mappings()
            .one()
        )
        attempt = uuid4()
        conn.execute(
            sa.insert(daily_forecast_attempts).values(
                **{
                    **row,
                    "id": attempt,
                    "ordinal": 2,
                    "parent_id": parent.id,
                    "state": "incomplete",
                    "recovery": sa.null(),
                    "summary": json.loads(outcomes_json(members)),
                }
            )
        )
    return SimpleNamespace(id=attempt)


class TestRetainedReferences:
    def test_incident_failed_retry_supplies_the_earlier_attempt(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        retry = day.claim(_later(day.key))
        day.finalize(retry, fail=(day.a, day.b))
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        summary, ref = reader.of(day.a, result)  # type: ignore[misc]
        assert summary.claim.id == first.id
        assert ref.source is DailyOutputSource.FORECAST
        assert reader.of(day.b, result) is None
        (label,) = labels.values()
        assert label.earlier_attempt_id == first.id
        assert label.superseding_attempt_id == retry.id
        assert label.superseding_state is DailyMemberState.MODEL_FAILED
        assert len(reader.calls) == 1

    def test_partial_retry_labels_only_the_retained_station(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        retry = day.claim(_later(day.key))
        day.finalize(retry, produce=(day.b,), fail=(day.a,))
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        assert reader.of(day.a, result)[0].claim.id == first.id  # type: ignore[index]
        assert reader.of(day.b, result)[0].claim.id == retry.id  # type: ignore[index]
        assert {key[0] for key in labels} == {day.a}

    def test_healthy_day_never_reads_earlier_attempts(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        day.finalize(day.claim(), produce=(day.a, day.b))
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        assert len(result) == 2 and not labels and reader.calls == []

    def test_absent_member_of_another_group_does_not_trigger(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        day.finalize(day.claim(), produce=(day.a, day.b))
        other = UUID("00000000-0000-0000-0000-000000000001")  # sorts first
        with day.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO station_groups (id, tenant_id, name) "
                    "VALUES (:g, :t, :n)"
                ),
                {"g": other, "t": day.key.tenant_id, "n": "other"},
            )
            row = (
                conn.execute(
                    sa.text(
                        "SELECT * FROM daily_forecast_attempts WHERE group_id = :g"
                    ),
                    {"g": day.key.group_id},
                )
                .mappings()
                .one()
            )
            only_b = [m for m in row["summary"] if m["station_id"] == str(day.b)]
            conn.execute(
                sa.text(
                    "INSERT INTO daily_forecast_attempts (id, tenant_id, deployment,"
                    " group_id, model_id, data_use, issued_at, trigger_kind,"
                    " scheduled_tick, ordinal, owner_id, started_at, finished_at,"
                    " state, artifact_id, configuration_sha256, roster,"
                    " output_contracts, summary) VALUES (:id, :t, :d, :g, :m,"
                    " 'standard', :i, 'scheduled', :i, 1, :o, :s, :s, 'complete',"
                    " :a, :c, CAST(:r AS jsonb), CAST(:oc AS jsonb),"
                    " CAST(:su AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "t": day.key.tenant_id,
                    "d": day.key.deployment,
                    "g": other,
                    "m": day.key.model_id,
                    "i": row["issued_at"],
                    "o": uuid4(),
                    "s": row["started_at"],
                    "a": row["artifact_id"],
                    "c": row["configuration_sha256"],
                    "r": json.dumps(
                        [m for m in row["roster"] if m["station_id"] == str(day.b)]
                    ),
                    "oc": json.dumps(row["output_contracts"]),
                    "su": json.dumps(only_b),
                },
            )
        reader = Reader(day, monkeypatch, group_ids=(other, day.key.group_id))
        result, _ = reader.run()
        assert reader.of(day.a, result) is not None  # absent in `other`, still unseen
        assert reader.of(day.b, result) is not None
        assert reader.calls == []

    def test_failed_member_on_an_older_day_does_not_trigger(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = day.claim(_yesterday(day))
        day.finalize(old, fail=(day.a,), produce=(day.b,))
        day.finalize(day.claim(), produce=(day.a, day.b))
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        assert reader.of(day.a, result)[0].claim.key == day.key  # type: ignore[index]
        assert not labels and reader.calls == []

    def test_operator_head_without_summary_does_not_trigger(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        second = day.claim(_later(day.key))
        day.finalize(second, fail=(day.a, day.b))
        operator_leaf(day, second)
        reader = Reader(day, monkeypatch)
        result, _ = reader.run()
        assert result == {} and reader.calls == []

    def test_failed_member_without_retained_source_is_seen(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = day.claim(_yesterday(day))
        day.finalize(old, produce=(day.a, day.b))
        day.finalize(day.claim(), fail=(day.a,), produce=(day.b,))
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        assert reader.of(day.a, result) is None and not labels
        assert reader.of(day.b, result) is not None
        assert len(reader.calls) == 1

    def test_operator_leaf_over_a_produced_attempt_permits_retention(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        second = day.claim(_later(day.key))
        day.finalize(second, fail=(day.a, day.b))
        leaf = operator_leaf(day, second)
        _fail_leaf(day, leaf)
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        assert reader.of(day.a, result)[0].claim.id == first.id  # type: ignore[index]
        (label,) = labels.values()
        assert label.superseding_attempt_id == leaf.id

    def test_rejected_intermediate_attempt_is_never_the_retained_source(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        second = _insert_attempt(day, first, _rejected(day), day.failed(day.b))
        leaf = operator_leaf(day, second)
        _fail_leaf(day, leaf)
        reader = Reader(day, monkeypatch)
        result, labels = reader.run()
        summary, ref = reader.of(day.a, result)  # type: ignore[misc]
        assert summary.claim.id == first.id
        assert ref.source is DailyOutputSource.FORECAST
        (label,) = labels.values()
        assert label.superseding_attempt_id == leaf.id

    def test_retention_stops_at_the_first_produced_earlier_member(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # oldest produced (ok) -> second PRODUCED with a REJECTED reference -> leaf
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        produced = replace(
            next(
                m
                for m in day.store.read_day(day.key).authoritative.outcomes  # type: ignore[union-attr]
                if m.station_id == day.a
            ),
            references=(_rejected(day).references[0],),
        )
        second = _insert_attempt(day, first, produced, day.failed(day.b))
        leaf = operator_leaf(day, second)
        _fail_leaf(day, leaf)
        reader = Reader(day, monkeypatch)
        result, _ = reader.run()
        summary, ref = reader.of(day.a, result)  # type: ignore[misc]
        assert summary.claim.id == second.id
        assert ref.source is DailyOutputSource.REJECTED

    def test_integrity_rejected_authority_shows_the_earlier_forecast(
        self, day: Day, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        _insert_attempt(day, first, _rejected(day, "water_level"), day.failed(day.b))
        result, _ = Reader(day, monkeypatch).run()
        # No stale REJECTED key for a parameter the earlier attempt lacks.
        assert {k[2] for k in result} == {"discharge"}
        assert all(v[0].claim.id == first.id for v in result.values())


class TestReadEarlierAttempts:
    def _chain(self, day: Day) -> tuple[DailyClaim, DailyClaim, Any]:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        second = day.claim(_later(day.key))
        day.finalize(second, fail=(day.a, day.b))
        leaf = operator_leaf(day, second)
        _fail_leaf(day, leaf)
        return first, second, leaf

    def test_matches_read_day_ordering_and_authority(self, day: Day) -> None:
        first, second, leaf = self._chain(day)
        snapshot = day.store.read_day(day.key)
        assert snapshot.authoritative is not None
        with day.engine.connect() as conn:
            store = PgDailyForecastStore(lambda: nullcontext(conn))
            bounded = store.read_days_bounded((day.key,))[day.key]
            assert bounded.authoritative is not None
            earlier = store.read_earlier_attempts(
                (day.key,), {day.key: bounded.authoritative.claim.id}
            )
        assert earlier[day.key] == snapshot.earlier
        assert tuple(s.claim.id for s in earlier[day.key]) == (second.id, first.id)

    def test_key_cap(self, day: Day) -> None:
        keys = tuple(
            replace(
                day.key,
                context=ScheduledDailyForecast(
                    scheduled_tick=ensure_utc(NOW - timedelta(days=n))
                ),
            )
            for n in range(113)
        )
        with pytest.raises(DailyAuthorityIntegrityError):
            day.store.read_earlier_attempts(keys, {})

    def test_attempt_cap(self, day: Day, monkeypatch: pytest.MonkeyPatch) -> None:
        _, _, leaf = self._chain(day)
        monkeypatch.setattr(store_module, "MAX_EARLIER_ATTEMPTS", 2)
        with pytest.raises(DailyAuthorityIntegrityError):
            day.store.read_earlier_attempts((day.key,), {day.key: leaf.id})

    def test_payload_cap(self, day: Day, monkeypatch: pytest.MonkeyPatch) -> None:
        first = day.claim()
        day.finalize(first, produce=(day.a,), fail=(day.b,))
        retry = day.claim(_later(day.key))
        day.finalize(retry, fail=(day.a, day.b))
        monkeypatch.setattr(store_module, "MAX_EARLIER_BYTES", 10)
        with pytest.raises(DailyAuthorityIntegrityError):
            day.store.read_earlier_attempts((day.key,), {day.key: retry.id})
