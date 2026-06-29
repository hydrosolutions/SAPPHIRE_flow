"""LOCKED regression test for the onboarding-service restatement path.

Milestone: obs-ingest-upsert-cadence (acceptance criterion 3).

The onboarding bulk-import path (``_run_onboarding`` -> Step 3) must keep its
counters correct once ``store_raw_observations`` upserts restatements:

  * A restated value for an already-stored natural key counts as an import
    (``observations_imported`` increments) — an updated row is a write, not a
    skip.
  * ``observation.duplicate_skipped`` is NOT emitted for that restated row.

MUST FAIL while ``store_raw_observations`` drops same-natural-key
restatements (the restated row is counted as a skip and logged as a
duplicate). Passes only once the upsert + uuid4 set-diff counting are correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from sapphire_flow.services.onboarding import _run_onboarding
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeFlowRegimeConfigStore,
    FakeHistoricalForcingStore,
    FakeObservationStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

_EPOCH = ensure_utc(datetime(2000, 1, 1, tzinfo=UTC))
_T = ensure_utc(datetime(2010, 6, 1, tzinfo=UTC))

_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(days=1),
            thresholds={"value_min": 0.0, "value_max": 10000.0},
        ),
    ),
)


def _fixed_clock() -> UtcDatetime:
    return _EPOCH


def _raw(station_id: StationId, value: float) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=_T,
        parameter="discharge",
        value=value,
        source=ObservationSource.MANUAL_IMPORT,
    )


def _basin(code: str) -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code=code,
        name=f"Basin {code}",
        geometry=None,
        area_km2=100.0,
        attributes=None,
        band_geometries=None,
        created_at=_EPOCH,
        network="bafu",
    )


class TestOnboardingRestatement:
    def test_restated_value_counts_as_import_and_no_duplicate_skip(self) -> None:
        sid = StationId(uuid4())
        station = make_station_config(station_id=sid, code="UPSERT-OB-001")
        basin = _basin("UPSERT-OB-001")

        obs_store = FakeObservationStore()
        # Pre-seed the natural key with the original value via the same store.
        obs_store.store_raw_observations([_raw(sid, 110.0)])

        with structlog.testing.capture_logs() as captured:
            result = _run_onboarding(
                stations=[station],
                basins=[basin],
                # Onboarding re-imports the SAME natural key with a new value.
                obs_by_station={sid: [_raw(sid, 130.0)]},
                forcing_by_station={sid: []},
                basin_store=FakeBasinStore(),
                station_store=FakeStationStore(),
                obs_store=obs_store,
                forcing_store=FakeHistoricalForcingStore(),
                baseline_store=FakeClimBaselineStore(),
                flow_regime_store=FakeFlowRegimeConfigStore(),
                qc_rules=_RULES,
                clock=_fixed_clock,
                start_utc=ensure_utc(datetime(1990, 1, 1, tzinfo=UTC)),
                end_utc=ensure_utc(datetime(2030, 1, 1, tzinfo=UTC)),
            )

        # The restatement is an import (updated row counts as a write).
        assert result.observations_imported == 1

        # No duplicate-skip log for the restated row.
        dup_events = [
            e for e in captured if e.get("event") == "observation.duplicate_skipped"
        ]
        assert dup_events == []

        # The stored value reflects the restatement.
        rows = obs_store.observations()
        assert len(rows) == 1
        assert rows[0].value == 130.0
