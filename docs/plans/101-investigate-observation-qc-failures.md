# Plan 101 — fix observation-QC failures (water_level datum mismatch)

**Status**: READY (pending final independent-review gate)
**Priority**: medium — surfaced on the mac-mini 2026-07-06: `ingest.qc_complete`
reports `failed=2` on obs ingest. Matters because the `nwp_regression`-with-lags
model consumes obs lag history, and water_level is 100 % QC-rejected, which breaks
the multi-parameter (discharge + water_level) experiment and floods QC monitoring
with false failures.
**Phase**: v0b — observation ingest / data quality
**Parent**: the operational obs feed (Plan 091); companion to Plan 100 (forecast-feed
resilience)
**Related**:
- `src/sapphire_flow/flows/ingest_observations.py` — the QC loop, `_run_qc_task`,
  the `ingest.qc_complete` structured event
- `src/sapphire_flow/services/qc.py` — `Stage1QualityChecker` + the per-rule appliers
- `src/sapphire_flow/services/run_station_forecast.py` — forecast-side QC gate
- `src/sapphire_flow/config/qc_rules.py`, `config.toml`,
  `src/sapphire_flow/config/forecast_qc_rules.py` — the QC rule config
- BAFU/LINDAS adapter `src/sapphire_flow/adapters/hydro_scraper.py` — source of the obs
**Created**: 2026-07-06

> **Citations re-verified against the code on 2026-07-06.** Every `file:line` below was
> checked with Read/Grep and corrected where stale (notably: `run_station_forecast.py`
> lives under `services/`, not `flows/`; the forecast checker is instantiated at
> `run_forecast_cycle.py:814` — `import` at `:812` — not `:811`; the abort gate is
> `run_station_forecast.py:218-229`; the forecast water_level `range_check` default is
> `config/forecast_qc_rules.py:169`).

---

## 1. Problem / root cause

The observation-ingest flow emits `ingest.qc_complete` with **`failed=2`** on the
mini. Reproduced on the local dev stack (same two stations, 2009/2091; 60k+ obs,
live-ingesting): **622 `qc_failed`** — **311 per station, all `water_level`**;
discharge is clean. Every failure is the same `range_check` rejection (from the stored
`qc_flags`):

```
2091 water_level 261.5   → "value 261.5 outside [-2.0, 20.0]"
2009 water_level 376.004 → "value 376.004 outside [-2.0, 20.0]"
```

**Root cause — a datum mismatch in the QC threshold, NOT bad data.** BAFU/LINDAS
delivers water level as **absolute metres above sea level** (~261 m at 2091, ~376 m at
2009); the adapter maps `waterLevel → water_level` verbatim with no datum conversion
(`adapters/hydro_scraper.py:48`). But the `water_level` `range_check` bound is
`{value_min: -2.0, value_max: 20.0}` — bounds appropriate for a **relative stage
height in metres**. So **every** `water_level` observation is out of range and marked
`qc_failed`. The mini's `failed=2` = one `water_level` per station per tick × 2
stations. Discharge is unaffected (its bounds `[0, 5000]` fit its absolute values).

A single global `water_level` range cannot fit both stations — absolute levels differ
~115 m between 2009 and 2091. The broken bound is also duplicated across the 10-min and
daily rule sets in both the Python defaults and the deployed `config.toml`.

**Impact:** benign for discharge forecasting today (discharge passes), but water_level
is 100 % rejected, blocking the multi-parameter experiment and polluting QC monitoring.

---

## 2. Decided design

Store the raw absolute value unchanged; **subtract a per-station datum from the
water_level VALUE before `checker.check`**, so every water_level rule operates on
relative stage against the existing relative bounds. No adapter change, no threshold
edits, no `StationQcOverride`, no `time_step` wiring.

1. **Keep-raw + subtract-before-check.** The `HydroScraperAdapter` is unchanged (a
   stateless HTTP boundary — it MUST stay store-free per the parse-don't-validate /
   preserve-raw-at-the-boundary principle). At QC time, build a datum-shifted
   **shadow copy** of each water_level obs (`value − datum`) via `dataclasses.replace`
   (`Observation` is `frozen=True, slots=True`, `types/observation.py:26`, so in-place
   mutation is impossible; `replace` preserves `.id`, so the returned flags dict still
   keys onto the real DB rows). The **stored** value stays raw absolute. `overrides`
   stays `[]`.

2. **Datum = surveyed gauge-zero elevation (BAFU Pegelnullpunkt), station METADATA
   provided at ONBOARDING, PERSISTED** — NOT inferred from history, NOT in-memory.
   It is durable and re-surveyed only every ~5–10 years, updated via `update_station`.
   Storage is a new nullable `stations.water_level_datum_masl` column. This supersedes
   every earlier "data-driven from history" / "compute a low-water statistic" /
   "persist-vs-in-memory" framing: there is no statistic to compute; it is a provided
   value whose provenance (surveyed / BAFU) is documented at onboarding.

3. **Key the datum by `(station_id, parameter)`, NOT `station_id`.** The adapter
   fetches discharge, waterLevel AND waterTemperature for every river station
   (`hydro_scraper.py:36-40`), so the QC loop also hits `(2009, "discharge")`. A
   `station_id`-only key would hand the ~261/376 m water_level datum to the discharge QC
   call and shift discharge by −datum → nonsense negatives → it would **corrupt
   discharge QC, the one parameter currently passing**. Pass
   `datum=datums.get((station_id, parameter))` → `None` (no shift) for discharge, the
   datum only for water_level.

4. **NULL datum (metadata not yet set) → SKIP the water_level range check** (mark
   raw/unknown, not failed). The datum-invariant rules (spike delta, rate_of_change,
   frozen_sensor) still run. **Global widening was REJECTED** — the independent review
   proved it is architecturally incompatible: `Stage1QualityChecker` applies ONE shared
   `QcRuleSet` per (parameter, time_step) with no separate datum path
   (`services/qc.py:219,239`). Widening the shared `water_level` `range_check` to a
   physical absolute band (e.g. `[0, 4500]`) would then also be applied to the
   datum-shifted **relative** values, erasing the tight `[-2, 20]` relative-stage QC the
   datum-subtract exists to provide. Widening and datum-subtract cannot coexist on one
   shared rule set → skip-until-datum instead.

5. **Forecast-side QC is fixed in the SAME slice.** v0 explicitly forecasts water_level
   (`docs/v0-scope.md:11,213`). `run_station_forecast` runs forecast QC per predicted
   parameter and **aborts on `QC_FAILED`** (`services/run_station_forecast.py:222`, gate
   `:218-229`, `check` at `:219`), and forecast defaults reject absolute water_level with
   `[-2, 20]` (`config/forecast_qc_rules.py:169`, plus the `negative_value` floor
   `value_min=-2.0` at `:155`/`:162`). Apply the same `(station, parameter)`-keyed
   datum-subtract + skip-until-datum on the forecast path, reading the same
   `water_level_datum_masl` metadata. Not deferred, not a separate gate. (Checker
   instantiated at `run_forecast_cycle.py:814`.)

6. **Spike rule: `tolerance` → absolute `max_delta`.** The percentage-of-`|prev|` spike
   form (`services/qc.py:147-153`) is **disabled** at absolute scale (`0.1 × 261 ≈ 26 m`
   never trips) and would become **hyper-sensitive** at low relative stage after
   datum-subtract (`0.1 × |prev| → 0` as a gauge nears dry; the `ref == 0.0` guard only
   catches exact zero). So for water_level, switch to an absolute-delta threshold and
   ship it in the SAME PR as the datum-subtract change. Make `_apply_spike`
   (`services/qc.py:136-164`) **dispatch on key presence** — `if "max_delta" in
   thresholds:` use `abs(obs.value − prev.value) > thresholds["max_delta"]` (and the
   symmetric next-value check), else fall through to the existing percentage form (which
   discharge keeps). **Replace** (do not augment) the water_level `{"tolerance": …}`
   with `{"max_delta": <N>}` at ALL FOUR locations — `config/qc_rules.py` 10-min +
   daily, and `config.toml` 10-min + daily. Production reads `config.toml` because
   `SAPPHIRE_CONFIG` is set in every Docker deploy (`docker-compose.yml:81,131`), so
   omitting the `config.toml` pair leaves the deployed spike rule on the old percentage
   form. Two distinct values: `N_10min` (provisional `1.0 m`) and a larger `N_daily`
   (a 1 m/10-min cap does not translate to 1 m/day).

7. **Flag-detail honesty.** range/spike/gross_outlier detail strings embed `obs.value`
   (`services/qc.py:66,160,187`) and are stored verbatim (`observation_store.py:119`,
   serialized at `:218`). After the shadow-shift they would read the relative value
   while the stored raw is absolute (e.g. "value 21 outside …" vs a stored ~282 m).
   When a datum shift is applied, augment the water_level flag detail with **raw +
   relative + datum** so diagnostics are not a lie.

8. **Baseline delete/replace.** `gross_outlier` compares `obs.value` (now relative) with
   `baseline.rolling_mean` (`services/qc.py:167,181`), so water_level `ClimBaseline` rows
   must be recomputed in **relative** terms (`rolling_mean − datum`; `rolling_std` is
   datum-invariant). `PgClimBaselineStore` only stores/fetches
   (`clim_baseline_store.py:16,44`) — add a delete/replace for `(station_id, parameter)`
   and run it **before** storing the relative recompute, else stale absolute rows survive
   and flag every relative obs as a gross outlier. (In v0 water_level was 100 % rejected,
   so likely no water_level baselines exist yet — audit and document explicitly.)

9. **`qc_rule_version` resolved ONCE** at the top of `_run_qc_task`, before the flags
   loop (`ingest_observations.py:179-189`): `version = "1.1-datum" if datum is not None
   else "1.0"`, passed to every `update_qc` call in the loop (currently the hardcoded
   literal `"1.0"` at `:183`). Constant per invocation — do not branch per-flag.

10. **Backfill.** Set the known BAFU gauge datums for the already-onboarded 2009/2091 via
    `update_station`, then re-QC the existing water_level rows against the datum-shifted
    shadow copies (bump `qc_rule_version` for audit). A re-survey later updates the column
    via `update_station` and triggers an explicit re-QC (not silent drift).

11. **Observability.** Add a `qc.rejected` per-obs debug event **inside** the flags loop
    (`ingest_observations.py:179-189`) emitting `station_id`, `parameter`, `rule_id`,
    `value`, `threshold` for each `QC_FAILED` flag. Today there is NO per-obs rejection
    log event: failures are only counted into `counts["failed"]`, and `ingest.qc_failed`
    (`ingest_observations.py:352`) fires only on a task **exception**, not per-obs. Leave
    the `ingest.qc_failed` exception event untouched.

12. **`_run_qc_task` signature.** The caller (flow QC loop) passes the datum **float**
    into `_run_qc_task`, not the store — keep the task pure/testable.

**Why datum is onboarding metadata (not history-derived):** it is a surveyed physical
constant (gauge-zero elevation), re-measured only on a re-survey. Deriving it from
observation history is fragile (regime shifts, sensor drift) and does not fit BAFU LINDAS
live stations that never pass through CAMELS onboarding. Persisted (not in-memory) because
it is durable and must not be recomputed per run.

---

## 3. Implementation surface (ordered checklist)

All of the following ship in **one implementation slice / PR** except the backfill script
(step 12), which runs after datum values are written.

1. **Datum storage — two files, not just the migration:**
   - **SQLAlchemy `Table` object** — add
     `sa.Column("water_level_datum_masl", sa.Float, nullable=True)` to
     `stations = sa.Table(...)` in `db/metadata.py` (block `:67-130`; place beside
     `altitude_masl` at `:74`). Without this, `select(stations)` omits the column and
     `_row_to_station` would `KeyError`.
   - **Alembic migration** — `op.add_column("stations", sa.Column(
     "water_level_datum_masl", sa.Float(), nullable=True))` (new versions file; adds the
     physical column to the deployed DB).

2. **`StationConfig` field** — add `water_level_datum_masl: float | None = None`
   (`types/station.py:26-43`; the frozen dataclass fields end at `gauging_status` on
   `:43` — add the new defaulted field there).

3. **`PgStationStore` — persist/round-trip in all three sites:**
   - `store_station` (`station_store.py:94`, `sa.insert`) — add
     `water_level_datum_masl=station.water_level_datum_masl` to `.values(...)`, else new
     inserts are always NULL.
   - `update_station` (`station_store.py:126`, `sa.update`) — add the same key to
     `.values(...)` (it currently omits it — this is the backfill/re-survey write path).
   - `_row_to_station` (`station_store.py:258`) — read `row["water_level_datum_masl"]`
     and pass it to `StationConfig`, else `fetch_station` never surfaces the datum.

4. **Onboarding input** — the onboarding flow (`flows/onboard.py` /
   `services/onboarding.py`) accepts a `water_level_datum_masl` input and threads it into
   the `StationConfig` it constructs; document provenance (surveyed / BAFU gauge-zero).

5. **`_run_qc_task` datum application** (`ingest_observations.py:141-191`):
   - Receive `datum: float | None` as a parameter (passed by the caller).
   - Add `replace` to the `dataclasses` import (`ingest_observations.py:4` currently
     imports only `dataclass`), else the call raises `NameError`.
   - When `datum is not None`, build shadow copies of **every** water_level obs in
     `all_obs` (the full context window — `:153-158`), **guarded for `MISSING` rows**:
     `replace(obs, value=obs.value - datum) if obs.value is not None else obs`.
     `all_obs` is fetched with no `qc_status` filter, so it can contain `value=None`
     `MISSING` rows (`types/observation.py:32`); `None - datum` raises `TypeError` before
     `__post_init__` runs. `MISSING` rows pass through unchanged — safe, since
     `_apply_range_check` (`qc.py:55`) and `_apply_spike` (`qc.py:145`) both short-circuit
     on `value is None`. All context obs must be shifted too (a mixed list would make
     `rate_of_change`/`spike` compute a ~−datum delta).
   - When `datum is None` → **skip the water_level range check** (skip-until-datum). Do
     NOT widen the shared bound.
   - Pass the shadow list to `checker.check(overrides=[])`; the returned
     `dict[ObservationId, list[QcFlag]]` keys onto the real IDs, so the `raw_ids` filter +
     `update_qc` at `:179-189` are unchanged and the stored DB value is never touched.
   - Resolve `version = "1.1-datum" if datum is not None else "1.0"` once, before the
     flags loop; pass `qc_rule_version=version` to every `update_qc` (`:183`).
   - Augment water_level flag detail with raw + relative + datum when a shift is applied
     (design item 7).

6. **`_run_qc_task` caller wiring** (`ingest_observations.py`):
   - Immediately after `eligible` is built (`:272`), construct the datum map from the
     `StationConfig` objects already in scope — **zero extra DB round-trips**, keyed by
     `(station_id, parameter)`:
     `datums = {(s.id, "water_level"): s.water_level_datum_masl for s in eligible}`.
   - In the QC loop (`:336-349`, iterating `station_params` at `:329-331` which holds both
     `(station_id, "discharge")` and `(station_id, "water_level")` per river station), pass
     `datum=datums.get((station_id, parameter))` into each `_run_qc_task` call → `None` for
     discharge (no shift), the datum for water_level.

7. **Spike-rule code + config** (design item 6):
   - `services/qc.py` `_apply_spike` (`:136-164`) — dispatch on `"max_delta"` key presence
     (`:147` currently reads `thresholds["tolerance"]` unconditionally); absolute-delta form
     when present, else the existing percentage form (`:147-153`, `ref == 0.0` guard retained).
   - Replace water_level `{"tolerance": …}` → `{"max_delta": <N>}` at ALL FOUR locations:
     `config/qc_rules.py:143` (10-min, block `:139-143`), `config/qc_rules.py:179` (daily,
     block `:175-179`), `config.toml:252` (10-min, block `:248-252`), `config.toml:308`
     (daily, block `:304-308`). Discharge spike entries stay `{"tolerance": …}`
     (`config/qc_rules.py:71,107` + `config.toml:217,280`).

8. **Forecast-side datum-subtract** (design item 5) — apply the same
   `(station, parameter)`-keyed shift + skip-until-datum to `run_station_forecast`'s QC
   (`services/run_station_forecast.py:218-229`), reading `water_level_datum_masl`. Do NOT
   widen `config/forecast_qc_rules.py` (`:169`, `:155`/`:162`) — same shared-rule-set
   argument as obs QC.

9. **Flag-detail honesty** — augment water_level `range_check`/`spike`/`gross_outlier`
   detail with raw + relative + datum when shifted (`services/qc.py:66,160,187`; stored via
   `observation_store.py:119`, serialized `:218`).

10. **Baseline delete/replace** — add a delete-then-store for `(station_id, parameter)` to
    `PgClimBaselineStore` (`clim_baseline_store.py:16,44` only store/fetch today); run it
    before storing the relative recompute (design item 8). Runs in the backfill script.

11. **`qc.rejected` observability event** — inside the flags loop
    (`ingest_observations.py:179-189`), per design item 11.

12. **Backfill script** (one-shot admin script, `uv run python3 << 'EOF'` per CLAUDE.md —
    NOT a new flow): (a) delete + recompute any water_level `ClimBaseline` rows from the
    relative series; (b) re-QC the existing water_level rows on datum-shifted shadow copies
    (`dataclasses.replace`, keyed by the unchanged `ObservationId`), writing
    `qc_rule_version="1.1-datum-reqc"` so the re-QC pass is distinguishable from the
    original `"1.0"`. 622 rows one-by-one via the existing single-row `update_qc` in one
    transaction is fine — no `bulk_update_qc` API needed at this size.

---

## 4. Tests

Part of the datum-subtract PR:

- **Fixtures:** add optional `water_level_datum_masl: float | None = None` to
  `make_station_config` (`tests/conftest.py:83`, `StationConfig(...)` at `:108`) and pass
  it through. `FakeStationStore` (`tests/fakes/fake_stores.py:794`) needs no change — it
  stores/returns `StationConfig` verbatim and the new field is a defaulted attribute.
- **`_run_qc_task`:** (i) datum applied → water_level QC'd on relative stage;
  (ii) NULL datum → range check **skipped** (marked raw/unknown, not failed), spike +
  rate_of_change still run; (iii) a `MISSING` (`value=None`) obs in the context window
  passes through unchanged (regresses the `TypeError` blocker); (iv) `qc_rule_version` is
  `"1.1-datum"` when a datum is applied and `"1.0"` when not; (v) **the water_level datum
  is NOT applied to the same station's discharge QC** — a station with
  `water_level_datum_masl` set, run through the `(station_id, "discharge")` path, produces
  flags identical to `datum=None` (regresses the discharge-corruption blocker).
- **`_apply_spike` dispatch:** (a) `thresholds={"max_delta": 1.0}` → deviation >1 m flags,
  <1 m does not; (b) `thresholds={"tolerance": 0.1}` → the legacy percentage form still
  applies (discharge regression guard).
- **Forecast-side:** datum-subtract applied on the forecast QC path; NULL datum → skip.
- **Backfill:** re-QC of existing water_level rows produces the expected pass/skip and the
  `"1.1-datum-reqc"` version.

Any test that hits `/observations.json` or the reflected schema is an **integration** test,
not a unit test (`get_reflected` runs `MetaData.reflect`).

---

## 5. Backfill & rollout

1. Ship the datum-subtract slice (surface §3 items 1–11) hold-at-PR with a version bump.
2. Write the known BAFU gauge-zero datums for the already-onboarded 2009/2091 via
   `update_station` (they are live rows — `store_station`'s `sa.insert` would PK-conflict).
3. Run the one-shot backfill script (§3 item 12): delete + recompute any water_level
   baselines in relative terms first, then re-QC the 622 existing water_level rows on
   datum-shifted shadow copies, writing `qc_rule_version="1.1-datum-reqc"` for audit.
4. On a future re-survey: update the column via `update_station` and re-run the re-QC
   against the revised datum (explicit, not silent drift).

---

## 6. Non-goals

- **Adapter unit conversion** (absolute → relative in `hydro_scraper.py`) — rejected;
  keep-raw at the boundary.
- **Global bound widening** as a NULL-datum backstop — rejected as architecturally
  incompatible with the shared per-(parameter, time_step) rule set (design item 4).
- **The NWP-off forecast blackout / fallback resilience** — Plan 100.
- **A wholesale QC-rule redesign** — scoped to the observed water_level `range_check`
  failure and the directly-entangled spike/baseline/flag-detail/forecast fixes.
- **The rating-curve / published gauge-zero work (Nepal v1)** — reference only; the
  `water_level_datum_masl` column is designed to be compatible with, not to replace, that
  future work.

---

## ⚠ Flags for reviewer

- **Datum-computation for a brand-new station with no surveyed datum:** the design keeps
  the column NULL → skip-until-datum. Confirm there is an operational path to obtain the
  BAFU Pegelnullpunkt for all onboarded stations (2009/2091 have known values; the general
  onboarding input assumes provenance is available).
- **`N_10min` / `N_daily` spike `max_delta` values** are provisional (`1.0 m` for 10-min;
  `N_daily` unspecified, "distinct larger"). These are the only remaining numeric residuals
  to pin at implementation.
