from __future__ import annotations

import sqlalchemy as sa


class TestDatabaseSetup:
    def test_migration_applied(self, db_connection: sa.Connection) -> None:
        """Verify Alembic migration created all expected tables."""
        inspector = sa.inspect(db_connection)
        tables = set(inspector.get_table_names())

        expected = {
            "parameters",
            "basins",
            "stations",
            "station_thresholds",
            "station_weather_sources",
            "station_groups",
            "station_group_members",
            "observations",
            "weather_forecasts",
            "models",
            "model_artifacts",
            "model_assignments",
            "model_states",
            "forecasts",
            "forecast_values",
            "hindcast_forecasts",
            "hindcast_values",
            "skill_scores",
            "skill_diagrams",
            "flow_regime_configs",
            "alerts",
            "pipeline_health",
        }
        # alembic_version table also exists
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_parameters_seeded(self, db_connection: sa.Connection) -> None:
        """Verify canonical parameters were seeded."""
        result = db_connection.execute(
            sa.text("SELECT name FROM parameters ORDER BY name")
        )
        names = {row[0] for row in result}
        expected = {
            "discharge",
            "water_level",
            "precipitation",
            "temperature",
            "humidity",
            "radiation",
            "wind_speed",
            "snow_depth",
            "reference_et",
            "swe",
        }
        assert expected == names

    def test_postgis_available(self, db_connection: sa.Connection) -> None:
        """Verify PostGIS extension is available."""
        result = db_connection.execute(sa.text("SELECT PostGIS_Version()"))
        version = result.scalar()
        assert version is not None
