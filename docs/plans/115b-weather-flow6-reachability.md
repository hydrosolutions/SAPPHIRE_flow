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
rhiresd    71 files   1961-01-01 .. 2026-05-31   <- DEFINITIVE precip, free, ~45-day publication lag
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
> **`RhiresD` + `TabsD` + `TminD` + `TmaxD`, from 1981-01-01 → T-45d, with `RprelimD` for the live tail
> ONLY — superseded by `RhiresD` when it publishes.**
>
> **CAMELS-CH forcing becomes a VALIDATION REFERENCE, not a data tier.**

Because `RhiresD` reaches back to **1961**, it fully covers CAMELS' own 1981-2020 window — **so there
is no splice anywhere.** This kills all three consistency axes at once:

| axis | how it dies |
|---|---|
| **Product identity** | One product per parameter, end to end. The only product boundary is `RhiresD`↔`RprelimD` at the live tail — and it is **temporary**, resolved by supersession. |
| **Spatial aggregation** | **Our** polygons, **our** `exactextract`, throughout. CAMELS computed its basin means with *its* polygons and method; that mismatch simply leaves the series. |
| **Version / supersession** | The preliminary tail is **replaced** by definitive data as it publishes. `historical_forcing` **already carries a `version` column with latest-version supersession** (`historical_forcing_store.py:55`) — the mechanism exists and nothing was ever wired to it. |

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

For the most recent **~45 days**, inference runs on **preliminary** precipitation while the model was
trained on **definitive**. **No real-time system can escape this** — definitive data does not exist in
real time. But it must be **measured** (§8 quantifies exactly this) and **declared**, not discovered in
production.

## Scope

### 1. Adapter — add `RhiresD`, and teach it the archive asset family

`MeteoSwissOpenDataReanalysisAdapter` today fetches **per-day** STAC features
(`_fetch_day_feature(day_iso)`) and knows four products, precipitation being `RprelimD`
(`_PRODUCT_REGISTRY`, `:81-106`).

Two changes:

- **Add `RhiresD`** as the precipitation product (`ForcingSource.METEOSWISS_RHIRESD`,
  `raw_var="RhiresD"`, token `rhiresd`). `RprelimD` **stays** — it is the live-tail product, not a
  mistake to be deleted.
- **Support the `archive` asset family.** The historical files are **per-year NetCDFs**
  (`…-archive.rhiresd_ch01h.swiss.lv95_19810101000000_19811231000000.nc`), not per-day features. The
  current per-day fetch cannot read them. The backfill (§2) needs a year-file path; the daily
  operational path stays as-is.

**Grid note:** `rhiresd` publishes on `ch01h`; the other products on `ch01r`. Confirm the CRS/geometry
handling covers both, or the extraction will be silently wrong. *(Verify before implementing — do not
assume.)*

### 2. Backfill — derive 1981 → present through our polygons

A one-shot, resumable backfill producing `historical_forcing` rows for **every operational station**:

| source tag | parameter | span |
|---|---|---|
| `meteoswiss_rhiresd` | precipitation | 1981-01-01 → T-45d |
| `meteoswiss_tabsd` | temperature | 1981-01-01 → T-1d |
| `meteoswiss_tmind` | temperature_min | 1981-01-01 → T-1d |
| `meteoswiss_tmaxd` | temperature_max | 1981-01-01 → T-1d |
| `meteoswiss_rprelimd` | precipitation | T-45d → T-1d *(live tail; superseded)* |

**Scale check before building:** rows ≈ `stations × days × parameters`. At 2 stations × 16,436 days ×
4 = ~131k rows — trivial. **At the v0 target of ~1000 stations that is ~66M rows.** Size the backfill
(and the extraction, which is the real cost — 45 years × 4 products of grid files) accordingly. It is a
batch job, not a request path.

### 3. Supersession — wire the mechanism that already exists

`historical_forcing` has a `version` column and `fetch_forcing` applies **latest-version supersession**
when `version` is absent (`historical_forcing_store.py:55`). Nothing uses it.

Wire it so that when `RhiresD` publishes for a date already covered by `RprelimD`, the definitive row
**supersedes** the preliminary one. This is the mechanism that makes the live-tail compromise
acceptable rather than permanent.

### 4. The MeteoSwiss binding — for EXISTING stations, not just new ones

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

> **Open item:** what becomes of the `camels-ch` binding once CAMELS is no longer a data tier? Under
> `hybrid` the bindings only select *which stations participate* (`PerSourceStoreReader` keys on
> `station_id` and ignores `nwp_source`), so leaving it is harmless — but it then describes a source we
> no longer read. Decide: retire it, or keep it as a provenance marker. **Do not leave this implicit.**

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
   working), and when `stations_targeted > 0 and rows_stored == 0` over a full window (bound, but
   silent). HEALTHY on `rows_stored > 0`.

Two distinct failures, deliberately distinguished: **"nobody is bound to this feed"** vs **"the feed is
bound but silent."** Today both look identical — and both look like success.

### 8. 🔬 The validation experiment — our series vs CAMELS (do this EARLY)

**This is the payoff of the 1981 backfill depth, and it should run before §6's flip.**

Compare our self-derived basin means against CAMELS' own, **same basins, same dates, 1981-2020**:

- **Precipitation** — ours (`RhiresD` via `exactextract`, our polygons) vs CAMELS'. Both are
  RhiresD-derived, so **any difference is purely our aggregation method/polygons.** This is a positive
  control on the whole extraction pipeline against a **published reference**.
- **Temperature** — same, via `TabsD`.
- Report **per-basin bias, RMSE, and the seasonal + intensity structure** of the difference.
  Precipitation biases are rarely uniform: expect them to concentrate in high-intensity and winter/snow
  events — exactly where a flood-forecasting model is most sensitive.
- **Separately, quantify the live-tail residual**: `RprelimD` vs `RhiresD` over their overlap. That
  number *is* the honest uncertainty on the most recent 45 days of every operational forecast.

**If ours and CAMELS' disagree materially, stop.** Either our polygons differ from CAMELS' catchments,
or our aggregation is wrong — and we would be about to retrain every model on a silently different
series.

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

## Doc sync

- `docs/v0-scope.md §A12` and `docs/architecture-context.md:140,574` — these describe CAMELS-CH as *the*
  v0 training-forcing source. Under §0 it is a **validation reference**; the forcing series is
  self-derived from MeteoSwiss. **Record the provenance explicitly** (`RhiresD`/`TabsD`/`TminD`/`TmaxD`,
  our polygons) — the absence of exactly this note is what let the whole bug through.
- `docs/conventions.md` — the `ForcingSource` values, incl. the new `METEOSWISS_RHIRESD`.
- Annotations on **071** (falsified premise) and **072** (three defects) are already in place.
