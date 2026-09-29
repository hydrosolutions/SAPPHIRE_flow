from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from sapphire_flow.api.schemas import PublicationDecisionResponse
from sapphire_flow.types.forecast_publication import PublicationAction, PublicationKey

if TYPE_CHECKING:
    from sapphire_flow.store.forecast_publication_store import (
        PgForecastPublicationStore,
    )
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.forecast_publication import PublicationDecision
    from sapphire_flow.types.ids import TenantId


def decision_response(decision: PublicationDecision) -> PublicationDecisionResponse:
    return PublicationDecisionResponse(
        id=str(decision.id),
        station_id=str(decision.key.station_id),
        parameter=decision.key.parameter,
        issued_at=decision.key.issued_at,
        forecast_id=str(decision.forecast_id),
        actor_user_id=str(decision.actor_user_id),
        action=decision.action.value,
        selection_version=decision.selection_version,
        forecast_version=decision.forecast_version,
        preservation_at_publish=(
            decision.preservation_at_publish.value
            if decision.preservation_at_publish is not None
            else None
        ),
        replaced_forecast_id=(
            str(decision.replaced_forecast_id)
            if decision.replaced_forecast_id is not None
            else None
        ),
        replaced_decision_id=(
            str(decision.replaced_decision_id)
            if decision.replaced_decision_id is not None
            else None
        ),
        reason_code=decision.reason_code.value if decision.reason_code else None,
        reason_text=decision.reason_text,
        created_at=decision.created_at,
    )


def publication_metadata(
    store: PgForecastPublicationStore,
    forecast: OperationalForecast,
    tenant_id: TenantId,
) -> tuple[dict[str, Any], list[PublicationDecision]]:
    decisions = store.fetch_decisions(forecast.id)
    key = PublicationKey(
        tenant_id=tenant_id,
        station_id=forecast.station_id,
        parameter=forecast.ensemble.parameter,
        issued_at=forecast.issued_at,
    )
    selection = store.fetch_selection(key)
    if not decisions:
        state = "unpublished"
        published = None
    elif decisions[-1].action is PublicationAction.WITHDRAW:
        state = "withdrawn"
        published = next(
            (d for d in reversed(decisions) if d.action is PublicationAction.PUBLISH),
            None,
        )
    else:
        state = (
            "selected"
            if selection is not None and selection.selected_forecast_id == forecast.id
            else "replaced"
        )
        published = decisions[-1]
    latest = decisions[-1] if decisions else None
    return {
        "forecast_status": forecast.status.value,
        "forecast_version": forecast.version,
        "publication_state": state,
        "publication_decision_id": str(latest.id) if latest else None,
        "selection_version": selection.version if selection else None,
        "published_at": published.created_at if published else None,
        "preservation_at_publish": (
            published.preservation_at_publish.value
            if published and published.preservation_at_publish
            else None
        ),
    }, decisions


def parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(raw) != 8:
            raise ValueError("invalid length")
        return int.from_bytes(raw, "big")
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid publication cursor"
        ) from exc


def make_cursor(sequence: int) -> str:
    return base64.urlsafe_b64encode(sequence.to_bytes(8, "big")).decode().rstrip("=")
