# All tests in this module call external APIs (MeteoSwiss STAC / BAFU LINDAS).
# Excluded from default CI; runs via integration-nightly workflow.
"""Weekly live-LINDAS schema-drift check — multi-station mixed-kind quorum.

Gated behind the ``live`` / ``live_lindas`` markers — skipped in normal CI.
Run by .github/workflows/live-lindas-weekly.yml on a weekly schedule and by
the integration-nightly workflow via the unified ``live`` marker.

Design (Plan 074):
  - 6 river reference stations form the quorum; ≥1 must return observations.
  - 1 lake-path station (2004) is queried opportunistically; its absence does
    not fail the test.
  - A schema rename or endpoint outage on the river path empties **all** river
    stations simultaneously — a far stronger signal than a single-station check.

Failure semantics:
  "all N river stations empty" → LINDAS river endpoint outage or schema drift.
  "missing expected river parameter(s)" → parameter name drift.
  shape violation → parser drift (timestamp/value type changed).

Station codes are sourced from tests/fixtures/reference/stations.toml.
Station 2004 intentionally uses StationKind.LAKE here even though the TOML
entry says station_kind = "river" — live probing on 2026-05-04 confirmed that
LINDAS only serves 2004 under the lake/observation/<code> URI path.
See Plan 058 §T1 for the recommended roster-widening follow-up.
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

# Expected parameter coverage across all returned river observations combined.
_EXPECTED_RIVER_PARAMETERS = {"discharge", "water_level", "water_temperature"}

# Lake station 2004: only water_level is served under the lake URI path.
_EXPECTED_LAKE_PARAMETERS = {"water_level"}

_CREATED_AT = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _make_reference_stations() -> list[StationConfig]:
    """Return the 7 BAFU reference stations used in the live quorum check.

    Codes are sourced from tests/fixtures/reference/stations.toml.
    LINDAS kind is hardcoded per live probe (2026-05-04) and may differ from
    the hydrological classification in the TOML — notably 2004 is LAKE here.
    """
    # (code, name, lon, lat, station_kind, measured_parameters)
    roster: list[tuple[str, str, float, float, StationKind, frozenset[str]]] = [
        (
            "2004",
            "Bern, Schönau (Aare)",
            7.4459,
            46.9448,
            StationKind.LAKE,
            frozenset({"water_level"}),
        ),
        (
            "2009",
            "Thun (Aare)",
            7.6280,
            46.7503,
            StationKind.RIVER,
            frozenset({"discharge", "water_level", "water_temperature"}),
        ),
        (
            "2033",
            "Murgenthal (Aare)",
            7.8468,
            47.2692,
            StationKind.RIVER,
            frozenset({"discharge", "water_level", "water_temperature"}),
        ),
        (
            "2044",
            "Hagneck (Aarezufluss zum Bielersee)",
            7.1737,
            47.0542,
            StationKind.RIVER,
            frozenset({"discharge", "water_level", "water_temperature"}),
        ),
        (
            "2091",
            "Brugg (Aare)",
            8.2097,
            47.4844,
            StationKind.RIVER,
            frozenset({"discharge", "water_level", "water_temperature"}),
        ),
        (
            "2159",
            "Untersiggenthal (Aare)",
            8.2253,
            47.5256,
            StationKind.RIVER,
            frozenset({"discharge", "water_level", "water_temperature"}),
        ),
        (
            "2085",
            "Biel/Bienne (Schüss)",
            7.2439,
            47.1436,
            StationKind.RIVER,
            frozenset({"discharge", "water_level", "water_temperature"}),
        ),
    ]
    return [
        StationConfig(
            id=StationId(UUID(f"00000000-0000-0000-0000-{int(code):012d}")),
            code=code,
            name=name,
            location=GeoCoord(lon=lon, lat=lat),
            station_kind=kind,
            basin_id=None,
            timezone="Europe/Zurich",
            regulation_type=None,
            forecast_targets=None,
            measured_parameters=params,
            station_status=StationStatus.OPERATIONAL,
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
            network="bafu",
            ownership=StationOwnership.FOREIGN,
            wigos_id=None,
        )
        for code, name, lon, lat, kind, params in roster
    ]


@pytest.mark.live_lindas
class TestLiveLindasSchema:
    """Live-endpoint schema-drift check for BAFU LINDAS.

    Only runs when ``-m live_lindas`` is passed explicitly.
    """

    def test_reference_station_quorum_returns_well_formed_observations(self) -> None:
        """River quorum ≥1 station non-empty; global river-parameter coverage passes."""
        client = httpx.Client(timeout=_LIVE_TIMEOUT_S)
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        stations = _make_reference_stations()
        since = {s.id: ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)) for s in stations}

        observations = adapter.fetch_observations(stations, since)

        # Group by station_id for quorum checks.
        obs_by_station: dict[StationId, list] = {}
        for obs in observations:
            obs_by_station.setdefault(obs.station_id, []).append(obs)

        river_station_ids = {
            s.id for s in stations if s.station_kind == StationKind.RIVER
        }
        lake_station_ids = {
            s.id for s in stations if s.station_kind == StationKind.LAKE
        }
        river_codes = [s.code for s in stations if s.station_kind == StationKind.RIVER]

        # --- River quorum assertion ---
        river_non_empty_count = sum(
            1 for sid in river_station_ids if obs_by_station.get(sid)
        )
        assert river_non_empty_count >= 1, (
            f"All {len(river_station_ids)} reference BAFU river stations returned "
            "0 observations — LINDAS river endpoint outage or schema drift. "
            f"Codes: {river_codes}"
        )

        # --- Global river-parameter coverage ---
        river_parameters = {
            obs.parameter
            for sid in river_station_ids
            for obs in obs_by_station.get(sid, [])
        }
        missing = _EXPECTED_RIVER_PARAMETERS - river_parameters
        assert not missing, (
            f"Missing expected river parameter(s) {missing!r} across all returned "
            "river observations — LINDAS parameter name drift or adapter regression. "
            f"Observed river parameters: {river_parameters}"
        )

        # --- Opportunistic lake-path check (2004) ---
        lake_obs = [
            obs for sid in lake_station_ids for obs in obs_by_station.get(sid, [])
        ]
        if lake_obs:
            for obs in lake_obs:
                assert obs.parameter in _EXPECTED_LAKE_PARAMETERS, (
                    f"Lake observation has unexpected parameter {obs.parameter!r}; "
                    f"expected one of {_EXPECTED_LAKE_PARAMETERS}"
                )

        # --- Per-observation shape assertions (all stations) ---
        for obs in observations:
            # Timestamp must be timezone-aware UTC.
            assert obs.timestamp.tzinfo is not None, (
                f"station {obs.station_id}: timestamp is naive"
            )
            assert obs.timestamp.utcoffset() is not None, (
                f"station {obs.station_id}: timestamp has no utcoffset"
            )

            # Value must be a finite number.
            assert isinstance(obs.value, float), (
                f"station {obs.station_id}: value is {type(obs.value)}, expected float"
            )
            assert math.isfinite(obs.value), (
                f"station {obs.station_id}: value is non-finite: {obs.value}"
            )

            # Parameter must be in the known set.
            assert obs.parameter in _EXPECTED_PARAMETERS, (
                f"station {obs.station_id}: unexpected parameter {obs.parameter!r} — "
                "LINDAS schema may have changed"
            )
