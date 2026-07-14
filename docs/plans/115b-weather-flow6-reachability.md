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

**⚠️ The live DB audit changes what this plan IS.** If `historical_forcing` is frozen at the CAMELS
import, this is a **first implementation**, not a fix — and past-dynamic features have been stale in
every forecast since onboarding. Do not size or schedule this plan before the audit runs.

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

**Required fix:** a parameter with no configured chain must **not** vanish. Either pass it through
(fall back to the row's own provenance when no chain is configured) or **raise** — never silently
`continue`. Decide explicitly and test it. *A parameter that a model requests and the reader
silently discards is indistinguishable from missing data.*

### 2. Backfill the MeteoSwiss binding for EXISTING stations

*(Blocker from the 115 review — a falsified claim.)* Creating the binding at onboarding does
**nothing** for already-deployed stations: onboarding runs at *station onboarding*, not at deploy.
Flow 6 would stay empty for the entire existing fleet.

So the release must carry a **one-shot data backfill** inserting the
`meteoswiss_open_data_reanalysis` / REANALYSIS binding for every eligible existing station
(eligibility = the same rule onboarding will use — a non-weather station with a basin). Test Flow 6
**against migrated data**, not just against a freshly-onboarded fixture.

Onboarding also gains the binding for new stations, so the two paths agree.

### 3. Flip the reanalysis default to `hybrid`

`config/deployment.py:111`. Only after §1. Note
`tests/unit/config/test_deployment_reanalysis_source.py:25` currently locks the `"single"` default
and must be updated deliberately, not incidentally.

Verify the flip is safe for **CAMELS-only** stations (i.e. every Swiss station today): the chains
fall back to `CAMELS_CH`, so those rows still resolve — but this must be a **test**, not an
assumption.

### 4. Make an empty Flow 6 loud

A scheduled ingest that matches **zero** stations is a misconfiguration, not a no-op. It must not
report green. This is the observability hole that let the condition persist undetected — and the
reason nobody noticed a production feed was dark.

Emit at WARNING/ERROR with the source name and the station count, and surface it to the Flow 4
pipeline-monitoring path the way other staleness signals are surfaced.

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
- **The parameter-drop test (§1):** a forcing parameter with no configured chain is **not** silently
  discarded. *Must fail against the current `continue`.*
- **CAMELS-only station survives the flip** — the chain falls back to `CAMELS_CH` and past-dynamic
  features are unchanged. This is the "did we break Switzerland" gate.
- **Flow 6 against migrated data** (§2), not a fresh fixture — an existing station gets the binding
  and ingests.
- **Empty Flow 6 is loud**, not a green zero.
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
