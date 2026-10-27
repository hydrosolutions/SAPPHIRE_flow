"""Daily scheduled claims and internal manual/replay outcome summaries."""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE daily_forecast_attempts (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    deployment text NOT NULL CHECK (length(btrim(deployment)) > 0),
    group_id uuid NOT NULL,
    model_id text NOT NULL REFERENCES models(id),
    data_use text NOT NULL CHECK (data_use = 'standard'),
    issued_at timestamptz NOT NULL,
    trigger_kind text NOT NULL DEFAULT 'scheduled'
        CHECK (trigger_kind IN ('scheduled', 'manual', 'replay')),
    scheduled_tick timestamptz,
    ordinal integer,
    parent_id uuid REFERENCES daily_forecast_attempts(id),
    owner_id uuid NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    state text NOT NULL CHECK (state IN
        ('active', 'failed', 'complete', 'incomplete', 'abandoned')),
    failure_reason text CHECK (failure_reason IN (
        'no_operational_stations', 'shared_input_failure', 'configuration_changed',
        'configuration_unavailable', 'model_failure', 'integrity_failure',
        'internal_failure', 'persistence_failure')),
    artifact_id uuid REFERENCES model_artifacts(id),
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    roster jsonb NOT NULL CHECK (jsonb_typeof(roster) = 'array'),
    output_contracts jsonb NOT NULL CHECK (jsonb_typeof(output_contracts) = 'array'),
    summary jsonb CHECK (jsonb_typeof(summary) = 'array'),
    recovery jsonb CHECK (jsonb_typeof(recovery) = 'object'),
    CONSTRAINT fk_daily_group_tenant FOREIGN KEY (group_id, tenant_id)
        REFERENCES station_groups(id, tenant_id),
    CONSTRAINT ck_daily_identity CHECK (
        (trigger_kind = 'scheduled' AND scheduled_tick IS NOT NULL
            AND ordinal IS NOT NULL AND ordinal IN (1, 2)
            AND issued_at = date_trunc('day', scheduled_tick AT TIME ZONE 'UTC')
                AT TIME ZONE 'UTC'
            AND ((ordinal = 1 AND parent_id IS NULL)
                OR (ordinal = 2 AND parent_id IS NOT NULL))) OR
        (trigger_kind IN ('manual', 'replay') AND scheduled_tick IS NULL
            AND ordinal IS NULL AND parent_id IS NULL
            AND issued_at = date_trunc('day', issued_at AT TIME ZONE 'UTC')
                AT TIME ZONE 'UTC')),
    CONSTRAINT ck_daily_recovery CHECK
        (trigger_kind = 'scheduled' OR (state <> 'abandoned' AND recovery IS NULL)),
    CONSTRAINT ck_daily_terminal CHECK
        ((state = 'active' AND finished_at IS NULL AND failure_reason IS NULL
            AND summary IS NULL AND recovery IS NULL) OR
         (state = 'failed' AND finished_at IS NOT NULL AND failure_reason IS NOT NULL
            AND summary IS NULL AND recovery IS NULL) OR
         (state IN ('complete', 'incomplete') AND finished_at IS NOT NULL
            AND failure_reason IS NULL AND summary IS NOT NULL AND recovery IS NULL) OR
         (state = 'abandoned' AND finished_at IS NOT NULL AND failure_reason IS NULL
            AND summary IS NULL AND recovery IS NOT NULL)),
    CONSTRAINT ck_daily_artifact CHECK
        (state NOT IN ('complete', 'incomplete') OR artifact_id IS NOT NULL),
    CONSTRAINT ck_daily_finish CHECK (finished_at >= started_at)
);
CREATE UNIQUE INDEX uq_daily_active ON daily_forecast_attempts
    (tenant_id, deployment, group_id, model_id, data_use, issued_at)
    WHERE state = 'active' AND trigger_kind = 'scheduled';
CREATE UNIQUE INDEX uq_daily_ordinal ON daily_forecast_attempts
    (tenant_id, deployment, group_id, model_id, data_use, issued_at, ordinal)
    WHERE trigger_kind = 'scheduled';

CREATE FUNCTION public.daily_attempt_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'daily attempts cannot be deleted';
    END IF;
    IF OLD.state <> 'active' OR
       (to_jsonb(OLD) - ARRAY[
           'state', 'finished_at', 'failure_reason', 'summary', 'recovery'])
           IS DISTINCT FROM
       (to_jsonb(NEW) - ARRAY[
           'state', 'finished_at', 'failure_reason', 'summary', 'recovery']) THEN
        RAISE EXCEPTION 'daily attempt identity and finalized outcomes are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER daily_attempt_immutable
BEFORE UPDATE OR DELETE ON daily_forecast_attempts
FOR EACH ROW EXECUTE FUNCTION public.daily_attempt_immutable();
REVOKE ALL ON daily_forecast_attempts FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE daily_forecast_attempts")
    op.execute("DROP FUNCTION public.daily_attempt_immutable()")
