# Plan 094 — cap onboarding/hindcast window to the actual data range

**Status**: DRAFT
**Priority**: low — cosmetic + efficiency; not a correctness bug.
**Phase**: v0b — onboarding efficiency
**Parent**: epic 088 (NWP-on); surfaced during the 2026-07-03 Mac-mini onboarding
**Related**: `services/onboarding.py` (`_WIDE_START`/`_WIDE_END`, the hindcast
loop), the hindcast service, `hindcast.skip.no_observations`
**Created**: 2026-07-03

---

## Problem

Onboarding defaults the ingest/hindcast window to a deliberately wide
**1980-01-01 → 2030-01-01** (`services/onboarding.py:58-59`,
`_WIDE_START`/`_WIDE_END`) so it captures whatever a source provides. CAMELS-CH
discharge observations end **~2020**, so the historical hindcast walks daily
issue-times all the way to 2030 and logs `hindcast.skip.no_observations` for
every date past ~2020 — ~9 years of empty future iterations per station.

Harmless (skipped, onboarding completes) but wasteful and alarming to watch
("why is the hindcast in 2029?").

## Goal

The hindcast (and the ingest/QC/baseline range where sensible) is bounded to the
station's **actual observation coverage** — e.g. `min(_WIDE_END, max(observation
timestamp) + small margin)` — so no empty future steps are iterated.

## Open design questions (grill-me before READY)

1. **Where to cap.** Derive the effective `end_utc` from the max observation (or
   forcing) timestamp after ingest, and pass that to the hindcast loop; vs a
   config `onboarding.max_hindcast_end`. Prefer data-derived.
2. **Per-station vs run-level.** Observation coverage differs per station — cap
   per station (each hindcast bounded to its own max obs) vs one run-level end.
3. **Interaction with live/operational.** Ensure capping the *historical*
   hindcast window does not affect operational forecast issue-times (which are
   "now"-anchored) or future re-onboarding once live data accrues past 2020.
4. **Forcing vs observation range.** If the basin-average forcing extends past
   the discharge obs, decide which bounds the hindcast (obs, since skill needs
   observed targets).

## Non-goals

- The `nwp_regression` matmul/lag robustness (Plan 093).
- Changing the wide *ingest* default if a source legitimately has recent data.

## Process

DRAFT until grill-me, then phases → READY. Small change in
`services/onboarding.py`; add a test that a station whose observations end at T
produces no `hindcast.skip.no_observations` beyond T.
