from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from sapphire_flow.services.daily_forecast_completion import DailyCompletionStore
from sapphire_flow.types.daily_forecast_claim import DailyClaim, DailyFailureReason

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from sapphire_flow.types.daily_forecast import (
        DailyAttemptDecision,
        ScheduledDailyForecast,
    )
    from sapphire_flow.types.daily_forecast_claim import (
        DailyClaimKey,
        FrozenDailyAssignment,
    )
    from sapphire_flow.types.daily_forecast_one_shot import DailyExecution
    from sapphire_flow.types.daily_forecast_snapshot import DailyDaySnapshot
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationGroupId, TenantId


class DailyClaimCoordinatorStore(Protocol):
    def pending_keys(
        self, tenant_id: TenantId, deployment: str, context: ScheduledDailyForecast
    ) -> tuple[DailyClaimKey, ...]: ...

    def read_day(self, key: DailyClaimKey) -> DailyDaySnapshot: ...

    def claim(
        self,
        key: DailyClaimKey,
        frozen: FrozenDailyAssignment,
        *,
        attempt_id: UUID,
        owner_id: UUID,
        started_at: UtcDatetime,
    ) -> DailyClaim | DailyAttemptDecision: ...

    def fail(
        self,
        claim: DailyExecution,
        *,
        reason: DailyFailureReason,
        finished_at: UtcDatetime,
    ) -> None: ...


class DailyRuntimeStore(DailyClaimCoordinatorStore, DailyCompletionStore, Protocol):
    pass


def claim_daily_candidates(
    *,
    tenant_id: TenantId,
    deployment: str,
    context: ScheduledDailyForecast,
    candidates: Mapping[DailyClaimKey, FrozenDailyAssignment],
    store: DailyClaimCoordinatorStore,
    owner_id: UUID,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    unavailable_group_ids: frozenset[StationGroupId] = frozenset(),
    on_claim: Callable[[DailyClaim], None] | None = None,
    on_closed: Callable[[DailyClaim], None] | None = None,
    on_error: Callable[[DailyClaimKey, UUID | None, Exception], None] | None = None,
) -> tuple[DailyClaim, ...]:
    if any(
        key.tenant_id != tenant_id
        or key.deployment != deployment
        or key.context != context
        for key in candidates
    ):
        raise ValueError(
            "daily candidates must belong to the requested scheduled scope"
        )
    keys = set(candidates) | set(store.pending_keys(tenant_id, deployment, context))
    claims: list[DailyClaim] = []
    for key in sorted(keys, key=lambda key: (str(key.group_id), str(key.model_id))):
        if key.group_id in unavailable_group_ids:
            continue
        attempt_id: UUID | None = None
        try:
            current = candidates.get(key)
            frozen = current
            if frozen is None:
                previous = store.read_day(key).latest
                if previous is None:
                    raise ValueError("pending daily key has no persisted attempt")
                frozen = previous.claim.frozen
            attempt_id = id_gen()
            result = store.claim(
                key,
                frozen,
                attempt_id=attempt_id,
                owner_id=owner_id,
                started_at=clock(),
            )
            if not isinstance(result, DailyClaim):
                continue
            if on_claim is not None:
                on_claim(result)
            if current is None or (
                current.artifact_id != result.frozen.artifact_id
                or current.configuration_sha256 != result.frozen.configuration_sha256
                or current.output_contracts != result.frozen.output_contracts
            ):
                store.fail(
                    result,
                    reason=DailyFailureReason.CONFIGURATION_CHANGED,
                    finished_at=clock(),
                )
                if on_closed is not None:
                    on_closed(result)
                continue
            if current.artifact_id is None:
                store.fail(
                    result,
                    reason=DailyFailureReason.CONFIGURATION_UNAVAILABLE,
                    finished_at=clock(),
                )
                if on_closed is not None:
                    on_closed(result)
                continue
            claims.append(result)
        except Exception as exc:
            if on_error is None:
                raise
            on_error(key, attempt_id, exc)
    return tuple(claims)
