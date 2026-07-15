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
relative_sunshine_duration:  SRELD   (decision 5; REANALYSIS/PAST-only — see the SrelD contract, §SrelD)
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

## §0a — Two-product precipitation: how the adapter DISAMBIGUATES (round-3 blocker 1)

*(Round-3 review: once `RhiresD` and `RprelimD` both map to canonical `precipitation`, the adapter is
ambiguous — it selects products only by `p.parameter in requested` (`meteoswiss_open_data_reanalysis.py:145`),
and the `WeatherReanalysisSource` protocol passes only `parameters`, never a product/source
(`protocols/adapters.py:47`). "Add `RhiresD` beside `RprelimD`" is underspecified without saying which one
a precipitation request returns.)*

**The fix — a source-scoped fetch, and Flow 6 splits precipitation by the discovered boundary `R`:**

1. **Give the WRITER a product-scoped fetch — and DO NOT touch the reader protocol (build-scoping,
   round-4 blocker 1).** The `WeatherReanalysisSource` protocol (`protocols/adapters.py:47-54`) and Flow
   6's local protocol (`ingest_weather_history.py:66-78`) are **parameter-only**, and the adapter selects
   by `p.parameter in requested` (`meteoswiss_open_data_reanalysis.py:138-147`) — adding both precip
   products makes `"precipitation"` ambiguous on that path. **Resolution: add a SEPARATE writer-side
   product-scoped entry point on the concrete `MeteoSwissOpenDataReanalysisAdapter`** —
   `fetch_products(products: list[ForcingSource], start, end, parameters) -> list[RawHistoricalForcing]`,
   used ONLY by the backfill + Flow 6 ingest. **The SPLIT RULE (which product per date, relative to `R`)
   is one rule applied over TWO different windows by two different callers (round-5 blocker 1):**
   - **Phase-3 backfill** owns the historical span: `RHIRESD` over `[1981-01-01, R]`, `RPRELIMD` over
     `[R+1d, T-1d]`. (This is NOT Flow 6 — Flow 6 is a rolling window, `ingest_weather_history.py:50,299`.)
   - **Flow 6 (rolling ingest)** applies the SAME rule over ITS window `[start, end]`:
     `RHIRESD` over `[start, min(R+1d, end))`, `RPRELIMD` over `[max(start, R+1d), end)` — two
     `fetch_products` calls (the current single `_CANONICAL_PARAMETERS` call at
     `ingest_weather_history.py:318` becomes these two).
   **`fetch_reanalysis(..., ["precipitation"])` — the OLD parameter-keyed path — must FAIL CLOSED once
   `_PRODUCT_REGISTRY` holds two precipitation products (round-5 blocker 2):** it raises
   `ConfigurationError` when a requested parameter maps to >1 product, so precipitation is served ONLY via
   `fetch_products`. The other four parameters (1 product each) still resolve on the parameter path
   unchanged. **The read-side `WeatherReanalysisSource` protocol, its fakes, and
   `PerSourceStoreReader`/`HybridForcingSource` are UNCHANGED** — the reader reads the STORE (not the
   adapter) and the hybrid priority chain breaks any overlap (§3). This bounds the ripple to the one
   concrete adapter (a new `fetch_products` + a fail-closed guard on `fetch_reanalysis`) + the Flow 6 caller +
   the Flow 6 caller; it does **not** widen the shared protocol or touch every implementation/fake.
2. **Flow 6 writes precipitation from the RIGHT product per date range**, keyed on the discovered `R`
   (latest published `RhiresD` date, §2). **Canonical inclusivity (defined ONCE here, referenced
   everywhere): `RhiresD` covers `[1981-01-01, R]` inclusive; `RprelimD` covers `[R+1d, T-1d]`.** The two
   archive spans are therefore **disjoint by construction** — no overlapping rows in `historical_forcing`
   from the backfill. Both land under their own source tags (`meteoswiss_rhiresd` / `meteoswiss_rprelimd`).
   *(The `RhiresD`/`RprelimD` overlap that §8's live-tail measurement needs is a SEPARATE one-off fetch,
   not part of this archive path — see §8.)*
3. **The READER (hybrid) resolves the overlap by PRIORITY, not the writer** — for any date where both
   exist, `RHIRESD → RPRELIMD` prefers definitive (§3). The writer never has to overwrite; both rows coexist.

So "which product" is answered in two places, cleanly separated: the **writer** picks by date-vs-`R`, the
**reader** breaks any overlap tie by priority. The canonical parameter (`precipitation`) is never asked to
carry product identity.

## Live STAC probe (2026-07-15) — phase-1 assumptions verified against real data

Run before build so nobody implements against a guessed shape. Collection
`ch.meteoschweiz.ogd-surface-derived-grid`.

| finding | result | consequence for the plan |
|---|---|---|
| **SrelD exists** | ✅ `sreld`, archive **1971 → 2025** (per-year), plus a `last` family for the recent months | decision 5 is buildable; token is `sreld` |
| **SrelD grid** | **`ch01r`** (the *temperature* family), **not** `ch01h` | the CRS path must handle `ch01r` for sunshine too — same as TabsD/TminD/TmaxD |
| **RhiresD archive is per-year addressable** | ✅ `…-archive.rhiresd_ch01h.swiss.lv95_19610101…_19611231….nc` — filename carries the full-year span | phase-1 `1B-archive-asset-selection` keys on the year in the filename; confirmed real |
| **RhiresD reach** | archive **1961 → 2025**, `last` family extends to **2026-05-31** | definitive coverage currently ends **2026-05-31** (today is 07-15 → ~6-week lag, matches "monthly") |
| **RprelimD shape** | per-**day** items under the **bare** collection name (no `archive`/`last` suffix), **2026-05-16 → 2026-07-13** (59 days) | it is the recent tail only, exactly as modelled; the adapter must address it by day, not by year |
| **🎯 The live-tail overlap EXISTS and is fetchable** | RprelimD dates that fall within RhiresD's definitive coverage: **2026-05-16 → 2026-05-31 (16 days)**; RprelimD-only tail: **2026-06-01 → 2026-07-13 (43 days)** | **resolves round-2 blocker 3** — the phase-4 overlap fetch (`4C`) has real data to pull. **But the window is SMALL (~2-3 weeks) and MOVING**, so the live-tail measurement must be run opportunistically/periodically, not as a one-shot; a single grab yields only ~2 weeks of paired days |

**Two things the builder must NOT assume:**
- The **43-day RprelimD-only tail** (06-01 → 07-13) confirms the preliminary window is currently ~6
  weeks and will swing with each monthly RhiresD publication — the boundary `R` (§2) is genuinely
  dynamic, as the plan already says. Discover it from `rhiresd`'s max `last`-family date.
- The overlap for §8's clean live-tail number is only **~16 days right now**. That is enough to
  *start*, but a robust residual estimate needs the measurement **accumulated over several monthly
  cycles**. Note this in `4D` — do not present a two-week sample as the definitive residual.

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
| `SrelD` | **`ch01r`** | relative sunshine duration (decision 5) — the `ch01r` family, same as TabsD/Tmin/Tmax |
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
| `meteoswiss_sreld` | `relative_sunshine_duration` | 1981-01-01 → T-1d |
| `meteoswiss_rprelimd` | precipitation | **R+1d → T-1d** *(live tail; disjoint from RhiresD)* |

> **✅ OWNER DECISION (2026-07-14): include `SrelD` now.** CAMELS-CH itself uses relative sunshine
> duration (Höge et al., App. A1.2), no model requires it *yet*, and it is free (1971 → present). Adding
> it to this backfill costs one more product; adding it **later** costs a **40-year re-run**. Take it now.
>
> **Canonical name (owner, 2026-07-15): `relative_sunshine_duration`, unit `%`.** Explicit and
> unambiguous — SrelD is *relative* sunshine duration (% of the astronomically possible maximum), not
> absolute hours; the short `sunshine_duration` would mislead and could collide with a future absolute
> product. Consistent with the existing spelled-out pattern (`temperature_min`/`temperature_max`).
>
> **A parameter name is a permanent contract — round 2 found the repo LOCKS exactly four forcing
> parameters in five places. All five must change together, BEFORE any `SrelD` row is written:**
>
> 1. `types/forcing_schema.py` — add `relative_sunshine_duration: "%"` to `CANONICAL_FORCING_SCHEMA`
>    (`:37-46`), and update the tests that assert exactly four entries.
> 2. `types/forcing_sources.py` — add `ForcingSource.METEOSWISS_SRELD = "meteoswiss_sreld"` (`:18`) and
>    its `SOURCE_ATTRIBUTIONS` entry.
> 3. adapter `_PRODUCT_REGISTRY` — add the `sreld` / `SrelD` product row.
> 4. `flows/ingest_weather_history.py` — add it to the requested-parameters list (`:57-63`).
> 5. `adapters/hybrid_reanalysis_factories.py` — a **single-source** chain
>    `relative_sunshine_duration: (METEOSWISS_SRELD,)` and add it to the default `parameters_in_scope`
>    (`:26-38`).
> 6. `config/deployment.py` — the availability split is a **REQUIRED CODE CHANGE, not an "if conflated"**
>    (round-4 blocker 3 confirmed the code IS conflated). Today `available_nwp_parameters` is the **only**
>    set (`config/deployment.py:124-127`); `onboard_model` passes it straight through as `available_features`
>    (`onboard_model.py:252-258`); and model-compat subtracts **both** past and future requirements from that
>    one set (`model_onboarding.py:213-214`). There is **no forecast sunshine product** (ICON fetches only
>    `tot_prec`+`t_2m`, `meteoswiss_nwp.py:56-62`; future-dynamic input comes from `weather_forecasts`,
>    `operational_inputs.py:347-354`), so advertising `relative_sunshine_duration` in that single set would
>    let a model declare it as a **future** feature that can never be delivered. **Required build:** introduce
>    a **past-availability** set distinct from the **forecast/future-availability** set (or a per-parameter
>    past/future flag). Thread it through **BOTH** compatibility paths — the unit path
>    (`model_onboarding.py:213`) AND the older station-level helper (`model_onboarding.py:126`), both of
>    which today subtract the one set from both past and future — plus `onboard_model.py` and the service
>    onboarding callers. Add `relative_sunshine_duration` to **past-available only**. Its own phase task
>    (**1E**) with tests proving it is **accepted as `past_dynamic` and REJECTED as `future_dynamic`**.
>    `SrelD` is REANALYSIS/PAST-only.
> 7. A parameter-table **seed/migration** — the DB DOES enumerate valid parameter names
>    (`alembic/versions/0001_v0_schema.py:770` seeds `parameters`), so this is **required, not "if"**: add
>    the `relative_sunshine_duration` seed row via a migration, plus tests for the new five-parameter set.
>
> Until a model actually *requests* `relative_sunshine_duration`, it is ingested-and-stored only — which
> is the whole point of decision 5 (have it in the archive before a model needs it, so no second 40-year
> re-run).

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

**Eligibility:** "every operational station" means **every station with a valid basin polygon**.
**Pre-enumerate** the eligible set explicitly — do NOT rely on extractor behaviour: the extractor
**skips** invalid-geometry stations and raises only if **none** are valid
(`exact_extract_grid_extractor.py:67-89`), so a station silently missing a polygon would be silently
dropped from the backfill, not flagged. Enumerate, and log any operational station excluded for lack of
geometry.

**Scale — and the current path CANNOT do it.** Rows ≈ `stations × days × source-rows`. Post-SrelD there
are **5 canonical parameters but 6 source rows** (precipitation split across `RhiresD` + `RprelimD`). Over
1981-01-01 → T-1d (~16,630 days) at 2 stations × ~6 source rows ≈ ~200k rows (trivial); **at the v0 target
of ~1000 stations that is ~100M rows.**
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

> **⚠️ ORDERING — TWO constraints (review rounds 1 & 2).**
>
> **(a) The camels-ch retirement (§4/2C) must NOT precede the hybrid flip (phase 5).** `single` — the
> default reader until phase 5 — looks a binding up by `cfg.nwp_source` (`store_backed_reanalysis.py:31`).
> If we retire the `camels-ch` binding while `single` is still default, and the MeteoSwiss rows live under
> product tags (`meteoswiss_rhiresd`) that `single` cannot resolve from the `meteoswiss_open_data_reanalysis`
> binding name, a station is left with **no readable reanalysis source at all** in the intermediate state.
> **So: keep the camels-ch binding until the hybrid chain is the default (make retirement atomic with the
> flip), or guard `single` first.** The dependency graph is corrected below — `2C` moves to phase 5.
>
> **(b) The MeteoSwiss binding must exist BEFORE the backfill runs.** The adapter only
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

Under §0 the reader resolves `RHIRESD → RPRELIMD` and merges the **five** canonical parameters
(precipitation, temperature, temperature_min, temperature_max, relative_sunshine_duration) — exactly what
`HybridForcingSource` does. It remains the right component; **only its chain contents change.**

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
> - **The biggest confound is *probably* eliminated — verify before relying on it.** Our basins are
>   loaded from **CAMELS-CH's own shipped geometry files** (`camelsch_adapter.py:262-301` →
>   `geometry_to_basin`, stored at `onboarding.py:240-248`) — confirmed in code. **But that proves we use
>   the shapefiles CAMELS SHIPS, not necessarily the exact masks CAMELS used to AGGREGATE its forcing**
>   (a dataset can ship one catchment boundary and aggregate gridded forcing over a slightly different
>   raster mask). ⚠️ **NEEDS-EXTERNAL-CHECK** against the CAMELS-CH paper/package before §8 leans on
>   "same polygons". If they differ, that confound returns and §8's tolerances must widen.
>
> ### Tolerances (owner, 2026-07-14) — EXACT metrics, per parameter
>
> *(Round 2 flagged "≤5% / >5% / >20%" as uncomputable — it named no metric, and "% error in °C" is
> meaningless near 0. Fixed: precipitation gates on **relative bias of the long-run total**; temperature
> gates on **absolute error in °C**.)*
>
> **PRECIPITATION — gate on per-basin relative bias of the 1981-2020 TOTAL:**
> ```
> rel_bias = ( Σ(ours) − Σ(camels) ) / Σ(camels)     over all days 1981-2020, per basin
>   |rel_bias| ≤  5%   → pass
>   |rel_bias| >  5%   → FLAG   (report per basin)
>   |rel_bias| > 20%   → ESCALATE to owner — NOT an automatic stop
> ```
> The long-run total is the stable, physically-meaningful figure: it washes out per-day noise and the
> grid-resolution event scatter, and answers directly "does our series carry the same water as CAMELS".
>
> **TEMPERATURE — gate on per-basin ABSOLUTE error in °C** (percent is banned — it explodes near 0 °C).
> **BOTH `mean_bias` AND `rmse` are thresholded (round-3 blocker 3 — an earlier revision named RMSE but
> never gated it):**
> ```
> mean_bias = mean(ours − camels)   [°C]        rmse = sqrt(mean((ours−camels)²))   [°C]   per basin, 1981-2020
>   pass      ⟺  |mean_bias| ≤ 0.5 °C  AND  rmse ≤ 1.0 °C
>   FLAG      ⟺  |mean_bias| > 0.5 °C  OR   rmse > 1.0 °C
>   ESCALATE  ⟺  |mean_bias| > 1.0 °C  OR   rmse > 2.0 °C
> ```
> *(0.5 / 1.0 °C bias and 1.0 / 2.0 °C RMSE are the working thresholds — owner to confirm against the first
> results; a hydrologist's call, not a code constant. The point locked here is that RMSE **has** a
> pass/flag/escalate rule, not that these exact numbers are final.)*
>
> **NON-GATING DIAGNOSTICS (reported, never thresholded):** per-season totals, per-event maxima, and
> wet-day RMSE for precipitation. **This is where the 2 km-vs-1 km grid effect legitimately lives** — a
> convective cell smeared across one 2 km cell resolves differently at 1 km, so large per-*event*
> discrepancies are physically expected and must not trip a gate. **Why >20% (on the TOTAL) still only
> escalates and never auto-stops** (owner's reasoning, recorded so nobody "fixes" it into a hard stop):
> even a large whole-period bias may be a legitimate consequence of the grid change and must be
> **explained**, not shrugged off — and not silently accepted either.

Design it honestly:

- Compare our self-derived basin means against CAMELS' own — **same basins** (literally the same
  polygons), **same dates, 1981-2020** — precipitation (`RhiresD`) and temperature (`TabsD`).
- Report **per-basin bias, RMSE, and the seasonal + intensity structure** of the difference. Expect the
  precipitation differences to concentrate in high-intensity and winter/snow events — exactly where a
  flood-forecasting model is most sensitive, and exactly where the grid-resolution effect lives.
- Apply the **exact** gates above: precipitation on the 1981-2020 per-basin relative-bias of the TOTAL
  (≤5 / >5 / >20 %); temperature on per-basin absolute error in °C, gating BOTH mean-bias AND RMSE (pass ⟺
  |mean_bias|≤0.5 AND rmse≤1.0; owner to confirm the numbers). Per-season / per-event / wet-day-RMSE are diagnostics, reported but never gated.

**Separately — and this one IS clean:** quantify the **live-tail residual**, `RprelimD` vs `RhiresD`
over their overlap, same pipeline, same polygons, same grid, same vintage. **No confounds.** It is the
one genuinely attributable number in this plan.

> ⚠️ **The overlap must be FETCHED for this — it does not exist in our DB (review round 2, blocker 3).**
> The audit proved `historical_forcing` holds only `camels-ch`. And the §2 backfill table ingests
> `RhiresD` over `[1981-01-01, R]` and `RprelimD` over `[R+1d, T-1d]` — **disjoint by construction, no overlap**.
> So this comparison needs its own step: pull, for a recent window that STAC still serves BOTH products
> (`RprelimD` is retained ~2 months, and `RhiresD` republishes over that same window with its lag), the
> two products for the same basins/dates, and compare. It is a **one-off measurement fetch**, not part of
> the archive backfill — add it as an explicit task in phase 4.

### 9. ~~Fix the CAMELS binding's `extraction_type`~~ — MOOT under decision 6

*(An earlier revision fixed the CAMELS weather binding's `extraction_type` (`POINT` where its rows are
`BASIN_AVERAGE`). **Decision 6 RETIRES that binding entirely**, so there is nothing to fix — you cannot
both delete a binding and migrate its final value (review round 2, blocker 1).*

**So §9 collapses into §4's retirement:** the `camels-ch` weather binding is removed; onboarding stops
writing it. The only assertion that remains valid is **"no `camels-ch` weather binding exists after
115b"**. The CAMELS forcing **rows** are untouched (they carry `spatial_type` on the
`historical_forcing` row itself — `camelsch_adapter.py:130` — not on the retired binding, so their
provenance is unaffected).

### 10. Converter guards Plan 071 specified but never landed

`preprocessing/converters.py:17,46` — `basin_avg_to_records` / `point_forecast_to_records` must reject
reanalysis source tags, so a reanalysis row can never be written into the forecast table. Plan 071 §243
called for this; the code has no such check.

### 11. The dashboard forcing endpoint merges provenance streams

`api/routes/stations.py:452-490` reads `historical_forcing` directly and **ignores `source`**. Once
several provenance tags coexist for the same station/parameter — which is exactly what this plan
creates — it silently merges them into one series.

**DECIDED (round-5 major 1 — no longer "decide"): HYBRID-RESOLVED, winning source shown per point.**
The endpoint resolves the same way the models consume the data — route it through
`select_reanalysis_source(mode=hybrid)` so it returns exactly what a forecast would have used — and the
response carries, per data point, the **winning `source` tag** (e.g. `meteoswiss_rhiresd` vs
`meteoswiss_rprelimd`) so an operator can see which product won and spot a stuck/preliminary tail.
Concretely: `stations.py:452-490` stops grouping by parameter-only over the raw
`historical_forcing` read and instead serves the hybrid-resolved series with a `source` field on each
point. *(Rationale: a provenance-separated raw view would show overlapping rows the models never see —
misleading; the operator wants the served truth plus its provenance.)*

## Tests

- **The validation experiment (§8) is a gate, not a test** — but its outputs get pinned: a regression
  test asserts our basin-mean derivation is stable against a small committed fixture.
- **The double-dark regression:** with the MeteoSwiss binding present and `hybrid` default, rows written
  under product tags are readable **end to end** by the default consumer. *Must fail against today's
  wiring.*
- **Priority, not supersession (§3):** for a `(station, valid_time, parameter)` covered by BOTH sources,
  a **direct source-keyed fetch returns BOTH rows** (they legally coexist), while the **hybrid reader
  returns only the `RhiresD` winner**. *Two assertions, because §3's whole correction is that these are
  different rows resolved by priority — not one row overwriting another.*
- **Parameter drop (§5):** a parameter with no configured chain **raises**, and does not vanish. *Must
  fail against the current `continue`.*
- **Overlap (§5):** two sources returning the same unconfigured parameter **raise** — no
  nondeterministic winner.
- **Flow 6 against MIGRATED data** (§4), not a fresh fixture — an existing station gets the binding (all
  four fields pinned) and ingests, asserted via an **advancing `MAX(valid_time)`** per source (not
  `rows_stored`, per §7).
- **Flow 6 health (§7) — measured by EFFECT, never `rows_stored`:** `stations_targeted == 0` records
  UNHEALTHY (not a green zero); and a run that INSERTS nothing over a full window records UNHEALTHY too —
  asserted via **actual DB rowcount** or a **non-advancing `MAX(valid_time)` per source**, NOT via
  `rows_stored` (which is `len(records)` after `on_conflict_do_nothing`, so a pure-duplicate re-fetch
  looks healthy — the exact bug §7 rejects).
- **`temperature_min`/`temperature_max` now exist** — they resolve from `TminD`/`TmaxD`, with no CAMELS
  tier to fall back to.
- **No `camels-ch` weather binding remains** after 115b (decision 6); the CAMELS forcing rows in
  `historical_forcing` are untouched and still readable by a direct source-keyed fetch.
- Converter guards reject a reanalysis tag.
- **Writer-side product-scoped fetch (§0a/1F):** the adapter's product-scoped entry point returns ONLY the
  requested product; a `RHIRESD`-scoped call never yields `RprelimD` rows and vice versa. Flow 6 issues the
  two calls split at `R` (`[1981-01-01,R]` RhiresD, `[R+1d,T-1d]` RprelimD) and the two archive spans do not
  overlap. *Soundness: fails against the old single `_CANONICAL_PARAMETERS` call.*
- **SrelD priority resolution:** a `relative_sunshine_duration` request resolves via the single-source
  `SRELD` chain; the hybrid reader (keyed on exact `row.parameter`, `hybrid_reanalysis.py:77-84`) returns
  the `meteoswiss_sreld` row.
- **SrelD past-vs-future (§1E — the round-4 blocker):** a model declaring `relative_sunshine_duration` as a
  **`past_dynamic`** feature onboards successfully; declaring it as a **`future_dynamic`** feature is
  **rejected** by model-compat. *Soundness: fails against today's single conflated `available_nwp_parameters`.*
- **Phase-5 ordering:** the retire-camels-binding migration cannot leave a station unreadable — a test (or
  a documented deploy-gate check) that the hybrid default is serving BEFORE the binding is retired.
- **Existing four-parameter pins updated, not just added-to:** `tests/unit/types/test_forcing_schema.py:24-29`,
  `tests/unit/flows/test_ingest_weather_history.py:79-84`, and
  `tests/unit/adapters/test_hybrid_reanalysis_factories.py:17-28,48` all assert exactly four parameters
  today; each must move to the five-parameter set, not break.

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
- `docs/conventions.md` — the `ForcingSource` values, incl. the new `METEOSWISS_RHIRESD` **and
  `METEOSWISS_SRELD`**.
- Record the provenance line as `RhiresD`/`TabsD`/`TminD`/`TmaxD`/**`SrelD`** (five products), our polygons.
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
      "name": "Adapter: RhiresD + SrelD + archive asset family + writer-side product-scoped fetch + past/future availability split",
      "tasks": ["1A-products-rhiresd-sreld", "1B-archive-asset-selection", "1C-real-lv95-fixture", "1D-dynamic-rhiresd-boundary", "1E-past-vs-future-availability-split", "1F-writer-side-product-scoped-fetch"],
      "parallel": false,
      "depends_on": ["plan-115a"]
    },
    {
      "id": "phase-2",
      "name": "Bindings first + per-station onboarding backfill. NOTE: camels-ch retirement is NOT here; it is 5E, atomic with the flip.",
      "tasks": ["2A-backfill-meteoswiss-binding", "2B-onboarding-writes-binding", "2C-onboarding-per-station-meteoswiss-backfill-or-hold"],
      "note": "2C (round-5 blocker 3): a binding alone gives a NEW station no forcing rows — onboarding still imports CAMELS only (onboarding.py:341,365). Onboarding must run the per-station MeteoSwiss backfill BEFORE the station is promoted operational/trainable, or explicitly HOLD it out. Task + test, not just the 2B binding write.",
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
      "name": "Reference comparison vs CAMELS + the clean live-tail measurement (a GATE)",
      "tasks": ["4A-basin-mean-comparison-1981-2020", "4B-tolerance-report", "4C-fetch-rprelimd-rhiresd-overlap-window", "4D-live-tail-residual"],
      "parallel": false,
      "task_depends_on": {"4B-tolerance-report": ["4A-basin-mean-comparison-1981-2020"], "4D-live-tail-residual": ["4C-fetch-rprelimd-rhiresd-overlap-window"]},
      "note": "NOT fully parallel (round-3 major 1): 4B needs 4A's basin means; 4D needs 4C's fetched overlap. 4A||4C may start together; 4B after 4A, 4D after 4C.",
      "depends_on": ["phase-3"]
    },
    {
      "id": "phase-5",
      "name": "Reader: parameter-drop fix, then the priority chain, then the flip — and camels-ch retirement CHOREOGRAPHED with the flip",
      "tasks": ["5A-hybrid-parameter-drop-raise", "5B-chain-rhiresd-then-rprelimd-no-camels-tier", "5C-distribution-shift-gate", "5D-flip-default-to-hybrid", "5E-retire-camels-weather-binding"],
      "parallel": false,
      "note": "STRICT ORDER (round-3 major 2 — 5E is not merely 'adjacent' to 5D). See the deployment-choreography subsection below.",
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

### Phase-5 deployment choreography — why 5E is NOT just "adjacent" to 5D (round-3 major 2)

`5E` (retire the `camels-ch` weather binding) and `5D` (flip the default to `hybrid`) are **not two
independent tasks that happen to sit next to each other** — the wrong interleaving leaves a station with
**no readable reanalysis source at all**. Today `single` is still the default (`config/deployment.py:111`)
and `single`/`StoreBackedReanalysisSource` reads `cfg.nwp_source` **directly**
(`store_backed_reanalysis.py:35`). So:

- If `5E` lands **before** `5D` on a running deployment: the station loses its `camels-ch` binding while
  the default reader is still `single`, which cannot resolve the MeteoSwiss product-tag rows from the
  `meteoswiss_open_data_reanalysis` binding name → **the past-dynamic feed goes dark** in the window
  between the two.

**The required order, as a single atomic deployment step (not two commits shipped whenever):**

1. Land the code for `5A`–`5C` and both `5D` and `5E` together.
2. On deploy, **flip the default to `hybrid` FIRST** (config), confirm the hybrid reader is serving
   (a station returns rows via the `RHIRESD → RPRELIMD`/`TABSD`/… chain), **then** run the `camels-ch`
   binding-retirement migration. Never the reverse order.
3. If the deployment must roll back, roll back **both** — the retirement migration's `downgrade()` restores
   the `camels-ch` binding, and the config reverts to `single`, together.

State this in the migration's docstring and the deploy runbook, so "atomic with the flip" is an
**executable procedure**, not a task-name adjective.

## Review deltas (independent Codex review round 3, 2026-07-15) — verdict NOT-READY, folded

Run on a clean machine after two earlier round-3 attempts hung under codex congestion. All folded:

- **BLOCKER — RhiresD/RprelimD product disambiguation.** Once both map to canonical `precipitation`, the
  adapter (selects by `p.parameter in requested`) is ambiguous. → new **§0a**: a source-scoped fetch path;
  Flow 6 writes the right product by date-vs-`R`; the hybrid reader breaks any overlap by priority. The
  canonical parameter never carries product identity.
- **BLOCKER — SrelD wrongly advertised as NWP/forecast-available.** `available_nwp_parameters` gates BOTH
  past and future features, but there is no forecast sunshine product (`meteoswiss_nwp.py:56` fetches only
  tot_prec+t_2m). → SrelD is REANALYSIS/PAST-only; wiring item 6 corrected to NOT add it to the forecast
  set (split the set if the code conflates them).
- **BLOCKER — temperature RMSE named but not thresholded.** → both `mean_bias` AND `rmse` now have explicit
  pass/flag/escalate rules (pass ⟺ both within threshold).
- **MAJOR — phase 4 marked parallel but 4B⟵4A and 4D⟵4C.** → phase 4 set non-parallel with explicit
  task-level dependencies (4A‖4C may start; 4B after 4A; 4D after 4C).
- **MAJOR — "5E atomic with 5D" was only a label.** → new phase-5 deployment-choreography subsection: flip
  to hybrid FIRST, confirm serving, THEN run the binding-retirement migration; roll back both together.
  Recorded as an executable procedure in the migration docstring + runbook.
- **MINORS** — the priority-chain block now lists SrelD; phase-2's name no longer claims the camels
  retirement (moved to 5E).

**Codex CONFIRMED-SOUND:** cross-source precedence is hybrid priority not `version` supersession; hybrid's
silent parameter drop is real; the extractor skips (not raises) per missing-geometry station; FI can
already represent SrelD's `%` via `Unit.PERCENT`; the DB parameter seed (`0001_v0_schema.py:770`) is real
and must be updated.

Round-3 findings resolved.

## Review deltas (Codex round 4, 2026-07-15) — verdict NOT-READY, folded

Round 4 was a **cross-section consistency** pass — it found that round-3's §0a + SrelD folds were carried
into some sections but not all (the sin a heavily-revised plan is prone to). All folded:

- **BLOCKER — source-scoped fetch was under-scoped against the real protocol.** The reader protocol is
  parameter-only; adding both precip products re-ambiguates `precipitation`. Resolved by a **writer-side
  product-scoped entry point** on the concrete adapter (Flow 6 calls it twice, split at `R`); the read-side
  protocol/fakes/`PerSourceStoreReader`/`HybridForcingSource` are UNCHANGED (§0a.1, phase 1F).
- **BLOCKER — SrelD naming inconsistency.** The chain used `rel_sunshine` while the canonical name is
  `relative_sunshine_duration`; the backfill table, §6, tests, doc-sync and row-count math all diverged.
  Reconciled to `relative_sunshine_duration` / `METEOSWISS_SRELD` everywhere.
- **BLOCKER — past-vs-future availability is a REQUIRED code change, not "if conflated."** The code has one
  conflated `available_nwp_parameters` used for both. New phase task **1E**: introduce a past-availability
  set, add SrelD to past-only, test accepted-as-past / rejected-as-future.
- **MAJOR — `R` inclusivity defined ONCE** (`RhiresD [1981-01-01, R]`, `RprelimD [R+1d, T-1d]`, disjoint);
  §0a, the backfill table and §8 now agree.
- **MAJOR — tests added** for the writer-side fetch, SrelD priority + past/future, phase-5 ordering; and the
  existing four-parameter pins are marked *migrate to five*, not merely add-to.
- **MAJOR — the health test wording** dropped `rows_stored == 0` for advancing-`MAX(valid_time)` / DB
  rowcount (§7's own metric).
- **MINORS** — doc-sync + row-count math updated for SrelD (5 params, 6 source rows, ~99M at 1000 stations).

**Codex CONFIRMED-SOUND:** hybrid-priority (not version) supersession; `available_nwp_parameters` genuinely
unsafe for SrelD future availability; the extractor skip behaviour; the phase-5 ordering rationale.

## Review deltas (Codex round 5, 2026-07-15) — verdict NOT-READY, folded. DESIGN CONFIRMED SOUND.

Round 5 was a fresh-eyes **buildability** pass. It confirmed the design (5 CONFIRMED-SOUND items:
hybrid-priority supersession, the genuinely-conflated past/future availability, the single Flow-6 call,
the dashboard provenance-merge, FI `Unit.PERCENT` for SrelD). Every finding was "make the spec
executable," not "the design is wrong" — the signature of convergence. Folded:

- **BLOCKER — the `[1981, R]` split was attributed to Flow 6, but Flow 6 is a ROLLING 60-day ingest.** The
  1981-present span is **phase-3 backfill**, not Flow 6. §0a now states the split as ONE rule over TWO
  windows: backfill covers `[1981, R]`/`[R+1d, T-1d]`; Flow 6 applies the same rule over its rolling
  `[start, end]`.
- **BLOCKER — `fetch_reanalysis(["precipitation"])` becomes ambiguous** with two precip products in the
  registry. Now specified: the parameter-keyed path **fails closed** (raises when a parameter maps to >1
  product); precipitation is served ONLY via the new `fetch_products(...)`. Signature pinned.
- **BLOCKER — per-station onboarding backfill had no phase task** (only the binding write, 2B). Added
  **2C**: onboarding runs the per-station MeteoSwiss backfill before promotion, or holds the station out.
- **MAJOR — `6D-dashboard-provenance` was still "decide."** DECIDED: hybrid-resolved via
  `select_reanalysis_source(hybrid)`, winning `source` shown per point.
- **MAJOR — SrelD missing from the grid table** (probe says `ch01r`). Added.
- **MINORS** — both model-compat functions (`:126` and `:213`); parameter seed is required not "if"
  (`0001_v0_schema.py:770`); row-count days corrected (~16,630 → ~100M at 1000 stations).

**Assessment:** five rounds in, the design is settled and the spec is now executable task-by-task. No open
design forks remain. A final confirmation pass is reasonable, but the arc has converged — each round now
finds smaller, more mechanical items. **This is close to READY; owner's call on whether one more
confirmation pass or straight to READY.** *(The plan is large — ~6 phases, ~25 tasks — but the reviewer
did not require a split; it is a coherent single track with a valid phase DAG.)*
