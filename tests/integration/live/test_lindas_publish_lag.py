# This test calls the external BAFU LINDAS endpoint repeatedly over an
# extended window. Excluded from default CI; run manually or on a dedicated
# longer-running schedule (NOT the live-lindas weekly workflow — its 10-min
# job timeout is too short for this test's default duration).
"""Plan 176 T7 — extended LINDAS publish-lag measurement.

D1's <=4 min max-poll-gap sizing (`docs/plans/176-lindas-archive-completeness.md`)
rests on a SINGLE clean publish gap (10.8 min) observed during that plan's
evidence-gathering. This test polls the live whole-graph endpoint at a fixed
cadence over a LONGER window, records each new network slot's first-sighting
wall-clock time, and reports the observed MINIMUM gap between consecutive
slot transitions — turning a single-sample assumption into a repeated
measurement, and giving an early signal if BAFU ever changes the grid or its
publish lag.

Deliberately reports rather than hard-fails on the gap value: per the plan,
a publish gap under ~8 min is a prompt for a human to TIGHTEN D1's bound,
not a CI failure — this test's job is to make the number visible (see the
`bafu_lindas_lag.summary` log line). It only asserts a sanity floor (a
transition gap under a minute would mean the poll cadence itself, not BAFU,
produced the reading) and that the window was long enough to observe at
least one real transition.

Excluded from BOTH the default gate (`live`) and the live-lindas WEEKLY
workflow's 10-minute timeout budget — this carries its OWN `live_lindas_lag`
marker, not `live_lindas`, and this test's own default duration (45 min)
exceeds that job's budget. Run manually::

    LINDAS_LAG_DURATION_S=2700 uv run pytest -m live_lindas_lag -v

or wire to a dedicated longer-running schedule.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest
import structlog

from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter
from sapphire_flow.adapters.lindas_rate_limiter import TokenBucketLindasLimiter
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_observation import BafuObservationRow
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

pytestmark = pytest.mark.live

_ENDPOINT = "https://lindas.admin.ch/query"
_LIVE_TIMEOUT_S = 30
# Poll cadence and total duration are both overridable via env var so a
# short smoke run can shrink the window without editing this file; a
# real measurement run should use (or widen) the defaults.
_POLL_INTERVAL_S = float(os.environ.get("LINDAS_LAG_POLL_INTERVAL_S", "60"))
_DURATION_S = float(os.environ.get("LINDAS_LAG_DURATION_S", str(45 * 60)))


def _modal_measurement_time(
    rows: list[BafuObservationRow],
) -> UtcDatetime | None:
    """Mirrors flows/collect_bafu_observations.py's `_modal_cycle_at`
    identity-grouping (without the grid truncation — this test wants the
    raw modal timestamp to measure lag against, not the archive key)."""
    if not rows:
        return None
    per_identity: dict[tuple[str, str], UtcDatetime] = {
        (r.gauge_code, r.lindas_kind): r.measurement_time for r in rows
    }
    counts = Counter(per_identity.values())
    max_count = max(counts.values())
    return min(ts for ts, count in counts.items() if count == max_count)


@pytest.mark.live_lindas_lag
class TestLiveLindasPublishLag:
    def test_slot_transition_gaps_over_an_extended_window(self) -> None:
        client = httpx.Client(timeout=_LIVE_TIMEOUT_S)
        limiter = TokenBucketLindasLimiter()
        adapter = BafuObservationAdapter(
            endpoint=_ENDPOINT, http_client=client, limiter=limiter
        )

        first_sightings: dict[UtcDatetime, UtcDatetime] = {}
        started = time.monotonic()
        last_slot: UtcDatetime | None = None

        while time.monotonic() - started < _DURATION_S:
            polled_at = ensure_utc(datetime.now(UTC))
            rows = adapter.fetch_all_observations()
            slot = _modal_measurement_time(rows)
            if slot is not None and slot != last_slot:
                first_sightings.setdefault(slot, polled_at)
                last_slot = slot
            time.sleep(_POLL_INTERVAL_S)

        sightings = sorted(first_sightings.items())
        assert len(sightings) >= 2, (
            f"observed only {len(sightings)} distinct network slot(s) over "
            f"{_DURATION_S / 60:.0f} min — window too short to measure a "
            "gap; widen LINDAS_LAG_DURATION_S"
        )

        gaps_s = [
            (b_seen - a_seen).total_seconds()
            for (_, a_seen), (_, b_seen) in zip(sightings, sightings[1:], strict=True)
        ]
        min_gap_s = min(gaps_s)

        log.info(
            "bafu_lindas_lag.summary",
            transitions_observed=len(sightings),
            duration_minutes=round(_DURATION_S / 60, 1),
            min_gap_minutes=round(min_gap_s / 60, 1),
            gaps_minutes=[round(g / 60, 1) for g in gaps_s],
        )
        if min_gap_s < 8 * 60:
            log.warning(
                "bafu_lindas_lag.gap_under_margin",
                min_gap_minutes=round(min_gap_s / 60, 1),
                note="D1's <=4 min poll-gap margin may need tightening",
            )

        # Sanity floor only, NOT the D1 margin itself (see module docstring).
        assert min_gap_s >= 60
