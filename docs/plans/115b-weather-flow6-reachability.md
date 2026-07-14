---
status: DRAFT
created: 2026-07-14
plan: 115b
parent: 115
title: Flow 6 reachability — hybrid parameter-drop fix, default flip, existing-station backfill
scope: Makes the scheduled reanalysis feed actually work. The risky landing; isolated on purpose.
depends_on: [115a]
blocks: [115c]
---

# Plan 115b — Flow 6 reachability + the hybrid default

> Shared context and locked decisions D1–D3 live in the umbrella:
> [Plan 115](115-weather-source-identity-model.md). Read it first.

## Status

**DRAFT.** Depends on **115a**. This is the **risky** landing — it changes what data reaches
models. It is isolated precisely so that if it goes wrong in staging, it can be reverted **without
dragging back the schema work 081/082 are waiting on**.

> ## 🔴 THE AUDIT RAN (2026-07-14). This is a FIRST IMPLEMENTATION, not a fix.
>
> ```
>   source   | count |   first    |    last
> -----------+-------+------------+------------
>  camels-ch | 58440 | 1981-01-01 | 2020-12-31
> ```
>
> `historical_forcing` holds **one source**: `camels-ch`, frozen at **2020-12-31**. There is **no
> MeteoSwiss reanalysis data at all** — the scheduled `ingest-weather-history` deployment has **never
> stored a single row**, while reporting green for its entire operational life. And `A2` confirms **no
> `meteoswiss_open_data_reanalysis` binding exists**, so Flow 6 has been matching zero stations and
> returning `0/0/0` as a success since the day it shipped.
>
> **Both halves of the double-dark diagnosis are now fact.** Nothing here is being *repaired* — the
> feed has never worked. Size and schedule this plan accordingly: it is building a data path, not
> patching one.
>
> **Why it has not yet caused a visible incident:** today's models declare no *past-dynamic* weather
> features, so nothing has been asking for the data that isn't there. That is luck. The moment a model
> needs recent forcing — and Nepal's will — this becomes a hard blocker.

## The problem, in one paragraph

The scheduled MeteoSwiss reanalysis feed is dark **twice over**. (1) `services/onboarding.py` — the
sole writer of bindings — never writes a `meteoswiss_open_data_reanalysis` row, so Flow 6's
`_reanalysis_sources(store, adapter.NWP_SOURCE)` can match **zero** stations, log
`weather_history.no_stations`, and return `0/0/0` **as a success** (`ingest_weather_history.py:309`).
(2) Even when it runs, it writes rows under **product tags** (`meteoswiss_rprelimd`, `tabsd`,
`tmind`, `tmaxd` — `meteoswiss_open_data_reanalysis.py:251`) while the default `single` reader looks
them up by **binding name** (`store_backed_reanalysis.py:31`, default at `config/deployment.py:111`).
Write-key ≠ read-key.

## Scope

### 1. ⚠️ Fix hybrid's silent parameter drop — BEFORE the flip

**This is the blocker on flipping the default.** `hybrid_reanalysis.py:66-72`:

```python
chain = self._priority.get(key[2], ())          # key[2] is the parameter
winner = next((by_source[t.value] for t in chain if t.value in by_source), None)
if winner is None:
    continue                                     # <-- row silently discarded
```

The priority chains are hardcoded to exactly four parameters
(`hybrid_reanalysis_factories.py:26-32`: precipitation, temperature, temperature_min,
temperature_max). **Any other forcing parameter is silently dropped.**
`StoreBackedReanalysisSource` — today's default — passes **any** parameter through. So flipping the
default as-is is a **silent data-loss regression**: precisely the bug family this whole track exists
to eliminate.

**The rule (decided — a READY plan may not defer this to the implementer):**

> A requested parameter with **no configured priority chain** raises `ConfigurationError` — **unless
> exactly one source is configured for that parameter**, in which case that source wins.

Rationale: "pass it through" is **ambiguous when two sources return the same unknown parameter** —
there is no chain to break the tie, so a pass-through would pick nondeterministically, which is the
`_select_nwp_source` bug all over again. Raising is the only deterministic answer that cannot
silently return partial or arbitrary data. The single-source case is unambiguous, so it is allowed.

Requires an **overlap test**: two sources both returning an unconfigured parameter must raise, not
pick a winner.

*A parameter that a model requests and the reader silently discards is indistinguishable from missing
data — that is the whole disease this track is treating.*

### 2. Backfill the MeteoSwiss binding for EXISTING stations

*(Blocker from the 115 review — a falsified claim.)* Creating the binding at onboarding does
**nothing** for already-deployed stations: onboarding runs at *station onboarding*, not at deploy.
Flow 6 would stay empty for the entire existing fleet.

So the release must carry a **one-shot data backfill** inserting the binding for every eligible
existing station (eligibility = the same rule onboarding will use — a non-weather station with a
basin). Onboarding also gains the binding for new stations, so the two paths agree.

**Pin all FOUR fields, not just the name and role.** The adapter's match
(`meteoswiss_open_data_reanalysis.py:155-162`) requires **all** of:

| field | value | why |
|---|---|---|
| `nwp_source` | `meteoswiss_open_data_reanalysis` | Flow 6 selects by adapter identity |
| `role` | `REANALYSIS` | 115a's role filter |
| `status` | `ACTIVE` | the adapter checks it explicitly |
| `extraction_type` | `BASIN_AVERAGE` | the adapter's emission-shape guard |

*(Missing any one of them leaves the feed dark in exactly the way this plan exists to fix — and the
flow would still report green.)*

**Test Flow 6 against MIGRATED data**, not a freshly-onboarded fixture, and **assert a non-zero
`rows_stored`**. A green flow is not evidence.

### 3. Flip the reanalysis default to `hybrid`

`config/deployment.py:111`. Only after §1. Note
`tests/unit/config/test_deployment_reanalysis_source.py:25` currently locks the `"single"` default
and must be updated deliberately, not incidentally.

Verify the flip is safe for **CAMELS-only** stations (i.e. every Swiss station today): the chains
fall back to `CAMELS_CH`, so those rows still resolve — but this must be a **test**, not an
assumption.

#### ⚠️ 3a. Distribution-shift gate — the flip can silently change what a model is fed

*(Major from review round 6. **Plan 072 §175 already recorded this risk** and it was about to be
walked straight past.)*

The flip changes **where a feature's value comes from**. A model fitted on **CAMELS-sourced**
precipitation that suddenly reads **MeteoSwiss-sourced** precipitation is being fed a *different
distribution than it was trained on* — the numbers still arrive, the flow still goes green, and the
forecast is quietly wrong. This is not a wiring bug; it is a silent model-validity bug, and it would
be very hard to attribute after the fact.

The same path serves training (`training_data.py:177`), hindcast (`hindcast.py:292`) **and** the live
forecast cycle's past-dynamic inputs (`operational_inputs.py:327`) — so a shift hits fitted
artifacts and live inference together.

**Required gate before the flip (audit A4 in the umbrella):**

1. Enumerate **active** model artifacts and their `past_dynamic` / `future_dynamic` requirements.
2. For each, determine whether the flip re-sources any feature it actually consumes.
3. **If yes → retrain on hybrid-resolved forcing before the flip**, or hold the flip for those
   stations. Do not flip under a fitted artifact whose training distribution has moved.

Repo-level review suggests today's registered models are **probably** unaffected — native/fallback
models declare no past/future dynamic features (`linear_regression_daily.py:54`), and the FI NWP model
requires only *future* precipitation/temperature (`nwp_regression.py:126`), which comes from the
forecast path, not this one. **That is an inference, not a fact**: only the live artifact/assignment
tables settle which models are actually active. **NEEDS-LIVE-DB.**

### 4. Make an empty Flow 6 loud

A scheduled ingest that matches **zero** stations is a misconfiguration, not a no-op. It must not
report green. This is the observability hole that let the condition persist undetected — and the
reason nobody noticed a production feed was dark.

**Specified, not hand-waved** *(blocker from review round 6 — "surface it to Flow 4" had no
executable target)*. `ingest_weather_history_flow` has **no** `pipeline_health_store` parameter
(`ingest_weather_history.py:256`), its production setup pulls only station/forcing/basin stores
(`:267`), and `PipelineCheckType` (`types/enums.py:151`) has **no** weather-history check type. So
this needs building, not wiring:

1. Add a `WEATHER_HISTORY_INGEST` value to `PipelineCheckType` (+ the enum's DB constraint and a
   migration, + `docs/conventions.md`'s enum table).
2. Thread `pipeline_health_store` into `ingest_weather_history_flow` and its production setup.
3. Record a health check per run: subject = the reanalysis `nwp_source`; **UNHEALTHY when
   `stations_targeted == 0`** (a configuration fault — the feed cannot possibly be working), and
   also when `stations_targeted > 0` but `rows_stored == 0` over a full window (the feed is
   configured but delivering nothing).
4. `rows_stored > 0` on a normal run is HEALTHY.

Two distinct failures, deliberately distinguished: **"nobody is bound to this feed"** and **"the feed
is bound but silent."** Today both look identical — and both look like success.

### 5. Fix the CAMELS binding's `extraction_type`

Onboarding writes the CAMELS binding as `POINT` (`onboarding.py:364`) while its forcing records are
`BASIN_AVERAGE` (`camelsch_adapter.py:130`). `extraction_type` is therefore **already wrong in the
database**. Fix the write site **and** migrate existing rows.

*(This is why 115a's backfill keys off the source name and never off `extraction_type` — the field
cannot be trusted until this lands.)*

### 6. Converter guards Plan 071 specified but never landed

`preprocessing/converters.py:17,46` — `basin_avg_to_records` / `point_forecast_to_records` must
reject reanalysis source tags, so a reanalysis row can never be written into the forecast table.
Plan 071 §243 called for this; the code has no such check.

### 7. The dashboard forcing endpoint merges provenance streams

*(Major from the 115 review.)* `api/routes/stations.py:452-490` reads `historical_forcing` directly
and **ignores `source`**. Once CAMELS and MeteoSwiss product rows coexist for the same station and
parameter — which is exactly what this plan creates — it will silently merge multiple provenance
streams into one series.

**Decide:** does the dashboard show provenance-separated series (honest, and useful for spotting a
dark feed), or the same hybrid-resolved view the models consume (consistent with what the forecast
actually used)? Recommend **hybrid-resolved, with provenance shown per point** — the operator needs
to see *which* source won.

## Tests

- **The double-dark regression test:** with the MeteoSwiss binding present and `hybrid` default,
  rows written under product tags are readable **end to end** by the default consumer.
  *Must fail against today's wiring.*
- **The parameter-drop test (§1):** a forcing parameter with no configured chain **raises
  `ConfigurationError`** rather than being silently discarded. *Must fail against the current
  `continue`.*
- **The overlap test (§1):** two sources both returning an unconfigured parameter **raise** — they do
  not pick a nondeterministic winner.
- **Flow 6 health (§4):** `stations_targeted == 0` records UNHEALTHY, not a green zero; and
  `stations_targeted > 0` with `rows_stored == 0` over a full window also records UNHEALTHY.
- **CAMELS-only station survives the flip** — the chain falls back to `CAMELS_CH` and past-dynamic
  features are unchanged. This is the "did we break Switzerland" gate.
- **Flow 6 against migrated data** (§2), not a fresh fixture — an existing station gets the binding
  (all four fields pinned) and ingests, with `rows_stored > 0` asserted.
- CAMELS binding `extraction_type` is `BASIN_AVERAGE`, at the write site and after migration.
- Converter guards reject a reanalysis tag.

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Deploy gate (do not skip):** after the flip, confirm on staging that
`ingest-weather-history` reports a **non-zero** `rows_stored`, and that `historical_forcing`'s
`MAX(valid_time)` per source **advances**. A green flow is not evidence — that is the entire lesson
of this plan.
