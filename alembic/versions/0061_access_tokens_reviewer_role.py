"""access_tokens: the `reviewer` role (Plan 401 T1)

Revision ID: 0061
Revises: 0060
Create Date: 2026-09-26

A third HTTP role for the review dashboards. A reviewer is tenant-bound like
a consumer and may use either scope mode; admin is unchanged. The three role
constraints are re-created with the same names:

- `ck_access_tokens_role` admits `reviewer`;
- `ck_access_tokens_role_tenant`: admin has no tenant, consumer and reviewer
  have one;
- `ck_access_tokens_tenant_mode_is_consumer` KEEPS ITS NAME (tests and docs
  match on it) and admits tenant mode for consumer and reviewer.

Downgrade refuses while any reviewer row exists, revoked or not: an image
without this revision parses roles fail-closed, so a reviewer row would crash
its token listing and turn a reviewer request into a 500. The migration never
deletes tokens itself — the refusal prints the operator statements, run as the
database owner (the API role has no DELETE on `access_tokens`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "access_tokens"

_ROLE_BEFORE = "role IN ('consumer', 'admin')"
_ROLE_AFTER = "role IN ('consumer', 'reviewer', 'admin')"
_ROLE_TENANT_BEFORE = (
    "(role = 'admin' AND tenant_id IS NULL) OR "
    "(role = 'consumer' AND tenant_id IS NOT NULL)"
)
_ROLE_TENANT_AFTER = (
    "(role = 'admin' AND tenant_id IS NULL) OR "
    "(role IN ('consumer', 'reviewer') AND tenant_id IS NOT NULL)"
)
_TENANT_MODE_BEFORE = "scope_mode = 'stations' OR role = 'consumer'"
_TENANT_MODE_AFTER = "scope_mode = 'stations' OR role IN ('consumer', 'reviewer')"

_OPERATOR_DELETE = (
    "DELETE FROM access_token_stations\n"
    "  WHERE token_id IN (SELECT id FROM access_tokens WHERE role = 'reviewer');\n"
    "DELETE FROM access_tokens WHERE role = 'reviewer';"
)


def _replace_constraints(role: str, role_tenant: str, tenant_mode: str) -> None:
    op.drop_constraint("ck_access_tokens_role", _TABLE, type_="check")
    op.drop_constraint("ck_access_tokens_role_tenant", _TABLE, type_="check")
    op.drop_constraint(
        "ck_access_tokens_tenant_mode_is_consumer", _TABLE, type_="check"
    )
    op.create_check_constraint("ck_access_tokens_role", _TABLE, role)
    op.create_check_constraint("ck_access_tokens_role_tenant", _TABLE, role_tenant)
    op.create_check_constraint(
        "ck_access_tokens_tenant_mode_is_consumer", _TABLE, tenant_mode
    )


def upgrade() -> None:
    _replace_constraints(_ROLE_AFTER, _ROLE_TENANT_AFTER, _TENANT_MODE_AFTER)


def downgrade() -> None:
    reviewer_rows = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM access_tokens WHERE role = 'reviewer'"))
        .scalar_one()
    )
    if reviewer_rows:
        raise RuntimeError(
            f"migration 0061: refusing to downgrade — {reviewer_rows} reviewer "
            "access token(s) exist (revoked tokens count: revoking only sets "
            "disabled_at). An image without this revision cannot parse the "
            "role. Delete them first, as the database owner:\n\n"
            "  docker compose exec -T postgres psql -U ${DB_USER:-sapphire} "
            "-d sapphire\n\n"
            f"{_OPERATOR_DELETE}\n"
        )
    _replace_constraints(_ROLE_BEFORE, _ROLE_TENANT_BEFORE, _TENANT_MODE_BEFORE)
