"""Audited operator retries without extending the automatic daily budget."""

from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
LOCK TABLE daily_forecast_attempts IN ACCESS EXCLUSIVE MODE;
ALTER TABLE daily_forecast_attempts ADD COLUMN operator_metadata jsonb;
ALTER TABLE daily_forecast_attempts
    DROP CONSTRAINT daily_forecast_attempts_trigger_kind_check;
ALTER TABLE daily_forecast_attempts ADD CHECK
    (trigger_kind IN ('scheduled','operator_retry','manual','replay'));
ALTER TABLE daily_forecast_attempts DROP CONSTRAINT ck_daily_identity;
ALTER TABLE daily_forecast_attempts ADD CONSTRAINT ck_daily_identity CHECK (
    (trigger_kind='scheduled' AND scheduled_tick IS NOT NULL
      AND ordinal IS NOT NULL AND ordinal IN(1,2)
      AND issued_at=date_trunc('day',scheduled_tick AT TIME ZONE 'UTC')
        AT TIME ZONE 'UTC'
      AND ((ordinal=1 AND parent_id IS NULL)
        OR (ordinal=2 AND parent_id IS NOT NULL))) OR
    (trigger_kind='operator_retry' AND scheduled_tick IS NULL AND ordinal IS NULL
      AND parent_id IS NOT NULL AND parent_id <> id
      AND issued_at=date_trunc('day',issued_at AT TIME ZONE 'UTC')
        AT TIME ZONE 'UTC') OR
    (trigger_kind IN('manual','replay') AND scheduled_tick IS NULL AND ordinal IS NULL
      AND parent_id IS NULL
      AND issued_at=date_trunc('day',issued_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'));
ALTER TABLE daily_forecast_attempts DROP CONSTRAINT ck_daily_recovery;
ALTER TABLE daily_forecast_attempts ADD CONSTRAINT ck_daily_recovery CHECK
    (trigger_kind IN('scheduled','operator_retry')
      OR (state<>'abandoned' AND recovery IS NULL));
ALTER TABLE daily_forecast_attempts ADD CONSTRAINT ck_daily_operator_metadata CHECK
    ((trigger_kind='operator_retry' AND operator_metadata IS NOT NULL
       AND jsonb_typeof(operator_metadata)='object') OR
     (trigger_kind<>'operator_retry' AND operator_metadata IS NULL));
DROP INDEX uq_daily_active;
CREATE UNIQUE INDEX uq_daily_active ON daily_forecast_attempts
    (tenant_id,deployment,group_id,model_id,data_use,issued_at)
    WHERE state='active' AND trigger_kind IN('scheduled','operator_retry');
CREATE UNIQUE INDEX uq_daily_operator_parent ON daily_forecast_attempts(parent_id)
    WHERE trigger_kind='operator_retry';
    """)


def downgrade() -> None:
    raise RuntimeError(
        "operator retry evidence requires a forward-compatible release; "
        "no destructive downgrade"
    )
