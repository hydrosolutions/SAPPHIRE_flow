from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import (
    human_station_grants,
    stations,
    user_external_identities,
    users,
)
from sapphire_flow.types.human_auth import (
    HumanPermission,
    HumanPrincipal,
    HumanStatus,
    HumanUser,
    StationGrant,
)
from sapphire_flow.types.ids import StationId, TenantId, UserId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class HumanIdentityError(ValueError):
    pass


class PgHumanIdentityStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def create_user(
        self,
        *,
        user_id: UserId,
        tenant_id: TenantId,
        display_name: str,
        issuer: str,
        subject: str,
        now: UtcDatetime,
    ) -> HumanUser:
        if not display_name.strip() or not issuer.strip() or not subject.strip():
            raise HumanIdentityError("display name, issuer and subject are required")
        self._conn.execute(
            sa.insert(users).values(
                id=user_id,
                tenant_id=tenant_id,
                display_name=display_name.strip(),
                role="forecaster",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        self._conn.execute(
            sa.insert(user_external_identities).values(
                issuer=issuer,
                subject=subject,
                user_id=user_id,
                created_at=now,
            )
        )
        return HumanUser(
            id=user_id,
            tenant_id=tenant_id,
            display_name=display_name.strip(),
            status=HumanStatus.ACTIVE,
        )

    def fetch_user(self, user_id: UserId) -> HumanUser | None:
        row = (
            self._conn.execute(
                sa.select(
                    users.c.id,
                    users.c.tenant_id,
                    users.c.display_name,
                    users.c.is_active,
                ).where(users.c.id == user_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return HumanUser(
            id=UserId(row["id"]),
            tenant_id=TenantId(row["tenant_id"]),
            display_name=row["display_name"],
            status=HumanStatus.ACTIVE if row["is_active"] else HumanStatus.DISABLED,
        )

    def _lock_user(self, user_id: UserId) -> HumanUser:
        row = (
            self._conn.execute(
                sa.select(
                    users.c.id,
                    users.c.tenant_id,
                    users.c.display_name,
                    users.c.is_active,
                )
                .where(users.c.id == user_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HumanIdentityError("user is unknown")
        return HumanUser(
            id=UserId(row["id"]),
            tenant_id=TenantId(row["tenant_id"]),
            display_name=row["display_name"],
            status=HumanStatus.ACTIVE if row["is_active"] else HumanStatus.DISABLED,
        )

    def link_identity(
        self, user_id: UserId, *, issuer: str, subject: str, now: UtcDatetime
    ) -> None:
        user = self._lock_user(user_id)
        if user.status is HumanStatus.DISABLED:
            raise HumanIdentityError("user is unknown or disabled")
        if not issuer.strip() or not subject.strip():
            raise HumanIdentityError("issuer and subject are required")
        self._conn.execute(
            sa.insert(user_external_identities).values(
                issuer=issuer, subject=subject, user_id=user_id, created_at=now
            )
        )

    def disable_user(self, user_id: UserId, *, now: UtcDatetime) -> None:
        result = self._conn.execute(
            sa.update(users)
            .where(users.c.id == user_id, users.c.is_active.is_(True))
            .values(is_active=False, updated_at=now)
        )
        if result.rowcount != 1:
            raise HumanIdentityError("user is unknown or already disabled")

    def grant(
        self,
        user_id: UserId,
        station_id: StationId,
        permission: HumanPermission,
        *,
        now: UtcDatetime,
    ) -> bool:
        user = self._lock_user(user_id)
        if user.status is HumanStatus.DISABLED:
            raise HumanIdentityError("user is unknown or disabled")
        station_tenant = self._conn.execute(
            sa.select(stations.c.tenant_id).where(stations.c.id == station_id)
        ).scalar_one_or_none()
        if station_tenant is None or station_tenant != user.tenant_id:
            raise HumanIdentityError("station is unknown or outside user's tenant")
        if permission is HumanPermission.PUBLISH and not self._has_grant(
            user_id, station_id, HumanPermission.REVIEW
        ):
            raise HumanIdentityError("publish requires a review grant on the station")
        inserted = self._conn.execute(
            pg_insert(human_station_grants)
            .values(
                user_id=user_id,
                tenant_id=user.tenant_id,
                station_id=station_id,
                permission=permission.value,
                granted_at=now,
            )
            .on_conflict_do_nothing()
            .returning(human_station_grants.c.user_id)
        ).scalar_one_or_none()
        return inserted is not None

    def revoke(
        self, user_id: UserId, station_id: StationId, permission: HumanPermission
    ) -> bool:
        self._lock_user(user_id)
        if permission is HumanPermission.REVIEW:
            self._conn.execute(
                sa.delete(human_station_grants).where(
                    human_station_grants.c.user_id == user_id,
                    human_station_grants.c.station_id == station_id,
                    human_station_grants.c.permission == HumanPermission.PUBLISH.value,
                )
            )
        result = self._conn.execute(
            sa.delete(human_station_grants).where(
                human_station_grants.c.user_id == user_id,
                human_station_grants.c.station_id == station_id,
                human_station_grants.c.permission == permission.value,
            )
        )
        return result.rowcount == 1

    def _has_grant(
        self, user_id: UserId, station_id: StationId, permission: HumanPermission
    ) -> bool:
        return (
            self._conn.execute(
                sa.select(human_station_grants.c.user_id).where(
                    human_station_grants.c.user_id == user_id,
                    human_station_grants.c.station_id == station_id,
                    human_station_grants.c.permission == permission.value,
                )
            ).first()
            is not None
        )

    def resolve_principal(self, *, issuer: str, subject: str) -> HumanPrincipal | None:
        user_row = (
            self._conn.execute(
                sa.select(users.c.id, users.c.tenant_id)
                .join(
                    user_external_identities,
                    user_external_identities.c.user_id == users.c.id,
                )
                .where(
                    user_external_identities.c.issuer == issuer,
                    user_external_identities.c.subject == subject,
                    users.c.is_active.is_(True),
                )
                .with_for_update(read=True, of=users)
            )
            .mappings()
            .one_or_none()
        )
        if user_row is None:
            return None
        user_id = UserId(UUID(str(user_row["id"])))
        grants = self._conn.execute(
            sa.select(
                human_station_grants.c.station_id, human_station_grants.c.permission
            ).where(
                human_station_grants.c.user_id == user_id,
                human_station_grants.c.tenant_id == user_row["tenant_id"],
            )
        ).all()
        return HumanPrincipal(
            user_id=user_id,
            tenant_id=TenantId(user_row["tenant_id"]),
            grants=frozenset(
                StationGrant(
                    station_id=StationId(station_id),
                    permission=HumanPermission(permission),
                )
                for station_id, permission in grants
            ),
        )
