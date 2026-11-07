from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.operational_weather_cache import PgOperationalWeatherCache
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    WeatherAttemptState,
    WeatherCacheCompletion,
    WeatherCacheKey,
    WeatherFailureReason,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.operational_weather import FINGERPRINT, ISSUE
from tests.integration.db.test_role_bootstrap import role_harness as role_harness

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


class TestOperationalWeatherRoles:
    def test_worker_lifecycle_and_api_isolation(
        self, role_harness: _RoleBootstrapHarness
    ) -> None:
        result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
        assert result.returncode == 0, result.stderr
        station = make_station_config(
            station_id=StationId(uuid4()),
            code="weather-" + uuid4().hex[:8],
            network="dhm",
        )
        with role_harness.owner_engine.begin() as conn:
            PgStationStore(conn).store_station(station)
        key = WeatherCacheKey(station_id=station.id, binding_fingerprint=FINGERPRINT)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        with role_harness.owner_engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            store = PgOperationalWeatherCache(conn)
            attempt = store.begin(
                key=key,
                principal=principal,
                attempt_id=uuid4(),
                issue_time=ISSUE,
                started_at=ISSUE,
            )
            outcome = store.finish(
                attempt=attempt,
                principal=principal,
                earliest_cycle=ISSUE,
                completion=WeatherCacheCompletion(
                    finished_at=ISSUE,
                    future_attempts=(),
                    past_outcome=WeatherLegOutcome(
                        state=WeatherLegState.UNAVAILABLE,
                        reason=WeatherFailureReason.TRANSPORT,
                    ),
                ),
            )
            assert outcome.state is WeatherAttemptState.FAILED
        for role, statement in (
            ("sapphire_api", "SELECT * FROM operational_weather_cache"),
            ("sapphire_api", "UPDATE operational_weather_cache SET outcome='failed'"),
            ("sapphire_worker", "DELETE FROM operational_weather_cache"),
            ("sapphire_worker", "TRUNCATE operational_weather_cache"),
        ):
            with role_harness.owner_engine.begin() as conn:
                conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
                with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
                    conn.execute(sa.text(statement))
