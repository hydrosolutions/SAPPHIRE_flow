from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 -- Pydantic resolves this at runtime.

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.pool import NullPool

from sapphire_flow.services.daily_forecast_dispatch import next_daily_attempt
from sapphire_flow.store.daily_forecast_documents import (
    contracts_json,
    outcomes_json,
    parse_contracts,
    parse_outcomes,
    parse_recovery,
)
from sapphire_flow.store.daily_output_verification import verify_daily_output
from sapphire_flow.types.daily_forecast import (
    DailyAttemptDecision,
    DailyAttemptOrdinal,
    DailyAttemptState,
    ScheduledDailyAttempt,
    ScheduledDailyForecast,
)
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_one_shot import (
    DailyExecution,
    OneShotDailyExecution,
    OneShotDailyKey,
    OneShotDailySnapshot,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberState,
    finalized_outcome_state,
)
from sapphire_flow.types.daily_forecast_recovery import DailyRecoveryStatus
from sapphire_flow.types.daily_forecast_snapshot import (
    DailyAttemptSnapshot,
    DailyDaySnapshot,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.ids import (
    ArtifactId,
    ModelId,
    StationGroupId,
    StationId,
    TenantId,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from sqlalchemy import RowMapping

    from sapphire_flow.types.daily_forecast_recovery import ConfirmedDailyTermination
    from sapphire_flow.types.daily_output import DailyOutputEvidence
    from sapphire_flow.types.datetime import UtcDatetime


class _MemberDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    station_id: UUID
    required_parameters: tuple[str, ...]


def _roster_json(frozen: FrozenDailyAssignment) -> str:
    return json.dumps(
        [
            _MemberDocument(
                station_id=member.station_id,
                required_parameters=member.required_parameters,
            ).model_dump(mode="json")
            for member in frozen.members
        ]
    )


_SCHEDULED_SCOPE = """tenant_id = :tenant_id AND deployment = :deployment
    AND group_id = :group_id
    AND model_id = :model_id AND data_use = :data_use AND issued_at = :issued_at
    AND trigger_kind = 'scheduled'"""


def _frozen_from_row(row: RowMapping) -> FrozenDailyAssignment:
    documents = [_MemberDocument.model_validate(item) for item in row["roster"]]
    return FrozenDailyAssignment(
        artifact_id=ArtifactId(row["artifact_id"])
        if row["artifact_id"] is not None
        else None,
        configuration_sha256=row["configuration_sha256"],
        output_contracts=parse_contracts(row["output_contracts"]),
        members=tuple(
            DailyMemberOutcome(
                station_id=StationId(doc.station_id),
                required_parameters=doc.required_parameters,
                state=DailyMemberState.PENDING,
            )
            for doc in documents
        ),
    )


def _snapshot(row: RowMapping) -> DailyAttemptSnapshot:
    if row["trigger_kind"] != "scheduled":
        raise ValueError("scheduled snapshot cannot parse a one-shot")
    key = DailyClaimKey(
        tenant_id=TenantId(row["tenant_id"]),
        deployment=row["deployment"],
        group_id=StationGroupId(row["group_id"]),
        model_id=ModelId(row["model_id"]),
        data_use=ForecastDataUse(row["data_use"]),
        context=ScheduledDailyForecast(
            scheduled_tick=ensure_utc(row["scheduled_tick"])
        ),
    )
    claim = DailyClaim(
        id=row["id"],
        owner_id=row["owner_id"],
        key=key,
        ordinal=DailyAttemptOrdinal(row["ordinal"]),
        frozen=_frozen_from_row(row),
        parent_id=row["parent_id"],
        started_at=ensure_utc(row["started_at"]),
    )
    return DailyAttemptSnapshot(
        claim=claim,
        state=DailyAttemptState(row["state"]),
        finished_at=ensure_utc(row["finished_at"]) if row["finished_at"] else None,
        outcomes=parse_outcomes(row["summary"]) if row["summary"] is not None else None,
        failure_reason=DailyFailureReason(row["failure_reason"])
        if row["failure_reason"]
        else None,
        recovery=parse_recovery(row["recovery"]) if row["recovery"] else None,
    )


def daily_forecast_transaction_factory(
    url: sa.URL,
) -> Callable[[], AbstractContextManager[sa.Connection]]:
    engine = sa.create_engine(
        url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    return engine.begin


class PgDailyForecastStore:
    def __init__(
        self, transaction_factory: Callable[[], AbstractContextManager[sa.Connection]]
    ) -> None:
        self._begin = transaction_factory

    def start_one_shot(
        self,
        key: OneShotDailyKey,
        frozen: FrozenDailyAssignment,
        *,
        execution_id: UUID,
        owner_id: UUID,
        started_at: UtcDatetime,
    ) -> OneShotDailyExecution:
        """Internal only: the caller must own an authorized disposable output bundle."""
        execution = OneShotDailyExecution(
            id=execution_id,
            owner_id=owner_id,
            key=key,
            frozen=frozen,
            started_at=started_at,
        )
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            self._verify_assignment(txn, execution)
            inserted = txn.execute(
                sa.text("""INSERT INTO daily_forecast_attempts
                (id, tenant_id, deployment, group_id, model_id, data_use, issued_at,
                 trigger_kind, scheduled_tick, ordinal, parent_id, owner_id,
                 started_at, state, artifact_id, configuration_sha256, roster,
                 output_contracts)
                VALUES (:id, :tenant, :deployment, :group, :model, :data_use, :issue,
                 :trigger, NULL, NULL, NULL, :owner, :started, 'active', :artifact,
                 :configuration, CAST(:roster AS jsonb), CAST(:contracts AS jsonb))
                ON CONFLICT (id) DO NOTHING RETURNING id"""),
                {
                    "id": execution.id,
                    "tenant": key.tenant_id,
                    "deployment": key.deployment,
                    "group": key.group_id,
                    "model": key.model_id,
                    "data_use": key.data_use.value,
                    "issue": key.context.issue_time,
                    "trigger": key.context.trigger.value,
                    "owner": execution.owner_id,
                    "started": execution.started_at,
                    "artifact": frozen.artifact_id,
                    "configuration": frozen.configuration_sha256,
                    "roster": _roster_json(frozen),
                    "contracts": contracts_json(frozen.output_contracts),
                },
            ).scalar_one_or_none()
            if inserted is None:
                raise ValueError("one-shot execution ID has already been admitted")
        return execution

    def read_one_shot(
        self, key: OneShotDailyKey, *, execution_id: UUID
    ) -> OneShotDailySnapshot | None:
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            row = (
                txn.execute(
                    sa.text("""SELECT * FROM daily_forecast_attempts
                WHERE id = :id AND tenant_id = :tenant AND deployment = :deployment
                AND group_id = :group AND model_id = :model AND data_use = :data_use
                AND issued_at = :issue AND trigger_kind = :trigger"""),
                    {
                        "id": execution_id,
                        "tenant": key.tenant_id,
                        "deployment": key.deployment,
                        "group": key.group_id,
                        "model": key.model_id,
                        "data_use": key.data_use.value,
                        "issue": key.context.issue_time,
                        "trigger": key.context.trigger.value,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if any(
            row[field] is not None
            for field in ("scheduled_tick", "ordinal", "parent_id", "recovery")
        ):
            raise ValueError("invalid one-shot database identity")
        return OneShotDailySnapshot(
            execution=OneShotDailyExecution(
                id=row["id"],
                owner_id=row["owner_id"],
                key=key,
                frozen=_frozen_from_row(row),
                started_at=ensure_utc(row["started_at"]),
            ),
            state=DailyAttemptState(row["state"]),
            finished_at=ensure_utc(row["finished_at"]) if row["finished_at"] else None,
            outcomes=parse_outcomes(row["summary"])
            if row["summary"] is not None
            else None,
            failure_reason=DailyFailureReason(row["failure_reason"])
            if row["failure_reason"]
            else None,
        )

    def claim(
        self,
        key: DailyClaimKey,
        frozen: FrozenDailyAssignment,
        *,
        attempt_id: UUID,
        owner_id: UUID,
        started_at: UtcDatetime,
    ) -> DailyClaim | DailyAttemptDecision:
        fields = dict(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            group_id=key.group_id,
            model_id=key.model_id,
            data_use=key.data_use.value,
            issued_at=key.context.issue_time,
        )
        lock_key = json.dumps([str(value) for value in fields.values()])
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            txn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            row = (
                txn.execute(
                    sa.text(
                        "SELECT * FROM daily_forecast_attempts WHERE "
                        + _SCHEDULED_SCOPE
                        + " ORDER BY ordinal DESC LIMIT 1"
                    ),
                    fields,
                )
                .mappings()
                .one_or_none()
            )
            previous = (
                None
                if row is None
                else ScheduledDailyAttempt(
                    context=ScheduledDailyForecast(
                        scheduled_tick=ensure_utc(row["scheduled_tick"])
                    ),
                    ordinal=DailyAttemptOrdinal(row["ordinal"]),
                    state=DailyAttemptState(row["state"]),
                )
            )
            decision = next_daily_attempt(key.context, previous)
            if decision not in (
                DailyAttemptDecision.INITIAL,
                DailyAttemptDecision.RETRY,
            ):
                return decision
            if row is not None:
                frozen = _frozen_from_row(row)
            claim = DailyClaim(
                id=attempt_id,
                owner_id=owner_id,
                key=key,
                ordinal=DailyAttemptOrdinal.INITIAL
                if row is None
                else DailyAttemptOrdinal.RETRY,
                parent_id=None if row is None else row["id"],
                frozen=frozen,
                started_at=started_at,
            )
            self._verify_assignment(txn, claim)
            txn.execute(
                sa.text("""INSERT INTO daily_forecast_attempts
                (id, tenant_id, deployment, group_id, model_id, data_use, issued_at,
                 trigger_kind, scheduled_tick, ordinal, parent_id, owner_id,
                 started_at, state,
                 artifact_id, configuration_sha256, roster, output_contracts)
                VALUES (:id, :tenant_id, :deployment, :group_id, :model_id, :data_use,
                 :issued_at, 'scheduled', :scheduled_tick, :ordinal, :parent_id,
                 :owner_id, :started_at,
                 'active', :artifact_id, :configuration_sha256,
                 CAST(:roster AS jsonb), CAST(:output_contracts AS jsonb))"""),
                {
                    **fields,
                    "id": claim.id,
                    "owner_id": claim.owner_id,
                    "scheduled_tick": key.context.scheduled_tick,
                    "ordinal": claim.ordinal.value,
                    "parent_id": claim.parent_id,
                    "started_at": claim.started_at,
                    "artifact_id": frozen.artifact_id,
                    "configuration_sha256": frozen.configuration_sha256,
                    "roster": _roster_json(frozen),
                    "output_contracts": contracts_json(frozen.output_contracts),
                },
            )
        return claim

    @staticmethod
    def _verify_assignment(txn: sa.Connection, claim: DailyExecution) -> None:
        artifact = txn.execute(
            sa.text("""SELECT 1 FROM model_artifacts
            WHERE id = :artifact AND model_id = :model AND group_id = :group"""),
            {
                "artifact": claim.frozen.artifact_id,
                "model": claim.key.model_id,
                "group": claim.key.group_id,
            },
        ).scalar_one_or_none()
        if artifact is None and claim.frozen.artifact_id is not None:
            raise ValueError(
                "daily artifact must belong to the assigned group and model"
            )
        for member in claim.frozen.members:
            tenant = txn.execute(
                sa.text("SELECT tenant_id FROM stations WHERE id = :id"),
                {"id": member.station_id},
            ).scalar_one_or_none()
            if tenant != claim.key.tenant_id:
                raise ValueError("daily roster member does not belong to tenant")

    @staticmethod
    def _verify_active_identity(txn: sa.Connection, claim: DailyExecution) -> None:
        row = (
            txn.execute(
                sa.text("""SELECT * FROM daily_forecast_attempts
            WHERE id = :id FOR UPDATE"""),
                {"id": claim.id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["state"] != "active" or row["owner_id"] != claim.owner_id:
            raise ValueError("daily transition requires the exact active owned claim")
        identity = {
            "tenant_id": claim.key.tenant_id,
            "deployment": claim.key.deployment,
            "group_id": claim.key.group_id,
            "model_id": claim.key.model_id,
            "data_use": claim.key.data_use.value,
            "issued_at": claim.key.context.issue_time,
            "trigger_kind": "scheduled"
            if isinstance(claim, DailyClaim)
            else claim.key.context.trigger.value,
            "scheduled_tick": claim.key.context.scheduled_tick
            if isinstance(claim, DailyClaim)
            else None,
            "ordinal": claim.ordinal.value if isinstance(claim, DailyClaim) else None,
            "parent_id": claim.parent_id if isinstance(claim, DailyClaim) else None,
            "started_at": claim.started_at,
            "artifact_id": claim.frozen.artifact_id,
            "configuration_sha256": claim.frozen.configuration_sha256,
        }
        if any(row[key] != value for key, value in identity.items()) or (
            row["roster"] != json.loads(_roster_json(claim.frozen))
            or row["output_contracts"]
            != json.loads(contracts_json(claim.frozen.output_contracts))
        ):
            raise ValueError("daily claim differs from its frozen database identity")

    def fail(
        self,
        claim: DailyExecution,
        *,
        reason: DailyFailureReason,
        finished_at: UtcDatetime,
    ) -> None:
        """Call only after owned writes stop; uncertain ownership stays active."""
        if finished_at.utcoffset() != timedelta(0) or finished_at < claim.started_at:
            raise ValueError("finished_at must be aware UTC and not precede start")
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            self._verify_active_identity(txn, claim)
            updated = txn.execute(
                sa.text("""UPDATE daily_forecast_attempts
                SET state = 'failed', failure_reason = :reason, finished_at = :finished
                WHERE id = :id AND owner_id = :owner
                  AND tenant_id = :tenant AND state = 'active'
                RETURNING id"""),
                {
                    "id": claim.id,
                    "owner": claim.owner_id,
                    "tenant": claim.key.tenant_id,
                    "reason": reason.value,
                    "finished": finished_at,
                },
            ).scalar_one_or_none()
            if updated is None:
                raise ValueError("failure requires the exact active owned claim")

    def finalize(
        self,
        claim: DailyExecution,
        *,
        outcomes: tuple[DailyMemberOutcome, ...],
        evidence: tuple[DailyOutputEvidence, ...],
        finished_at: UtcDatetime,
    ) -> DailyAttemptState:
        if claim.frozen.artifact_id is None:
            raise ValueError("daily output finalization requires a frozen artifact")
        if finished_at.utcoffset() != timedelta(0) or finished_at < claim.started_at:
            raise ValueError("daily finish must be aware UTC and not precede start")
        state = finalized_outcome_state(outcomes)
        roster = {
            member.station_id: member.required_parameters
            for member in claim.frozen.members
        }
        if {
            member.station_id: member.required_parameters for member in outcomes
        } != roster:
            raise ValueError(
                "daily outcomes must cover the exact frozen roster and parameters"
            )
        expected = {
            ref: member.state for member in outcomes for ref in member.references
        }
        supplied = {item.reference: item for item in evidence}
        if expected.keys() != supplied.keys() or len(supplied) != len(evidence):
            raise ValueError(
                "daily finalization requires exact evidence for every reference"
            )
        for member in outcomes:
            if any(
                supplied[ref].provenance != member.provenance
                for ref in member.references
            ):
                raise ValueError(
                    "daily member provenance differs from captured output evidence"
                )
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            self._verify_active_identity(txn, claim)
            self._verify_assignment(txn, claim)
            for reference, member_state in expected.items():
                verify_daily_output(txn, claim, supplied[reference], member_state)
            txn.execute(
                sa.text("""UPDATE daily_forecast_attempts
                SET state = :state, finished_at = :finished,
                    summary = CAST(:summary AS jsonb)
                WHERE id = :id"""),
                {
                    "state": state.value,
                    "finished": finished_at,
                    "summary": outcomes_json(outcomes),
                    "id": claim.id,
                },
            )
        return state

    def abandon(
        self, claim: DailyClaim, confirmation: ConfirmedDailyTermination
    ) -> None:
        """The operator must first verify that the run AND all writers stopped."""
        if not isinstance(claim, DailyClaim):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("one-shot recovery is unsupported")
        if (
            confirmation.attempt_id != claim.id
            or confirmation.owner_id != claim.owner_id
        ):
            raise ValueError(
                "daily recovery confirmation must identify the exact claim"
            )
        if confirmation.confirmed_at < claim.started_at:
            raise ValueError("daily recovery confirmation predates the claim")
        recovery = json.dumps(
            {
                "attempt_id": str(confirmation.attempt_id),
                "owner_id": str(confirmation.owner_id),
                "operator": confirmation.operator,
                "confirmed_at": confirmation.confirmed_at.isoformat(),
                "run_state": confirmation.run_state.value,
                "writer_state": confirmation.writer_state.value,
            }
        )
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            updated = txn.execute(
                sa.text("""UPDATE daily_forecast_attempts
                SET state = 'abandoned', finished_at = :finished,
                    recovery = CAST(:recovery AS jsonb)
                WHERE id = :id AND owner_id = :owner
                  AND tenant_id = :tenant AND state = 'active'
                  AND trigger_kind = 'scheduled'
                RETURNING id"""),
                {
                    "id": claim.id,
                    "owner": claim.owner_id,
                    "tenant": claim.key.tenant_id,
                    "finished": confirmation.confirmed_at,
                    "recovery": recovery,
                },
            ).scalar_one_or_none()
            if updated is None:
                raise ValueError("daily recovery requires the exact active owned claim")

    def recovery_status(self, key: DailyClaimKey) -> DailyRecoveryStatus:
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            recovered = txn.execute(
                sa.text(
                    "SELECT 1 FROM daily_forecast_attempts WHERE "
                    + _SCHEDULED_SCOPE
                    + " AND recovery IS NOT NULL LIMIT 1"
                ),
                {
                    "tenant_id": key.tenant_id,
                    "deployment": key.deployment,
                    "group_id": key.group_id,
                    "model_id": key.model_id,
                    "data_use": key.data_use.value,
                    "issued_at": key.context.issue_time,
                },
            ).scalar_one_or_none()
        return (
            DailyRecoveryStatus.OPERATOR_RECOVERED
            if recovered is not None
            else DailyRecoveryStatus.UNTOUCHED
        )

    def read_day(self, key: DailyClaimKey) -> DailyDaySnapshot:
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            rows = (
                txn.execute(
                    sa.text(
                        "SELECT * FROM daily_forecast_attempts WHERE "
                        + _SCHEDULED_SCOPE
                        + " ORDER BY ordinal DESC"
                    ),
                    {
                        "tenant_id": key.tenant_id,
                        "deployment": key.deployment,
                        "group_id": key.group_id,
                        "model_id": key.model_id,
                        "data_use": key.data_use.value,
                        "issued_at": key.context.issue_time,
                    },
                )
                .mappings()
                .all()
            )
        attempts = tuple(_snapshot(row) for row in rows)
        authoritative = next(
            (attempt for attempt in attempts if attempt.outcomes is not None), None
        )
        recovery = (
            DailyRecoveryStatus.OPERATOR_RECOVERED
            if any(attempt.recovery is not None for attempt in attempts)
            else DailyRecoveryStatus.UNTOUCHED
        )
        return DailyDaySnapshot(
            latest=attempts[0] if attempts else None,
            authoritative=authoritative,
            recovery_status=recovery,
        )

    def pending_keys(
        self,
        tenant_id: TenantId,
        deployment: str,
        context: ScheduledDailyForecast,
    ) -> tuple[DailyClaimKey, ...]:
        with self._begin() as txn:
            txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            rows = (
                txn.execute(
                    sa.text("""SELECT DISTINCT ON (group_id, model_id) *
                FROM daily_forecast_attempts WHERE tenant_id = :tenant
                AND deployment = :deployment AND issued_at = :issued_at
                AND data_use = 'standard' AND trigger_kind = 'scheduled'
                ORDER BY group_id, model_id, ordinal DESC"""),
                    {
                        "tenant": tenant_id,
                        "deployment": deployment,
                        "issued_at": context.issue_time,
                    },
                )
                .mappings()
                .all()
            )
        pending: list[DailyClaimKey] = []
        for row in rows:
            previous = ScheduledDailyAttempt(
                context=ScheduledDailyForecast(
                    scheduled_tick=ensure_utc(row["scheduled_tick"])
                ),
                ordinal=DailyAttemptOrdinal(row["ordinal"]),
                state=DailyAttemptState(row["state"]),
            )
            decision = next_daily_attempt(context, previous)
            if decision in (DailyAttemptDecision.OWNED, DailyAttemptDecision.RETRY):
                pending.append(
                    DailyClaimKey(
                        tenant_id=tenant_id,
                        deployment=deployment,
                        group_id=StationGroupId(row["group_id"]),
                        model_id=ModelId(row["model_id"]),
                        context=context,
                    )
                )
        return tuple(pending)
