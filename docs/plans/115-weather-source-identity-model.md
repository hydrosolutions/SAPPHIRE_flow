---
status: DRAFT
created: 2026-07-14
plan: 115
title: Weather-source identity model — binding vs provenance, roles, and reachability
scope: The single owning plan for weather data ingestion + management. Supersedes 114; gates 081/082/113.
depends_on: []
blocks: [081, 082, 113]
supersedes: [114]
---

# Plan 115 — Weather-source identity model

## Status

**DRAFT.** Do not implement or dispatch subagents until promoted to READY.

**One open item gates READY: the live DB audit (§7).** The mac-mini was unreachable
on 2026-07-14 (`192.168.1.136`, 100% packet loss, ARP incomplete), so it is still
outstanding. It is not a formality — it decides whether Flow 6 has *ever* ingested a
row, which changes what parts of this plan are a fix versus a first implementation.

## Why this plan exists (and why 114 is superseded)

Weather ingestion has been designed along **two independent lines that never
reconciled**, confirmed by an independent repo-grounded investigation (Codex,
2026-07-14):

- The **071/072 line** treats `historical_forcing.source` as **immutable product
  provenance** and resolves multi-source reads at *read* time (`HybridForcingSource`,
  `PerSourceStoreReader`).
- The **081/082/114 line** treats `station_weather_sources.nwp_source` as a
  **role-specific operational binding**.

**They collide inside Flow 6**: selection uses binding identity, storage uses product
provenance, and the default read path uses binding identity again.

The root cause, stated plainly: **`nwp_source` is used as four different things at
once** — station binding key, adapter selector, forecast storage key, and historical
provenance tag. Every weather bug chased in this session is a symptom of that one
conflation:

| symptom | where |
|---|---|
| no forecast/reanalysis role; forecast path can select the reanalysis source | `types/station.py::StationWeatherSource`, `run_forecast_cycle.py::_select_nwp_source` |
| unfiltered binding lists reach consumers that cannot cope (found **twice**, in two different consumers, by two different reviews) | `run_forecast_cycle.py:1247`, `operational_inputs.py:327`, `hindcast.py:287/455`, `training_data.py:181` |
| Flow 6 may select **zero** configs and report success | `ingest_weather_history.py:309` |
| Flow 6's rows are **unreadable** by the default reader even if it did run | writes product tags (`meteoswiss_open_data_reanalysis.py:251`) / reads binding name (`store_backed_reanalysis.py:31`) |
| `extraction_type` is **already wrong** in the DB | CAMELS forcing is `BASIN_AVERAGE` (`camelsch_adapter.py:130`), onboarding writes the binding `POINT` (`onboarding.py:364`) |

Plan **114** correctly diagnosed one facet (the missing role) but is scoped to that
facet. It failed three successive reviews — each one finding a *different* unfiltered
consumer or a false rationale — because it was patching a symptom of an identity
problem it did not own. **114 is superseded by this plan**; its surviving, reviewed
content (the role enum, the migration split, the containment fix, the consumer table)
is carried forward here.

## Objective

Establish one coherent identity model for weather data, so that ingestion and
management are consistent by construction rather than by convention — and so that the
Nepal gateway (081/082) has a correct foundation to build on instead of inheriting the
conflation.

## Locked decisions (owner, 2026-07-14)

### D1. Split binding identity from data provenance

Two explicit namespaces that must stop being conflated:

- **`station_weather_sources` = OPERATIONAL BINDING.** Answers *"where do I get data
  for this station, and what is it for?"* — `(station_id, source, role, extraction_type,
  status)`.
- **`historical_forcing.source` = PROVENANCE.** Answers *"where did this number
  actually come from?"* — `camels-ch`, `meteoswiss_rprelimd`, `meteoswiss_tabsd`, …

The read path **resolves** a binding into the provenance tags that satisfy it.
Provenance is **preserved, never collapsed**: the distinction between preliminary
(`rprelimd`) and definitive (`tabsd`) data is real and will matter when definitive data
supersedes preliminary.

> **Rejected:** having Flow 6 write under the binding name to force one namespace. It is
> simpler and would make the `single` reader work immediately, but it destroys product
> provenance — unacceptable for a system that must re-ingest definitive over preliminary.

### D2. `hybrid` becomes the production default for reanalysis reads

`HybridForcingSource` + `PerSourceStoreReader` (Plan 072) were built **precisely** to
read across provenance tags with a per-parameter priority chain — then left opt-in, so
production runs `single` (`config/deployment.py:111`) and cannot see Flow 6's rows. Flip
the default to `hybrid`, and create the missing MeteoSwiss reanalysis **binding** so
Flow 6 selects a non-empty config set.

This is D1's read-side consequence: hybrid *is* the binding→provenance resolver. No new
machinery — we are promoting what exists from workaround to model.

### D3. Role filtering moves into the store: make the wrong thing unrepresentable

Replace the raw `fetch_weather_sources(station_id)` with **role-scoped accessors**:

```python
def fetch_forecast_binding(station_id) -> StationWeatherSource   # exactly 1, else ConfigurationError
def fetch_reanalysis_bindings(station_id) -> list[StationWeatherSource]  # 0..n
```

A caller then **cannot obtain** an unfiltered mixed list to misuse. This is the
`CLAUDE.md` "invalid states unrepresentable" discipline, and it is the only option that
structurally ends the whack-a-mole: two reviews each found a *different* consumer that
forgot to filter, which is the signature of a missing type, not of careless callers.

`fetch_weather_sources` survives **only** for display (`api/routes/api_stations.py:181`),
which legitimately wants every binding — and now shows each one's role.

## Scope

### Phase 1 — The type and the store

1. `WeatherSourceRole(FORECAST | REANALYSIS)` in `types/enums.py`; **required, no
   default** `role` field on the frozen `StationWeatherSource` (a default would silently
   mis-role exactly the Nepal bindings this exists to disambiguate). Two values suffice —
   a source serving both roles is two bindings under the `(station_id, nwp_source)` PK.
2. Role-scoped store accessors (D3). `fetch_forecast_binding` raises `ConfigurationError`
   on 0 **or** ≥2 — both are station-config faults, and tolerating an ambiguous set is
   exactly where today's non-determinism comes from.
3. **No `status` filter is added by this plan.** Today nothing filters on `status`
   (`store/station_store.py:219`), so an INACTIVE binding *is* currently used to forecast.
   Adding the filter here would silently start skipping stations on day one. It is a real
   bug — **its own plan**, with its own decision about what deactivating a source means.
   A test locks today's behaviour so it cannot drift in unnoticed.

### Phase 2 — Migration (two releases, per `cicd.md` one-version rollback rule)

**Revision `0030`** (off head `0029`): add `role` **nullable**; pre-flight allowlist
guard (raise on unknown `nwp_source` — an unknown name is a human decision, not a `CASE`
fallthrough); backfill; NULL-tolerant check constraint so the previous image can still
write during the rollback window.

**Backfill keys off the source NAME, not `extraction_type`** — because `extraction_type`
is *already wrong in the database* (D-list above: CAMELS is basin-average, stored as
POINT). `FORECAST` is a closed set: `services/onboarding.py` is the sole writer of
bindings, and the only forecast binding anyone writes is the hard-coded `icon_ch2_eps`
literal.

```sql
UPDATE station_weather_sources
   SET role = CASE WHEN nwp_source = 'icon_ch2_eps' THEN 'forecast' ELSE 'reanalysis' END;
```

App-side, `_row_to_weather_source` carries a transitional NULL shim for rows the *old*
image may write during the window. **The shim carries the same allowlist** and raises on
an unknown name — an open `else` would silently classify an unknown source as REANALYSIS,
bypassing the guard and contradicting the rule above.

**Revision `0031`** (deferred, non-gating): re-run the allowlist over stragglers, `SET NOT
NULL`, tighten the check, delete the shim. 081/082 depend on `0030` (the field), not `0031`.

### Phase 3 — Rewire every consumer through the role-scoped accessors

The complete, grep-derived consumer set (`grep -rn "fetch_weather_sources" src/`). Every
row is accounted for; any future consumer must be added here.

| # | Consumer | Needs |
|---|---|---|
| 1 | `run_forecast_cycle.py:1243-1247` — Phase A `flat_weather_configs` → `WeatherForecastSource` | **FORECAST** |
| 2 | `run_forecast_cycle.py:821` — Phase A `configs_for_source` (grid extraction) | **FORECAST** + matching `nwp_source` |
| 3 | `run_forecast_cycle.py::_select_nwp_source` — Phase B per-station | **FORECAST** |
| 4 | `run_forecast_cycle.py::_select_nwp_source` — Phase B group | **FORECAST** |
| 5 | `operational_inputs.py:327` — **live** past-dynamic assembly | REANALYSIS |
| 6 | `hindcast.py:287` / `hindcast.py:455` | REANALYSIS |
| 7 | `training_data.py:181` | REANALYSIS |
| 8 | `ingest_weather_history.py:250::_reanalysis_sources` (Flow 6) | REANALYSIS |
| 9 | `api/routes/api_stations.py:181` — display | *all*, with role shown (§Phase 6) |

**Retire `_select_nwp_source`'s heuristic entirely** — the exact-ICON pass, the
first-`BASIN_AVERAGE` pass, the `_ICON_NWP_SOURCE` fallback, and the now-false docstring.
The fallback *guesses* a Swiss source string, which is the guessing this plan exists to
kill, and is simply wrong for Nepal.

**Contain the raise per-station.** The group loop is already contained by its
`try/except … continue`. The per-station call is **not**: it sits before the nearest
`try`, and the function-level `try` in `run_forecast_cycle_flow` has **no `except`** —
only a `finally`. An uncaught `ConfigurationError` there aborts the cycle for **every**
station and group. Wrap it, mirroring the existing "configured model missing" pattern in
the same loop: log, `errors.append`, `stations_failed += 1`, `continue`.

**Behaviour change, named and accepted:** a station with **zero** weather-source rows
today still forecasts, via the hardcoded fallback — and
`test_falls_back_to_underscore_icon_source_string` locks that. It is **rewritten** (not
deleted) to assert the loud contained skip. Swiss behaviour is otherwise preserved for
every correctly-onboarded station. The underscore-vs-hyphen bug that test originally
guarded stays covered by its siblings, which use a real `icon_ch2_eps` binding.

### Phase 4 — Adapter-level guards (defence in depth)

Call-site filtering alone lets a future caller reintroduce the bug; the accessors (D3)
make that hard, these make it impossible:

- `store_backed_reanalysis.py:31` — reads `fetch_forcing(source=cfg.nwp_source)` for
  **every** config handed to it. Enforce REANALYSIS.
- `per_source_store_reader.py:45-52` — **discards `nwp_source` entirely**, reducing to
  unique `station_id`s against a *fixed* source tag, so it fabricates reanalysis reads for
  stations with no reanalysis binding. Filter before the `dict.fromkeys` reduction.
- `hybrid_reanalysis.py:61` — filter to REANALYSIS before fanning out to children.
- `meteoswiss_open_data_reanalysis.py:155-162` — add the role check. Its existing
  `nwp_source` / `status` / `extraction_type` checks all **stay**; the `extraction_type`
  one is a genuine *emission-shape* guard (this adapter only emits basin-average rows),
  **not** a role proxy. Do not remove it.
- `replay/nwp.py:40-42` — its same-source homogeneity check **stays**; post-115 it only
  ever sees FORECAST bindings, so the check becomes a real invariant instead of a tripwire.

> Why #1/#2 went unnoticed: the **production** ICON adapter ignores `station_configs`
> entirely (`meteoswiss_nwp.py:587`, `# noqa: ARG002`) — it downloads the whole grid. But
> `ReplayNwpAdapter` **raises** on a mixed list, and Plan 081's `RecapGatewayAdapter` is a
> *per-station* source that **will** read the list — it would be handed the ERA5-Land
> reanalysis binding and asked to fetch a forecast from it.

### Phase 5 — Make Flow 6 a real feed (D2)

1. Flip the production reanalysis default to `hybrid` (`config/deployment.py:111`).
2. Create the missing **MeteoSwiss reanalysis binding** at onboarding (role=REANALYSIS),
   so Flow 6 selects a non-empty config set instead of logging
   `weather_history.no_stations` and returning `0/0/0` **as a success**.
3. Make the empty-config case **loud**: a scheduled ingest that matches zero stations is a
   misconfiguration, not a no-op. It must not report green. *(This is the observability
   hole that let the condition persist undetected.)*
4. Fix the **CAMELS binding's `extraction_type`**: it is written `POINT`
   (`onboarding.py:364`) while its forcing records are `BASIN_AVERAGE`
   (`camelsch_adapter.py:130`). Requires a data migration for existing rows.
5. Add the converter guards Plan 071 specified but never landed —
   `basin_avg_to_records` / `point_forecast_to_records` must reject reanalysis tags
   (`preprocessing/converters.py:17,46`).

### Phase 6 — Surfaces and remaining gaps

- **API/dashboard**: `WeatherSourceResponse` gains `role`; the station-detail Weather
  Sources table gains a Role column. This is the operator surface for verifying the very
  bindings this plan introduces — pyright cannot catch its absence.
- **`train_models_flow` does not self-wire a reanalysis source** — it creates stores but
  no `forcing_store`/`forcing_source` (`train_models.py:220,248`), then forwards it to
  code that dereferences it when weather features are required (`training_data.py:186`).
  `run_hindcast.py:192` already has the correct pattern (`select_reanalysis_source`).
  Copy it. *(Real gap, covered by no plan.)*

## Tests

- Swiss round-trip unchanged for every correctly-onboarded station; the **one** sanctioned
  exception is the zero-binding station (Phase 3).
- The Nepal shape on Swiss infrastructure: a station with **two `BASIN_AVERAGE` bindings**
  (one FORECAST, one REANALYSIS) routes each path to the correct source.
- `fetch_forecast_binding` raises on 0 and on 2+ bindings.
- **Flow-level containment:** in a cycle where exactly one station has a broken binding,
  the others still forecast, the bad one lands in `stations_failed`/`errors`, and the flow
  returns normally. *Soundness: must fail against an uncontained raise.*
- **Forecast fan-out:** a two-binding station passes **only** the FORECAST binding to the
  `WeatherForecastSource`. Run it against `ReplayNwpAdapter`, which raises on a mixed list
  — the natural positive control. *Must fail against an implementation that forwards the
  raw list.*
- **Hybrid stack:** `PerSourceStoreReader` and `HybridForcingSource`, handed a raw list
  containing a FORECAST binding (and separately a FORECAST-only list), produce **no**
  reanalysis rows. `PerSourceStoreReader` needs its own case because it discards
  `nwp_source`. Note `tests/unit/adapters/test_per_source_store_reader.py:190-199`
  currently locks the mixed-config behaviour and must be updated.
- **Flow 6 reachability:** with the MeteoSwiss binding present and `hybrid` default, rows
  written under product tags are **readable end to end** by the default consumer. *This is
  the regression test for the double-dark feed; it must fail against today's wiring.*
- **Flow 6 empty-config is loud**, not a green zero.
- An INACTIVE FORECAST binding is **still selected** — locks the deliberate no-status-filter
  decision so it cannot drift.
- Backfill correctness + allowlist guard raises on an unknown `nwp_source`; the NULL shim
  raises on NULL + unknown name.

## §7 — The live DB audit (BLOCKS READY)

Read-only, against staging **and** production:

```sql
SELECT DISTINCT nwp_source, extraction_type FROM station_weather_sources;
SELECT source, COUNT(*), MIN(valid_time), MAX(valid_time) FROM historical_forcing GROUP BY source;
```

Plus: is `weather_history.no_stations` firing in the Prefect logs for the
`ingest-weather-history` deployment? That alone proves the dark feed without touching the DB.

It settles three things at once: whether the migration allowlist is complete; whether
Flow 6 has **ever** ingested a row; and whether `historical_forcing` is frozen at the
CAMELS import (`MAX(valid_time)` will show it immediately). If it is frozen, Phase 5 is a
**first implementation**, not a fix — and past-dynamic features have been stale in every
forecast since onboarding.

**Blocked 2026-07-14:** mac-mini unreachable (100% packet loss, ARP incomplete).

## Relationship to other plans

- **114** — **superseded by this plan.** Its reviewed content is carried forward.
- **081** (gateway adapter) — can be *built* in parallel; it is only *correct* on top of
  this identity model. Note `config.toml`'s adapter `type` is **decorative** — runtime
  hardcodes the adapter (`run_forecast_cycle.py:1090`, `ingest_weather_history.py:168`),
  so 081's adapter can be fully built and still be **dead in production wiring**. 081/082
  own that dispatch fix; this plan owns the identity it dispatches on.
- **082 Task 2C** — depends on this plan (`082.depends_on`: `114` → `115`).
- **113** (schedule/NWP-cycle alignment) — sequence **after**; align the schedule once the
  source path is coherent.
- **091** — stale against current code (claims 090 unmerged and that `mac-mini.toml`
  disables NWP; neither is true). Flag for cleanup, out of scope here.

## Dependency-ordered track

1. **Live DB audit** (§7) — gates everything.
2. **This plan** (identity model, roles, accessors, consumer rewiring, Flow 6 reachability).
3. **081/082** — gateway adapter + dispatch, on a correct foundation.
4. **082 Task 3B** — parametric multi-year backfill (Flow 6 is hardcoded to 60 days,
   `ingest_weather_history.py:50`).
5. **113** — schedule alignment.

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Doc sync (mandatory):**

- `docs/spec/types-and-protocols.md` — `StationWeatherSource` gains `role`; add the
  `WeatherSourceRole` enum; document the role-scoped store accessors.
- `docs/spec/database-schema.md:88-92` **and** `:542-546` — add `role`; **also fix the
  pre-existing staleness**: both still show `active: BOOL`, but the column has been
  `status` since Alembic `0009`.
- `docs/architecture-context.md:1718-1723` — same block, same two fixes.
- `docs/conventions.md:396` — add a `station_weather_sources.role` / `WeatherSourceRole`
  row (`forecast`, `reanalysis`).
- `docs/touchpoint-maps.md` — the operational-inputs map must name the role accessors.
- `docs/standards/cicd.md` — the `0030`→`0031` two-release sequence.

## Provenance

Independent repo-grounded investigation (Codex, 2026-07-14) commissioned after Plan 114
failed three successive reviews, each finding a different facet of the same conflation.
Owner decisions D1–D3 locked 2026-07-14. Supersedes 114.
