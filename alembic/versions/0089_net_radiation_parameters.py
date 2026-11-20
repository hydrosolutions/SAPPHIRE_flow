"""Register interval-qualified net radiation means."""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
DO $$
BEGIN
 IF EXISTS (
  SELECT 1 FROM parameters
  WHERE name IN ('solar_net_radiation', 'thermal_net_radiation')
  AND (unit <> 'W/m²' OR parameter_domain <> 'weather'
       OR aggregation_method <> 'mean')
 ) THEN
  RAISE EXCEPTION 'conflicting net radiation parameter definition';
 END IF;
END $$;
INSERT INTO parameters (name, display_name, unit, parameter_domain, aggregation_method)
VALUES ('solar_net_radiation', 'Net solar radiation', 'W/m²', 'weather', 'mean'),
       ('thermal_net_radiation', 'Net thermal radiation', 'W/m²', 'weather', 'mean')
ON CONFLICT (name) DO NOTHING;
""")


def downgrade() -> None:
    # Retained evidence can reference parameters inside bounded compressed blobs.
    # Refuse conservatively rather than introducing another decoder in Alembic.
    op.execute("""
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM historical_forcing
            WHERE parameter IN ('solar_net_radiation', 'thermal_net_radiation'))
 OR EXISTS (SELECT 1 FROM weather_forecasts
            WHERE parameter IN ('solar_net_radiation', 'thermal_net_radiation'))
 OR EXISTS (SELECT 1 FROM operational_weather_cache)
 OR EXISTS (SELECT 1 FROM forecast_evidence) THEN
  RAISE EXCEPTION 'cannot downgrade net radiation with retained forcing or evidence';
 END IF;
END $$;
DELETE FROM parameters
WHERE name IN ('solar_net_radiation', 'thermal_net_radiation');
""")
