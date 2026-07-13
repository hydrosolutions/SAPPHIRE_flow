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

- MeteoSwiss issues ICON-CH2-EPS cycles at **00/06/12/18 UTC** (`meteoswiss_nwp.py:_CYCLE_HOURS`).
- The forecast schedule is **`SCHEDULE_FORECAST_CYCLE = "0 */6 * * *"`** — 00/06/12/18 UTC,
  i.e. **coincident with the NWP cycle times**.
- **CORRECTION (Codex independent review, 2026-07-13): the selection gate is
  `nwp_cycle_min_age_minutes` (`DeploymentConfig` default = **105 min**; the mac-mini does not
  override it), NOT the `expected_delivery_offset_hours = 5.0` value** — the latter is used only
  for NWP-grid *staleness monitoring* (`run_forecast_cycle.py` `_check_nwp_grid_staleness`), not
  cycle selection. `_snap_to_cycle` snaps to the current cycle, then the age gate rejects a cycle
  younger than 105 min and walks back one 6 h slot (`meteoswiss_nwp.py` ~493/499). A run at
  exactly HH:00 sees a 0-min-old cycle → fails the gate → **every forecast uses the (HH−6):00
  cycle = 6 h-stale = `fallback`, never `primary`.**
- The daily NWP models need `required_steps = forecast_horizon_steps = 5` clean future daily
  buckets per variable AND per member (`services/nwp_coverage.py:assess_future_coverage`;
  `_HORIZON = 5` in `models/nwp_regression.py`). **"Clean" = non-null after UTC-daily
  aggregation** (`operational_inputs.py` → `training_data.py group_by_dynamic(every="1d")`) — it
  is NOT a proven-full-24 h bucket (the plan's earlier "full UTC day" phrasing was imprecise).
  The 6 h-stale cycle yields 5 buckets at 06/12/18 but only **4 at 00:00** → 1 short → the
  first-success loop falls through to `linear_regression_daily` (obs-only, `runoff_only`). See
  the worked example below for exactly why only 00:00.

**Evidence (mac-mini `forecasts` table, 07-11 → 07-13, every day identical):**

| forecast cycle | winning model | `nwp_cycle_source` | NWP cycle used |
|---|---|---|---|
| **00:00** | `linear_regression_daily` | fallback | previous-day 18:00 |
| 06:00 | `nwp_regression` | fallback | 00:00 same-day |
| 12:00 | `nwp_regression` | fallback | 06:00 |
| 18:00 | `nwp_regression` | fallback | 12:00 |

**Why only 00:00 — RESOLVED (Codex independent investigation, 2026-07-13).** It is
UTC-left-labelled daily bucketing + the strict `valid_time > issue_time` filter
(`operational_inputs.py:_filter_and_cap_daily_records`), combined with the previous-day-18Z
fallback that occurs *only* at the 00Z forecast slot:

| forecast issue | stale cycle used | NWP window (+120 h) | daily bucket labels (UTC, left-edge) | kept by `valid_time > issue_time` |
|---|---|---|---|---|
| 07-13 **00:00** | 07-12 18:00 | → 07-17 18:00 | 12, 13, 14, 15, 16, 17 | 14–17 = **4** |
| 07-13 06:00 | 07-13 00:00 | → 07-18 00:00 | 13, 14, 15, 16, 17, 18 | 14–18 = **5** |

At 00Z the issue-day bucket label (`Jul 13 00:00`) equals `issue_time` exactly → dropped by the
strict `>`; and the 18Z fallback's +120 h endpoint only reaches D+4 18:00, so there is no D+5
label to compensate. At 06Z the same-day-00Z fallback's +120 h reaches the fifth calendar date,
so the boundary bucket is counted. So the "daily-bucket alignment" hypothesis is **confirmed and
made precise** — and it is NOT delivery-offset arithmetic.

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
| **B (chosen direction)** | **Offset the schedule past NWP delivery** | e.g. `0 5,11,17,23 * * *` | Each forecast runs after its cycle *should* be published → can select `primary`; keeps 4×/day. **CAVEAT (Codex): only sufficient with a *measured* delivery margin.** If the offset run still falls back (STAC hasn't published the current cycle yet), it inherits the SAME 4-bucket shortfall — it just moves the risk to a different slot. Must verify the chosen slots actually select `primary`, not merely offset the cron. |
| C | Shorten the model horizon | `required_steps` 5 → 4 | **Rejected.** `NwpRegression.predict` uses `horizon = len(future_times)` (`nwp_regression.py:216`), so relaxing only the guard reintroduces truncated NWP forecasts; changing the model horizon to 4 is a product/model change, not a scheduling fix. |
| D (missed by the plan; deeper) | Fix the daily-bucketing / coverage semantics | e.g. label daily aggregates by bucket-**end** before the `>` filter, or make coverage check *expected* forecast-step timestamps instead of a non-null row count | Removes the 00Z boundary artifact across **all four** slots at once — but changes model input timestamp semantics → needs validation / retraining. A model-semantics change, not a quick operational patch. |

**Owner decision (2026-07-13): pursue B eventually, low priority.** Do not apply A/B now —
leave as a documented finding. **Codex recommendation:** implement **B with a measured delivery
margin** first (operational, low-risk); treat **D** (bucketing refactor) as a separate
model-semantics change if the never-`primary` behaviour or the boundary artifact needs a real
fix. Do NOT relax the coverage guard (C) alone.

## B — what a real fix needs (when picked up)

1. **Measure real cycle *publication* timing** on the mini — for a few cycles, when does STAC
   first serve the cycle, and how does that relate to the **105 min** `nwp_cycle_min_age_minutes`
   gate? The offset must be late enough that the intended cycle is BOTH published on STAC AND
   older than 105 min, so `_snap_to_cycle` selects it as `primary` (not a walk-back). Picking an
   offset without this measurement is the trap — see the B caveat above.
2. **Prove `primary` selection, not just a shifted cron.** After choosing the offset, confirm
   the resulting forecasts carry `nwp_cycle_source = primary` and `available_steps = 5` at
   **every** slot — otherwise B just relocated the 4-bucket shortfall.
3. **Decide the schedule** — `0 5,11,17,23 * * *` (4×/day, offset) vs a once/daily run. For a
   *daily* 5-day-horizon model, 4×/day may be more churn than value; weigh against alerting
   freshness needs.
4. **Reproduce on the dev stack first** (see the memory's dev-stack note) before touching the
   mini — verify the coverage count at each candidate slot.
5. **Where the value lives:** the schedule is the env var `SCHEDULE_FORECAST_CYCLE`
   (`cli/register_deployments.py`) — a per-deployment cron. Changing it is an env/redeploy
   op (auto-registered on `up -d`), OR change the default in `register_deployments.py`
   (code, hold-at-PR + bump). Consider whether the offset belongs in the code default (all
   deployments) or only the mac-mini env (Swiss-specific NWP timing).
6. **Interaction with Plan 090** — this does NOT touch the age-delay guard; it moves the
   *forecast* schedule so the guard's chosen cycle is well-covered. Keep them independent.

## Non-goals
- Changing Plan 090's cycle-selection / age-delay logic.
- Changing model requirements (`required_steps` / horizon).
- Anything about the BAFU collector (Plan 111) — unrelated.
