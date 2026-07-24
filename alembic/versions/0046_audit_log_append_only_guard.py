"""audit_log role-independent append-only guard (Plan 147 Slice B, 2/2)

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-24

The append-only GUARANTEE, owned here (not by the later DB-roles slice):
a `BEFORE UPDATE OR DELETE` trigger that RAISEs unconditionally, for EVERY
role including the table owner / migration role. Slice D's per-role
`INSERT`+`SELECT`-only grants are defense-in-depth on top of this, not the
primary mechanism — so append-only holds even before scoped roles exist.
Own revision (mirrors the 0037/0038 table+trigger split) so rollback is
granular: dropping the trigger does not require touching the table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION = """
CREATE OR REPLACE FUNCTION reject_audit_log_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: % is not permitted (role=%)',
        TG_OP, current_user;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER trg_audit_log_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION reject_audit_log_mutation();
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_log_mutation()")
