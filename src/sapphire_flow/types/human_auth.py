from dataclasses import dataclass
from enum import Enum

from sapphire_flow.types.ids import StationId, TenantId, UserId


class HumanPermission(Enum):
    REVIEW = "review"
    PUBLISH = "publish"


class HumanStatus(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationGrant:
    station_id: StationId
    permission: HumanPermission


@dataclass(frozen=True, kw_only=True, slots=True)
class HumanPrincipal:
    user_id: UserId
    tenant_id: TenantId
    grants: frozenset[StationGrant]

    def allows(self, station_id: StationId, permission: HumanPermission) -> bool:
        review = StationGrant(station_id=station_id, permission=HumanPermission.REVIEW)
        if review not in self.grants:
            return False
        if permission is HumanPermission.REVIEW:
            return True
        publish = StationGrant(
            station_id=station_id, permission=HumanPermission.PUBLISH
        )
        return publish in self.grants


@dataclass(frozen=True, kw_only=True, slots=True)
class HumanUser:
    id: UserId
    tenant_id: TenantId
    display_name: str
    status: HumanStatus
