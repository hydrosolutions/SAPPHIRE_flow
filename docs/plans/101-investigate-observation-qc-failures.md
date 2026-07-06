# Plan 101 — investigate observation-QC failures (`ingest.qc_complete failed=2`)

**Status**: READY (pending 2 owner-confirmables at end) — root cause FOUND; grill-me
DONE; raw-vs-relative → KEEP-RAW;
**SCOPE LOCKED by owner (2026-07-06): (A) per-station datum** as the *primary,
per-station* precision mechanism (the plan-review's option (B) global-widening is
NOT the primary fix, but its config-widening artefact **survives as the NULL-datum
backstop** — see below). Realization = **subtract the datum from the water_level
VALUE before `checker.check`** (NOT per-rule `StationQcOverride` overrides). This
dissolves the override/`time_step`-wiring blockers, but it does **NOT** dissolve the
spike-rule bug, the gross_outlier baseline-representation issue, or the NULL-datum
fallback — those remain real work (see "SCOPE LOCKED" at the end, which is the single
authoritative implementation surface; the DECIDED DIRECTION + Remediation-artefacts
sections above it are superseded where they describe the override seam). Remaining =
the multi-file datum storage/compute + spike fix + backstop + a datum statistic/window
residual. **Plan-review (WF1) DONE — 3 rounds total across two passes; round-3
residuals (3 blockers + 2 majors) folded as "FINAL CORRECTIONS" (incl. the critical
`(station_id, parameter)` datum-keying fix so discharge isn't shifted). Status →
READY** for implementation (hold-at-PR), pending two owner-confirmables noted at the
end (null-datum backstop = widen vs skip; datum statistic/window).
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
- `src/sapphire_flow/services/forecast_qc.py` + `src/sapphire_flow/config/forecast_qc_rules.py:151-177` (forecast-side QC — parallel `[-2.0, 20.0]` / `negative_value` defaults, applied at `flows/run_forecast_cycle.py:812`; symmetrically broken, gated follow-on)
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
between these two stations, so no one `[min,max]` fits both. Structural facts:
- The `[-2.0, 20.0]` bound lives in **THREE co-equal places** that must be fixed
  atomically (missing any one leaves a broken fallback path):
  1. `config/qc_rules.py:122` — `_default_swiss_qc_rules()`, the 10-min operational
     `water_level` `range_check` (`{value_min: -2.0, value_max: 20.0}`).
  2. `config/forecast_qc_rules.py:169` — `_default_swiss_forecast_qc_rules()`, the
     forecast-side counterpart (plus its `negative_value` floor `value_min=-2.0` at
     `:155`/`:162` and the hourly `range_check` at `:176`).
  3. `config.toml:231` — the `[[qc_rules.rules]]` `range_check` for `water_level`,
     used **in production** whenever `SAPPHIRE_CONFIG` is set (the mac-mini path).
  The `config/*.py` defaults are the fallback used ONLY when no `SAPPHIRE_CONFIG` is
  set (`ingest_observations.py:53-58` → `_default_swiss_qc_rules()`) — i.e. tests /
  local dev without a config file. So the fix targets both the deployed `config.toml`
  AND the two in-code defaults; this is a systemic default, not a one-off.
- The QC checker supports per-observation `overrides` (`services/qc.py`
  `checker.check(..., overrides=...)`), but `ingest_observations.py:174` passes
  `overrides=[]` — per-station threshold overrides are **not wired from any store**.
  This is the seam the datum fix plugs into (see DECIDED DIRECTION).
- **Other water_level rules are datum-sensitive too** — the range_check is not the
  only rule that breaks under absolute vs relative values. See the mandatory
  "per-rule audit" step below; the `spike` and `gross_outlier` rules are affected.

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
- Observability note (corrected): the rejection **reason is already persisted** in
  `qc_flags` (that is how this was diagnosed). But the log-stream gap is more severe
  than "the summary omits it": there is currently **no per-observation QC-rejection
  log event at all**. Per-obs failures are only counted silently into `counts["failed"]`
  in the loop at `ingest_observations.py:179-189`. The existing `ingest.qc_failed`
  event at `:351-357` fires **only when the QC task raises an exception** (task crash)
  — it is NOT a per-obs rejection signal. So a `qc.rejected` debug event would be the
  FIRST per-obs rejection log event, and it must be added **inside the `flags` loop at
  `:179-189`** (emitting `station_id`, `parameter`, `rule_id`, `value`, `threshold`
  for each `QcFlag` whose `status == QC_FAILED`), not folded into the summary. The
  `ingest.qc_failed` exception event stays untouched.

## Goal

Characterise the QC failures precisely, decide the category (legit / threshold /
bug), and record the remediation (adjust threshold, fix rule, or accept as correct
rejection). This plan is **investigation-first**; any code fix is a follow-on.

## DECIDED DIRECTION (grill-me 2026-07-06; datum-storage + raw-vs-relative fork RESOLVED at plan-review)

Root cause is settled (datum/unit mismatch, not bad data). The two design forks the
grill-me left open — (i) raw-preserved vs relative-only, and (ii) *where the datum
lives* — are now **resolved** so implementation is unblocked:

- **Keep the raw absolute value; subtract the datum in QC (NOT in the adapter).**
  The earlier "adapter converts absolute → relative stage" wording contradicted the
  "keep raw" recommendation: if the adapter converts and stores relative, the raw
  BAFU m a.s.l. value is **discarded** — which violates the *parse-don't-validate /
  preserve-raw-at-the-boundary* principle (CLAUDE.md) and loses the source value.
  RESOLUTION: the `HydroScraperAdapter` is unchanged — it stores the raw absolute
  value as received (`hydro_scraper.py:48` keeps mapping `waterLevel → water_level`
  verbatim; the adapter is a stateless HTTP boundary with no store access and MUST
  stay that way). The per-station datum is applied **inside the QC path** by
  evaluating each `water_level` observation against `value − datum` (relative stage)
  before `checker.check`, while the **stored** value stays raw absolute.
  **⚠ SUPERSEDED MECHANISM:** an earlier revision of this section proposed populating
  the `overrides=[]` argument (`ingest_observations.py:174`) with a per-station
  `range_check` override of bounds `[datum − 2.0, datum + 20.0]`. **That override-seam
  approach is abandoned.** It only shifts `range_check` thresholds and leaves the
  `spike`/`gross_outlier`/`rate_of_change` rules operating on absolute values (an
  override cannot re-scale a percentage-of-`prev` spike threshold). The final design
  is **datum-subtract-before-check** (shift the input value so **all** rules see
  relative stage), realized per **SCOPE LOCKED** at the end of this doc. `overrides`
  stays `[]`; `_merge_thresholds` (a **module-level** function in `services/qc.py:25`,
  NOT a method on `Stage1QualityChecker` — importing `merge_thresholds` from
  `services/_qc_helpers.py:9`) is **not involved** in the chosen design. See SCOPE
  LOCKED for the final realization (datum-subtract-before-check, `overrides=[]`
  unchanged). (If display ever needs relative stage, compute `value − datum` on read;
  do not persist a second field in v0.)
- **Datum storage = one nullable column on the `stations` table.** RESOLUTION (was
  the single blocking gap): add a nullable `water_level_datum_masl FLOAT` column to
  the `stations` table in **both** the SQLAlchemy `sa.Table` object
  (`db/metadata.py:67-130`, alongside the existing `altitude_masl` column at `:74` — so
  `select(stations)` returns it) **and** an Alembic migration for the deployed DB; add
  the field to
  `StationConfig` (`types/station.py:26-43`) as `water_level_datum_masl: float | None
  = None`; and add a `StationStore` accessor (`protocols/stores.py:484` — the
  Protocol already has `fetch_station`/`store_station` at `:485`/`:503`) so
  `_run_qc_task` can read it. **A dedicated `station_datums` table is rejected as
  over-engineering** for a single scalar per station. **NULL datum = no datum yet →
  fall back to the wide global default** (see backstop below), so a fresh station
  with no history QCs against a permissive absolute band rather than rejecting 100%.
  The datum is passed **into** the QC task as a `float | None` parameter by the flow
  caller (NOT by the adapter). **Sourcing — resolved at SCOPE LOCKED item 2:** the flow
  reuses the `eligible: list[StationConfig]` already in scope
  (`ingest_observations.py:272`) to build a **`(station_id, parameter)`-keyed** map
  `{(s.id, "water_level"): s.water_level_datum_masl for s in eligible}` with **zero extra
  DB round-trips** (no per-station `fetch_station` call), and passes
  `datum=datums.get((station_id, parameter))` into `_run_qc_task`. **The `(station_id,
  parameter)` key is mandatory, not cosmetic:** the adapter (`hydro_scraper.py:36-40`)
  fetches discharge, waterLevel AND waterTemperature for every RIVER station, so
  `station_params` (`ingest_observations.py:329-331`) contains both `(2009, "discharge")`
  and `(2009, "water_level")`. A `station_id`-only key would return the ~261/376 m
  water_level datum for the `(2009, "discharge")` QC call and shift every discharge obs by
  −datum → nonsense negatives → it would **corrupt discharge QC, the one parameter
  currently passing.** Keying by `(station_id, parameter)` returns `None` (no shift) for
  discharge and the datum only for water_level. This keeps the task pure and avoids the
  N-round-trip / time-of-check-time-of-use trap the earlier "fetch per station inside the
  task" wording implied.
- **Datum = data-driven from history, computed at onboarding.** Compute each
  station's reference datum from its observed `water_level` history (a robust
  low-water reference — see sub-decision 2), rather than `altitude_masl` (unreliable
  as the water datum) or manual config (doesn't scale to ~1000 stations). The
  onboarding flow computes it and writes `water_level_datum_masl` via
  `StationStore.update_station` for an **already-onboarded** station (2009/2091 are live
  rows — `store_station` uses `sa.insert`, `station_store.py:94`, and would PK-conflict;
  `update_station` at `:126` is the correct path). Only a brand-new station's datum rides
  the initial `store_station` INSERT. A station with insufficient history leaves the column
  NULL until enough accrues, then a recompute pass populates it (sub-decision 3).
- **Global default becomes a physical backstop for the NULL-datum path.** When a
  station's datum is NULL, **no subtraction happens** (the checker sees raw absolute
  ~261–376 m). Against the current `[-2.0, 20.0]` config bound that is still 100%
  rejection — the original bug. So the global `water_level` `range_check` MUST be
  widened to a **safe physical absolute band for the network** (e.g. `[0.0, 4500.0]`
  for Swiss stations — no BAFU gauge sits above the highest Alpine outlet) in **all**
  config locations (10-min + daily; see Findings + SCOPE LOCKED artefact 3). This
  widening is the **NULL-datum fallback guard** — it is REQUIRED regardless of the
  datum-subtract mechanism (it is NOT the abandoned per-station-override approach). A
  station WITH a datum is checked precisely against relative bounds; a station WITHOUT
  one QCs loosely (gross faults only) against this absolute backstop until its datum is
  computed. **⚠ trade-off (not a regression):** a newly-onboarded / datum-less station
  passes anything inside `[0, 4500]` — coarse, but strictly better than 100% rejection
  and physically bounded.
- **Re-QC existing after the fix.** Once the datum + datum-subtract wiring is in place,
  re-evaluate the 622 existing `qc_failed` water_level rows (see sub-decision 4).

### Mandatory per-rule audit — ALL five water_level QC rules (not just range_check)

The fix touches `range_check` directly, but three other `water_level` rules
(`config/qc_rules.py:116-151`) have threshold semantics that interact with the
absolute-vs-relative representation. Under the **datum-subtract-before-check** design
(SCOPE LOCKED), the *stored* value stays raw absolute, but the *value the checker sees*
is relative stage (`value − datum`, ~0–20 m). Each rule must be audited against the
**relative** value the checker receives, and the outcome recorded before READY:

1. **`range_check`** — FIXED by datum-subtract (the existing `[-2.0, 20.0]` relative
   bound now applies to the datum-shifted value). The global `[-2.0, 20.0]` config
   bound is **not edited** for the datum path; but it is **widened as the NULL-datum
   backstop** (see below and SCOPE LOCKED artefact — the widening survives as the
   fallback guard, not as the per-station bound).
2. **`rate_of_change`** (`services/qc.py`, `max_rate=0.5`) — operates on the
   **delta** between consecutive values; a delta is datum-invariant (`Δabsolute =
   Δrelative`). **Correct as-is under keep-raw.** No change.
3. **`frozen_sensor`** (`tolerance=0.001`) — operates on consecutive-value equality
   within a tolerance; also delta-based / datum-invariant. **Correct as-is.** No change.
4. **`spike`** (`services/qc.py:136-164`, `tolerance=0.1`) — **PRE-EXISTING BUG that
   datum-subtract does NOT dissolve — it only moves it.** It flags when
   `abs(obs.value − prev.value) > tolerance * abs(prev.value)` (`:148,:152`). With
   absolute values (~261 m) the threshold is `0.1 × 261 ≈ 26 m`, so it silently passes
   every genuine spike — spike detection is effectively *disabled* at absolute scale.
   **Under datum-subtract the value becomes relative (~0–20 m), so `0.1 × prev` is now
   `0.1 × ~5 m ≈ 0.5 m` for mid-stage — but the percentage-of-`prev` form is now
   *hyper-sensitive at low stage*: as `prev → 0` (a near-dry gauge), the threshold
   `0.1 × |prev| → 0`, so any tiny fluctuation trips a spike flag.** The
   `if ref == 0.0: return None` guard (`:149`) only catches the **exact-zero** case; it
   does NOT protect the near-zero band. So datum-subtract merely swaps a disabled spike
   detector (absolute) for a hyper-sensitive one (relative, near zero). RESOLUTION
   (unchanged from the earlier audit — **still required**): for stage-like data the
   spike threshold must be an **absolute delta** (`max_delta` in metres), not a
   percentage of `prev.value`. Concretely: make `_apply_spike` (`qc.py:136-164`, which
   today reads `thresholds["tolerance"]` unconditionally at `:147`) **dispatch on key
   presence** — `if "max_delta" in thresholds:` use the absolute-delta check, else the
   existing percentage form; and **replace** the `water_level` spike entry's
   `{"tolerance": ...}` with `{"max_delta": <N>}` in **all FOUR spike locations**
   (mirroring the range_check atomicity discipline — the `config.toml` entries are the
   production path when `SAPPHIRE_CONFIG` is set, so omitting them leaves the deployed
   spike rule on the old percentage form and the mac-mini fix is a no-op): (1)
   `config/qc_rules.py:138-144` (10-min, `tolerance=0.1`), (2) `config/qc_rules.py:174-180`
   (daily, `tolerance=0.3`), (3) `config.toml:248-252` (10-min, `tolerance=0.1`), (4)
   `config.toml:304-308` (daily, `tolerance=0.5`). Discharge's `{"tolerance": ...}`
   (`config/qc_rules.py:67-72` + `config.toml:276-280`) is left untouched. The 10-min and
   daily `max_delta` values differ (see sub-decision 2 — a 1 m/10-min cap does not
   translate to 1 m/day). This is the only rule whose *code* changes, not just config, and
   it must ship **alongside** the datum-subtract change (artefact 4a) — see SCOPE LOCKED
   artefact 4.
5. **`gross_outlier`** (`services/qc.py:167-192`, `k_sigma=5.0`) — compares
   `abs(obs.value − baseline.rolling_mean) > k_sigma × baseline.rolling_std` against
   `ClimBaseline` rows (`qc.py:181`). **Under datum-subtract the checker receives
   relative-stage values (~0–20 m)**, so the baseline it is compared against must ALSO
   be in **relative stage** (`raw_value − datum`), NOT raw absolute m a.s.l. — otherwise
   a relative obs (~5 m) vs an absolute baseline mean (~261 m) yields a ~256 m deviation
   and every obs is flagged a gross outlier (a new false-failure replacing the old one).
   RESOLUTION: audit whether any `water_level` `ClimBaseline` rows exist
   (`baseline_store.fetch_baselines`, `ingest_observations.py:168`). If none exist yet
   (likely in v0, since water_level was 100% rejected), **document that explicitly** so
   the backfill does not leave stale baselines. If any exist, they must be **deleted and
   recomputed from the datum-shifted (relative) series** — equivalently, shift each
   `rolling_mean` by `− datum` (`rolling_std` is datum-invariant) — as part of the
   backfill (sub-decision 4). The representation switch (absolute→relative) makes this
   mandatory, not optional.

### Forecast-side QC — parallel fix, gated on first water_level forecast run

`forecast_qc_rules.py` is symmetrically broken: the same absolute-scale defaults at
`:169` (`range_check [-2.0, 20.0]`) and the `negative_value` floor `value_min=-2.0` at
`:155`/`:162`, applied in `run_forecast_cycle.py:812` via `ForecastOutputQualityChecker`.
Because we keep water_level **absolute** end-to-end, forecast QC must use the same
absolute bounds as obs QC. RESOLUTION: update `forecast_qc_rules.py` in parallel —
widen `range_check` to the physical backstop and set `negative_value` `value_min` to a
physical floor (`0.0` is the lowest plausible absolute water level? — no; use the same
network-wide low bound, e.g. `0.0` only if 0 m a.s.l. is impossible for the network,
else the backstop min). **v0 gate:** water_level forecasting is not yet enabled, so
gate this step behind a "before the first water_level forecast run" checkpoint rather
than shipping it in the obs-QC PR; but it MUST be listed as a tracked follow-on so the
two QC configs do not diverge.

### ⚠ Residual sub-decisions (owner confirm before READY — narrowed, no longer blocking)

1. ~~Raw-data preservation vs relative-only~~ **RESOLVED above: keep raw absolute in
   storage, subtract datum on the value fed to `checker.check` (datum-subtract-before-
   check).** No adapter change, no second stored field, no `overrides` seam.
2. **Datum definition (statistic + window) AND the spike `max_delta`.** Exactly which
   statistic (min? p1? a robust low-water reference?) over which history window, and the
   margin. Ties to the rating-curve / gauge-zero work (Nepal v1) — a datum here should be
   compatible with, not contradict, a future published gauge-zero. **Record BOTH spike
   `max_delta` values (metres, artefact 4a) as a sub-decision here too** — `N_10min`
   (provisional `1.0 m`, the max stage change between consecutive 10-min readings) AND a
   distinct larger `N_daily` (the max change between DAILY readings; a 1 m/10-min cap does
   NOT translate to 1 m/day). Both are chosen alongside the datum recipe. (Design detail,
   not a blocker: the storage + wiring + dispatch logic are fixed above; only the numeric
   recipes remain.)
3. **Datum stability / recompute policy.** Compute once at onboarding vs periodically
   (a regime shift or a re-levelled gauge changes the true datum). Recommend
   compute-at-onboarding + a documented recompute path (a re-run that overwrites
   `water_level_datum_masl` via `update_station` — `sa.update`, not `store_station`'s
   `sa.insert`, which would PK-conflict on the existing row), not silent drift.
4. **Backfill mechanics + audit (actor + version string SPECIFIED).** Re-QC-ing the
   622 rows:
   - **Actor:** a one-shot admin script (`uv run python3 << 'EOF'` per CLAUDE.md),
     NOT a new Prefect flow. It reads each station's `water_level_datum_masl`, builds
     datum-shifted (`value − datum`) shadow copies of the stored raw obs via
     `dataclasses.replace`, re-runs `Stage1QualityChecker.check` over those relative
     values, and writes the new status keyed by the (unchanged) `ObservationId`.
   - **Bulk-update API gap:** `ObservationStore.update_qc`
     (`protocols/stores.py:95-102`, impl `store/observation_store.py:119-134`) updates
     **one** `ObservationId` per call and hardcodes nothing — but the flow passes
     `qc_rule_version="1.0"` at `ingest_observations.py:183`. Re-QC-ing 622 rows
     one-by-one is acceptable at this size (single transaction in the script; 622 rows
     is not a scale concern), so **no `bulk_update_qc` method is required** — the script
     wraps the loop in one transaction. (If this were 10^5+ rows a bulk helper would be
     warranted; note the trade-off but do not add API surface for 622 rows.)
   - **Version string:** write `qc_rule_version="1.1-datum-reqc"` (a concrete literal)
     for the re-QC'd rows so they are distinguishable from the original `"1.0"` pass.
     `qc_rule_version` is a free-form `str | None` today (no registry); this documents
     the convention rather than inventing a silent one.
   - If any `water_level` `ClimBaseline` rows exist (see audit rule 5), **delete and
     recompute them from the datum-shifted (relative) series** in the same script
     before the re-QC pass — the checker now sees relative values, so absolute-scale
     baselines would flag every obs as a gross outlier.
5. **Plan 102 ripple.** Plan 102's decided unit label is **"m a.s.l."** Because we
   **keep raw absolute** (not relative stage), the displayed value stays absolute m
   a.s.l., so **Plan 102's `PARAM_UNITS` label "m a.s.l." remains correct** — no change
   needed (this is simpler than the earlier relative-stage assumption). Flag retained
   only so a future switch to displaying relative stage revisits it.

**Observability (decided, no fork):** per-observation rejections have **no log event
today** — only a count in `ingest.qc_complete`. The existing `ingest.qc_failed`
(`ingest_observations.py:351-357`) is a **task-exception** event, not a per-obs signal.
Add a `qc.rejected` debug event **inside the `flags` loop at `:179-189`**, emitting
`station_id`, `parameter`, `rule_id`, `value`, `threshold` for each `QcFlag` with
`status == QC_FAILED`. This is a code add inside `_run_qc_task`, not a summary tweak.

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

## Remediation artefacts (settled — the implementation surface)

> **⚠ AUTHORITATIVE SURFACE = "SCOPE LOCKED" (end of doc).** This list is kept in
> sync with it and reflects the **datum-subtract-before-check** design. An earlier
> revision of this list prescribed an `overrides=`-based `range_check` shift (old
> artefact 2), config-threshold *replacement* (old artefact 3), and treated the spike
> fix / forecast-QC fix inconsistently. Those have been reconciled below. There is
> exactly ONE mechanism: shift the water_level value by the datum before `checker.check`
> (via `dataclasses.replace`), leave `overrides=[]`, and widen the global bound only as
> the NULL-datum backstop.

The DECIDED DIRECTION resolves to this concrete, ordered change set (each grounded
in a real file:line):

1. **Datum storage** (unblocks everything else). **Two separate files must change for
   the column to exist AND be queryable — the migration alone is not enough:**
   - **(1a) SQLAlchemy `Table` object** — add
     `sa.Column("water_level_datum_masl", sa.Float, nullable=True)` to the
     `stations = sa.Table(...)` definition in `db/metadata.py` (the block spans `:67-130`;
     place it alongside `altitude_masl` at `:74`). **Without this, `sa.select(stations)`
     omits the column from every result row regardless of the migration, and
     `_row_to_station` (`station_store.py:258`) raises `KeyError` on
     `row["water_level_datum_masl"]`.** This is a distinct change from the migration.
   - **(1b) Alembic migration** — `op.add_column("stations", sa.Column(
     "water_level_datum_masl", sa.Float(), nullable=True))` (a separate migration file
     under the Alembic versions dir; adds the physical column to the deployed DB).
   - `StationConfig.water_level_datum_masl: float | None = None`
     (`types/station.py:26-43`).
   - **Persist + round-trip the column in ALL three `PgStationStore` sites** (a review
     found `update_station` alone is insufficient):
     - `update_station` (`station_store.py:126`) — add
       `water_level_datum_masl=station.water_level_datum_masl` to the `.values(...)`.
     - `store_station` (`station_store.py:94`) — add the same key to the `sa.insert`
       `.values(...)`, else newly-inserted stations are always NULL.
     - `_row_to_station` (`station_store.py:258`) — read
       `row["water_level_datum_masl"]` and pass it to `StationConfig`, else
       `fetch_station` never surfaces the datum even after it is written.
   - `StationStore` accessor to read the datum (`protocols/stores.py:484`; the
     Protocol already exposes `fetch_station`/`store_station`). `fetch_station`
     returning the enriched `StationConfig` suffices — no dedicated method needed.
2. **QC-side datum application (datum-subtract-before-check):** in `_run_qc_task`
   (`ingest_observations.py:141-191`) receive the station's datum as a **float
   parameter** (`datum: float | None`, passed by the caller — see wiring below), then
   build datum-shifted **shadow copies** of **every** water_level obs in `all_obs`
   (the full context window, not only `raw_obs`) via
   `replace(obs, value=obs.value - datum)` — `Observation` is
   `frozen=True, slots=True` (`types/observation.py:26`) so in-place mutation raises
   `FrozenInstanceError`; `replace` is the only path, and it preserves `.id`. **⚠ IMPORT:
   `ingest_observations.py:4` currently imports only `dataclass` from `dataclasses`; add
   `replace` → `from dataclasses import dataclass, replace`, else the call raises
   `NameError`.** **⚠ BLOCKER — None-guard is mandatory:** `fetch_observations` in
   `_run_qc_task` (`:153-158`) applies **no `qc_status` filter**, so `all_obs` can include
   `MISSING` observations whose `value is None` (`types/observation.py:32`,
   `value: float | None`). `obs.value - datum` on such a row raises `TypeError: unsupported
   operand type(s) for -: 'NoneType' and 'float'` **before** `__post_init__` ever runs.
   The shift MUST therefore be guarded per-obs:
   `replace(obs, value=obs.value - datum) if obs.value is not None else obs` — `MISSING`
   rows pass through **unchanged**, which is safe because `_apply_range_check`
   (`qc.py:55-56`) and `_apply_spike` (`qc.py:145-146`) both short-circuit `if obs.value
   is None: return None`. Pass the shadow list to `checker.check(overrides=[])`. The
   returned `dict[ObservationId, list[QcFlag]]` is keyed by the real (unchanged) IDs, so
   the `raw_ids` filter and `obs_store.update_qc` at `:179-189` work unchanged; the stored
   DB value is never touched. **All context obs must be shifted too** — a mixed
   list (raw shifted, context absolute) would make `rate_of_change`/`spike` compute a
   ~−datum delta (`qc.py:80,:152`) and flag every obs. **NULL datum → skip the shift**
   (raw absolute values go to the checker, caught by the widened backstop, artefact 3).
   **⚠ Distinct forward version string — resolve ONCE, outside the flags loop.** The
   version is constant for a whole `_run_qc_task` invocation (a station/parameter pair is
   either datum-shifted or not — never per-flag). So resolve it **once at the top of
   `_run_qc_task`, before the `for obs_id, obs_flags in flags.items()` loop
   (`:179-189`)**: `version = "1.1-datum" if datum is not None else "1.0"`, then pass
   `qc_rule_version=version` to **every** `update_qc` call inside the loop (`:183`,
   currently the hardcoded literal `"1.0"`). Do NOT branch per-flag inside the loop. This
   makes live datum-shifted forward passes auditably distinct from both the broken pre-fix
   `"1.0"` pass and the one-shot backfill `"1.1-datum-reqc"` (artefact 7); NULL-datum (no
   shift) rows keep `"1.0"`.
   **Wiring:** `ingest_observations_flow` already takes `station_store`
   (`ingest_observations.py:212`). Build the datum map **immediately after `eligible` is
   built** (`:272`) with **zero extra DB round-trips** by reusing the `StationConfig`
   objects already in scope, **keyed by `(station_id, parameter)`** so the water_level
   datum is never mis-applied to a discharge QC run:
   `datums = {(s.id, "water_level"): s.water_level_datum_masl for s in eligible}` (once
   artefact 1 adds the field). This avoids N per-station `fetch_station` calls and a
   time-of-check/time-of-use window, and keeps the datum consistent with the station
   snapshot used for the rest of the flow. Then in the QC loop (`:336-349`) — which
   iterates `station_params` (`:329-331`) containing both `(station_id, "discharge")` and
   `(station_id, "water_level")` for every RIVER station (the adapter fetches all three
   parameters, `hydro_scraper.py:36-40`) — pass `datum=datums.get((station_id, parameter))`
   into each `_run_qc_task` call. This returns `None` (no shift) for the discharge call and
   the datum only for water_level; a `station_id`-only key would shift discharge obs by
   −datum and corrupt the one parameter currently passing. The task stays pure/testable
   (takes a float, not the store).
3. **Global backstop widening = the NULL-datum guard (survives; NOT the abandoned
   override).** Widen the `water_level` `range_check` to a physical absolute band
   (e.g. `[0.0, 4500.0]`) in ALL locations atomically — 10-min AND daily:
   `config/qc_rules.py:122` (10-min) + `:158` (daily), and the deployed
   `config.toml:231` (+ the daily `config.toml:292`). This is required because a
   NULL-datum station gets NO subtraction (artefact 2) and would otherwise 100%-fail
   against `[-2.0, 20.0]`. (Daily deployed `range_check` thresholds are at
   `config.toml:294`, block `:290-294`.) Forecast-side counterparts are artefact 8.
4. **Spike-rule fix** for `water_level`, split into a code change (ships with artefact 2)
   and a baseline recompute (runs in the backfill, artefact 7):
   - **4a. Spike-rule CODE fix — ships in the SAME PR as artefact 2 (hard dependency,
     NOT deferrable to backfill).** Once datum-subtract makes the checker see relative
     stage, the percentage-of-`|prev|` form becomes hyper-sensitive as `prev → 0`
     (per-rule audit rule 4), so the fix must land with the datum change or the
     datum-subtract PR ships a live spike bug. Change the `water_level` spike rule to an
     **absolute-delta** threshold. **Threshold schema (specified to remove the
     implementer fork):** `_apply_spike` (`qc.py:136-164`) today reads
     `thresholds["tolerance"]` **unconditionally** (`:147`). Update it to **dispatch on
     key presence**: `if "max_delta" in thresholds:` use the absolute-delta form
     (`abs(obs.value - prev.value) > thresholds["max_delta"]` and the symmetric
     next-value check), **else** fall through to the existing percentage-of-`|prev|` form
     (`:147-153`, `ref == 0.0` guard retained). **Replace** the `water_level` spike
     entry's `{"tolerance": ...}` with `{"max_delta": <N>}` in **ALL FOUR spike-rule
     locations, atomically, in this same PR** (do NOT keep both keys — leaving `tolerance`
     present would leave the branch ambiguous; and the `config.toml` entries are the
     PRODUCTION path whenever `SAPPHIRE_CONFIG` is set on the mac-mini/Docker deployment,
     `docker-compose.yml:81,131`, so `_load_qc_rules()` loads `config.toml`, NOT the
     Python defaults — omitting them makes the deployed spike fix a no-op that falls through
     to the old percentage-of-`|prev|` form):
       1. `config/qc_rules.py:138-144` — 10-min (`{"tolerance": 0.1}` → `{"max_delta":
          <N_10min>}`).
       2. `config/qc_rules.py:174-180` — daily (`{"tolerance": 0.3}` → `{"max_delta":
          <N_daily>}`). **This entry DOES exist (verified) — apply the swap unconditionally,
          no hedge.**
       3. `config.toml:248-252` — 10-min production (`tolerance = 0.1` → `max_delta =
          <N_10min>`).
       4. `config.toml:304-308` — daily production (`tolerance = 0.5` → `max_delta =
          <N_daily>`).
     **Discharge spike rules are unchanged** — they keep their own `{"tolerance": ...}`
     entry (`config/qc_rules.py:67-72` + `config.toml:276-280`), so parameter-scoping is
     already in place and the key-presence dispatch routes each parameter correctly.
     **`max_delta` values = a sub-decision.** `N_10min` is the max plausible absolute stage
     change between consecutive 10-min readings (provisional `1.0 m`); `N_daily` is the max
     plausible change between DAILY readings — a **different, larger** number (a 1 m/10-min
     cap does not translate to 1 m/day). Record BOTH alongside the datum statistic
     (sub-decision 2).
   - **4b. Baseline recompute — runs in the backfill (artefact 7), NOT in the datum PR.**
     Delete + recompute any water_level `ClimBaseline` rows from the **relative**
     (`value − datum`) series (`rolling_mean − datum`; `rolling_std` is datum-invariant),
     before the re-QC pass, so `gross_outlier` compares relative obs against relative
     baselines (per-rule audit rule 5).
5. **`qc.rejected` debug log event** inside the `flags` loop at
   `ingest_observations.py:179-189`.
6. **Onboarding datum computation** writing `water_level_datum_masl` — for the
   already-onboarded live stations 2009/2091 via **`update_station`** (`station_store.py:126`;
   `store_station` at `:94` uses `sa.insert` and would PK-conflict on existing rows). The
   script `fetch_station`s the existing config, `dataclasses.replace(config,
   water_level_datum_masl=...)`, then `update_station(updated)`. Datum is computed from the
   station's **`observations` water_level history in the DB** (statistic per sub-decision
   2), decoupled from CAMELS onboarding so it covers BAFU LINDAS live stations (2009/2091).
   Run at onboarding if history exists, else via the recompute task (sub-decision 3). Only
   a genuinely new station's datum uses the `store_station` INSERT path (item 1).
7. **Backfill script** (one-shot, per sub-decision 4) re-QC-ing the 622 rows on
   datum-shifted shadow copies with `qc_rule_version="1.1-datum-reqc"`, deleting +
   recomputing any water_level baselines from the **relative** series first.
8. **Forecast-QC parallel fix** (`forecast_qc_rules.py`) — because forecast water_level
   values are also absolute and the forecast path has no datum plumbing yet, widen its
   `range_check`/`negative_value` to the same physical backstop. Gated behind the first
   water_level forecast run, tracked as a follow-on so the two QC configs do not diverge.

**PR grouping:** the datum-subtract PR bundles artefacts 1, 2, **4a (spike CODE fix —
hard dependency of artefact 2)**, 3, and 5; the backfill script (artefact 7) carries
**4b (baseline recompute)** and runs after datum values are written. Do NOT defer 4a to
the backfill — it must ship with artefact 2. Step 6 lands in the onboarding/recompute
path; step 8 is a gated follow-on.

## Non-goals

- The NWP-off forecast blackout and fallback resilience — Plan 100.
- A wholesale QC-rule redesign — this is scoped to understanding + fixing the
  observed `failed=2`.

## Process

DRAFT → investigation (DONE — findings + root cause above) → design (DONE — DECIDED
DIRECTION + Remediation artefacts settled the datum-storage and raw-vs-relative forks)
→ plan-review sign-off → READY. The code fix is multi-artefact (see Remediation
artefacts); it goes **hold-at-PR** with a version bump. The `qc.rejected` observability
add (artefact 5) is the smallest slice and a reasonable first PR; the datum column +
QC-override wiring (artefacts 1–4, 7) is the core fix. Onboarding datum computation
(6) and forecast-QC (8) follow.

**Trade-offs noted (not regressions):** (a) the widened global backstop
(`[0, 4500]`) is deliberately coarse — it only catches gross sensor faults; the tight
per-station QC comes from the datum-subtract, and a NULL-datum station QCs **loosely
against the absolute backstop** (which is why the widening is REQUIRED, not dropped —
without it a NULL-datum station 100%-fails) until its datum is computed. A datum-less
station therefore accepts any reading inside `[0, 4500]` m a.s.l. — coarse but
physically bounded and strictly better than 100% rejection. (b) Re-QC-ing 622 rows
one-by-one via the existing single-row `update_qc` is accepted over adding a
`bulk_update_qc` API, given the small row count; revisit if the backfill grows by
orders of magnitude.

## Plan-review verdict (round 2, 2026-07-06) — SCOPE DECISION REQUIRED

Plan-review resolved the raw-vs-relative fork (→ **keep-raw**, datum subtracted at
QC time via `overrides`, datum in a `stations.water_level_datum_masl` column) but did
**not converge**: 6 blockers + 7 majors, all real and code-grounded. They cluster
into one conclusion — **the precise per-station datum fix is a genuine multi-file
feature, not a threshold tweak:**

1. **The broken bound is duplicated far beyond "three places."** Besides the 10-min
   `range_check [-2,20]`, the **daily** `range_check [-5,30]` (`qc_rules.py:153`,
   `config.toml:292`) is equally broken for absolute values, and the **spike** rules
   (10-min + daily, percentage-form) mis-scale at ~261–376 m — across
   `qc_rules.py`, `config.toml`, **and** `forecast_qc_rules.py`. Any atomic fix must
   hit **all** of them or a fallback path silently rejects again.
2. **`StationQcOverride` matches `time_step` EXACTLY** (`_qc_helpers.py:22`). A single
   datum override won't apply to both the 600 s and 86 400 s rules — need one override
   per time_step, or make `time_step` a nullable wildcard (cleaner). Unspecified =
   silent no-op.
3. **Signature + schema cascade.** `_run_qc_task` has no `station_store` param;
   adding the datum needs it threaded through the call sites; adding
   `water_level_datum_masl` to the frozen `StationConfig` cascades; and
   `PgStationStore.update_station` doesn't persist that column today.
4. **Datum-computation path doesn't fit onboarding.** "Compute the datum at
   onboarding" assumes CAMELS-CH onboarding, but 2009/2091 are **BAFU LINDAS live**
   stations that don't come through that path — so *where/when* the data-driven datum
   is computed for the actual problem stations is unresolved.

### The scope fork (owner decides)

- **(A) Full precise per-station datum QC** — the keep-raw design above, done right:
  schema column + migration, `StationConfig`/store/`update_station` updates,
  `_run_qc_task` signature, per-`time_step` overrides (or the wildcard refactor), fix
  **all** duplicated bound/spike locations, a datum-computation path that covers BAFU
  stations, and the re-QC backfill. Precise QC, but a real multi-file feature.
- **(B) v0 global-widening backstop (pragmatic) + defer precise QC** — widen the
  `water_level` `range_check` (and fix the spike scaling) across all config locations
  to physically plausible **absolute** bounds for the deployment (e.g. Switzerland
  `[0, 4500]` m a.s.l.), unblocking the feed + dashboard now and still catching gross
  errors (negative, absurd). Loses per-station precision (a 261 m gauge reading 376 m
  would pass). Defer (A) to when multi-parameter validation / v1 rating-curve/gauge-
  zero work needs it. **Small, all-config, low-risk.**

**Recommendation:** given water_level QC is **secondary in v0** (models forecast
discharge, which is clean), do **(B) now** to stop the false-failure flood + make the
dashboard usable, and **reopen (A)** under the rating-curve/gauge-zero track when
per-station stage precision actually matters. If the owner wants precision now, take
(A) and fold all 13 findings first. Either way: also add the `qc.rejected` per-obs log
event (observability gap, orthogonal to the scope choice).

## SCOPE LOCKED (owner 2026-07-06): (A) per-station datum — realized by datum-subtract-BEFORE-QC

Owner chose **(A) precise per-station datum** as the primary mechanism ("the widening
was a misunderstanding — we use the per-station datum, that's enough"). Option (B)
global-widening is **not the primary fix**, but its config-widening survives as the
**NULL-datum backstop** (artefact 3 below) — the two are not mutually exclusive.
Realization is refined from the plan-review's override-based framing to the simpler
subtract-before-check form:

**Subtract the datum from the water_level VALUE before `checker.check`, NOT via
per-rule `StationQcOverride`.** For each `water_level` observation, evaluate QC on
`value − station_datum` (a relative-stage **shadow copy** built with
`dataclasses.replace` — `Observation` is `frozen=True, slots=True`,
`types/observation.py:26`, so in-place mutation is impossible; `replace` preserves
`.id`, so the returned flags dict keys still map to the DB rows). The **stored** value
stays raw absolute (keep-raw). The shift is applied to **every** water_level obs in
`all_obs` — raw AND context — so all rules see a consistent relative frame, **except
`MISSING` obs whose `value is None`, which pass through unchanged** (guard:
`replace(obs, value=obs.value - datum) if obs.value is not None else obs`; subtracting on
`None` raises `TypeError` — see artefact 2 / item 2 below). This makes ALL water_level
rules operate on relative stage.

**What subtract-before-check DOES dissolve — and what it does NOT:**
- ❌→✅ *`StationQcOverride.time_step` exact-match / per-time_step overrides* — **not
  used at all**; `overrides` stays `[]`. No domain-type or `_qc_helpers` refactor.
- ❌→✅ *daily `range_check [-5,30]`, 10-min `range_check [-2,20]`* — apply **as-is to
  the relative value**; no per-station threshold edit for the datum path.
- ✅ *rate_of_change* — Δ is datum-invariant; unchanged (given all-obs shift, above).
- ⚠ **NOT dissolved — spike rule.** The percentage-of-`|prev|` form (`qc.py:148,:152`)
  goes from *disabled* at absolute scale to *hyper-sensitive* at low relative stage
  (as `prev → 0`, threshold `0.1·|prev| → 0`; the `ref == 0.0` guard only catches exact
  zero). The absolute-delta spike fix (per-rule audit rule 4) is **still required** and
  ships with this change. **Do NOT claim the spike bug is "correct as-is."**
- ⚠ **NOT dissolved — gross_outlier baselines.** The checker now sees relative values,
  so any `water_level` `ClimBaseline` must be in the **relative** frame (`value − datum`);
  absolute-scale baselines would flag every obs. Delete + recompute relative (artefact 4).
- ⚠ **NOT dissolved — NULL-datum fallback.** A NULL-datum station gets no shift, so its
  raw absolute values hit the config `range_check`; that bound MUST be widened to a
  physical absolute backstop (artefact 3) or the station 100%-fails again.
- ⚠ **NOT dissolved — forecast-side QC.** `forecast_qc_rules.py` has no datum plumbing,
  so its water_level values stay absolute; it needs the same backstop widening
  (artefact 8, gated on the first water_level forecast run).

**Remaining real work (accepted — this is the multi-file part the owner signed up for):**
1. **Datum storage.** Add nullable `stations.water_level_datum_masl` in **two files**:
   (i) the SQLAlchemy `stations = sa.Table(...)` object in `db/metadata.py` (block
   `:67-130`; `sa.Column("water_level_datum_masl", sa.Float, nullable=True)` next to
   `altitude_masl` at `:74`) — **required so `select(stations)` includes the column and
   `_row_to_station` does not `KeyError`**; and (ii) an Alembic migration
   (`op.add_column(...)`) for the deployed DB. Add the field on `StationConfig`
   (`types/station.py:26-43`). Persist/round-trip it in **all three** `PgStationStore`
   sites: `store_station` (`station_store.py:94`, INSERT), `update_station` (`:126`,
   UPDATE), and `_row_to_station` (`:258`, read-back) — a review found `update_station`
   alone leaves inserts NULL and fetches blind. A fetch accessor is not separately needed;
   `fetch_station` surfaces the enriched `StationConfig`.
2. **`_run_qc_task` gets the datum.** The **caller** (the flow loop) builds the datum
   map with **zero extra DB queries** from the `eligible` list already in scope
   (`ingest_observations.py:272`), **keyed by `(station_id, parameter)`**:
   `datums = {(s.id, "water_level"): s.water_level_datum_masl for s in eligible}` (once
   artefact 1 adds the field). It then passes the **datum float**
   (`datum: float | None`) into `_run_qc_task` at the QC loop (`:336-349`) via
   `datum=datums.get((station_id, parameter))` — pass the float, not the whole store, to
   keep the task pure/testable. **The `(station_id, parameter)` key is required:**
   `station_params` (`:329-331`) has both a `(station_id, "discharge")` and a
   `(station_id, "water_level")` entry for every RIVER station (adapter fetches all three
   params, `hydro_scraper.py:36-40`); a `station_id`-only key would hand the water_level
   datum to the discharge QC call and shift discharge by −datum, corrupting the one
   parameter that currently passes. `.get((station_id, parameter))` returns `None` for
   discharge (no shift) and the datum only for water_level. (This deliberately does **not**
   call `fetch_station` per station:
   `fetch_station` returns `StationConfig | None` and would add N round-trips plus a
   time-of-check/time-of-use window; reusing `eligible` is one snapshot, no extra I/O.)
   `_run_qc_task` builds datum-shifted shadow copies of every water_level obs in `all_obs`
   via `replace(obs, value=obs.value - datum)`, **guarded for `MISSING` obs**:
   `replace(obs, value=obs.value - datum) if obs.value is not None else obs` — `all_obs`
   is fetched with no `qc_status` filter (`:153-158`) so it can contain `value=None`
   `MISSING` rows, and `None - datum` raises `TypeError` before `__post_init__` runs.
   Add `replace` to the `dataclasses` import (`:4`). Pass the shadow list to
   `checker.check`; the returned flags dict is keyed by the original `.id`, so the
   `raw_ids` filter + `update_qc` at `:179-189` are unchanged. Resolve the version
   **once, before the flags loop** — `version = "1.1-datum" if datum is not None else
   "1.0"` — and pass `qc_rule_version=version` into every `update_qc` call in the loop (not
   per-flag): the value is constant for the whole task invocation. **NULL datum → no shift**
   (backstop applies, item 3; version is `"1.0"`).
   `Observation.__post_init__` (`types/observation.py:41-47` — validates only
   `MISSING`/`None` consistency, NOT the numeric range) accepts the shifted copy: value
   stays a non-None float and `qc_status` is unchanged, so the invariant holds provided
   the `None`-guard above skips `MISSING` rows.
3. **Backstop widening (the NULL-datum guard — REQUIRED, survives from option B).**
   Widen the `water_level` `range_check` to a physical absolute band (`[0.0, 4500.0]`)
   in every config location — 10-min `config/qc_rules.py:122`, daily
   `config/qc_rules.py:158`, and deployed `config.toml:231` (10-min) + `:294` (daily
   `water_level` `range_check`, block at `:290-294`).
   This is the ONLY thing keeping a NULL-datum station from 100%-failing; it is NOT the
   abandoned per-station override.
4. **Spike-rule code fix + baseline recompute.** (a) **[ships with item 2]** Change the
   `water_level` spike rule to an **absolute-delta** threshold: make `_apply_spike`
   (`services/qc.py:136-164`) **dispatch on key presence** — `if "max_delta" in
   thresholds:` use the absolute-delta form, else the existing percentage-of-`|prev|`
   form (`:147-153`, unchanged for discharge which keeps `{"tolerance": ...}` at
   `config/qc_rules.py:67-72` + `config.toml:276-280`). **Replace** (not augment) the
   `water_level` spike entry's `{"tolerance": ...}` with `{"max_delta": <N>}` in **all four
   locations atomically**: `config/qc_rules.py:138-144` (10-min) and `:174-180` (daily),
   AND the production `config.toml:248-252` (10-min) and `:304-308` (daily) — the
   `config.toml` entries are the deployed path when `SAPPHIRE_CONFIG` is set
   (`docker-compose.yml:81,131`), so skipping them leaves the mac-mini spike rule on the
   old percentage form (both keys present would also leave the branch ambiguous). `<N>` =
   an absolute stage delta in metres — `N_10min` provisional `1.0 m`, `N_daily` a distinct
   larger value; confirm both alongside the datum statistic (sub-decision 2).
   (b) **[runs in the backfill, item 6/artefact 7]** delete + recompute any water_level
   `ClimBaseline` rows from the **relative** (`value − datum`) series first.
5. **Datum computation — from DB history, decoupled from onboarding (resolves the
   "BAFU stations don't go through CAMELS onboarding" blocker).** Compute the datum
   from the station's **`observations` water_level history** (a robust low-water
   reference — statistic + window for plan-review to finalize), reading the DB
   directly, so it works regardless of onboarding path. 2009/2091 already have
   water_level history in the DB. Run as an explicit step (at onboarding if history
   exists; otherwise a backfill/recompute task) — NOT wedged into CAMELS onboarding.
   **⚠ Persist via `update_station`, NOT `store_station`, for already-onboarded stations.**
   2009/2091 are live rows; `store_station` (`station_store.py:94`) uses `sa.insert` and
   would raise a primary-key conflict. The computation script must `fetch_station` the
   existing `StationConfig`, `dataclasses.replace(config,
   water_level_datum_masl=computed_datum)`, then `station_store.update_station(updated)`
   (`station_store.py:126`, `sa.update`). `store_station` is only the first-insert path
   (a brand-new station gets its datum via the INSERT `.values(...)` added in item 1).
6. **Backfill + audit.** Re-QC existing `water_level` rows on datum-shifted shadow
   copies; bump `qc_rule_version="1.1-datum-reqc"` so the bulk re-evaluation is
   traceable. (Baseline recompute is item 4b, run first.)
7. **Observability (orthogonal).** Add the `qc.rejected` per-obs event inside the
   `flags` loop (`ingest_observations.py:179-189`).
8. **Forecast-QC parallel fix (gated).** Widen `forecast_qc_rules.py`
   `range_check`/`negative_value` to the same absolute backstop; gated behind the first
   water_level forecast run, tracked as a follow-on.

**Test fixtures + coverage (part of the datum-subtract PR):**
- Add an optional `water_level_datum_masl: float | None = None` parameter to
  `make_station_config` (`tests/conftest.py:83-126`) and pass it through to
  `StationConfig(...)` so datum-path tests can build stations with a non-None datum
  without ad-hoc `replace` calls. `FakeStationStore` (`tests/fakes/fake_stores.py:794`)
  needs **no** change — it stores/returns `StationConfig` verbatim (the new field is a
  defaulted frozen-dataclass attribute, so existing call sites still construct).
- New `_run_qc_task` unit tests: (i) datum applied → water_level QC'd on relative stage;
  (ii) NULL datum → no shift, backstop bound applies; (iii) a `MISSING` (`value=None`)
  obs in the context window passes through unchanged (regresses the `TypeError` blocker);
  (iv) `qc_rule_version` is `"1.1-datum"` when a datum is applied and `"1.0"` when not;
  (v) **the datum for a water_level station is NOT applied to that station's discharge
  QC** — a station with `water_level_datum_masl` set, run through the `(station_id,
  "discharge")` QC path, produces flags identical to `datum=None` (regresses the
  discharge-corruption blocker).
- New `_apply_spike` (`services/qc.py:136-164`) unit tests for the key-presence dispatch:
  (a) `thresholds={"max_delta": 1.0}` → an absolute deviation >1 m triggers the spike flag
  and a deviation <1 m does not; (b) `thresholds={"tolerance": 0.1}` → the existing
  percentage-of-`|prev|` form still applies (regression guard for discharge spike
  detection, which keeps `tolerance`). Without these, a mis-wired dispatch (reading
  `thresholds["max_delta"]` when absent, or leaving both keys present) goes undetected
  until integration.

**Only genuine residual for plan-review to finalize:** the datum **statistic + history
window** (#5), the spike **`max_delta`** values (both `N_10min` and `N_daily`, artefact
4a), and the **recompute policy**.
The structural blockers are resolved by
the subtract-before-check realization PLUS the surviving spike/backstop/baseline work
above. Re-run plan-review to confirm convergence under this framing.

## Plan-review round 3 (2026-07-06) — FINAL CORRECTIONS (authoritative)

Re-review under the subtract-before-QC framing converged to 3 blockers + 2 majors,
all implementation-completeness (no design forks). Folded:

- **C1 (blocker, critical) — key the datum by `(station_id, parameter)`, NOT
  `station_id`.** The adapter fetches discharge + water_level + water_temperature for
  every river station, so the QC loop hits `(2009, 'discharge')` too. Passing the
  water_level datum (~261 m) there would shift **discharge** by −261 m and corrupt
  the one clean parameter. **Fix:** `datums = {(s.id, 'water_level'):
  s.water_level_datum_masl for s in eligible}` and pass
  `datum=datums.get((station_id, parameter))` → `None` (no shift) for discharge. (Or
  guard `if parameter == 'water_level' and datum is not None` inside `_run_qc_task`;
  keying at the call site is cleaner.)
- **C2 (blocker) — the spike fix (`tolerance` → `max_delta`) must hit ALL FOUR
  locations, incl. `config.toml` (the production path).** Since `SAPPHIRE_CONFIG` is
  set in every Docker deploy (`docker-compose.yml:81,131`), production loads
  `config.toml`, not the Python defaults — so the swap must cover: (1)
  `config/qc_rules.py:138-144` (10-min), (2) `config/qc_rules.py:174-180` (daily),
  (3) `config.toml:248-252` (10-min), (4) `config.toml:303-308` (daily). Omitting the
  `config.toml` pair leaves the deployed spike rule percentage-form (hyper-sensitive)
  after datum-subtract. Two distinct `max_delta` values: 10-min (~1 m) vs daily
  (larger) — a 10-min cap does not translate to a daily one. Same atomicity as the
  range_check "all locations" rule.
- **C3 (major) — datum is persisted via `update_station`, NOT `store_station`.**
  2009/2091 are already onboarded; `store_station` uses `sa.insert` → PK conflict on
  existing rows. `PgStationStore.update_station` (`station_store.py:126`) must be
  extended to write `water_level_datum_masl` (it currently omits it).
- **C4 (major) — resolve `qc_rule_version` ONCE.** At the top of `_run_qc_task`, set
  `version = "1.1-datum" if datum is not None else "1.0"`, then pass
  `qc_rule_version=version` to every `update_qc` inside the flags loop — not per-flag.
- **C5 (minor) — daily spike swap is unconditional** (the daily entry exists at
  `config/qc_rules.py:174-180`; drop the "if one exists" hedge) — covered by C2.
- **C6 (minor) — add a `_apply_spike` test** for the `max_delta`-key dispatch vs the
  legacy `tolerance` key.

### Two owner-confirmables before implementation (design-level, not folded):

1. **NULL-datum backstop = widening (fallback only).** For a station whose
   `water_level_datum_masl` is not yet computed (nullable), the value can't be
   shifted, so QC falls back to a **widened absolute range** (the plan-review's option-B
   artefact, retained ONLY as this fallback — NOT the primary fix, which stays the
   per-station datum). Alternative: a datum-less station **skips** water_level range QC
   (marks it unknown/raw, not failed) until its datum exists. **Confirm:** widen-as-
   backstop vs skip-until-datum. (You rejected widening as the *primary* fix — this is
   just the not-yet-computed fallback.)
2. **Datum statistic + window** (from DB water_level history) — the last numeric
   residual; recommend a robust low-water reference (e.g. a low percentile over the
   available history) — pin the exact statistic/window at implementation.

With C1–C6 folded and the two confirmables above, the design is settled and Plan 101
is **READY** for implementation (hold-at-PR).
