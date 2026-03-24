from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.store.parameter_store import PgParameterStore
from sapphire_flow.types.enums import AggregationMethod, ParameterDomain

if TYPE_CHECKING:
    import sqlalchemy as sa


class TestPgParameterStore:
    def test_fetch_all_returns_seeded_parameters(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgParameterStore(db_connection)
        results = store.fetch_all()
        assert len(results) == 10
        names = {p.name for p in results}
        assert names == {
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

    def test_fetch_by_name_found(self, db_connection: sa.Connection) -> None:
        store = PgParameterStore(db_connection)
        result = store.fetch_by_name("discharge")
        assert result is not None
        assert result.name == "discharge"
        assert result.display_name == "Discharge"
        assert result.unit == "m³/s"
        assert result.parameter_domain == ParameterDomain.RIVER
        assert result.aggregation_method == AggregationMethod.MEAN

    def test_fetch_by_name_not_found(self, db_connection: sa.Connection) -> None:
        store = PgParameterStore(db_connection)
        result = store.fetch_by_name("nonexistent_parameter")
        assert result is None
