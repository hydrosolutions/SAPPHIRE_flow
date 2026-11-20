from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.operational_weather_cache import PgOperationalWeatherCache
from sapphire_flow.types.operational_weather import WeatherCacheKey
from sapphire_flow.types.write_principal import WritePrincipal
from tests.fakes.operational_weather import ISSUE
from tests.integration.store.test_forecast_evidence_store import _evidence
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)


def test_parameter_rows_and_non_destructive_empty_downgrade(
    db_connection: sa.Connection,
) -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0089")
    assert revision is not None
    before = db_connection.execute(
        sa.text("SELECT name, unit FROM parameters ORDER BY name")
    ).all()
    with Operations.context(MigrationContext.configure(db_connection)):
        revision.module.downgrade()
        revision.module.upgrade()
    assert (
        db_connection.execute(
            sa.text("SELECT name, unit FROM parameters ORDER BY name")
        ).all()
        == before
    )
    rows = db_connection.execute(
        sa.text(
            "SELECT name, unit, aggregation_method, parameter_domain FROM parameters "
            "WHERE name LIKE '%_net_radiation' ORDER BY name"
        )
    ).all()
    assert rows == [
        ("solar_net_radiation", "W/m²", "mean", "weather"),
        ("thermal_net_radiation", "W/m²", "mean", "weather"),
    ]


@pytest.mark.parametrize("retained", ["cache", "evidence"])
def test_downgrade_refuses_retained_only_data(
    db_connection: sa.Connection, retained: str
) -> None:
    sid = _seed_station(db_connection)
    if retained == "cache":
        from sapphire_flow.store.station_store import PgStationStore

        station = PgStationStore(db_connection).fetch_station(sid)
        assert station is not None
        PgOperationalWeatherCache(db_connection).begin(
            key=WeatherCacheKey(station_id=sid, binding_fingerprint="a" * 64),
            principal=WritePrincipal(id=None, tenant_id=station.tenant_id),
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
    else:
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_forecast(replace(_make_forecast(sid, mid, aid), evidence=_evidence()))
    assert db_connection.scalar(sa.text("SELECT count(*) FROM historical_forcing")) == 0
    assert db_connection.scalar(sa.text("SELECT count(*) FROM weather_forecasts")) == 0
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0089")
    assert revision is not None
    with (
        pytest.raises(sa.exc.DBAPIError, match="retained forcing or evidence"),
        db_connection.begin_nested(),
        Operations.context(MigrationContext.configure(db_connection)),
    ):
        revision.module.downgrade()


def test_conflicting_existing_unit_refused(db_connection: sa.Connection) -> None:
    db_connection.execute(
        sa.text("UPDATE parameters SET unit='J/m²' WHERE name='solar_net_radiation'")
    )
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0089")
    assert revision is not None
    with (
        pytest.raises(sa.exc.DBAPIError, match="conflicting net radiation"),
        db_connection.begin_nested(),
        Operations.context(MigrationContext.configure(db_connection)),
    ):
        revision.module.upgrade()
