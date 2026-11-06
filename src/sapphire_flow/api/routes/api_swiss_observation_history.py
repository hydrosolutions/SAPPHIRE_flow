# ruff: noqa: TC001, TC003 -- FastAPI resolves annotations at runtime.
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import DBAPIError

from sapphire_flow.api.deps import (
    get_deployment_config,
    get_evaluation_clock,
    get_stores,
)
from sapphire_flow.api.security import Principal, require_reviewer
from sapphire_flow.api.swiss_observation_history_schemas import (
    HistoryCoarseGrid,
    HistoryStation,
    ObservationHistoryItem,
    SwissObservationHistoryResponse,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.swiss_overview import load_swiss_roster
from sapphire_flow.services.observation_history import summarize_history
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.observation_history import (
    HistoryGrid,
    HistoryRead,
    HistoryReadTimeoutError,
    HistoryRowLimitError,
    HistoryWindow,
)

router = APIRouter(prefix="/api/v1", tags=["api-swiss-observation-history"])
log = structlog.get_logger(__name__)
# Provisional ceiling, not approved operational admission.
HISTORY_BYTE_CAP = 2097152
_UNAVAILABLE = "Swiss observation history is unavailable"


def get_history_generation_id() -> UUID:
    return uuid4()


def parse_history_window(
    request: Request, principal: Principal = Depends(require_reviewer)
) -> HistoryWindow:
    pairs = request.query_params.multi_items()
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, value in pairs:
        if key != "window_hours" or key in seen or value not in {"72", "168"}:
            errors.append(
                {
                    "type": "value_error",
                    "loc": ("query", key),
                    "msg": "Invalid history query",
                    "input": None,
                }
            )
        seen.add(key)
    if errors:
        raise RequestValidationError(errors)
    return HistoryWindow(int(request.query_params.get("window_hours", "168")))


@router.get(
    "/swiss/observation-history",
    response_model=SwissObservationHistoryResponse,
    openapi_extra={
        "parameters": [
            {
                "name": "window_hours",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "enum": [72, 168], "default": 168},
            }
        ]
    },
)
def get_swiss_observation_history(
    window: HistoryWindow = Depends(parse_history_window),
    principal: Principal = Depends(require_reviewer),
    stores: dict[str, Any] = Depends(get_stores),
    config: DeploymentConfig | None = Depends(get_deployment_config),
    now: datetime = Depends(get_evaluation_clock),
    generation_id: UUID = Depends(get_history_generation_id),
) -> Response:
    grid = HistoryGrid(as_of=ensure_utc(now), window=window)
    if config is None or not config.swiss_observation_history:
        log.warning("observation_history.read_failed", cause="disabled")
        raise HTTPException(503, _UNAVAILABLE)
    try:
        if config.swiss_overview is None or config.evaluation_forecasts is None:
            raise ValueError("missing configuration")
        settings = config.evaluation_forecasts.to_domain()
        roster = load_swiss_roster(config.swiss_overview)
        stations = {s.id: s for s in stores["station_store"].fetch_all_stations()}
        if any(
            (station := stations.get(entry.station_id)) is None
            or station.tenant_id != settings.tenant_id
            or station.network != entry.network
            or station.code != entry.code
            for entry in roster
        ):
            raise ValueError("roster metadata mismatch")
    except (OSError, ValueError):
        log.error("observation_history.read_failed", cause="config_drift")
        raise HTTPException(503, _UNAVAILABLE) from None
    scoped = tuple(
        entry
        for entry in roster
        if principal.is_admin
        or (
            principal.tenant_id == settings.tenant_id
            and entry.station_id in principal.station_ids
        )
    )
    request = HistoryRead(
        station_ids=tuple(entry.station_id for entry in scoped),
        start=grid.start,
        as_of=grid.as_of,
        excluded_delivery_id=DELIVERY_ID,
    )
    try:
        rows = stores["obs_store"].fetch_observation_history(request)
    except HistoryRowLimitError as exc:
        fields: dict[str, object] = {
            "bound": exc.bound,
            "eligible_rows": exc.eligible_rows,
        }
        if exc.station_id is not None and exc.station_id in request.station_ids:
            fields["station_id"] = str(exc.station_id)
        log.warning("observation_history.read_failed", cause="row_cap", **fields)
        raise HTTPException(503, _UNAVAILABLE) from None
    except HistoryReadTimeoutError:
        log.warning("observation_history.read_failed", cause="timeout")
        raise HTTPException(503, _UNAVAILABLE) from None
    except DBAPIError:
        log.error("observation_history.read_failed", cause="database_fault")
        raise
    summaries = summarize_history(grid, request.station_ids, rows)
    body = SwissObservationHistoryResponse(
        generation_id=generation_id,
        generated_at=grid.as_of,
        as_of=grid.as_of,
        expires_at=grid.as_of + timedelta(minutes=20),
        window_hours=window.value,
        window_start=grid.start,
        native_start=grid.native_start,
        coarse=HistoryCoarseGrid(
            grid_start=grid.grid_start,
            first_start=grid.start,
            block_count=grid.block_count,
            first_block_clipped=grid.start != grid.grid_start,
        ),
        station_count=len(scoped),
        items=[
            ObservationHistoryItem(
                station=HistoryStation(
                    id=entry.station_id, network=entry.network, code=entry.code
                ),
                coarse=[
                    (block.mean, block.minimum, block.maximum, block.count)
                    for block in summary.coarse
                ],
                native=[(point.timestamp, point.value) for point in summary.native],
            )
            for entry, summary in zip(scoped, summaries, strict=True)
        ],
    )
    encoded = body.model_dump_json().encode("utf-8")
    if len(encoded) > HISTORY_BYTE_CAP:
        log.warning(
            "observation_history.read_failed",
            cause="byte_cap",
            bound=HISTORY_BYTE_CAP,
            encoded_bytes=len(encoded),
        )
        raise HTTPException(503, _UNAVAILABLE)
    return Response(
        encoded, media_type="application/json", headers={"Cache-Control": "no-store"}
    )
