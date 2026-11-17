from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from sapphire_flow.adapters.recap_operational import (
    operational_binding_fingerprint,
    operational_binding_fingerprint_v2,
)
from sapphire_flow.store.operational_weather_cache import PgOperationalWeatherCache
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.operational_weather import WeatherCacheKey
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.weather_periods import GRID, ISSUE, SID
from tests.integration.store.test_operational_weather_cache import successful_completion
from tests.unit.adapters.test_recap_operational import PolygonResolver, make_binding


def test_upgrade_preserves_v1_and_grid_keys_coexist_with_downgrade_guard(
    db_connection: sa.Connection,
) -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0086")
    assert revision is not None
    module = revision.module
    station = make_station_config(station_id=SID)
    PgStationStore(db_connection).store_station(station)
    principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
    source = make_binding()
    ref = PolygonResolver().resolve(source)
    key = WeatherCacheKey(
        station_id=SID, binding_fingerprint=operational_binding_fingerprint(source, ref)
    )
    store = PgOperationalWeatherCache(db_connection)
    # The legacy fixture has its own issue, before the period-context fixture.
    completion = successful_completion(key)
    assert completion.past_snapshot is not None
    completion = replace(
        completion,
        past_snapshot=make_past_snapshot(
            window=replace(
                completion.past_snapshot.window,
                binding_fingerprint=key.binding_fingerprint,
            ),
            retrieved_at=completion.finished_at,
            snapshot_id=uuid4(),
            max_bytes=2**20,
        ),
    )
    attempt = store.begin(
        key=key,
        principal=principal,
        attempt_id=uuid4(),
        issue_time=completion.finished_at,
        started_at=completion.finished_at,
    )
    before = store.finish(
        attempt=attempt,
        principal=principal,
        completion=completion,
        earliest_cycle=completion.finished_at,
    )
    with Operations.context(MigrationContext.configure(db_connection)):
        module.downgrade()
        module.upgrade()
    assert store.fetch(key) == before
    period_key = WeatherCacheKey(
        station_id=SID,
        binding_fingerprint=operational_binding_fingerprint_v2(source, ref, GRID),
    )
    assert period_key != key
    period_attempt = store.begin_period(
        key=period_key,
        principal=principal,
        attempt_id=uuid4(),
        issue_time=ISSUE,
        started_at=ISSUE,
        grid=GRID,
        eligibility_cutoff=ISSUE,
    )
    assert store.fetch_period(period_key, grid=GRID).attempt == period_attempt
    assert store.fetch(key) == before
    with pytest.raises(ValueError, match="version"):
        store.fetch(period_key)
    with (
        pytest.raises(sa.exc.DBAPIError, match="cannot remove retained v2"),
        db_connection.begin_nested(),
        Operations.context(MigrationContext.configure(db_connection)),
    ):
        module.downgrade()
    assert store.fetch_period(period_key, grid=GRID).attempt == period_attempt
