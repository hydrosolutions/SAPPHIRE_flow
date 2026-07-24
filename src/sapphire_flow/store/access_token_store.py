# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import access_token_stations, access_tokens, stations
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId, StationId, TenantId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class CrossTenantScopeError(ValueError):
    """Raised when a token's scope would include a station outside its own
    tenant (Plan 147 Slice C R2 — scope-membership validation)."""


class PgAccessTokenStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def create_token(
        self, token: AccessToken, *, station_ids: frozenset[StationId]
    ) -> None:
        if station_ids and token.role is AccessTokenRole.ADMIN:
            # Admin tokens are unscoped by definition — scope rows would be
            # meaningless and misleading.
            raise ValueError("admin tokens cannot carry a station scope")
        if station_ids:
            self._assert_stations_in_tenant(station_ids, token.tenant_id)

        self._conn.execute(
            sa.insert(access_tokens).values(
                id=token.id,
                token_hash=token.token_hash,
                key_prefix=token.key_prefix,
                name=token.name,
                role=token.role.value,
                tenant_id=token.tenant_id,
                pepper_version=token.pepper_version,
                expires_at=token.expires_at,
                disabled_at=token.disabled_at,
                created_at=token.created_at,
                last_used_at=token.last_used_at,
            )
        )
        if station_ids:
            self._conn.execute(
                sa.insert(access_token_stations),
                [
                    {"token_id": token.id, "station_id": sid}
                    for sid in sorted(station_ids, key=str)
                ],
            )

    def _assert_stations_in_tenant(
        self, station_ids: frozenset[StationId], tenant_id: TenantId | None
    ) -> None:
        if tenant_id is None:
            # A tenant-unbound (global-admin-owned) consumer scope — nothing
            # to validate against; every station is in-tenant for "no tenant".
            return
        rows = (
            self._conn.execute(
                sa.select(stations.c.id, stations.c.tenant_id).where(
                    stations.c.id.in_(station_ids)
                )
            )
            .mappings()
            .all()
        )
        found = {row["id"] for row in rows}
        missing = set(station_ids) - found
        if missing:
            raise CrossTenantScopeError(
                f"scope references unknown station ids: {sorted(missing, key=str)}"
            )
        mismatched = [row["id"] for row in rows if row["tenant_id"] != tenant_id]
        if mismatched:
            raise CrossTenantScopeError(
                f"scope includes stations outside token tenant {tenant_id}: "
                f"{sorted(mismatched, key=str)}"
            )

    def fetch_by_key_prefix(self, key_prefix: str) -> AccessToken | None:
        row = (
            self._conn.execute(
                sa.select(access_tokens).where(access_tokens.c.key_prefix == key_prefix)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._row_to_token(row)

    def fetch_token(self, token_id: AccessTokenId) -> AccessToken | None:
        row = (
            self._conn.execute(
                sa.select(access_tokens).where(access_tokens.c.id == token_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._row_to_token(row)

    def fetch_all_tokens(self) -> list[AccessToken]:
        rows = (
            self._conn.execute(
                sa.select(access_tokens).order_by(access_tokens.c.created_at)
            )
            .mappings()
            .all()
        )
        return [self._row_to_token(row) for row in rows]

    def revoke_token(self, token_id: AccessTokenId, *, revoked_at: UtcDatetime) -> None:
        self._conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token_id)
            .values(disabled_at=revoked_at)
        )

    def _row_to_token(self, row: sa.engine.row.RowMapping) -> AccessToken:
        scope_rows = (
            self._conn.execute(
                sa.select(access_token_stations.c.station_id).where(
                    access_token_stations.c.token_id == row["id"]
                )
            )
            .scalars()
            .all()
        )
        return AccessToken(
            id=AccessTokenId(row["id"]),
            token_hash=row["token_hash"],
            key_prefix=row["key_prefix"],
            name=row["name"],
            role=AccessTokenRole(row["role"]),
            tenant_id=TenantId(row["tenant_id"]) if row["tenant_id"] else None,
            pepper_version=row["pepper_version"],
            expires_at=utc_from_row(row["expires_at"]),
            disabled_at=utc_or_none(row["disabled_at"]),
            created_at=utc_from_row(row["created_at"]),
            last_used_at=utc_or_none(row["last_used_at"]),
            station_ids=frozenset(StationId(sid) for sid in scope_rows),
        )
