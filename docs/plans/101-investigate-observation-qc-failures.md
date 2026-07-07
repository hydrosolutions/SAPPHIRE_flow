# Plan 101 — fix observation-QC failures (water_level datum mismatch)

**Status**: READY-TO-IMPLEMENT (2026-07-07) — passed 4 independent-review gates
(review 1 caught the widening-vs-datum architecture conflict; 2 caught the missing
GROUP-forecast/onboarding QC call sites; 3 the onboarding baseline/datum-input gaps;
4 = clean). Only residual = the spike `max_delta` `N_10min`/`N_daily` numeric values,
a tunable constant chosen at implementation. WF2-able (hold-at-PR).
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
- `src/sapphire_flow/services/run_station_forecast.py`,
  `src/sapphire_flow/services/run_group_forecast.py` — forecast-side QC gates (STATION + GROUP)
- `src/sapphire_flow/services/onboarding.py` — onboarding QC gate
- `src/sapphire_flow/config/qc_rules.py`, `config.toml`,
  `src/sapphire_flow/config/forecast_qc_rules.py` — the QC rule config
- BAFU/LINDAS adapter `src/sapphire_flow/adapters/hydro_scraper.py` — source of the obs
**Created**: 2026-07-06

> **Citations re-verified against the code on 2026-07-07.** Every `file:line` below was
> checked with Read/Grep and corrected where stale. Notably: `run_station_forecast.py`
> lives under `services/`, not `flows/`; the STATION forecast checker is instantiated at
> `run_forecast_cycle.py:814` (`import` at `:812`), not `:811`; the STATION abort gate is
> `run_station_forecast.py:218-229`; the forecast water_level `range_check` default is
> `config/forecast_qc_rules.py:169`. The spec covers **all four QC call sites**:
> obs-ingest (`ingest_observations._run_qc_task`), station-forecast
> (`run_station_forecast`), group-forecast (`run_group_forecast._build_station_result`,
> abort at `:272`), and onboarding (`services/onboarding.py:381`).

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
~115 m between 2009 and 2091. The broken bound is duplicated across the 10-min and daily
rule sets in both the Python defaults and the deployed `config.toml`, at **distinct
relative-stage values**: the **10-min** water_level range is `[-2, 20]`
(`config/qc_rules.py:118-122`, `config.toml:226-231`) and the **daily** range is
`[-5, 30]` (`config/qc_rules.py:154-158`, `config.toml:290-294`). Both are relative-stage
bounds — the datum-subtract fix restores each to correctness without editing either.

**Impact:** benign for discharge forecasting today (discharge passes), but water_level
is 100 % rejected, blocking the multi-parameter experiment and polluting QC monitoring.

---

## 2. Decided design

Store the raw absolute value unchanged; **subtract a per-station datum from the
water_level VALUE before `checker.check`**, so every water_level rule operates on
relative stage against the existing relative bounds. No adapter change, no threshold
edits, no `StationQcOverride`, no `time_step` wiring.

> **Boundary unit assumption + guard (added 2026-07-07).** This design assumes
> water_level arrives in **metres** — either metres above ground (relative, datum `0`)
> or metres above sea level (absolute, datum = surveyed gauge-zero). The datum is a
> pure metres **offset**; it does NOT rescale units. **v0/BAFU is all metres**, so no
> conversion is needed. But DHM/v1 will send some stations in **cm** (or m above
> ground), and a cm value silently checked against relative-metre bounds would be
> mis-QC'd. **Guard (implement here):** record the station's `water_level_unit` at
> onboarding (alongside the datum) and, at the onboarding gate, **assert it is one of
> `{m, m a.s.l.}`** (the metres-based units this plan supports); **fail onboarding with
> a clear `ConfigurationError` for any other unit (e.g. `cm`)** so an unsupported-unit
> station cannot be silently onboarded and mis-QC'd before the v1 normalization exists.
> The actual **cm→m normalization is an adapter-boundary concern for v1/DHM**, out of
> scope here (see §6) — this plan only *guards* the metres assumption.

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
   value whose provenance (operator-supplied surveyed BAFU gauge-zero) is documented at
   onboarding.
   **Datum-source surface = a TOML table keyed by station code.** The CAMELS onboarding
   config schema (`config/onboarding.py:14` `OnboardingConfig`, parsed at `:37`) has only
   `data_source` + `basin_ids` today, and the `StationConfig` is built in
   `adapters/camelsch_adapter.py` (`attributes_to_station`, `:145`; the
   `return StationConfig(...)` at `:179`) with no datum field. Add a config table
   `[onboarding.water_level_datums_masl]` keyed by **station code** (e.g. `"2009" = <masl>`,
   `"2091" = <masl>`), parse it into `OnboardingConfig`, and apply it by `code` in
   `attributes_to_station` (`camelsch_adapter.py:145/179`), setting
   `water_level_datum_masl` (→ `None` when the code is absent from the table).

3. **Key the datum by `(station_id, parameter)`, NOT `station_id`.** The adapter
   fetches discharge, waterLevel AND waterTemperature for every river station
   (`hydro_scraper.py:36-40`), so the QC loop also hits `(2009, "discharge")`. A
   `station_id`-only key would hand the ~261/376 m water_level datum to the discharge QC
   call and shift discharge by −datum → nonsense negatives → it would **corrupt
   discharge QC, the one parameter currently passing**. Pass
   `datum=datums.get((station_id, parameter))` → `None` (no shift) for discharge, the
   datum only for water_level.

4. **NULL datum (metadata not yet set) → SKIP EVERY datum-dependent rule; still run +
   aggregate the datum-invariant rules.** There is **no UNKNOWN QC status**
   (`types/enums.py:4-9` — the statuses are RAW, QC_PASSED, QC_FAILED, QC_SUSPECT, MISSING)
   and `aggregate_qc_status` returns `QC_PASSED` only for an **empty** flag list
   (`types/domain.py:104`). So a null-datum water_level obs must NOT be marked with a
   fictional "unknown". Instead, skip the **datum-dependent rules** and run the
   **datum-invariant rules** normally, letting the obs aggregate to a **real** status from
   those. The skip set is NOT just `range_check` — any rule that compares an absolute value
   or an (absolute) baseline is datum-dependent:
   - **obs QC (datum-dependent, SKIP when null):** `range_check`, `gross_outlier` (compares
     `obs.value` to `baseline.rolling_mean`, `services/qc.py:167,181`).
   - **forecast QC (datum-dependent, SKIP when null):** `range_check`, `negative_value`
     (a relative lower-bound `value_min=-2.0`, `config/forecast_qc_rules.py:150,157`),
     `climatology_outlier` (baseline compare, `services/forecast_qc.py:170`).
   - **datum-invariant (always RUN):** `spike`/`max_delta`, `rate_of_change`,
     `frozen_sensor` (all delta-based).

   Persist `qc_rule_version = "1.1-datum-skip"` so null-datum passes are auditable and
   distinct from both the datum-applied (`"1.1-datum"`) and legacy (`"1.0"`) passes.
   **Global widening was REJECTED** — the independent review proved it is architecturally
   incompatible: `Stage1QualityChecker` applies ONE shared `QcRuleSet` per (parameter,
   time_step) with no separate datum path (`services/qc.py:219,239`). Widening the shared
   `water_level` `range_check` to a physical absolute band (e.g. `[0, 4500]`) would then
   also be applied to the datum-shifted **relative** values, erasing the tight
   relative-stage QC the datum-subtract exists to provide. Widening and datum-subtract
   cannot coexist on one shared rule set → skip-datum-dependent-rules-until-datum instead.

5. **ALL forecast + onboarding QC paths are fixed in the SAME slice.** v0 explicitly
   forecasts water_level via BOTH a per-station and an operational GROUP path
   (`docs/v0-scope.md:11,213`). Forecast defaults reject absolute water_level with
   `[-2, 20]` (`config/forecast_qc_rules.py:169`, plus the `negative_value` floor
   `value_min=-2.0` at `:150`/`:157`). Every path that runs a checker on absolute
   water_level must get the same `(station_id, parameter)`-keyed datum-subtract +
   skip-datum-dependent-rules-until-datum (the full skip set per design item 4), reading
   the same `water_level_datum_masl` metadata.
   Four call sites total (spelled out in §3):
   - **obs-ingest** — `ingest_observations._run_qc_task` (the primary fix).
   - **station-forecast** — `run_station_forecast` runs forecast QC per predicted
     parameter and **aborts on `QC_FAILED`** (`services/run_station_forecast.py:219`
     `check`, abort at `:222`, gate `:218-229`; checker instantiated at
     `run_forecast_cycle.py:814`).
   - **group-forecast** — the operational GROUP path
     (`services/run_group_forecast._build_station_result:248`) runs
     `qc_checker.check` at `:269` and **aborts the station's output on `QC_FAILED`** at
     `:272`, called from `flows/run_forecast_cycle.py:1363`. Same shift, same skip, same
     baseline + flag-detail handling as the station path.
   - **onboarding** — `services/onboarding.py` runs `Stage1QualityChecker.check` directly
     on raw obs (`:381`, before baseline computation at `:416`). The datum **is** available
     at onboarding (it is the metadata input), so apply the same shift + skip there too.
   Not deferred, not separate gates.

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

7. **Flag-detail honesty (obs AND forecast).** Detail strings embed the (shifted) value,
   which would read as relative while the stored/native value is absolute (e.g. "value 21
   outside …" vs a stored ~282 m). When a datum shift is applied, augment the water_level
   flag detail with **raw + relative + datum** so diagnostics are not a lie, at BOTH:
   - **obs QC** — `range_check`/`spike`/`gross_outlier` detail
     (`services/qc.py:66,160,187`), stored verbatim (`observation_store.py:119`, serialized
     at `:218`).
   - **forecast QC** — `negative_value`/`range_check`/`climatology_outlier` detail
     (`services/forecast_qc.py:40,62,170`), for shifted forecast flags on both the STATION
     and GROUP paths.

8. **Baseline delete/replace — a new `delete_baselines` on the Protocol, Pg store, and
   fake.** `gross_outlier` compares `obs.value` (now relative) with `baseline.rolling_mean`
   (`services/qc.py:167,181`), so water_level `ClimBaseline` rows must be recomputed in
   **relative** terms (`rolling_mean − datum`; `rolling_std` is datum-invariant). Both the
   Protocol and the Pg store only store/fetch today (`ClimBaselineStore` Protocol at
   `protocols/stores.py:685`; `PgClimBaselineStore` at `clim_baseline_store.py:16,44`), so
   add `delete_baselines(station_id, parameter)` to the `ClimBaselineStore` **Protocol**
   (`protocols/stores.py:685`), the **`PgClimBaselineStore`** impl, and the **fake**
   (`FakeClimBaselineStore`, `tests/fakes/fake_stores.py:1182`), plus integration tests.
   Run the delete **before** storing the relative recompute, else stale absolute rows
   survive and flag every relative obs as a gross outlier. (In v0 water_level was 100 %
   rejected, so likely no water_level baselines exist yet — audit and document explicitly.)

   **Onboarding builds baselines too — they must be RELATIVE from the start.** Onboarding
   QC (`services/onboarding.py:381`) is immediately followed by fetching the QC_PASSED obs
   (`:409`) and `compute_clim_baselines(qc_passed, …)` (`:416`), and
   `compute_clim_baselines` uses raw `obs.value` directly (`services/baselines.py:23`). So
   a new water_level station would store **absolute** baselines that later get compared to
   **shifted relative** QC values — a half-shifted comparison that flags every obs. Fix:
   - **Datum present at onboarding:** compute water_level baselines from datum-**shifted**
     shadow observations — subtract the station datum from the water_level obs (the same
     `(station, parameter)`-keyed shadow-copy helper used on the QC path) **before**
     `compute_clim_baselines`, so the stored baseline is relative. (Prefer shifting the obs
     input to one consistent shadow-copy path over post-hoc adjusting `rolling_mean`.)
   - **Null datum at onboarding:** **SKIP water_level baseline creation** until the datum
     exists (a null-datum station has no relative frame to build a baseline in), consistent
     with the null-datum skip of `gross_outlier` in design item 4. When the datum is later
     filled via `update_station`, delete + recompute the water_level baselines
     (`delete_baselines(station_id, parameter)` + a relative recompute) — see §5 rollout.

9. **`qc_rule_version` resolved ONCE** at the top of `_run_qc_task`, before the flags
   loop (`ingest_observations.py:179-189`): for water_level, `version = "1.1-datum" if
   datum is not None else "1.1-datum-skip"` (the skip path per design item 4); for other
   parameters (no datum concept) keep `"1.0"`. Pass it to every `update_qc` call in the
   loop (currently the hardcoded literal `"1.0"` at `:183`). Constant per invocation — do
   not branch per-flag.

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
(step 13), which runs after datum values are written. The slice fixes **all four QC call
sites** — obs-ingest (5–6), station-forecast (8), group-forecast (9), onboarding (4).

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

4. **Onboarding datum input + onboarding QC + onboarding baselines**
   (`config/onboarding.py`, `adapters/camelsch_adapter.py`, `services/onboarding.py`):
   - **Datum input surface (TOML).** Extend `OnboardingConfig` (`config/onboarding.py:14`)
     with a `water_level_datums_masl: dict[str, float]` field and parse the
     `[onboarding.water_level_datums_masl]` table (keyed by station code) in
     `load_onboarding_config` (`:37`). Apply it by `code` in `attributes_to_station`
     (`adapters/camelsch_adapter.py:145`; `return StationConfig(...)` at `:179`), setting
     `water_level_datum_masl` from the table (`None` when the code is absent). Provenance =
     operator-supplied surveyed BAFU Pegelnullpunkt.
   - **Apply the datum-shift + skip-datum-dependent-rules-until-datum to the onboarding QC
     pass.** `onboarding.py` runs `Stage1QualityChecker.check` directly on raw obs (`:381`,
     before baseline computation at `:416`); the datum is available (the metadata input), so
     build the same `(station_id, "water_level")`-keyed datum-shifted shadow copies before
     `.check` there too, and skip the datum-dependent rules (`range_check`, `gross_outlier`)
     when the datum is null (design item 4). Without this, onboarding re-introduces the
     100 %-rejection on the very obs it is validating.
   - **Onboarding baselines must be RELATIVE.** `compute_clim_baselines` (`:416`, using raw
     `obs.value` at `services/baselines.py:23`) must receive datum-**shifted** water_level
     obs when a datum exists, and be **skipped for water_level when the datum is null**
     (design item 8). Without the shift, onboarding stores absolute baselines that
     `gross_outlier` later compares to shifted relative values.

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
   - When `datum is None` → **skip the datum-dependent rules** for water_level — on the obs
     path `range_check` and `gross_outlier` (design item 4); still run + aggregate the
     datum-invariant rules. Do NOT widen the shared bound and do NOT mark a fictional
     "unknown" (no such status exists).
   - Pass the shadow list to `checker.check(overrides=[])`; the returned
     `dict[ObservationId, list[QcFlag]]` keys onto the real IDs, so the `raw_ids` filter +
     `update_qc` at `:179-189` are unchanged and the stored DB value is never touched.
   - Resolve the version once, before the flags loop — for water_level
     `"1.1-datum" if datum is not None else "1.1-datum-skip"`, else `"1.0"` — and pass
     `qc_rule_version=version` to every `update_qc` (`:183`).
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

8. **Forecast-side datum-subtract — STATION path + signature cascade** (design item 5).
   Apply the same `(station_id, parameter)`-keyed shift before the checker at
   `services/run_station_forecast.py:219` (abort gate `:218-229`), reading
   `water_level_datum_masl`; when the datum is null, skip the forecast datum-dependent
   rules — `range_check`, `negative_value` (`config/forecast_qc_rules.py:150,157`), and
   `climatology_outlier` (`services/forecast_qc.py:170`) — per design item 4. Do NOT widen
   `config/forecast_qc_rules.py` (`:169`, `:150`/`:157`) — same shared-rule-set argument as
   obs QC. **Signature cascade:**
   `run_station_forecast` receives `station_id: StationId`, not the datum
   (`services/run_station_forecast.py:344`), so thread the datum (or the resolved shift)
   through the chain `_run_single_model` (`:92`, `station_id` at `:93`) →
   `run_all_station_forecasts` (`:283`, `station_id` at `:284`) → `run_station_forecast`
   (`:344`), and update both call sites in the cycle flow — `run_station_forecast` at
   `flows/run_forecast_cycle.py:1063` and `run_all_station_forecasts` at `:1118` — plus
   the affected unit tests. Also augment forecast flag detail (design item 7,
   `forecast_qc.py:40,62,170`).

9. **Forecast-side datum-subtract — GROUP path + signature cascade** (design item 5,
   BLOCKER-fold). The operational GROUP path
   (`services/run_group_forecast._build_station_result:248`) runs `qc_checker.check` at
   `:269` and aborts the station's output on `QC_FAILED` at `:272`, called from
   `flows/run_forecast_cycle.py:1363`. Apply the identical `(station_id, parameter)`-keyed
   shift, null-datum skip of the forecast datum-dependent rules (`range_check`,
   `negative_value`, `climatology_outlier`; design item 4), baseline handling, and forecast
   flag-detail honesty here, threading the datum through `run_group_forecast` (`:335`) →
   `_build_station_result` (`:248`), and update the call site + affected unit tests —
   parallel to the STATION cascade.

10. **Flag-detail honesty (obs + forecast)** — augment water_level flag detail with raw +
    relative + datum when shifted, at obs QC
    (`services/qc.py:66,160,187`; stored via `observation_store.py:119`, serialized `:218`)
    AND forecast QC (`services/forecast_qc.py:40,62,170`, STATION + GROUP). Per design
    item 7.

11. **Baseline delete/replace — Protocol + Pg + fake.** Add
    `delete_baselines(station_id, parameter)` to the `ClimBaselineStore` Protocol
    (`protocols/stores.py:685`), the `PgClimBaselineStore` impl
    (`clim_baseline_store.py:16,44` only store/fetch today), and the fake
    `FakeClimBaselineStore` (`tests/fakes/fake_stores.py:1182`), with integration tests.
    Run it before storing the relative recompute (design item 8). The recompute itself runs
    in the backfill script.

12. **`qc.rejected` observability event** — inside the flags loop
    (`ingest_observations.py:179-189`), per design item 11.

13. **Backfill script** (one-shot admin script, `uv run python3 << 'EOF'` per CLAUDE.md —
    NOT a new flow): (a) delete + recompute any water_level `ClimBaseline` rows from the
    relative series (via the new `delete_baselines` + `store_baselines`); (b) re-QC the
    existing water_level rows on datum-shifted shadow copies (`dataclasses.replace`, keyed
    by the unchanged `ObservationId`), writing `qc_rule_version="1.1-datum-reqc"` so the
    re-QC pass is distinguishable from the live passes. 622 rows one-by-one via the existing
    single-row `update_qc` in one transaction is fine — no `bulk_update_qc` API needed at
    this size.

---

## 4. Tests

Part of the datum-subtract PR:

- **Fixtures:** add optional `water_level_datum_masl: float | None = None` to
  `make_station_config` (`tests/conftest.py:83`, `StationConfig(...)` at `:108`) and pass
  it through. `FakeStationStore` (`tests/fakes/fake_stores.py:794`) needs no change — it
  stores/returns `StationConfig` verbatim and the new field is a defaulted attribute.
- **`_run_qc_task`:** (i) datum applied → water_level QC'd on relative stage;
  (ii) NULL datum → **the datum-dependent rules are skipped** (`range_check` and
  `gross_outlier`); the datum-invariant rules still run and the obs aggregates to a real
  status (NOT a fictional "unknown"), and `qc_rule_version == "1.1-datum-skip"`; (iii) a
  `MISSING` (`value=None`) obs in the context window passes through unchanged (regresses
  the `TypeError` blocker); (iv) `qc_rule_version` is `"1.1-datum"` when a datum is applied;
  (v) **the water_level datum is NOT applied to the same station's discharge QC** — a
  station with `water_level_datum_masl` set, run through the `(station_id, "discharge")`
  path, produces flags identical to `datum=None` (regresses the discharge-corruption
  blocker).
- **`_apply_spike` dispatch:** (a) `thresholds={"max_delta": 1.0}` → deviation >1 m flags,
  <1 m does not; (b) `thresholds={"tolerance": 0.1}` → the legacy percentage form still
  applies (discharge regression guard).
- **Station-forecast:** datum-subtract applied on the `run_station_forecast` QC path (with
  the datum threaded through the signature cascade); NULL datum → `range_check` skipped.
- **Group-forecast:** datum-subtract applied on the `run_group_forecast` /
  `_build_station_result` QC path; the water_level datum is NOT applied to the discharge
  ensemble; NULL datum → `range_check` skipped.
- **Onboarding datum input:** `load_onboarding_config` parses
  `[onboarding.water_level_datums_masl]` and `attributes_to_station` applies it by station
  code (code present → `water_level_datum_masl` set; code absent → `None`).
- **Onboarding QC:** the onboarding `Stage1QualityChecker.check` pass applies the
  datum-shift so onboarded water_level obs are not re-rejected; NULL datum → skip the
  datum-dependent rules.
- **Onboarding baselines:** with a datum, `compute_clim_baselines` receives shifted obs so
  the stored water_level baseline is relative; with a null datum, water_level baseline
  creation is skipped.
- **Forecast flag-detail:** a shifted forecast water_level flag detail carries raw +
  relative + datum.
- **Baseline `delete_baselines`:** the fake and Pg store delete only the targeted
  `(station_id, parameter)` rows; a delete-then-store yields no stale absolute rows.
- **Backfill:** re-QC of existing water_level rows produces the expected pass/skip and the
  `"1.1-datum-reqc"` version.

Any test that hits `/observations.json` or the reflected schema is an **integration** test,
not a unit test (`get_reflected` runs `MetaData.reflect`).

---

## 5. Backfill & rollout

1. Ship the datum-subtract slice (surface §3 items 1–12) hold-at-PR with a version bump.
2. Write the known BAFU gauge-zero datums for the already-onboarded 2009/2091 via
   `update_station` (they are live rows — `store_station`'s `sa.insert` would PK-conflict).
3. Run the one-shot backfill script (§3 item 12): delete + recompute any water_level
   baselines in relative terms first, then re-QC the 622 existing water_level rows on
   datum-shifted shadow copies, writing `qc_rule_version="1.1-datum-reqc"` for audit.
4. **Datum-fill / re-survey path (`update_station`):** whenever a station's
   `water_level_datum_masl` transitions null→value (first fill for a station onboarded
   without a datum) or value→value (a re-survey), **delete + recompute the water_level
   `ClimBaseline` rows** (`delete_baselines(station_id, "water_level")` + a relative
   recompute) and re-QC the existing water_level rows against the (revised) datum. This
   covers a station that onboarded with a null datum (baselines were skipped, design item 8)
   and only later received one. Explicit, not silent drift.

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
- **cm→m unit normalization for DHM/v1** — out of scope. v0/BAFU water_level is
  metres-based (m a.s.l.). DHM/v1 stations may report **cm** or **m above ground**;
  normalizing to canonical metres is an **adapter-boundary** responsibility (configured
  from the onboarding `water_level_unit` metadata, applied at BOTH the historical import
  and the operational ingest — a cm gauge reports cm on every reading, not just once).
  This plan only adds the metres-assumption **guard** (§2 boundary note); the conversion
  itself is tracked for the **DHM-adapter / recap-DG-client v1 track**. Once units are
  normalized at the boundary, this plan's datum-subtract needs no change.

---

## ⚠ Residuals for implementation

All prior review findings are **RESOLVED in this spec** — GROUP-forecast QC, null-datum
status/version semantics (now the full datum-dependent skip set), forecast flag-detail
honesty, in-slice onboarding QC, the `delete_baselines` Protocol/fake surface, the
onboarding relative-baseline fix, and the onboarding datum **input** surface (the
`[onboarding.water_level_datums_masl]` TOML table — which also resolves the earlier BAFU
Pegelnullpunkt provenance residual). The only remaining residual is a numeric input, not a
design fork:

- **`N_10min` / `N_daily` spike `max_delta` values.** Provisional `1.0 m` for 10-min;
  `N_daily` unspecified ("distinct larger" — a 1 m/10-min cap does not translate to
  1 m/day). Pin both numeric values at implementation.
