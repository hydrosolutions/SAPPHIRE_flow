# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import access_token_stations, access_tokens, stations
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.enums import AccessTokenRole, ScopeMode
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
            # A station scope always belongs to a consumer token, and
            # AccessToken.__post_init__ + the DB CHECK constraint both
            # require role=consumer -> tenant_id IS NOT NULL. A None here
            # means a caller tried to attach a scope to a tenantless token —
            # structurally invalid, never a silent "everything matches".
            raise CrossTenantScopeError(
                "a station scope requires a non-null tenant_id — a "
                "tenantless (global) token cannot be scoped to stations"
            )
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

    def grant_station(
        self,
        token_id: AccessTokenId,
        station_id: StationId,
    ) -> None:
        """Plan 215 T1: widen a `'stations'`-mode token's scope by one
        station, reusing the SAME tenant-containment invariant
        `create_token` enforces rather than a second copy. Idempotent —
        `ON CONFLICT DO NOTHING` on the composite PK (token_id, station_id).

        **The tenant is DERIVED from `token_id`, never taken from the
        caller** (independent Codex review of the Plan 215 diff). An earlier
        signature accepted `tenant_id` as a keyword, which let a caller pair
        token A with tenant B and insert B's station into A's scope, or
        attach a grant row to an admin token — the docstring claimed
        `create_token`'s invariant while not actually enforcing it, because
        `create_token` derives the tenant from the token it is handed.
        A public store boundary must not depend on its callers passing the
        right tenant."""
        token = self.fetch_token(token_id)
        if token is None:
            raise ValueError(f"unknown access token: {token_id}")
        if token.role is AccessTokenRole.ADMIN:
            # Same refusal as create_token — admin tokens are unscoped by
            # definition, so a scope row is meaningless and misleading.
            raise ValueError("admin tokens cannot carry a station scope")
        self._assert_stations_in_tenant(frozenset({station_id}), token.tenant_id)
        self._conn.execute(
            pg_insert(access_token_stations)
            .values(token_id=token_id, station_id=station_id)
            .on_conflict_do_nothing(index_elements=["token_id", "station_id"])
        )

    def revoke_station(self, token_id: AccessTokenId, station_id: StationId) -> bool:
        """Plan 215 T1: narrow a `'stations'`-mode token's scope by one
        station. Idempotent — deleting an absent row succeeds and returns
        False so the CLI can report "was not in scope" rather than claim a
        change that did not happen."""
        result = self._conn.execute(
            sa.delete(access_token_stations).where(
                access_token_stations.c.token_id == token_id,
                access_token_stations.c.station_id == station_id,
            )
        )
        return result.rowcount > 0

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
        # Fail-closed parse (Plan 215 D2.1): an unexpected value raises
        # ValueError rather than silently degrading into "no mode, assume
        # tenant" — a NULL is already impossible (NOT NULL column), this is
        # the second line of defence for a value written out-of-band.
        scope_mode = ScopeMode(row["scope_mode"])
        tenant_id = TenantId(row["tenant_id"]) if row["tenant_id"] else None

        if scope_mode is ScopeMode.TENANT:
            # D2.2: the scope is DERIVED from tenant membership at load
            # time, not materialised — deliberately unfiltered by
            # network/station_kind (owner-confirmed 2026-08-29, see the
            # plan's D2.2). `_assert_stations_in_tenant` is skipped here:
            # the tenant predicate that produced this set IS the assertion,
            # re-running it over the rows just read would be a tautology.
            scope_rows = (
                self._conn.execute(
                    sa.select(stations.c.id).where(stations.c.tenant_id == tenant_id)
                )
                .scalars()
                .all()
            )
            station_ids = frozenset(StationId(sid) for sid in scope_rows)
        else:
            scope_rows = (
                self._conn.execute(
                    sa.select(access_token_stations.c.station_id).where(
                        access_token_stations.c.token_id == row["id"]
                    )
                )
                .scalars()
                .all()
            )
            station_ids = frozenset(StationId(sid) for sid in scope_rows)
            if station_ids:
                # Fail-closed re-validation on the READ/auth path (Plan 147
                # Slice C, Codex round 2): a scope row introduced out-of-band
                # (corruption, a future bug, direct SQL) must NOT become an
                # authorized principal scope. Re-assert every scope station
                # belongs to this token's tenant — raises
                # CrossTenantScopeError if not (create-time validation at
                # `_assert_stations_in_tenant` is not enough alone).
                self._assert_stations_in_tenant(station_ids, tenant_id)

        return AccessToken(
            id=AccessTokenId(row["id"]),
            token_hash=row["token_hash"],
            key_prefix=row["key_prefix"],
            name=row["name"],
            role=AccessTokenRole(row["role"]),
            tenant_id=tenant_id,
            pepper_version=row["pepper_version"],
            expires_at=utc_from_row(row["expires_at"]),
            disabled_at=utc_or_none(row["disabled_at"]),
            created_at=utc_from_row(row["created_at"]),
            last_used_at=utc_or_none(row["last_used_at"]),
            station_ids=station_ids,
            scope_mode=scope_mode,
        )
