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

**The probe-start artifact, and why the first observed slot is a BASELINE,
never a measurement.** The plan's own evidence explicitly retracts a false
21.6 min lag / 2.5 min gap reading: the probe started mid-slot, so the slot
already visible at the first poll was NOT first-sighted then — its true
publish moment predates the probe entirely, and treating that poll's
timestamp as its "first sighting" recreates exactly the retracted artifact.
This test therefore discards its own first observed slot as a baseline and
only records TRANSITIONS seen after it — slots whose absence (then presence)
this test actually watched happen. Both the per-slot publish LAG
(`first_seen - slot`) and the gaps between consecutive clean transitions are
computed only from those.

Deliberately reports rather than hard-fails on the gap/lag values: per the
plan, a publish gap under ~8 min is a prompt for a human to TIGHTEN D1's
bound, not a CI failure — this test's job is to make the numbers visible
(see the `bafu_lindas_lag.summary` log line, and the `-s` flag below so it
prints on success, not only on failure). It only asserts a sanity floor (a
transition gap under a minute would mean the poll cadence itself, not BAFU,
produced the reading) and that the window was long enough to observe at
least two clean transitions after the baseline.

Excluded from BOTH the default gate (`live`) and the live-lindas WEEKLY
workflow's 10-minute timeout budget — this carries its OWN `live_lindas_lag`
marker, not `live_lindas`, and this test's own default duration (45 min)
exceeds that job's budget. Run manually::

    RUN_LINDAS_LAG_MEASUREMENT=1 LINDAS_LAG_DURATION_S=2700 \
        uv run pytest tests/integration/live/test_lindas_publish_lag.py -v -s

or wire to a dedicated longer-running schedule.

**The marker is NOT what excludes this test.** `integration-nightly.yml`
selects `tests/integration/live` by PATH with `--override-ini "addopts="`,
explicitly so it runs every test in this directory whatever marker it carries.
The binding exclusion is therefore the `RUN_LINDAS_LAG_MEASUREMENT=1` env gate
below; without it this file would add ~45 min of live polling against an
external government API to every nightly run, indefinitely.
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

# Markers alone do NOT keep this test out of CI. `integration-nightly.yml` runs
# `pytest tests/integration/live --override-ini "addopts="`, deliberately
# path-based so it sweeps in EVERY test under this directory "regardless of
# which live* marker it carries" — with `--timeout=3600`, comfortably longer
# than this test's 45-min default, so it would run to completion rather than
# being killed. Without the opt-in gate below, merging this file would silently
# start ~45 min of live polling against a government API every single night.
#
# So the real exclusion is this env gate; the markers are secondary.
_OPT_IN_ENV = "RUN_LINDAS_LAG_MEASUREMENT"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get(_OPT_IN_ENV) != "1",
        reason=(
            f"extended live measurement — set {_OPT_IN_ENV}=1 to run. "
            "Gated by env, not marker, because the nightly job's path-based "
            "selection overrides marker exclusion."
        ),
    ),
]

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

        # `baseline_slot` is the slot already visible at the FIRST poll — its
        # true first-sighting predates this test and is unobservable (the
        # probe-start artifact the plan explicitly retracts; see module
        # docstring). It is recorded only to detect the *next* transition
        # and is never treated as a measured sighting.
        first_sightings: dict[UtcDatetime, UtcDatetime] = {}
        baseline_slot: UtcDatetime | None = None
        started = time.monotonic()
        last_slot: UtcDatetime | None = None

        while time.monotonic() - started < _DURATION_S:
            polled_at = ensure_utc(datetime.now(UTC))
            rows = adapter.fetch_all_observations()
            slot = _modal_measurement_time(rows)
            if slot is not None and slot != last_slot:
                if baseline_slot is None:
                    baseline_slot = slot
                else:
                    first_sightings.setdefault(slot, polled_at)
                last_slot = slot
            time.sleep(_POLL_INTERVAL_S)

        sightings = sorted(first_sightings.items())
        assert len(sightings) >= 2, (
            f"observed only {len(sightings)} clean slot transition(s) (after "
            "discarding the probe-start baseline) over "
            f"{_DURATION_S / 60:.0f} min — window too short to measure a "
            "gap; widen LINDAS_LAG_DURATION_S"
        )

        # Publish lag per clean transition: how long after the slot's own
        # timestamp did we first see it published. NOT computed for the
        # baseline slot — its true publish moment is unknown.
        lags_s = [(seen - slot).total_seconds() for slot, seen in sightings]
        assert all(lag >= 0 for lag in lags_s), (
            f"a negative first_seen - slot lag ({lags_s}) means the sighting "
            "clock and the slot timestamps disagree — investigate before "
            "trusting the gap numbers below"
        )

        gaps_s = [
            (b_seen - a_seen).total_seconds()
            for (_, a_seen), (_, b_seen) in zip(sightings, sightings[1:], strict=True)
        ]
        min_gap_s = min(gaps_s)
        min_lag_s = min(lags_s)

        summary = {
            "transitions_observed": len(sightings),
            "duration_minutes": round(_DURATION_S / 60, 1),
            "min_gap_minutes": round(min_gap_s / 60, 1),
            "gaps_minutes": [round(g / 60, 1) for g in gaps_s],
            "min_lag_minutes": round(min_lag_s / 60, 1),
            "lags_minutes": [round(lag / 60, 1) for lag in lags_s],
        }
        # `-s` (see module docstring) so this prints even when every assert
        # below passes — the whole point of this test is the number, not a
        # pass/fail bit.
        print(f"bafu_lindas_lag.summary {summary}")  # noqa: T201
        log.info("bafu_lindas_lag.summary", **summary)
        if min_gap_s < 8 * 60:
            log.warning(
                "bafu_lindas_lag.gap_under_margin",
                min_gap_minutes=round(min_gap_s / 60, 1),
                note="D1's <=4 min poll-gap margin may need tightening",
            )

        # Sanity floor only, NOT the D1 margin itself (see module docstring).
        assert min_gap_s >= 60
