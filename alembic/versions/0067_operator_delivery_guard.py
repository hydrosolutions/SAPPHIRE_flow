"""Row-limit guard for the database-limited operator role (Plan 510 D2).

Revision ID: 0067
Revises: 0066

The operator role (`sapphire_operator`, created by docker/bootstrap-roles.sql,
never by a migration) may write `observations`, `rating_curves` and `stations`,
but the database itself refuses any row outside the one delivery it exists for.

* SECURITY INVOKER row triggers with `WHEN (session_user = 'sapphire_operator')`:
  they cost every other role nothing, and a SECURITY DEFINER function cannot
  slip past them (its `current_user` would be the owner, its `session_user` is
  still the login).
* One predicate: the row's `delivery_id` is the delivery constant AND its
  station belongs to the delivery tenant, looked up by code. Positive allow — a
  missing tenant refuses. NEW is checked on INSERT, OLD on DELETE, both on
  UPDATE, and an UPDATE may not move a row (station, delivery tag, curve
  validity dates).
* Relations are schema-qualified and the search_path pinned with pg_temp last,
  so a session's temporary table cannot stand in for `stations` or `tenants`.
  Temporary tables themselves are not prevented (TEMP is granted to PUBLIC).
* `stations` accepts an operator INSERT only into the delivery tenant; the
  bootstrap never grants the operator UPDATE, DELETE or TRUNCATE there.

The literals below are the delivery identity; tests tie them to
`DELIVERY_ID` and `DELIVERY_TENANT_CODE`. A second delivery is a deliberate new
migration that extends this allow-list, never a config change.

Downgrade revokes the operator's DML BEFORE dropping the triggers, so a
migration-only rollback never leaves an unguarded interval.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPERATOR_ROLE = "sapphire_operator"
GUARDED_DELIVERY_ID = "dhm-barkhk-2026-09-08"
GUARDED_TENANT_CODE = "chwrr"

_ROW_FUNCTION = f"""
CREATE FUNCTION public.operator_guard_delivery_row() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF OLD.delivery_id IS DISTINCT FROM '{GUARDED_DELIVERY_ID}'
           OR NOT EXISTS (
                SELECT 1
                FROM public.stations AS s
                JOIN public.tenants AS t ON t.id = s.tenant_id
                WHERE s.id = OLD.station_id AND t.code = '{GUARDED_TENANT_CODE}'
           )
        THEN
            RAISE EXCEPTION
                'operator % of %.% refused: the row is outside delivery % of tenant %',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME,
                '{GUARDED_DELIVERY_ID}', '{GUARDED_TENANT_CODE}'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF NEW.delivery_id IS DISTINCT FROM '{GUARDED_DELIVERY_ID}'
           OR NOT EXISTS (
                SELECT 1
                FROM public.stations AS s
                JOIN public.tenants AS t ON t.id = s.tenant_id
                WHERE s.id = NEW.station_id AND t.code = '{GUARDED_TENANT_CODE}'
           )
        THEN
            RAISE EXCEPTION
                'operator % of %.% refused: the row is outside delivery % of tenant %',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME,
                '{GUARDED_DELIVERY_ID}', '{GUARDED_TENANT_CODE}'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.station_id IS DISTINCT FROM OLD.station_id
           OR NEW.delivery_id IS DISTINCT FROM OLD.delivery_id
        THEN
            RAISE EXCEPTION
                'operator UPDATE of %.% may not move a row between stations or '
                'deliveries', TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_TABLE_NAME = 'rating_curves' THEN
            IF NEW.valid_from IS DISTINCT FROM OLD.valid_from
               OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
            THEN
                RAISE EXCEPTION
                    'operator UPDATE of a rating curve may not change its validity dates'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""

_STATION_FUNCTION = f"""
CREATE FUNCTION public.operator_guard_station_insert() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.tenants AS t
        WHERE t.id = NEW.tenant_id AND t.code = '{GUARDED_TENANT_CODE}'
    ) THEN
        RAISE EXCEPTION
            'operator may create stations only in tenant %',
            '{GUARDED_TENANT_CODE}'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

_TRUNCATE_FUNCTION = """
CREATE FUNCTION public.operator_guard_truncate() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'operator may not TRUNCATE %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

_WHEN = f"WHEN (session_user = '{OPERATOR_ROLE}')"

ROW_TRIGGERS: tuple[tuple[str, str, str], ...] = tuple(
    (table, f"trg_{table}_operator_guard_{event.lower()}", event)
    for table in ("observations", "rating_curves")
    for event in ("INSERT", "UPDATE", "DELETE")
)
STATION_TRIGGER = ("stations", "trg_stations_operator_guard_insert")
TRUNCATE_TRIGGERS: tuple[tuple[str, str], ...] = tuple(
    (table, f"trg_{table}_operator_guard_truncate")
    for table in ("observations", "rating_curves", "stations")
)
DML_TABLES = "observations, rating_curves, stations"

_REVOKE_OPERATOR_DML = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{OPERATOR_ROLE}') THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {DML_TABLES} '
                'FROM {OPERATOR_ROLE}';
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(_ROW_FUNCTION)
    op.execute(_STATION_FUNCTION)
    op.execute(_TRUNCATE_FUNCTION)
    for table, name, event in ROW_TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {event} ON public.{table} "
            f"FOR EACH ROW {_WHEN} "
            "EXECUTE FUNCTION public.operator_guard_delivery_row()"
        )
    table, name = STATION_TRIGGER
    op.execute(
        f"CREATE TRIGGER {name} BEFORE INSERT ON public.{table} "
        f"FOR EACH ROW {_WHEN} "
        "EXECUTE FUNCTION public.operator_guard_station_insert()"
    )
    for table, name in TRUNCATE_TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE TRUNCATE ON public.{table} "
            f"FOR EACH STATEMENT {_WHEN} "
            "EXECUTE FUNCTION public.operator_guard_truncate()"
        )


def downgrade() -> None:
    op.execute(_REVOKE_OPERATOR_DML)
    for table, name in TRUNCATE_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON public.{table}")
    table, name = STATION_TRIGGER
    op.execute(f"DROP TRIGGER IF EXISTS {name} ON public.{table}")
    for table, name, _ in reversed(ROW_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON public.{table}")
    op.execute("DROP FUNCTION IF EXISTS public.operator_guard_truncate()")
    op.execute("DROP FUNCTION IF EXISTS public.operator_guard_station_insert()")
    op.execute("DROP FUNCTION IF EXISTS public.operator_guard_delivery_row()")
