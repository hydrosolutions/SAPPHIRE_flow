# All tests in this module call external APIs (MeteoSwiss STAC / BAFU LINDAS).
# Excluded from default CI; runs via integration-nightly workflow.
"""Weekly live-LINDAS schema-drift check.

Gated behind the ``live`` / ``live_lindas`` markers — skipped in normal CI.
Run by .github/workflows/live-lindas-weekly.yml on a weekly schedule and by
the integration-nightly workflow via the unified ``live`` marker.

Failure = schema drift or endpoint outage = potential silent corruption of
observations accumulated since the last green run.  Halt the accumulation
phase and investigate.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import (
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationConfig

pytestmark = pytest.mark.live

_ENDPOINT = "https://lindas.admin.ch/query"
_LIVE_TIMEOUT_S = 30

# All valid parameter names the adapter may produce.
_EXPECTED_PARAMETERS = {"discharge", "water_level", "water_temperature"}


def _make_station_2044() -> StationConfig:
    return StationConfig(
        id=StationId(UUID("00000000-0000-0000-0000-000000002044")),
        code="2044",
        name="Hagneck (Aarezufluss zum Bielersee)",
        location=GeoCoord(lon=7.1737, lat=47.0542),
        station_kind=StationKind.RIVER,
        basin_id=None,
        timezone="Europe/Zurich",
        regulation_type=None,
        forecast_targets=None,
        measured_parameters=frozenset({"discharge"}),
        station_status=StationStatus.OPERATIONAL,
        created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        updated_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        network="bafu",
        ownership=StationOwnership.FOREIGN,
        wigos_id=None,
    )


@pytest.mark.live_lindas
class TestLiveLindasSchema:
    """Live-endpoint schema-drift check for BAFU LINDAS.

    Only runs when ``-m live_lindas`` is passed explicitly.
    """

    def test_fetch_returns_non_empty_raw_observations(self) -> None:
        """Adapter unpacks at least one RawObservation with the expected shape."""
        client = httpx.Client(timeout=_LIVE_TIMEOUT_S)
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        station = _make_station_2044()
        since = {station.id: ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))}

        observations = adapter.fetch_observations([station], since)

        assert len(observations) >= 1, (
            "LINDAS returned 0 observations for station 2044 — "
            "endpoint may be down or schema has changed"
        )
        for obs in observations:
            # Timestamp must be timezone-aware UTC
            assert obs.timestamp.tzinfo is not None, "timestamp is naive"
            assert obs.timestamp.utcoffset() is not None, "timestamp has no utcoffset"

            # Value must be a finite number (not NaN, not None at this stage)
            assert isinstance(obs.value, float), f"value is {type(obs.value)}"
            assert math.isfinite(obs.value), f"value is non-finite: {obs.value}"

            # Parameter must be in the known set
            assert obs.parameter in _EXPECTED_PARAMETERS, (
                f"unexpected parameter {obs.parameter!r} — "
                "LINDAS schema may have changed"
            )
