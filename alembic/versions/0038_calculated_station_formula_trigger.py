"""calculated_station_formulas eligibility trigger (Plan 015)

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-20

DB-level backstop for the D2 eligibility invariant: on INSERT or a relation-changing UPDATE,
the target must be gauging_status='calculated' and every component must be
gauging_status='gauged' AND station_status='operational'. A **closure-only UPDATE** (sets
effective_to while leaving calculated_station_id/component_station_id/parameter/weight/
effective_from unchanged) is EXEMPT — required so an admin can close a formula row after a
component is suspended/decommissioned (the documented decommissioning path). Own revision so
rollback is granular. Errors surface as SQLAlchemy IntegrityError (sync psycopg).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION = """
CREATE OR REPLACE FUNCTION check_csf_component_eligibility() RETURNS trigger AS $$
DECLARE
    target_gauging TEXT;
    comp_gauging   TEXT;
    comp_status    TEXT;
BEGIN
    -- Closure-only UPDATE exemption: sets effective_to while leaving the relation columns
    -- unchanged. Allowed even when the component is no longer operational, so the
    -- decommissioning path (suspend component, then close the formula) works.
    IF TG_OP = 'UPDATE'
       AND NEW.effective_to IS NOT NULL
       AND (NEW.calculated_station_id, NEW.component_station_id, NEW.parameter,
            NEW.weight, NEW.effective_from)
           IS NOT DISTINCT FROM
           (OLD.calculated_station_id, OLD.component_station_id, OLD.parameter,
            OLD.weight, OLD.effective_from)
    THEN
        RETURN NEW;
    END IF;

    SELECT gauging_status INTO target_gauging
        FROM stations WHERE id = NEW.calculated_station_id;
    IF target_gauging IS DISTINCT FROM 'calculated' THEN
        RAISE EXCEPTION
            'calculated_station_formulas: target station % must be gauging_status=calculated (got %)',
            NEW.calculated_station_id, target_gauging;
    END IF;

    SELECT gauging_status, station_status INTO comp_gauging, comp_status
        FROM stations WHERE id = NEW.component_station_id;
    IF comp_gauging IS DISTINCT FROM 'gauged' OR comp_status IS DISTINCT FROM 'operational' THEN
        RAISE EXCEPTION
            'calculated_station_formulas: component station % must be gauged+operational (got gauging=%, status=%). Close the formula first (effective_to = now()).',
            NEW.component_station_id, comp_gauging, comp_status;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER trg_csf_component_eligibility
    BEFORE INSERT OR UPDATE ON calculated_station_formulas
    FOR EACH ROW EXECUTE FUNCTION check_csf_component_eligibility();
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_csf_component_eligibility "
        "ON calculated_station_formulas"
    )
    op.execute("DROP FUNCTION IF EXISTS check_csf_component_eligibility()")
