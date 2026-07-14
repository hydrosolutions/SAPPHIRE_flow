---
status: DRAFT
created: 2026-07-14
plan: 115b
parent: 115
title: Forcing pipeline — self-derived MeteoSwiss series (RhiresD), supersession, and a live Flow 6
scope: Build the forcing data path properly. Re-authored 2026-07-14 after §0 was decided.
depends_on: [115a]
blocks: [115c]
---

# Plan 115b — The forcing pipeline

> Shared context and D1/D3 live in the umbrella: [Plan 115](115-weather-source-identity-model.md).
> **D2 is superseded by §0 below.**

## Status

**DRAFT — re-authored 2026-07-14.** Depends on **115a**.

This plan was originally "make Flow 6 reachable and flip the hybrid default." The audit and the §0
investigation changed what it is: **it now builds the forcing data path, because there has never been
one.**

> ## 🔴 THE AUDIT (2026-07-14, staging)
>
> ```
>   source   | count |   first    |    last
> -----------+-------+------------+------------
>  camels-ch | 58440 | 1981-01-01 | 2020-12-31
> ```
>
> **One source. Frozen at 2020-12-31.** No MeteoSwiss rows — ever. The scheduled
> `ingest-weather-history` deployment has **never stored a single row in production**, while reporting
> green for its entire operational life: no station carries the binding it selects on, so it matches
> zero stations and returns `0/0/0` **as a success** (`ingest_weather_history.py:309`).
>
> `58,440 = 2 stations × 14,610 days × 2 parameters` — so the archive holds **only** precipitation and
> temperature. **No `temperature_min`/`temperature_max` exist at all**, yet Plan 072 chains them to a
> `CAMELS_CH` fallback tier that contains no such rows (`hybrid_reanalysis_factories.py:29-30`).
>
> **This is a first implementation, not a repair.** It has not yet caused an incident only because
> today's models declare no past-dynamic weather features. That is luck. Nepal's models will need this.

## §0 — LOCKED DECISION (owner, 2026-07-14): the self-derived MeteoSwiss series

**Supersedes umbrella D2.** The question §0 answers: *CAMELS-CH forcing ends 2020-12-31 — what series
do we actually feed the models, and is it homogeneous end to end?*

### The facts that forced this

- **CAMELS-CH forcing provenance, CONFIRMED** — Höge et al. 2023, *ESSD* 15, 5755, App. A1.2
  ("MeteoSwiss data products"): precipitation = **`RhiresD`**, temperature = **`TabsD`**, sunshine =
  `SrelD`. *(The repo never recorded this. That omission is what let the bug through.)*
- **Flow 6 ingests `RprelimD` for precipitation** (`meteoswiss_open_data_reanalysis.py:83`) — the
  **preliminary** product (automatic stations only), **not** `RhiresD` (definitive, full network incl.
  manual collectors). They differ **systematically**.
- **Plan 071's founding premise is FALSE.** It claims (`071:66-68,117`) that daily `RhiresD` is not in
  open data and "requires commercial delivery". **Verified against the live STAC API** in
  `ch.meteoschweiz.ogd-surface-derived-grid` — *the collection the adapter already points at*:

```
rhiresd    71 files   1961-01-01 .. 2026-05-31   <- DEFINITIVE precip, free; MONTHLY publication (~3-6 week lag)
rprelimd   60 files   2026-05-15 .. 2026-07-13   <- PRELIMINARY, live tail ONLY
tabsd     131 files   1961-01-01 .. 2026-07-13
tmind     121 files   1971-01-01 .. 2026-07-13
tmaxd     121 files   1971-01-01 .. 2026-07-13
sreld     120 files   1971-01-01 .. 2026-07-13
```

`RhiresD` and `RprelimD` are **complementary, not alternatives**: `RprelimD` exists *solely* to cover
the window `RhiresD`'s publication lag has not yet reached.

### The decision

> **Derive the entire forcing series ourselves, through OUR basin polygons:**
> **`RhiresD` + `TabsD` + `TminD` + `TmaxD`, from 1981-01-01 → the latest published `RhiresD` date,
> with `RprelimD` for the live tail ONLY — and `RhiresD` taking PRIORITY over it once published.**
>
> **CAMELS-CH forcing becomes a VALIDATION REFERENCE, not a data tier.**

Because `RhiresD` reaches back to **1961**, it fully covers CAMELS' own 1981-2020 window — **so there
is no splice anywhere.** This kills all three consistency axes at once:

| axis | how it dies |
|---|---|
| **Product identity** | One product per parameter, end to end. The only product boundary is `RhiresD`↔`RprelimD` at the live tail — and it is **transient**: once `RhiresD` publishes for those dates, the priority chain prefers it. |
| **Spatial aggregation** | **Our** polygons, **our** `exactextract`, throughout. CAMELS computed its basin means with *its* polygons and method; that mismatch simply leaves the series. |
| **Definitive over preliminary** | Handled by the **hybrid PRIORITY chain** (`RHIRESD → RPRELIMD`), **not** by `version` supersession — see §3, which corrects an error in an earlier revision. Both rows coexist; the reader decides. |

**Backfill depth: 1981-01-01** (owner decision) — matching CAMELS' span exactly, so the validation
experiment (§8) compares **40 full years** of our basin means against CAMELS'. *(`RhiresD` offers 1961;
the extra 20 years are declined for now because they have no CAMELS counterpart to validate against,
and `TminD`/`TmaxD` only begin in 1971 regardless. Revisit if more training depth is wanted.)*

**The priority chain becomes** — per parameter:

```
precipitation:     RHIRESD → RPRELIMD          (definitive supersedes preliminary)
temperature:       TABSD
temperature_min:   TMIND
temperature_max:   TMAXD
```

**No `CAMELS_CH` tier.** *(Plan 072's `… → CAMELS_CH` chains are retired; see the annotation on 072.)*

**What CAMELS still provides**, and keeps providing: static attributes, **basin polygons**, and the
discharge record. Only its **forcing** is superseded.

### The irreducible residual — name it, measure it, declare it

For the most recent window, inference runs on **preliminary** precipitation while the model was trained
on **definitive**. **No real-time system can escape this** — definitive data does not exist in real time.

**That window is LARGER than first assumed.** `RhiresD` publishes **monthly, around the 25th of the
following month** (a 3-6 week lag), so the preliminary window swings between roughly **25 and 55 days**
and can approach **8 weeks** just before a publication. Every operational forecast in that window runs
on preliminary precipitation.

> **✅ OWNER DECISION (2026-07-14): ACCEPTED.** All operational forecasts run on preliminary
> precipitation for the current window, and **no extra API flag is required** — a forecast does not need
> to advertise that its recent forcing is preliminary. This is now **policy, not an open risk.**
>
> It is still **measured** (§8's live-tail comparison — the one part of §8 with no confounds), because
> knowing the size of the residual is worth having even when the residual is accepted. Measured and
> declared; not flagged per-forecast.

## Scope

### 1. Adapter — add `RhiresD`, and teach it the archive asset family

`MeteoSwissOpenDataReanalysisAdapter` today fetches **per-day** STAC features
(`_fetch_day_feature(day_iso)`) and knows four products, precipitation being `RprelimD`
(`_PRODUCT_REGISTRY`, `:81-106`).

Two changes:

- **Add `RhiresD`** as the precipitation product (`ForcingSource.METEOSWISS_RHIRESD`,
  `raw_var="RhiresD"`, token `rhiresd`). `RprelimD` **stays** — it is the live-tail product, not a
  mistake to be deleted.
- **Support the `archive` asset family — and this is bigger than it looks.** The historical files are
  **per-year NetCDFs** (`…-archive.rhiresd_ch01h.swiss.lv95_19810101000000_19811231000000.nc`), not
  per-day features. The current adapter queries **per-day items only**
  (`meteoswiss_open_data_reanalysis.py:174`), and `_asset_href` returns **the first matching product
  asset with no year/span selection at all** (`:231`) — so it cannot even *address* the right archive
  file. The backfill (§2) needs a year-file path; the daily operational path stays as-is.

  **Required before this is trusted:** a **real archive fixture** (a genuine LV95 NetCDF, not a
  synthetic one) and/or a live-gated smoke test proving **asset selection by year, variable names,
  dimensions, CRS normalisation, and `exactextract` compatibility**. The existing tests use
  **synthetic lat/lon NetCDFs** (`test_meteoswiss_open_data_reanalysis.py:89`) which prove nothing about
  the real files — the adapter could pass every test and still be unable to read a single archive file.

**Grid note — CORRECTED (an earlier revision had this wrong).** It is **not** "rhiresd is `ch01h`, the
others `ch01r`". Per the MeteoSwiss product docs:

| product | grid | note |
|---|---|---|
| `RhiresD` | **`ch01h`** | precipitation |
| `RprelimD` | **`ch01h`** | precipitation — **same grid family as RhiresD** |
| `TabsD` / `TminD` / `TmaxD` | **`ch01r`** | temperature |

So the split is **precipitation (`ch01h`) vs temperature (`ch01r`)**, not definitive-vs-preliminary.
That is *convenient* — the two precipitation products share a grid, so the chain
`RHIRESD → RPRELIMD` composes over one geometry. But the CRS/geometry path **must** handle both
families, or the extraction is silently wrong. **Verify against real archive files; do not assume.**

### 2. Backfill — derive 1981 → present through our polygons

A one-shot, resumable backfill producing `historical_forcing` rows for **every operational station**:

| source tag | parameter | span |
|---|---|---|
| `meteoswiss_rhiresd` | precipitation | 1981-01-01 → **R** *(see below)* |
| `meteoswiss_tabsd` | temperature | 1981-01-01 → T-1d |
| `meteoswiss_tmind` | temperature_min | 1981-01-01 → T-1d |
| `meteoswiss_tmaxd` | temperature_max | 1981-01-01 → T-1d |
| `meteoswiss_sreld` | *(relative sunshine duration)* | 1981-01-01 → T-1d |
| `meteoswiss_rprelimd` | precipitation | **R** → T-1d *(live tail)* |

> **✅ OWNER DECISION (2026-07-14): include `SrelD` now.** CAMELS-CH itself uses relative sunshine
> duration (Höge et al., App. A1.2), no model requires it *yet*, and it is free (1971 → present). Adding
> it to this backfill costs one more product; adding it **later** costs a **40-year re-run**. Take it now.
>
> **Implementation note:** it needs a canonical parameter string — the repo currently knows only
> `precipitation`, `temperature`, `temperature_min`, `temperature_max`. Align the new name with the
> ForecastInterface / model-requirement vocabulary **before** writing rows; a parameter name is a
> contract, and renaming it later means re-writing the archive.

> **⚠️ `T-45d` was a made-up constant and is RETRACTED.** Per the product docs, **`RhiresD` publishes
> MONTHLY — typically around the 25th of the *following* month** (a 3-6 week lag), while `RprelimD` is
> daily at D+1. So the boundary **R** is not a fixed offset: it **swings between roughly 25 and 55
> days** and jumps at each monthly publication.
>
> **R must be DISCOVERED, not assumed** — query the STAC collection for the latest available `RhiresD`
> date and derive the tail from it. A hardcoded offset would silently either (a) skip days RhiresD
> already covers, or (b) request days it does not yet have.
>
> **And note what this does to the residual (§0):** the preliminary window is **up to ~8 weeks**, not
> ~45 days. That makes §8's live-tail measurement *more* important, not less.

**Eligibility:** "every operational station" means **every station with a valid basin polygon** —
`ExactExtractGridExtractor` raises when a station has no usable geometry
(`exact_extract_grid_extractor.py:64`). Enumerate eligible stations explicitly; do not assume the two
sets coincide.

**Scale — and the current path CANNOT do it.** Rows ≈ `stations × days × parameters`: at 2 stations ×
16,436 days × 4 = ~131k rows (trivial), but **at the v0 target of ~1000 stations that is ~66M rows.**
The existing path is **all-in-memory end to end** and will not survive that:

- the adapter returns **one full `list[RawHistoricalForcing]`** (`meteoswiss_open_data_reanalysis.py:134`);
- Flow 6 stores **only after the entire fetch completes** (`ingest_weather_history.py:318`);
- the store **copies the whole input** before batching into 5k-row SQL chunks
  (`historical_forcing_store.py:35`).

**So the backfill needs its own chunked path**, not a large call to the existing one:
**work unit = (product, year, station-batch)**, with **per-chunk persistence** and **resumable gap
detection** (re-run and it fills only what is missing). It is a batch job, not a request path — and it
must be interruptible, because it will be interrupted.

### 3. Definitive-over-preliminary is a PRIORITY decision, not version supersession

> **⚠️ CORRECTED — an earlier revision of this plan was WRONG here, and the error was load-bearing.**
>
> It claimed `historical_forcing`'s `version` column would make an `RhiresD` row **supersede** an
> `RprelimD` row. **False.** Verified:
> - `fetch_forcing` filters **one `source` at a time** (`historical_forcing_store.py:65`) and partitions
>   its latest-version rows **BY `source`** (`:91`);
> - the natural key **includes `source` and `version`** (`db/metadata.py:413`), so two rows differing
>   only by source are **both legal and both retained**.
>
> `version` supersession operates **within** a source (a reprocessed `RhiresD` v2 superseding
> `RhiresD` v1). It **cannot** express "RhiresD beats RprelimD" — those are different sources.

**Cross-source precedence is `HybridForcingSource`'s priority chain** (`hybrid_reanalysis.py:66`), and
nothing else. So:

- Both rows **coexist** in `historical_forcing` for an overlapping date — that is correct and desirable
  (the preliminary value remains auditable; provenance is never destroyed, per D1).
- **The reader decides.** The chain `precipitation: RHIRESD → RPRELIMD` means: if a definitive row
  exists for that `(station, valid_time)`, it wins; otherwise the preliminary one is used.
- **Version supersession stays wired for its real job:** MeteoSwiss *reprocesses*, so a re-issued
  `RhiresD` must supersede an earlier `RhiresD` for the same date. That is within-source, and the
  existing mechanism handles it — keep it.

**Consequence for the backfill:** it does **not** need to delete or overwrite preliminary rows when the
definitive data lands. It simply ingests `RhiresD` for those dates, and the priority chain does the rest.
*(This is simpler than the mechanism the earlier draft invented.)*

### 4. The MeteoSwiss binding — BIND FIRST, and for EXISTING stations

> **⚠️ ORDERING (review finding): the binding must exist BEFORE the backfill runs.** The adapter only
> processes configs that declare its own `nwp_source` (`meteoswiss_open_data_reanalysis.py:145`), and
> Flow 6 selects its configs **from stored bindings** (`ingest_weather_history.py:243`). So a backfill
> that runs through the production adapter/flow with no binding in place **does nothing — and reports
> success**, which is the exact failure this plan exists to end.
>
> Either **bind first**, or have the backfill **construct its eligible basin-average configs explicitly**,
> outside the production selector. Do not leave the order implicit.

Creating the binding at onboarding does **nothing** for the deployed fleet — onboarding runs at
*station onboarding*, not at deploy. A **one-shot data backfill** must insert it for every eligible
existing station. **Pin all four fields**; the adapter's match
(`meteoswiss_open_data_reanalysis.py:155-162`) requires all of them:

| field | value |
|---|---|
| `nwp_source` | `meteoswiss_open_data_reanalysis` |
| `role` | `REANALYSIS` (115a) |
| `status` | `ACTIVE` |
| `extraction_type` | `BASIN_AVERAGE` |

Onboarding also gains the binding, so both paths agree.

> **⚠️ But a binding alone does NOT give a new station any data (review finding).** Onboarding still
> imports **CAMELS forcing only** (`camelsch_adapter.py:339`) and creates the CAMELS binding from
> `forcing[0].source` (`onboarding.py:364`). A newly-onboarded station would therefore get a MeteoSwiss
> **binding** and **zero MeteoSwiss rows** — no 1981-present series at all — while the one-shot fleet
> backfill (§2) only covers stations that existed when it ran.
>
> **So onboarding must run the per-station MeteoSwiss backfill** before the station is promoted to
> operational / trainable — or explicitly hold the station out until it has. Otherwise every station
> onboarded after this plan lands is silently forcing-less, and we have rebuilt the same class of bug in
> a new place.

> **✅ OWNER DECISION (2026-07-14) — retire CAMELS for weather, keep it for runoff.**
>
> - **The `camels-ch` weather-source binding is RETIRED.** CAMELS is no longer a forcing/NWP data tier,
>   so a `station_weather_sources` row pointing at it describes a source we no longer read. Remove it
>   (migration + onboarding stops writing it). Post-115b a station carries exactly two weather bindings:
>   `meteoswiss_open_data_reanalysis` (REANALYSIS) and `icon_ch2_eps` (FORECAST) — clean, and exactly
>   the shape 115a's role model expects.
> - **CAMELS remains the source of runoff/discharge observations**, plus static attributes and the basin
>   polygons. Nothing there changes. Only its role as a *weather* source ends.
> - **The CAMELS forcing ROWS in `historical_forcing` are NOT deleted** — they stay as the §8 validation
>   reference, and as an audit trail of what models were previously trained on. They are simply absent
>   from the priority chain, so no reader will ever select them.
>
> *(Retiring the binding while keeping the rows is coherent precisely because of D1: the binding says
> "where do I get data", the provenance tag says "where did this number come from". The rows keep their
> provenance; the station simply stops being bound to that source.)*

### 5. Fix hybrid's silent parameter drop — BEFORE the default flip

`hybrid_reanalysis.py:66-72`:

```python
chain = self._priority.get(key[2], ())          # key[2] is the parameter
winner = next((by_source[t.value] for t in chain if t.value in by_source), None)
if winner is None:
    continue                                     # <-- row silently discarded
```

A parameter with **no configured chain is silently dropped**. `StoreBackedReanalysisSource` — today's
default — passes *any* parameter through. So flipping the default as-is is a **silent data-loss
regression**.

**The rule (decided — a READY plan may not defer this to the implementer):**

> A requested parameter with **no configured priority chain** raises `ConfigurationError` — **unless
> exactly one source is configured for that parameter**, in which case that source wins.

"Pass through" is ambiguous when two sources return the same unconfigured parameter: with no chain to
break the tie it would pick **nondeterministically** — the `_select_nwp_source` bug all over again.
Raising is the only deterministic answer. Requires an **overlap test**.

### 6. Flip the reanalysis default to `hybrid`

`config/deployment.py:111`. **Only after §5.** `tests/unit/config/test_deployment_reanalysis_source.py:25`
locks the `"single"` default and must be updated deliberately.

Under §0 the reader must resolve `RHIRESD → RPRELIMD` and merge the four parameters — that is exactly
what `HybridForcingSource` does. It remains the right component; **only its chain contents change.**

#### 6a. Distribution-shift gate — the flip changes what a model is fed

*(Plan 072 §175 already recorded this risk and we were about to walk past it.)*

The same path serves training (`training_data.py:177`), hindcast (`hindcast.py:292`) **and** the live
forecast cycle's past-dynamic inputs (`operational_inputs.py:327`) — so a shift hits fitted artifacts
and live inference together. Numbers still arrive, the flow still goes green, and the forecast is
quietly wrong.

**Before the flip:** enumerate **active** model artifacts and their `past_dynamic`/`future_dynamic`
requirements; determine whether the flip re-sources any feature they consume; **retrain on the new
series, or hold the flip for those stations.** Review suggests today's models are *probably* unaffected
(native/fallback models declare no past/future dynamic features, `linear_regression_daily.py:54`; the FI
NWP model needs only *future* precip/temperature, `nwp_regression.py:126`) — **that is an inference, not
a fact.** Confirm against the live artifact/assignment tables.

### 7. Make an empty Flow 6 loud

A scheduled ingest matching **zero** stations is a misconfiguration, not a no-op — and it is the
observability hole that hid a dead feed for the deployment's entire life.

`ingest_weather_history_flow` has **no** `pipeline_health_store` parameter
(`ingest_weather_history.py:256`), its production setup pulls only station/forcing/basin stores
(`:267`), and `PipelineCheckType` (`types/enums.py:151`) has **no** weather-history type. So this is
building, not wiring:

1. Add `WEATHER_HISTORY_INGEST` to `PipelineCheckType` (+ DB constraint, migration, `conventions.md`).
2. Thread `pipeline_health_store` into the flow and its production setup.
3. Record per run: **UNHEALTHY when `stations_targeted == 0`** (a config fault — the feed *cannot* be
   working), and when `stations_targeted > 0` but no new data landed over a full window (bound, but
   silent).

> **⚠️ `rows_stored` is NOT a count of inserted rows (review finding) — do not build the health check on
> it.** `_store_forcing_task` returns `len(records)` (`ingest_weather_history.py:230`) *after* an
> `on_conflict_do_nothing()` insert (`historical_forcing_store.py:52`). So a run that inserts **nothing
> because every row already existed** still reports a large `rows_stored` and looks perfectly healthy.
>
> **A stuck feed re-fetching the same window forever would be indistinguishable from a working one** —
> which is precisely the disease this section exists to cure. Use the **actual DB rowcount**, or assert
> that **`MAX(valid_time)` per source ADVANCES**. Health must be measured by *effect*, not by the size
> of the input we handed to the writer.

Two distinct failures, deliberately distinguished: **"nobody is bound to this feed"** vs **"the feed is
bound but silent."** Today both look identical — and both look like success.

### 8. 🔬 The reference comparison — our series vs CAMELS (run EARLY, before §6's flip)

> **⚠️ CORRECTED — this is NOT the clean positive control an earlier revision claimed.**
>
> That revision asserted: *"both are RhiresD-derived, so any difference is **purely** our aggregation
> method/polygons."* **False, and dangerously so** — a confounded control produces a difference you
> **cannot attribute**, which is worse than no control at all.
>
> **The confounds, named:**
> 1. **Grid resolution / vintage.** CAMELS-CH aggregated from **2 km** gridded products (Höge et al.
>    2023, ESSD §"Meteorological data"); today's MeteoSwiss open-data grids are **1 km**-scale. Same
>    product *name*, different grid.
> 2. **Reprocessing.** The `RhiresD` served today is not necessarily the vintage CAMELS consumed in
>    ~2022. MeteoSwiss reprocesses.
> 3. **Polygons.** CAMELS used **its own catchment delineations**. Whether ours are identical is
>    exactly what is unknown *(and note we source basin polygons FROM CAMELS — confirm whether they are
>    the same set it used for forcing aggregation, or a different one)*.
> 4. **Coverage masks and timestamp/day-boundary conventions** (UTC vs local, and which 24 h window a
>    "daily" value spans).
> 5. Any **gauge-undercatch or snow correction** CAMELS may apply that we would not.

**So this is a *whole-pipeline reference comparison*, not an attributable control.**

> ### ✅ OWNER DECISIONS (2026-07-14) — the confounds are ACCEPTED, not eliminated
>
> - **Grid discrepancies are accepted.** We will **not** attempt to source or reproduce CAMELS' grid
>   vintage. *(So §8 is a reference comparison by choice — the cheap option was taken deliberately.)*
> - **The aggregation method may differ slightly, and CAMELS' exact method is NOT currently known.**
>   Treat this as a **named, unquantified confound**. Do not pretend to have matched it.
> - **One confound IS eliminated, and it is the biggest one: the polygons are the same.** Our basins
>   **are CAMELS-CH's own catchment geometries** — parsed from the CAMELS shapefiles
>   (`camelsch_adapter.py:301` → `geometry_to_basin`) and stored at `onboarding.py:248`. We are not
>   comparing across different catchment delineations.
>
> ### Tolerances (owner, 2026-07-14)
>
> | difference | action |
> |---|---|
> | **≤ 5%** | pass |
> | **> 5%** | **FLAG** — report it, per basin, with the seasonal + intensity breakdown |
> | **> 20%** | **Escalate to the owner.** Not an automatic stop. |
>
> **Why >20% is not an automatic stop** (owner's reasoning, recorded so nobody "fixes" it later):
> **rainfall events genuinely differ between a 2 km and a 1 km grid.** A convective cell that is
> smeared across one 2 km cell is resolved differently at 1 km, so large per-event discrepancies are
> **physically expected**, not necessarily a pipeline bug. Even discrepancies above 20% may be
> legitimate. They must be **explained**, not shrugged off — and not silently accepted either.

Design it honestly:

- Compare our self-derived basin means against CAMELS' own — **same basins** (literally the same
  polygons), **same dates, 1981-2020** — precipitation (`RhiresD`) and temperature (`TabsD`).
- Report **per-basin bias, RMSE, and the seasonal + intensity structure** of the difference. Expect the
  precipitation differences to concentrate in high-intensity and winter/snow events — exactly where a
  flood-forecasting model is most sensitive, and exactly where the grid-resolution effect lives.
- Apply the tolerance table above.

**Separately — and this one IS clean:** quantify the **live-tail residual**, `RprelimD` vs `RhiresD`
over their overlap, same pipeline, same polygons, same grid, same vintage. **No confounds.** It is the
one genuinely attributable number in this plan, and it is worth having even though the residual itself
is now accepted policy (§0).

### 9. Fix the CAMELS binding's `extraction_type`

Onboarding writes the CAMELS binding as `POINT` (`onboarding.py:364`) while its forcing records are
`BASIN_AVERAGE` (`camelsch_adapter.py:130`). `extraction_type` is **already wrong in the database**. Fix
the write site **and** migrate existing rows. *(This is why 115a's backfill keys off the source name and
never off `extraction_type` — the field cannot be trusted until this lands.)*

### 10. Converter guards Plan 071 specified but never landed

`preprocessing/converters.py:17,46` — `basin_avg_to_records` / `point_forecast_to_records` must reject
reanalysis source tags, so a reanalysis row can never be written into the forecast table. Plan 071 §243
called for this; the code has no such check.

### 11. The dashboard forcing endpoint merges provenance streams

`api/routes/stations.py:452-490` reads `historical_forcing` directly and **ignores `source`**. Once
several provenance tags coexist for the same station/parameter — which is exactly what this plan
creates — it silently merges them into one series.

**Decide:** provenance-separated series (honest; makes a dark feed visible), or the same
hybrid-resolved view the models consume (consistent with what the forecast actually used).
**Recommend: hybrid-resolved, with the winning source shown per point** — the operator needs to see
*which* source won.

## Tests

- **The validation experiment (§8) is a gate, not a test** — but its outputs get pinned: a regression
  test asserts our basin-mean derivation is stable against a small committed fixture.
- **The double-dark regression:** with the MeteoSwiss binding present and `hybrid` default, rows written
  under product tags are readable **end to end** by the default consumer. *Must fail against today's
  wiring.*
- **Supersession (§3):** an `RhiresD` row **replaces** an `RprelimD` row for the same
  `(station, valid_time, parameter)`. *Must fail against the current unwired version column.*
- **Parameter drop (§5):** a parameter with no configured chain **raises**, and does not vanish. *Must
  fail against the current `continue`.*
- **Overlap (§5):** two sources returning the same unconfigured parameter **raise** — no
  nondeterministic winner.
- **Flow 6 against MIGRATED data** (§4), not a fresh fixture — an existing station gets the binding (all
  four fields pinned) and ingests, with `rows_stored > 0` asserted.
- **Flow 6 health (§7):** `stations_targeted == 0` records UNHEALTHY, not a green zero; and
  `stations_targeted > 0 && rows_stored == 0` over a full window likewise.
- **`temperature_min`/`temperature_max` now exist** — they resolve from `TminD`/`TmaxD`, with no CAMELS
  tier to fall back to.
- CAMELS binding `extraction_type` is `BASIN_AVERAGE`, at the write site and after migration.
- Converter guards reject a reanalysis tag.

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Deploy gate (do not skip):** after the flip, confirm on staging that `ingest-weather-history` reports
a **non-zero** `rows_stored`, and that `historical_forcing`'s `MAX(valid_time)` per source **advances**.
**A green flow is not evidence** — that is the entire lesson of this plan.

## Review deltas (independent Codex review, 2026-07-14) — verdict NOT-READY, all folded

The review **falsified two load-bearing claims** this plan had made. Both are corrected above; recording
them so they are not reintroduced:

1. **"`version` supersession makes RhiresD replace RprelimD"** — **FALSE.** `fetch_forcing` filters one
   `source` at a time and partitions latest-version rows **by source**; the natural key includes `source`,
   so both rows legally coexist. Cross-source precedence is the **hybrid priority chain**, full stop
   (§3). `version` supersession is for *within-source* reprocessing, and stays wired for that.
2. **"Differences vs CAMELS are purely our aggregation/polygons"** — **FALSE, and dangerous.** CAMELS
   aggregated from **2 km** grids with **its own** catchment delineations; today's open data is **1 km**.
   Add reprocessing vintage, coverage masks and timestamp conventions, and the "control" is confounded —
   it would produce a difference we **cannot attribute**. §8 is downgraded to an honest **reference
   comparison with named confounds and explicit tolerances**, and the owner must choose between that and
   a true (expensive) reproduction of CAMELS' grid/vintage/polygons.

Also corrected:

- **The grid note was simply wrong.** `RprelimD` is `ch01h` too — the split is **precipitation (`ch01h`)
  vs temperature (`ch01r`)**, not definitive-vs-preliminary.
- **`T-45d` was an invented constant.** `RhiresD` publishes **monthly (~25th of the following month)**, so
  the boundary swings ~25-55 days and must be **discovered**, not assumed. The preliminary window reaches
  **~8 weeks** — making §8's live-tail measurement more important, not less.
- **The archive path cannot even address the right file** — `_asset_href` takes the first product match
  with no year selection, and the tests use synthetic lat/lon NetCDFs. A real LV95 archive fixture is
  required before any of this is trusted.
- **The 66M-row backfill cannot use the existing all-in-memory path** (adapter returns one list; the flow
  stores only after the full fetch; the store copies the whole input). It needs chunked
  `(product, year, station-batch)` work units with per-chunk persistence and resumable gap detection.
- **Bind before backfill** — the adapter needs its binding to exist, or the backfill silently does nothing.
- **A binding alone gives a new station no data** — onboarding must run the per-station backfill, or the
  next station onboarded is silently forcing-less.
- **`rows_stored` is `len(records)` after `on_conflict_do_nothing`** — a stuck feed re-fetching the same
  window forever would look healthy. Health must be measured by effect (DB rowcount / advancing
  `MAX(valid_time)`).
- **Minors:** add `METEOSWISS_RHIRESD` to `types/forcing_sources.py` and the converter guard constants
  (`preprocessing/converters.py:17`), not just the docs; "every operational station" means **every station
  with a valid basin polygon**; and this plan still needs the repo-required phase/task JSON dependency
  graph (`docs/workflow.md:24,49`) before it goes READY.

**Confirmed sound:** CAMELS-CH's RhiresD/TabsD/SrelD provenance; RhiresD being final daily precip
1961-present with a 3-6 week lag and RprelimD being preliminary and later superseded; the `CH archive`
yearly NetCDF + EPSG:2056 structure; and hybrid's silent parameter drop.

## Doc sync

- `docs/v0-scope.md §A12` and `docs/architecture-context.md:140,574` — these describe CAMELS-CH as *the*
  v0 training-forcing source. Under §0 it is a **validation reference**; the forcing series is
  self-derived from MeteoSwiss. **Record the provenance explicitly** (`RhiresD`/`TabsD`/`TminD`/`TmaxD`,
  our polygons) — the absence of exactly this note is what let the whole bug through.
- `docs/conventions.md` — the `ForcingSource` values, incl. the new `METEOSWISS_RHIRESD`.
- Annotations on **071** (falsified premise) and **072** (three defects) are already in place.

## Dependency graph

Ordering is not cosmetic here. Two hard constraints, both from the review:

- **Bind before backfill** — the adapter only processes configs declaring its own `nwp_source`, so a
  backfill with no binding in place does nothing *and reports success*.
- **Fix the parameter drop before the flip** — flipping to `hybrid` while it still silently discards
  unconfigured parameters is a data-loss regression.

And one from judgement: **the reference comparison (§8) runs BEFORE the flip**, not after. There is no
point re-sourcing every model's features and *then* asking whether the new series is sane.

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Adapter: RhiresD + SrelD + the archive asset family",
      "tasks": ["1A-products", "1B-archive-asset-selection", "1C-real-lv95-fixture", "1D-dynamic-rhiresd-boundary"],
      "parallel": false,
      "depends_on": ["plan-115a"]
    },
    {
      "id": "phase-2",
      "name": "Bindings first (existing fleet + onboarding + retire camels-ch weather binding)",
      "tasks": ["2A-backfill-meteoswiss-binding", "2B-onboarding-writes-binding", "2C-retire-camels-weather-binding", "2D-camels-extraction-type-fix"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "name": "Chunked, resumable backfill 1981 -> present through our polygons",
      "tasks": ["3A-chunked-work-units", "3B-per-chunk-persistence", "3C-resumable-gap-detection", "3D-eligible-stations-only"],
      "parallel": false,
      "depends_on": ["phase-2"]
    },
    {
      "id": "phase-4",
      "name": "Reference comparison vs CAMELS + the clean live-tail measurement",
      "tasks": ["4A-basin-mean-comparison-1981-2020", "4B-tolerance-report-5pct-20pct", "4C-live-tail-rprelimd-vs-rhiresd"],
      "parallel": true,
      "depends_on": ["phase-3"]
    },
    {
      "id": "phase-5",
      "name": "Reader: parameter-drop fix, then the priority chain, then the flip",
      "tasks": ["5A-hybrid-parameter-drop-raise", "5B-chain-rhiresd-then-rprelimd-no-camels-tier", "5C-distribution-shift-gate", "5D-flip-default-to-hybrid"],
      "parallel": false,
      "depends_on": ["phase-4"]
    },
    {
      "id": "phase-6",
      "name": "Loudness + guards",
      "tasks": ["6A-weather-history-ingest-check-type", "6B-health-by-effect-not-rows-stored", "6C-converter-guards", "6D-dashboard-provenance"],
      "parallel": true,
      "depends_on": ["phase-5"]
    }
  ]
}
```

**Phase 4 is a gate, not a deliverable.** If the comparison trips the >5% flag, it gets explained before
phase 5 proceeds; if it trips >20%, it escalates to the owner. Do not roll through it.
