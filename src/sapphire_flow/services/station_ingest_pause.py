"""Set or clear a station's ingest-paused switch (#501).

Stops observation polling only; forecasting, QC and ``station_status`` are
untouched. One audit row records who, when and why. The admin endpoint/UI
(#455) is a caller of this function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.write_principal import enforce_tenant_isolation
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.stores import AuditLogStore, StationStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.write_principal import WritePrincipal


def set_station_ingest_paused(
    *,
    station_id: StationId,
    paused: bool,
    reason: str,
    principal: WritePrincipal,
    stations: StationStore,
    audit: AuditLogStore,
    clock: Callable[[], UtcDatetime],
) -> bool:
    """Return True if the switch changed, False if it already had that value."""
    if not reason.strip():
        raise ConfigurationError("a reason is required to change the ingest switch")
    station = stations.fetch_station(station_id)
    if station is None:
        raise ConfigurationError(f"unknown station {station_id}")
    now = clock()
    enforce_tenant_isolation(
        principal=principal,
        target_tenant_id=station.tenant_id,
        audit_log_store=audit,
        event_type=AuditEventType.STATION_INGEST_PAUSE_CHANGED,
        target_type="station",
        target_id=str(station.id),
        detail={"code": station.code, "to": paused},
        now=now,
    )
    if station.ingest_paused is paused:
        return False
    stations.update_station_ingest_paused(station.id, paused)
    audit.append_entry(
        AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
            event_type=AuditEventType.STATION_INGEST_PAUSE_CHANGED,
            target_type="station",
            target_id=str(station.id),
            detail={
                "code": station.code,
                "network": station.network,
                "from": station.ingest_paused,
                "to": paused,
                "reason": reason.strip(),
                "operator": principal.id,
            },
            ip_address=None,
            created_at=now,
        )
    )
    return True
