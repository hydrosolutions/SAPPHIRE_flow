from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from sapphire_flow.services.publication_gate import publication_gate_active

if TYPE_CHECKING:
    from typing import Any

    from sapphire_flow.store.forecast_publication_store import (
        PgForecastPublicationStore,
    )
    from sapphire_flow.types.ids import StationId, TenantId


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationGate:
    active_tenant_ids: frozenset[TenantId] = frozenset()

    def active(self, tenant_id: TenantId) -> bool:
        return publication_gate_active(tenant_id, self.active_tenant_ids)


def get_publication_gate(request: Request) -> PublicationGate:
    return getattr(request.app.state, "publication_gate", PublicationGate())


def station_tenant_id(stores: dict[str, Any], station_id: StationId) -> TenantId:
    station = stores["station_store"].fetch_station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    return station.tenant_id


def require_active_publication(
    tenant_id: TenantId,
    gate: PublicationGate,
) -> None:
    if not gate.active(tenant_id):
        raise HTTPException(status_code=503, detail="Forecast publication is disabled")


def require_publication_store(stores: dict[str, Any]) -> PgForecastPublicationStore:
    store: PgForecastPublicationStore | None = stores.get("publication_store")
    if store is None:
        raise HTTPException(status_code=503, detail="Deployment config is unavailable")
    return store
