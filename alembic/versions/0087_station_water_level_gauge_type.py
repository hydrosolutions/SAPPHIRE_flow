"""Station water-level gauge type and bridge-sensor elevation (Plan 540 M2)."""

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE stations ADD COLUMN water_level_gauge_type text;
ALTER TABLE stations ADD COLUMN water_level_sensor_masl double precision;
ALTER TABLE stations ADD CONSTRAINT ck_stations_water_level_gauge_type
 CHECK (water_level_gauge_type IN ('staff_or_pressure', 'bridge_sensor'));
ALTER TABLE stations ADD CONSTRAINT ck_stations_water_level_sensor_pairing
 CHECK (water_level_sensor_masl IS NULL
        OR water_level_gauge_type IS NOT DISTINCT FROM 'bridge_sensor');
ALTER TABLE stations ADD CONSTRAINT ck_stations_water_level_gauge_datum
 CHECK (water_level_gauge_type IS NULL OR water_level_datum_masl IS NOT NULL);
ALTER TABLE stations ADD CONSTRAINT ck_stations_water_level_bridge_sensor
 CHECK (water_level_gauge_type IS DISTINCT FROM 'bridge_sensor'
        OR water_level_sensor_masl IS NOT NULL);
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE stations DROP CONSTRAINT ck_stations_water_level_bridge_sensor;
ALTER TABLE stations DROP CONSTRAINT ck_stations_water_level_gauge_datum;
ALTER TABLE stations DROP CONSTRAINT ck_stations_water_level_sensor_pairing;
ALTER TABLE stations DROP CONSTRAINT ck_stations_water_level_gauge_type;
ALTER TABLE stations DROP COLUMN water_level_sensor_masl;
ALTER TABLE stations DROP COLUMN water_level_gauge_type;
""")
