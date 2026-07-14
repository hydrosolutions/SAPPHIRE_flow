---
status: DRAFT
created: 2026-07-14
plan: 115a
parent: 115
title: Weather-source identity — role field, role-scoped store accessors, consumer rewiring
scope: Schema + routing only. No behaviour flip. Unblocks 081/082.
depends_on: []
blocks: [082, 115b, 115c]
---

# Plan 115a — Identity: role field, store accessors, consumer rewiring

> Shared context, root cause and locked decisions D1–D3 live in the umbrella:
> [Plan 115](115-weather-source-identity-model.md). Read it first.

## Status

**DRAFT — but the READY gate is now CLEARED.** The live DB audit ran 2026-07-14 against staging
(umbrella §Audit):

- **A1 (the gating query): PASS — 0 rows.** Both operational stations have **exactly one** forecast
  binding, so no station breaks under `fetch_forecast_binding`'s new contract. The behaviour change
  named below has **no victims** in the current fleet.
- **A2: the backfill allowlist is complete.** The only bindings in existence are `camels-ch`/`point`
  and `icon_ch2_eps`/`basin_average` — the `icon_ch2_eps → FORECAST, else REANALYSIS` rule covers
  reality exactly.

Nothing blocks this plan technically. **Owner promotes to READY.**

## Objective

Make the forecast/reanalysis distinction an explicit, type-enforced property of a station's
weather-source binding, and route every consumer through it. This is the piece 081/082 are waiting on.

**Schema and routing. No reader change, no default flip** — `single` stays the reanalysis default and
Flow 6 stays dark until 115b.

> **⚠️ It is NOT "no behaviour change" — an earlier draft claimed that and review falsified it.**
> There is exactly **one**, named and accepted: a station with **zero** forecast bindings forecasts
> today (via the retired fallback) and will be **loudly skipped** after this lands (§5). **Audit A1 in
> the umbrella exists precisely to find those stations before they break** — it is the gating query,
> and 115a cannot be READY until it returns zero rows or every row has an owner decision attached.

## Non-goals

Flow 6 reachability, the hybrid default flip, the existing-station binding backfill, and the
CAMELS `extraction_type` repair are **115b**. `0031` NOT NULL, the API/dashboard role column and
doc sync are **115c**.

## Scope

### 1. Type

`WeatherSourceRole(FORECAST | REANALYSIS)` in `types/enums.py` (lowercase values, mirroring
`WeatherSourceStatus`). Add `role: WeatherSourceRole` to the frozen `StationWeatherSource`
(`types/station.py`) — **required, no default**. A default would silently mis-role exactly the
Nepal bindings this exists to disambiguate; a missing role must be a construction error, not a guess.

**Invariant (D1): one `nwp_source` string serves exactly ONE role for a station.** The PK is
`(station_id, nwp_source)` (`db/metadata.py:186`) and the upsert conflicts on it
(`station_store.py:243`), so a name holding two roles would **silently overwrite**. Enforce loudly;
do not migrate the key.

**Construction-site sweep** *(restored from 114 — the previous 115 draft dropped it)*: `role=` must
be added to **every** `StationWeatherSource(...)` call — onboarding, `_row_to_weather_source`, the
fakes, and the test fixtures (dozens). The field being required + keyword-only means pyright and
failing constructors surface every miss; no exact inventory is carried here, it would only rot.

### 2. Store — role-scoped accessors (D3)

```python
def fetch_forecast_binding(station_id) -> StationWeatherSource        # exactly 1, else ConfigurationError
def fetch_reanalysis_bindings(station_id) -> list[StationWeatherSource]  # 0..n
```

`fetch_forecast_binding` raises `ConfigurationError` on **0** *or* **≥2** — both are station-config
faults, and tolerating an ambiguous set is precisely where today's non-determinism comes from
(`_select_nwp_source` returns the *first* `BASIN_AVERAGE` binding with no ordering).

Update the `StationStore` Protocol, `PgStationStore`, and every fake/replay store.
`fetch_weather_sources` **survives only for display** (`api/routes/api_stations.py:181`).

**No `status` filter is added by this plan.** Nothing filters on `status` today
(`station_store.py:219`), so an INACTIVE binding *is* currently used to forecast. Adding the filter
here would silently start skipping stations on day one. It is a real bug — **its own plan**, with
its own decision about what deactivating a source means. A test locks today's behaviour so it
cannot drift in unnoticed.

### 3. Migration `0030` (off head `0029`)

1. `add_column` `role` **nullable**;
2. **pre-flight allowlist guard** — raise on any `nwp_source` outside the allowlist rather than
   guessing. An unknown name is a human decision, not a `CASE` fallthrough;
3. backfill;
4. `CheckConstraint("role IS NULL OR role IN ('forecast','reanalysis')")` — NULL-tolerant, so the
   previous image tag can still write during the rollback window (`cicd.md` § Rollback:
   backwards-compatible for one version).

**Backfill keys off the source NAME, not `extraction_type`** — because `extraction_type` is
*already wrong in the database* (CAMELS forcing is basin-average; onboarding writes the binding
`POINT`). `FORECAST` is a closed set: `services/onboarding.py` is the sole writer of bindings
(verified — no script, migration, API route or bootstrap importer writes one), and the only
forecast binding anyone writes is the hard-coded `icon_ch2_eps` literal.

```sql
UPDATE station_weather_sources
   SET role = CASE WHEN nwp_source = 'icon_ch2_eps' THEN 'forecast' ELSE 'reanalysis' END;
```

**NULL-role read shim**, `_row_to_weather_source`: a NULL role (reachable only if the *old* image
wrote a row during the rollback window) maps by the same rule and logs
`weather_source.legacy_null_role` at WARNING. **It carries the same allowlist and raises on an
unknown name** — an open `else` would silently classify an unknown source as REANALYSIS, bypassing
the guard. Marked `# Plan 115c: delete with revision 0031`.

### 4. Onboarding sets the role explicitly

`services/onboarding.py::onboard_stations` (Step 4b): the forcing binding (`forcing[0].source`) →
`role=REANALYSIS`; the `icon_ch2_eps` binding → `role=FORECAST`. Explicit at the construction site;
the backfill rule is never re-derived here.

### 5. Rewire every consumer through the accessors

The complete consumer set. **Three separate reviews each found a different missed consumer**, so
this table is exhaustive by construction: it is `grep -rn "fetch_weather_sources\|forcing_source" src/ scripts/`,
and every row is accounted for. Any new consumer must be added here.

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
| 9 | `api/routes/api_stations.py:181` — display | *all bindings*, role shown (115c) |

**Retire `_select_nwp_source`'s heuristic entirely** — the exact-ICON pass, the
first-`BASIN_AVERAGE` pass, the `_ICON_NWP_SOURCE` fallback, and the now-false docstring. The
fallback *guesses* a Swiss source string: the guessing this track exists to kill, and simply wrong
for Nepal.

**Contain the raise per-station.** The group loop is already contained by its
`try/except … continue`. The per-station call is **not**: it sits before the nearest `try`
(`run_forecast_cycle.py:1498`), and the function-level `try` has **no `except`** — only a `finally`
(`:2016`). An uncaught `ConfigurationError` there aborts the cycle for **every** station and group.
Wrap it, mirroring the existing "configured model missing" pattern in the same loop: log,
`errors.append`, `stations_failed += 1`, unbind the contextvar, `continue`.

**Named, accepted behaviour change:** a station with **zero** weather-source rows today still
forecasts via the hardcoded fallback, and `test_falls_back_to_underscore_icon_source_string` locks
that. It is **rewritten** (not deleted) to assert the loud contained skip. Swiss behaviour is
otherwise preserved for every correctly-onboarded station. The underscore-vs-hyphen bug that test
originally guarded stays covered by its siblings, which use a real `icon_ch2_eps` binding.

### 6. Production reanalysis-source wiring — one factory, everywhere

*(Blocker from the 115 review.)* Four call sites construct or omit a reanalysis source
inconsistently, so any later default change (115b) would be **bypassed**:

- `flows/train_models.py:220,248` — creates stores but **no** `forcing_store`/`forcing_source`, then
  forwards it into code that dereferences it when weather features are required
  (`training_data.py:186`).
- `flows/onboard_model.py:527,705` — the **registered `onboard-model` deployment**
  (`cli/register_deployments.py:95`) passes `forcing_source=None` into training-data assembly *and*
  hindcast.
- `flows/onboard.py:129` and `scripts/onboard.py:247` — both **hardcode** `StoreBackedReanalysisSource`.

`flows/run_hindcast.py:192` already has the correct pattern. **Route all of them through the single
`select_reanalysis_source(forcing_store=…, mode=…)` factory** (`hybrid_reanalysis_factories.py:59`),
so the mode is a deployment decision made in exactly one place.

### 7. Adapter-level guards (defence in depth)

The accessors make the mixed list hard to obtain; these make misuse impossible:

- `store_backed_reanalysis.py:31` — reads `fetch_forcing(source=cfg.nwp_source)` for **every**
  config handed to it. Enforce REANALYSIS.
- `per_source_store_reader.py:45-52` — **discards `nwp_source` entirely**, reducing to unique
  `station_id`s against a *fixed* source tag, so it fabricates reanalysis reads for stations with no
  reanalysis binding. Filter before the `dict.fromkeys` reduction.
- `hybrid_reanalysis.py:61` — filter to REANALYSIS before fanning out to children.
- `meteoswiss_open_data_reanalysis.py:155-162` — add the role check. Its existing `nwp_source` /
  `status` / `extraction_type` checks all **stay**; the `extraction_type` one is a genuine
  *emission-shape* guard (this adapter only emits basin-average rows), **not** a role proxy. **Do
  not remove it.**
- `replay/nwp.py:40-42` — its same-source homogeneity check **stays**; post-115a it only ever sees
  FORECAST bindings, so the check becomes a real invariant rather than a tripwire.

> Why #1/#2 in the table went unnoticed for so long: the **production** ICON adapter ignores
> `station_configs` entirely (`meteoswiss_nwp.py:587`, `# noqa: ARG002`) — it downloads the whole
> grid. But `ReplayNwpAdapter` **raises** on a mixed list, and Plan 081's `RecapGatewayAdapter` is a
> *per-station* source that **will** read it — it would be handed the ERA5-Land reanalysis binding
> and asked to fetch a forecast from it.

### 8. Forecast storage contract (state it, before it bites)

*(Major from the 115 review.)* Forecast rows are stored under `forecast.nwp_source`
(`weather_forecast_store.py:34`) and read back by the selected station binding
(`operational_inputs.py:348`). That works only because the ICON adapter's `NWP_SOURCE` *equals* the
binding name. **Write the contract down**: a `WeatherForecastSource` MUST store under the selected
**FORECAST binding's** `nwp_source`, never under a product/provenance tag. Otherwise Flow 1 repeats
the exact Flow 6 unreadable-row bug for the next adapter — and Plan 081's gateway adapter is next.

## Tests

- Swiss round-trip unchanged for every correctly-onboarded station; the **one** sanctioned exception
  is the zero-binding station (§5).
- The Nepal shape on Swiss infrastructure: a station with **two `BASIN_AVERAGE` bindings** (one
  FORECAST, one REANALYSIS) routes each path to the correct source.
- `fetch_forecast_binding` raises on 0 and on 2+.
- **Flow-level containment:** in a cycle where exactly one station has a broken binding, the others
  still forecast, the bad one lands in `stations_failed`/`errors`, and the flow returns normally.
  *Soundness: must fail against an uncontained raise.*
- **Forecast fan-out:** a two-binding station passes **only** the FORECAST binding to the
  `WeatherForecastSource`. Run against `ReplayNwpAdapter`, which raises on a mixed list — the
  natural positive control. *Must fail against an implementation that forwards the raw list.*
- **Hybrid stack:** `PerSourceStoreReader` and `HybridForcingSource`, handed a raw list containing a
  FORECAST binding (and separately a FORECAST-only list), produce **no** reanalysis rows.
  `PerSourceStoreReader` needs its own case because it discards `nwp_source`. Note
  `tests/unit/adapters/test_per_source_store_reader.py:190-199` currently locks the mixed-config
  behaviour and must be updated.
- An INACTIVE FORECAST binding is **still selected** — locks the deliberate no-status-filter decision.
- Backfill correctness; the allowlist guard raises on an unknown `nwp_source`; the NULL shim raises
  on NULL + unknown name.
- One `nwp_source` with two roles is rejected loudly (not silently upserted away).

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

### Doc sync — ALL of it lands with 115a

*(Blocker from review round 6: an earlier draft deferred the schema docs to 115c. That violates the
repo rule "every code change updates affected docs" (`AGENTS.md:27`) and meant 115a was **not** a
standalone landing — 115c was holding 115a's exit-gate work. The `role` column is added **here**, so
its docs ship **here**. 115c keeps only the `0031` tightening docs.)*

- `docs/spec/types-and-protocols.md` — the `role` field, the `WeatherSourceRole` enum, and the
  role-scoped accessors on the `StationStore` Protocol.
- `docs/spec/database-schema.md:88-92` **and** `:542-546` — add `role`. **While here, fix the
  pre-existing staleness**: both still show `active: BOOL`, but the column has been `status` since
  Alembic `0009` (`db/metadata.py:179-185`).
- `docs/architecture-context.md:1718-1723` — the `station_weather_sources` block: add `role`, fix
  the stale `active` → `status`.
- `docs/architecture-context.md:1733` — **the "active entries" source-intersection paragraph.** It
  describes selection as intersecting on *active* entries, which **contradicts** this plan's
  deliberate decision to add **no** status filter (§2). Correct it, or the docs assert an invariant
  the code does not have.
- `docs/conventions.md:396` — add a `station_weather_sources.role` / `WeatherSourceRole` row
  (`forecast`, `reanalysis`) to the enum-value table.
- `docs/touchpoint-maps.md` — the operational-inputs / time-series-preprocessing map must name the
  role-scoped accessors (`assemble_station_operational_inputs` is a listed touchpoint).
- `docs/standards/cicd.md` — the `0030`→`0031` two-release sequence.
