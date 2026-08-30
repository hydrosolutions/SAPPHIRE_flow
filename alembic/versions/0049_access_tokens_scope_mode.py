"""access_tokens.scope_mode column (Plan 215 D2.1)

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-29

Adds a first-class `scope_mode` to `access_tokens` — the mechanism a
`consumer` token's station scope is resolved by. `'stations'` (the default,
matches every existing row's behaviour exactly) reads
`access_token_stations`, the join this table has always had. `'tenant'`
derives the scope from `stations.tenant_id` at load time instead
(`store/access_token_store.py`, D2.2) — no materialised copy, so a station
added after the token is in scope the moment it exists.

`NOT NULL DEFAULT 'stations'` backfills every existing row (consumer and
admin alike) in one statement (PG >= 11, no table rewrite) — there is no
third state, and no row's behaviour changes on upgrade. The second CHECK
(`ck_access_tokens_tenant_mode_is_consumer`) makes "an admin row in tenant
mode" structurally unrepresentable, mirrored by `AccessToken.__post_init__`.

Downgrade drops both CHECKs then the column. A token that was in `'tenant'`
mode reverts to whatever `access_token_stations` rows it holds — after
Plan 215 T6's mode-switch cleanup, none. Downgrade is therefore fail-closed
(that token then sees nothing), not fail-open.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "access_tokens",
        sa.Column(
            "scope_mode",
            sa.Text,
            nullable=False,
            server_default="stations",
        ),
    )
    op.create_check_constraint(
        "ck_access_tokens_scope_mode",
        "access_tokens",
        "scope_mode IN ('stations', 'tenant')",
    )
    op.create_check_constraint(
        "ck_access_tokens_tenant_mode_is_consumer",
        "access_tokens",
        "scope_mode = 'stations' OR role = 'consumer'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_access_tokens_tenant_mode_is_consumer", "access_tokens", type_="check"
    )
    op.drop_constraint("ck_access_tokens_scope_mode", "access_tokens", type_="check")
    op.drop_column("access_tokens", "scope_mode")
