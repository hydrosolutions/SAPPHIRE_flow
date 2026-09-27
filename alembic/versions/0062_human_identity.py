"""Local OIDC human identity and station grants (Plan 341 T1).

Revision ID: 0062
Revises: 0061
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("username", sa.Text(), nullable=True, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="forecaster"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('org_admin', 'it_admin', 'model_admin', 'forecaster')",
            name="ck_users_role",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_users_id_tenant_id"),
    )
    op.create_table(
        "user_external_identities",
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("issuer", "subject"),
    )
    op.create_index(
        "ix_user_external_identities_user_id", "user_external_identities", ["user_id"]
    )
    op.create_table(
        "human_station_grants",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("station_id", UUID(as_uuid=True), nullable=False),
        sa.Column("permission", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("user_id", "station_id", "permission"),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_human_station_grants_user_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["station_id", "tenant_id"],
            ["stations.id", "stations.tenant_id"],
            name="fk_human_station_grants_station_tenant",
        ),
        sa.CheckConstraint(
            "permission IN ('review', 'publish')",
            name="ck_human_station_grants_permission",
        ),
    )
    op.create_index(
        "ix_human_station_grants_station_id", "human_station_grants", ["station_id"]
    )
    op.execute(
        """
        CREATE FUNCTION check_human_publish_grant() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND
                (OLD.user_id, OLD.station_id) IS DISTINCT FROM
                (NEW.user_id, NEW.station_id) THEN
                RAISE EXCEPTION 'grant ownership and station cannot change'
                    USING ERRCODE = '23514';
            END IF;
            PERFORM 1 FROM public.users
            WHERE id = COALESCE(NEW.user_id, OLD.user_id)
            FOR UPDATE;
            IF EXISTS (
                SELECT 1 FROM public.human_station_grants AS publish_grant
                WHERE publish_grant.user_id = COALESCE(NEW.user_id, OLD.user_id)
                  AND publish_grant.station_id =
                      COALESCE(NEW.station_id, OLD.station_id)
                  AND publish_grant.permission = 'publish'
            ) AND NOT EXISTS (
                SELECT 1 FROM public.human_station_grants AS review_grant
                WHERE review_grant.user_id = COALESCE(NEW.user_id, OLD.user_id)
                  AND review_grant.station_id = COALESCE(NEW.station_id, OLD.station_id)
                  AND review_grant.permission = 'review'
            ) THEN
                RAISE EXCEPTION 'publish grant requires review grant'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER human_publish_requires_review
        AFTER INSERT OR DELETE OR UPDATE ON human_station_grants
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_human_publish_grant()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER human_publish_requires_review ON human_station_grants")
    op.execute("DROP FUNCTION check_human_publish_grant()")
    op.drop_index("ix_human_station_grants_station_id", "human_station_grants")
    op.drop_table("human_station_grants")
    op.drop_index("ix_user_external_identities_user_id", "user_external_identities")
    op.drop_table("user_external_identities")
    op.drop_table("users")
