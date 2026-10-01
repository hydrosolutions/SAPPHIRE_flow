"""Keep test forecasts outside normal publication and rejection capture dormant.

Revision ID: 0070
Revises: 0069
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM public.forecasts f
            WHERE f.data_use = 'expired_rating_test' AND (
                f.status IN ('reviewed', 'published') OR
                EXISTS (SELECT 1 FROM public.forecast_publication_selections s
                    WHERE s.selected_forecast_id = f.id) OR
                EXISTS (SELECT 1 FROM public.forecast_publication_decisions d
                    WHERE f.id IN (d.forecast_id, d.replaced_forecast_id)) OR
                EXISTS (SELECT 1 FROM public.forecast_publication_events e
                    WHERE f.id IN (e.forecast_id, e.replaced_forecast_id))))
    """)
    ):
        raise RuntimeError("test forecast publication exists; migration refused")
    op.create_check_constraint(
        "ck_forecasts_test_publication",
        "forecasts",
        "data_use = 'standard' OR status NOT IN ('reviewed', 'published')",
    )
    op.execute("""
        CREATE FUNCTION public.reject_test_forecast_publication() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE ids uuid[];
        BEGIN
            IF TG_TABLE_NAME = 'forecast_publication_selections' THEN
                ids := ARRAY[NEW.selected_forecast_id];
            ELSE
                ids := ARRAY[NEW.forecast_id, NEW.replaced_forecast_id];
            END IF;
            IF EXISTS (SELECT 1 FROM public.forecasts
                WHERE id = ANY(ids) AND data_use = 'expired_rating_test') THEN
                RAISE EXCEPTION 'test forecast cannot enter normal publication'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
    """)
    for table in (
        "forecast_publication_selections",
        "forecast_publication_decisions",
        "forecast_publication_events",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_standard_only BEFORE INSERT OR UPDATE"
            f" ON public.{table} FOR EACH ROW EXECUTE FUNCTION "
            f"public.reject_test_forecast_publication()"
        )
    op.add_column(
        "rejected_forecasts",
        sa.Column("data_use", sa.Text, nullable=False, server_default="standard"),
    )
    op.add_column(
        "rejected_forecasts", sa.Column("input_lineage", sa.Text, nullable=True)
    )
    op.create_check_constraint(
        "ck_rejected_forecasts_data_use",
        "rejected_forecasts",
        "data_use IN ('standard', 'expired_rating_test')",
    )
    op.create_check_constraint(
        "ck_rejected_forecasts_input_lineage",
        "rejected_forecasts",
        "(data_use = 'standard' AND input_lineage IS NULL) OR (data_use ="
        " 'expired_rating_test' AND input_lineage IS NOT NULL)",
    )
    # No gate, grants or bypass. Full source-linkage verification remains held.
    op.execute("""
        CREATE FUNCTION public.rejected_forecast_test_write_refused() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
        BEGIN
            IF NEW.data_use = 'expired_rating_test' THEN
                RAISE EXCEPTION
                    'test rejection writes are disabled pending deployment isolation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_rejected_forecast_test_write_refused
        BEFORE INSERT ON public.rejected_forecasts FOR EACH ROW
        EXECUTE FUNCTION public.rejected_forecast_test_write_refused();
    """)

    op.execute("""
        CREATE FUNCTION public.rejected_forecast_lineage_shape() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public, pg_temp AS $$
        DECLARE d jsonb; field text; sources integer := 0;
        BEGIN
            IF NEW.data_use = 'standard' THEN RETURN NEW; END IF;
            d := NEW.input_lineage::jsonb;
            IF NOT (jsonb_typeof(d) = 'object' AND d ?& ARRAY[
                'snapshots', 'static_attributes', 'provisional_discharge_fingerprints',
                'contributor_forecast_ids', 'transformation_versions'] AND
                d - ARRAY['snapshots', 'static_attributes',
                    'provisional_discharge_fingerprints', 'contributor_forecast_ids',
                    'transformation_versions'] = '{}'::jsonb) IS TRUE THEN
                RAISE EXCEPTION 'test rejection input lineage is malformed';
            END IF;
            FOREACH field IN ARRAY ARRAY['snapshots', 'static_attributes',
                'provisional_discharge_fingerprints', 'contributor_forecast_ids',
                'transformation_versions'] LOOP
                IF jsonb_typeof(d->field) <> 'array' THEN
                    RAISE EXCEPTION 'test rejection input lineage is malformed';
                END IF;
                IF field <> 'transformation_versions' THEN
                    sources := sources + jsonb_array_length(d->field);
                END IF;
            END LOOP;
            IF sources = 0 OR jsonb_array_length(d->'transformation_versions') = 0 THEN
                RAISE EXCEPTION 'test rejection input lineage is empty';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_rejected_forecast_lineage_shape AFTER INSERT
        ON public.rejected_forecasts FOR EACH ROW
        EXECUTE FUNCTION public.rejected_forecast_lineage_shape();
    """)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM public.forecasts WHERE data_use <> 'standard')
            OR EXISTS (SELECT 1 FROM public.rejected_forecasts
                WHERE data_use <> 'standard')
    """)
    ):
        raise RuntimeError("test forecast or rejection exists; downgrade refused")
    op.execute(
        "DROP TRIGGER trg_rejected_forecast_test_write_refused ON "
        "public.rejected_forecasts"
    )
    op.execute("DROP FUNCTION public.rejected_forecast_test_write_refused()")
    op.execute(
        "DROP TRIGGER trg_rejected_forecast_lineage_shape ON public.rejected_forecasts"
    )
    op.execute("DROP FUNCTION public.rejected_forecast_lineage_shape()")
    op.drop_constraint("ck_rejected_forecasts_input_lineage", "rejected_forecasts")
    op.drop_constraint("ck_rejected_forecasts_data_use", "rejected_forecasts")
    op.drop_column("rejected_forecasts", "input_lineage")
    op.drop_column("rejected_forecasts", "data_use")
    for table in (
        "forecast_publication_selections",
        "forecast_publication_decisions",
        "forecast_publication_events",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_standard_only ON public.{table}")
    op.execute("DROP FUNCTION public.reject_test_forecast_publication()")
    op.drop_constraint("ck_forecasts_test_publication", "forecasts")
