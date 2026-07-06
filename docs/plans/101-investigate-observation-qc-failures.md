# Plan 101 — investigate observation-QC failures (`ingest.qc_complete failed=2`)

**Status**: DRAFT — **root cause FOUND (2026-07-06, local-stack repro); fix
decision pending grill-me** (see Findings).
**Priority**: medium — surfaced on the mac-mini 2026-07-06: `ingest.qc_complete`
reports `failed=2` on obs ingest. Not yet known whether this is legitimate
bad-data rejection, a too-tight QC threshold, or a rule bug. Matters because the
`nwp_regression`-with-lags model consumes obs lag history — QC-rejected obs degrade
the lag window and can contribute to model failures independently of NWP.
**Phase**: v0b — observation ingest / data quality
**Parent**: the operational obs feed (Plan 091); companion to Plan 100
(forecast-feed resilience)
**Related**:
- `src/sapphire_flow/flows/ingest_observations.py:178-187` (QC loop; `counts["failed"] += 1`), `:39,41` (`qc_failed`, `stations_failed` result fields), the `ingest.qc_complete` structured event
- the observation-QC service/rules (range / spike / flatline / stale checks) + their thresholds in config
- `src/sapphire_flow/services/forecast_qc.py` (forecast-side QC — confirm whether obs QC shares rule code)
- BAFU/LINDAS adapter `src/sapphire_flow/adapters/hydro_scraper.py` (source of the obs being QC'd)
**Created**: 2026-07-06

---

## Problem

The observation-ingest flow emits `ingest.qc_complete` with **`failed=2`** on the
mini. We do not yet know:
- **Which** stations / parameters are failing (station 2009? 2091? discharge?
  water_level?).
- **Which QC rule** rejects them (range bound, spike, flatline/stale, unit) and
  with what values vs threshold.
- Whether it is **legitimate** (genuine bad sensor data → correct reject),
  **misconfigured** (threshold too tight for these BAFU stations), or a **bug**
  (rule misfire / unit mismatch — note the `m³/s` unit-standardisation history).
- Whether `failed=2` is **persistent every tick** (systematic) or **occasional**
  (transient bad readings).

## Findings (2026-07-06 — reproduced on the local dev stack)

The local stack (same two stations, 2009/2091; 60k+ obs, live-ingesting) shows
**622 `qc_failed`** — **311 per station, all `water_level`** (discharge is clean).
Every failure is the same `range_check` rejection (from the stored `qc_flags`):

```
2091 water_level 261.5   → "value 261.5 outside [-2.0, 20.0]"
2009 water_level 376.004 → "value 376.004 outside [-2.0, 20.0]"
```

**Root cause — a datum/unit mismatch in the QC threshold, NOT bad data.**
`config.toml:225-231` sets a **global** `water_level` `range_check` of
`{value_min = -2.0, value_max = 20.0}` — bounds appropriate for a **relative stage
height in metres**. But BAFU/LINDAS delivers water level as **absolute metres above
sea level** (~261 m at 2091, ~376 m at 2009; the adapter maps `waterLevel →
water_level` verbatim, `hydro_scraper.py:48`, no datum conversion). So **every**
`water_level` observation is out of range and marked `qc_failed`. The mini's
`failed=2` = one `water_level` per station per tick × 2 stations.

A **single global** `water_level` range cannot work: absolute levels differ ~115 m
between these two stations, so no one `[min,max]` fits both. Two structural facts:
- `qc_rules.py:122` / `forecast_qc_rules.py:169` hardcode the same `[-2.0, 20.0]`
  default, so this is a systemic default, not a one-off.
- The QC checker supports per-observation `overrides` (`services/qc.py`
  `checker.check(..., overrides=...)`), but `ingest_observations.py:174` passes
  `overrides=[]` — per-station threshold overrides are **not wired from any store**.

**Impact:** benign for forecasting today (models use discharge, which passes), but
`water_level` is 100% rejected — which breaks the multi-parameter (discharge +
water_level) experiment and floods QC monitoring with false failures.

**Fix options (grill-me):**
- **(a) Per-station `water_level` range overrides** (recommended) — set bounds from
  each station's datum + plausible stage variation at onboarding, plumbed through
  the currently-empty `overrides` path. Handles the per-station absolute-datum
  reality directly.
- **(b) Adapter converts absolute m a.s.l. → relative stage** using a per-station
  datum, so the global `[-2,20]` applies. Cleaner semantically but needs a datum
  per station (ties to the rating-curve/datum work).
- **(c) Widen / drop the global `water_level` `value_max`** — rejected: masks real
  errors and still can't fit both stations' absolute levels.
- Minor observability note: the rejection **reason is already persisted** in
  `qc_flags` (that is how this was diagnosed) — the only gap is that the
  `ingest.qc_complete` **log event** omits it. A small `qc.rejected` debug event or
  including the flag summary would have surfaced this from logs alone. Optional.

## Goal

Characterise the QC failures precisely, decide the category (legit / threshold /
bug), and record the remediation (adjust threshold, fix rule, or accept as correct
rejection). This plan is **investigation-first**; any code fix is a follow-on.

## Investigation steps

1. **Locate + read the QC path.** `ingest_observations.py:178-187` counts
   pass/fail/suspect. Identify the QC checker it calls, the rule set, and where the
   per-observation failure reason is (or is not) logged. If the reason is not
   currently emitted, that is finding #1 — add a `qc.rejected` debug event with
   `station_id`, `parameter`, `rule`, `value`, `threshold` (a prerequisite for
   diagnosis, and generally useful).
2. **Get the concrete failures.** On the mini (or reproduced on the **local
   stack**, now up): dump the last N `ingest.qc_complete` events and, if available,
   the per-obs rejection reasons. Cross-check against the raw LINDAS values for
   those station/parameter/timestamps (the value that tripped the rule).
3. **Compare against thresholds.** Pull the QC rule config (ranges/spike/stale) for
   the failing station+parameter and compute whether the rejected value is
   genuinely out of physical range or just outside a conservative bound.
4. **Classify + decide.**
   - *Legit bad data* → confirm the reject is correct; document; consider whether
     these obs should still be visible (suspect vs failed) on the dashboard.
   - *Threshold too tight* → propose a per-station / per-network threshold
     adjustment (BAFU stations may have different plausible ranges).
   - *Rule bug / unit mismatch* → file the fix (and check the FI/QC-flag contract —
     obs QC flags vs the model-protocol return type).
5. **Assess lag-history impact.** Determine whether the 2 failing obs meaningfully
   degrade the `nwp_regression`-with-lags input window for 2009/2091 (i.e. whether
   this contributed to model failures beyond the NWP-off cause in Plan 100).

## Non-goals

- The NWP-off forecast blackout and fallback resilience — Plan 100.
- A wholesale QC-rule redesign — this is scoped to understanding + fixing the
  observed `failed=2`.

## Process

DRAFT → investigation (use the local stack + mini logs) → findings recorded here →
decision on remediation. If a code fix results (rule/threshold/logging), it goes
**hold-at-PR** with a version bump. If the reason-logging gap (#1) is confirmed,
that small observability add is likely the first PR.
