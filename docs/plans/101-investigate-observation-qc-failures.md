# Plan 101 — investigate observation-QC failures (`ingest.qc_complete failed=2`)

**Status**: DRAFT — root cause FOUND; grill-me DONE; raw-vs-relative → KEEP-RAW;
**SCOPE LOCKED by owner (2026-07-06): (A) per-station datum** (global-widening (B)
dropped — it was a misunderstanding). Realization refined to **subtract the datum
from the water_level VALUE before `checker.check`** (NOT per-rule overrides), which
**dissolves 4 of the 6 review blockers** (no threshold edits, no override/`time_step`
wiring) — see "SCOPE LOCKED" at the end. Remaining = the multi-file datum
storage/compute + a datum statistic/window residual. **Re-running plan-review (WF1)
to confirm convergence** under this framing.
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
  stay that way). The per-station datum is applied **inside `_run_qc_task`** by
  populating the currently-empty `overrides=[]` argument
  (`ingest_observations.py:174`) with a per-station `water_level` `range_check`
  override whose bounds are `[datum − 2.0, datum + 20.0]` (i.e. the global relative
  band shifted into absolute m a.s.l. for that station). Equivalent to subtracting
  the datum before the check, but expressed through the existing override seam that
  `Stage1QualityChecker._merge_thresholds` already supports — no new "relative-stage"
  storage field, no adapter change, and raw data fidelity preserved. QC thus compares
  the **raw absolute value** against **datum-derived absolute bounds**. (If display
  ever needs relative stage, compute `value − datum` on read; do not persist a second
  field in v0.)
- **Datum storage = one nullable column on the `stations` table.** RESOLUTION (was
  the single blocking gap): add a nullable `water_level_datum_masl FLOAT` column to
  the `stations` table (`db/metadata.py:67-129`, alongside the existing
  `altitude_masl` column at `:74`) via an Alembic migration; add the field to
  `StationConfig` (`types/station.py:26-43`) as `water_level_datum_masl: float | None
  = None`; and add a `StationStore` accessor (`protocols/stores.py:484` — the
  Protocol already has `fetch_station`/`store_station` at `:485`/`:503`) so
  `_run_qc_task` can read it. **A dedicated `station_datums` table is rejected as
  over-engineering** for a single scalar per station. **NULL datum = no datum yet →
  fall back to the wide global default** (see backstop below), so a fresh station
  with no history QCs against a permissive absolute band rather than rejecting 100%.
  The datum is read by the **QC flow task** (injected `StationStore`), NOT by the
  adapter and NOT at flow startup — it is fetched per station inside `_run_qc_task`
  next to the existing `baseline_store.fetch_baselines` call
  (`ingest_observations.py:168`).
- **Datum = data-driven from history, computed at onboarding.** Compute each
  station's reference datum from its observed `water_level` history (a robust
  low-water reference — see sub-decision 2), rather than `altitude_masl` (unreliable
  as the water datum) or manual config (doesn't scale to ~1000 stations). The
  onboarding flow computes it and writes `water_level_datum_masl` via
  `StationStore.store_station`. A station with insufficient history leaves the column
  NULL until enough accrues, then a recompute pass populates it (sub-decision 3).
- **Global default becomes a physical backstop, not a per-station bound.** Because the
  global `[-2.0, 20.0]` cannot fit absolute m a.s.l. for the NULL-datum fallback,
  widen the global `water_level` `range_check` in all three locations (Findings) to a
  **safe physical absolute band for the network** (e.g. `[0.0, 4500.0]` for Swiss
  stations — no BAFU gauge sits above the highest Alpine outlet). This is a coarse
  backstop that catches only gross sensor faults; the tight per-station QC comes from
  the datum-derived override. This keeps the fallback path safe when a datum is NULL.
- **Re-QC existing after the fix.** Once the datum + override wiring is in place,
  re-evaluate the 622 existing `qc_failed` water_level rows (see sub-decision 4).

### Mandatory per-rule audit — ALL five water_level QC rules (not just range_check)

The fix touches `range_check` directly, but three other `water_level` rules
(`config/qc_rules.py:116-151`) have threshold semantics that interact with the
absolute-vs-relative representation. Because we chose **keep-raw-absolute** (values
stay ~261–376 m), each rule must be audited and the outcome recorded before READY:

1. **`range_check`** — FIXED by the datum-derived per-station override + widened
   global backstop (above).
2. **`rate_of_change`** (`services/qc.py`, `max_rate=0.5`) — operates on the
   **delta** between consecutive values; a delta is datum-invariant (`Δabsolute =
   Δrelative`). **Correct as-is under keep-raw.** No change.
3. **`frozen_sensor`** (`tolerance=0.001`) — operates on consecutive-value equality
   within a tolerance; also delta-based / datum-invariant. **Correct as-is.** No change.
4. **`spike`** (`services/qc.py:136-164`, `tolerance=0.1`) — **PRE-EXISTING BUG that
   becomes load-bearing under keep-raw.** It flags when `abs(obs.value − prev.value) >
   tolerance * abs(prev.value)` (`:148,:152`). With absolute values (~261 m) the
   threshold is `0.1 × 261 ≈ 26 m`, so it silently passes every genuine spike —
   spike detection is effectively disabled at absolute scale. It also has a
   `if ref == 0.0: return None` guard (`:149`) that would skip checks at zero stage if
   we ever went relative. RESOLUTION: for stage-like data the spike threshold must be
   an **absolute delta** (e.g. `max_delta` in metres), not a percentage of `prev.value`.
   Since keep-raw keeps values absolute, flag this as a pre-existing bug and change the
   `water_level` spike rule to an absolute-delta threshold (add a `max_delta` threshold
   and branch, or scope the percentage form to `discharge` only). This is the only rule
   whose *code* changes, not just config.
5. **`gross_outlier`** (`services/qc.py:167-192`, `k_sigma=5.0`) — compares
   `abs(obs.value − baseline.rolling_mean) > k_sigma × baseline.rolling_std` against
   `ClimBaseline` rows. Under keep-raw, observations stay **absolute**, so baselines
   must ALSO be in absolute m a.s.l. for the comparison to be valid. RESOLUTION: audit
   whether any `water_level` `ClimBaseline` rows exist
   (`baseline_store.fetch_baselines`, `ingest_observations.py:168`). If none exist yet
   (likely in v0, since water_level was 100% rejected), **document that explicitly** so
   the backfill does not leave stale baselines. If any exist and were computed from a
   different representation, they must be **invalidated and recomputed from the raw
   absolute series** as part of the backfill (sub-decision 4). Since we keep values
   absolute end-to-end, no unit switch occurs — the risk is only stale/partial baselines.

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

1. ~~Raw-data preservation vs relative-only~~ **RESOLVED above: keep raw absolute,
   subtract datum in QC via the `overrides` path.** No adapter change, no second field.
2. **Datum definition (statistic + window).** Exactly which statistic (min? p1? a
   robust low-water reference?) over which history window, and the margin. Ties to
   the rating-curve / gauge-zero work (Nepal v1) — a datum here should be compatible
   with, not contradict, a future published gauge-zero. (Design detail, not a blocker:
   the storage + wiring are fixed above; only the numeric recipe remains.)
3. **Datum stability / recompute policy.** Compute once at onboarding vs periodically
   (a regime shift or a re-levelled gauge changes the true datum). Recommend
   compute-at-onboarding + a documented recompute path (a re-run that overwrites
   `water_level_datum_masl` via `store_station`), not silent drift.
4. **Backfill mechanics + audit (actor + version string SPECIFIED).** Re-QC-ing the
   622 rows:
   - **Actor:** a one-shot admin script (`uv run python3 << 'EOF'` per CLAUDE.md),
     NOT a new Prefect flow. It reads each station's `water_level_datum_masl`, rebuilds
     the datum-derived override, re-runs `Stage1QualityChecker.check` over the stored
     raw values, and writes the new status.
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
   - If any `water_level` `ClimBaseline` rows exist (see audit rule 5), recompute them
     from the raw absolute series in the same script before the re-QC pass.
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

The DECIDED DIRECTION resolves to this concrete, ordered change set (each grounded
in a real file:line):

1. **Datum storage** (unblocks everything else):
   - Alembic migration: nullable `water_level_datum_masl FLOAT` on the `stations`
     table (`db/metadata.py:67-129`, next to `altitude_masl` at `:74`).
   - `StationConfig.water_level_datum_masl: float | None = None`
     (`types/station.py:26-43`).
   - `StationStore` accessor to read the datum (`protocols/stores.py:484`; the
     Protocol already exposes `fetch_station`/`store_station`). `fetch_station`
     returning the enriched `StationConfig` may suffice — confirm during impl whether
     a dedicated method is warranted or the field on `StationConfig` is enough.
2. **QC-side datum application:** in `_run_qc_task` (`ingest_observations.py:168-176`)
   fetch the station's datum and build a per-station `water_level` `range_check`
   override `[datum − 2.0, datum + 20.0]`; pass it via `overrides=` (currently `[]`
   at `:174`). NULL datum → omit the override (global backstop applies).
3. **Global backstop widening** in all three locations atomically: `config/qc_rules.py:122`,
   `config/forecast_qc_rules.py:169` (+ `:155`/`:162`/`:176`), and `config.toml:231`.
4. **Spike-rule code fix** for `water_level` (absolute-delta threshold, not
   percentage-of-prev) — `services/qc.py:136-164` + the rule's thresholds at
   `config/qc_rules.py:138-144`.
5. **`qc.rejected` debug log event** inside the `flags` loop at
   `ingest_observations.py:179-189`.
6. **Onboarding datum computation** writing `water_level_datum_masl` via
   `store_station` (statistic per sub-decision 2).
7. **Backfill script** (one-shot, per sub-decision 4) re-QC-ing the 622 rows with
   `qc_rule_version="1.1-datum-reqc"`, recomputing any water_level baselines first.
8. **Forecast-QC parallel fix** (`forecast_qc_rules.py`) — gated behind the first
   water_level forecast run, tracked as a follow-on.

Steps 1–5 + 7 are the obs-QC fix PR(s); step 6 lands in the onboarding flow; step 8
is a gated follow-on.

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
per-station bound comes from the datum override, and a NULL-datum station QCs loosely
until its datum is computed. (b) Re-QC-ing 622 rows one-by-one via the existing
single-row `update_qc` is accepted over adding a `bulk_update_qc` API, given the small
row count; revisit if the backfill grows by orders of magnitude.

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

Owner chose **(A) precise per-station datum** ("the widening was a misunderstanding —
we use the per-station datum, that's enough"). **The global-widening option (B) is
dropped.** Realization is refined from the plan-review's override-based framing to the
simpler, blocker-dissolving form:

**Subtract the datum from the water_level VALUE before `checker.check`, NOT via
per-rule `StationQcOverride`.** For each `water_level` observation, evaluate QC on
`value − station_datum` (a relative-stage copy); the **stored** value stays raw
absolute (keep-raw). This makes ALL water_level rules operate on relative stage.

**This dissolves 4 of the 6 review blockers + several majors — because we change NO
thresholds and use NO override:**
- ❌→✅ *daily `range_check [-5,30]`, spike %, multiple bound locations,
  `forecast_qc_rules.py`* — all now **correct as-is**: once the value is relative,
  the existing relative bounds/percentages apply. No config bound edits.
- ❌→✅ *`StationQcOverride.time_step` exact-match / per-time_step overrides* — **not
  used at all**; `overrides` stays `[]`. No domain-type or `_qc_helpers` refactor.
- ✅ *rate_of_change* — Δ is datum-invariant; unchanged.

**Remaining real work (accepted — this is the multi-file part the owner signed up for):**
1. **Datum storage.** Add nullable `stations.water_level_datum_masl` (Alembic
   migration) + the field on `StationConfig` + persist it in
   `PgStationStore.update_station` (`station_store.py:126`, which today omits it) +
   a fetch accessor.
2. **`_run_qc_task` gets the datum.** The **caller** (the flow loop,
   `ingest_observations.py:338-349`) fetches the station's datum from `station_store`
   and passes the **datum value** into `_run_qc_task` (pass the float, not the whole
   store — keeps the task pure/testable). `_run_qc_task` subtracts it from each
   water_level obs before `checker.check`.
3. **Datum computation — from DB history, decoupled from onboarding (resolves the
   "BAFU stations don't go through CAMELS onboarding" blocker).** Compute the datum
   from the station's **`observations` water_level history** (a robust low-water
   reference — statistic + window for plan-review to finalize), reading the DB
   directly, so it works regardless of onboarding path. 2009/2091 already have
   water_level history in the DB. Run as an explicit step (at onboarding if history
   exists; otherwise a backfill/recompute task) — NOT wedged into CAMELS onboarding.
4. **Backfill + audit.** Re-QC existing `water_level` rows with the datum applied;
   bump `qc_rule_version` so the bulk re-evaluation is traceable.
5. **Observability (orthogonal).** Add the `qc.rejected` per-obs event inside the
   `flags` loop (`ingest_observations.py:179-189`).

**Only genuine residual for plan-review to finalize:** the datum **statistic + history
window** (#3) and its **recompute policy**. The structural blockers are resolved by
the subtract-before-QC realization. Re-run plan-review to confirm convergence under
this framing.
