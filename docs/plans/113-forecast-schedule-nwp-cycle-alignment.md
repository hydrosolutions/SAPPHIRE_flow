# Plan 113 — Align the forecast schedule with NWP cycle delivery

**Status:** DRAFT
**Priority:** Low — not urgent (forecasts are healthy 3 of 4 cycles/day). Owner: eventually
do option B; may fold into a broader forecast-timing pass.
**Type:** Code/config (forecast scheduling + NWP cycle selection). hold-at-PR for any code;
the immediate mitigation is an env-var change.
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-13
**Surfaced by:** the 2026-07-13 mac-mini investigation ("forecasts produced but no weather
forecasts") — see [[project_nwp_off_on_restart_blackout]].
**Related:** Plan 090 (NWP incomplete-cycle selection + age-delay guard — the guard this
plan works *with*, not against); `docs/standards/orchestration.md` (scheduling).

> **The finding.** The operational forecast schedule sits exactly on the NWP cycle
> boundaries, so every forecast uses a 6-hour-stale ("fallback") NWP cycle, and the
> **00:00 cycle specifically drops to the obs-only `linear_regression_daily`** model
> because its stale cycle yields one fewer clean daily bucket than the model requires.
> ~1 in 4 operational forecasts is therefore silently *not* weather-driven.

---

## Root cause (fully diagnosed 2026-07-13, mac-mini live data)

- MeteoSwiss issues ICON-CH2-EPS cycles at **00/06/12/18 UTC**
  (`meteoswiss_nwp.py:_CYCLE_HOURS`), published **~5 h after** each cycle time
  (`_DEFAULT_EXPECTED_DELIVERY_OFFSET_HOURS = 5.0`).
- The forecast schedule is **`SCHEDULE_FORECAST_CYCLE = "0 */6 * * *"`** — 00/06/12/18 UTC,
  i.e. **coincident with the NWP cycle times**.
- At each forecast run the *current* NWP cycle is 0 h old = undelivered. Plan 090's
  age-delay gate (`nwp_cycle_min_age_minutes`) correctly skips it and walks back one 6 h
  slot → **every forecast uses a 6 h-stale cycle** → `nwp_cycle_source = fallback` on every
  row (never `primary`).
- The daily NWP models need **5 clean full-UTC-day future buckets** for every variable and
  member (`services/nwp_coverage.py`, `required_steps = forecast_horizon_steps = 5`). The
  6 h-stale cycle yields 5 at the 06/12/18 slots but only **4 at the 00:00 slot** (its stale
  cycle is the *previous day's* 18:00 run, whose 5-day window leaves the final UTC day
  partial) → 1 short → first-success loop falls through to `linear_regression_daily`
  (obs-only, `runoff_only`).

**Evidence (mac-mini `forecasts` table, 07-11 → 07-13, every day identical):**

| forecast cycle | winning model | `nwp_cycle_source` | NWP cycle used |
|---|---|---|---|
| **00:00** | `linear_regression_daily` | fallback | previous-day 18:00 |
| 06:00 | `nwp_regression` | fallback | 00:00 same-day |
| 12:00 | `nwp_regression` | fallback | 06:00 |
| 18:00 | `nwp_regression` | fallback | 12:00 |

*Open sub-question (not blocking a fix):* the precise reason **only** the 00:00 slot loses a
bucket while 12:00/18:00 (also on off-00:00 stale cycles) keep 5 is a daily-bucket-alignment
subtlety not fully derived. The empirical pattern is stable across 3 days; the fixes below do
not depend on resolving it.

## Not a bug

This is Plan 090's age-delay guard working *as designed*, colliding with a schedule that
sits on the cycle boundaries. Forecasts are otherwise healthy: `nwp_regression` (future
precip/temp features + past discharge lags — a genuine weather-driven runoff forecast)
produces 21-member, 5-day ensembles at 06/12/18; NWP weather data (`weather_forecasts`) is
current; BAFU collection is healthy.

## Options

| # | Fix | Change | Trade-off |
|---|---|---|---|
| A (mitigation) | Drop the broken 00:00 slot | `SCHEDULE_FORECAST_CYCLE=0 6,12,18 * * *` | Zero code, immediate. Loses the (obs-only) 00:00 run; 06:00 covers it. Still never `primary`. |
| **B (chosen direction)** | **Offset the schedule past NWP delivery** | e.g. `0 5,11,17,23 * * *` (≈ cycle + 5 h) | Each forecast runs right after its cycle delivers → fresher, possibly `primary` cycles; keeps 4×/day; removes the 00:00 dropout. Needs validation of real delivery timing + the daily-bucket alignment. |
| C | Shorten the model horizon | `required_steps` 5 → 4 | Shortens the forecast horizon; model-level change. Rejected. |

**Owner decision (2026-07-13): pursue B eventually, low priority.** Do not apply A/B now —
leave as a documented finding.

## B — what a real fix needs (when picked up)

1. **Confirm actual delivery timing** on the mini (not just the 5 h default): for a few
   cycles, measure `now - cycle_time` when the cycle first becomes fetchable, to choose the
   offset (cycle + ~5–6 h).
2. **Decide the schedule** — `0 5,11,17,23 * * *` (4×/day, offset) vs a once/daily
   06:00-style run on the 00:00-aligned cycle. For a *daily* 5-day-horizon model, 4×/day may
   be more churn than value; weigh against alerting freshness needs.
3. **Verify the daily-bucket alignment** actually yields 5 clean buckets at all chosen slots
   (reproduce on the dev stack first — see the memory's dev-stack note — before touching the
   mini).
4. **Where the value lives:** the schedule is the env var `SCHEDULE_FORECAST_CYCLE`
   (`cli/register_deployments.py`) — a per-deployment cron. Changing it is an env/redeploy
   op (auto-registered on `up -d`), OR change the default in `register_deployments.py`
   (code, hold-at-PR + bump). Consider whether the offset belongs in the code default (all
   deployments) or only the mac-mini env (Swiss-specific NWP timing).
5. **Interaction with Plan 090** — this does NOT touch the age-delay guard; it moves the
   *forecast* schedule so the guard's chosen cycle is well-covered. Keep them independent.

## Non-goals
- Changing Plan 090's cycle-selection / age-delay logic.
- Changing model requirements (`required_steps` / horizon).
- Anything about the BAFU collector (Plan 111) — unrelated.
