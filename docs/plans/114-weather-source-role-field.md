---
status: DRAFT
created: 2026-07-13
plan: 114
title: StationWeatherSource forecast/reanalysis role field
scope: Swiss-testable schema + flow-filter change; prerequisite for 081/082 NWP-source dispatch
depends_on: []
blocks: [082]
---

# Plan 114 — `StationWeatherSource` forecast/reanalysis role field

## Status

**DRAFT.** Do not implement or dispatch subagents until promoted to READY.
Grill-me **DONE** (2026-07-13, 7 decisions locked below). Next: WF1 (plan-review)
→ owner READY → WF2.

## Provenance

Surfaced by the **independent Codex review of the Plan 081 grill-me (2026-07-13)**.
The review found that the repo distinguishes a station's *operational forecast*
source from its *training/reanalysis* source only **implicitly, by
`extraction_type`**: Swiss onboarding stores `icon_ch2_eps`/`BASIN_AVERAGE`
(forecast) + `camels-ch`/`POINT` (reanalysis) (`services/onboarding.py:357-386`),
and `_select_nwp_source` picks the `BASIN_AVERAGE` binding while `_reanalysis_sources`
matches by source name. **This collapses for Nepal**, where gateway forcing is
`BASIN_AVERAGE` for *both* IFS (forecast) and ERA5-Land (reanalysis). Two concrete
failures the implicit scheme cannot prevent:

- **`_select_nwp_source` non-determinism** — it returns the *first* `BASIN_AVERAGE`
  binding, with no ordering (`store/station_store.py:219`) and no role field on
  `StationWeatherSource` (`types/station.py`), so a station with both an
  `ifs_ecmwf` and an `era5_land` `BASIN_AVERAGE` binding can route the forecast
  path to the reanalysis source (`flows/run_forecast_cycle.py:87-106`).
- **Forecast/reanalysis source-key confusion** — with no role field, the
  `RecapGatewayAdapter` (Plan 081) is forced into a single `NWP_SOURCE` identity
  that cannot be both the IFS forecast storage key and the ERA5-Land reanalysis
  selector at once (Plan 081 "NWP-Source Dispatch Design"; Plan 082 Task 2C
  Phase A→B round-trip).

Decision (owner grill-me 2026-07-13): fix it at the root with an **explicit role
field**, not a fragile implicit proxy. This is the "invalid states unrepresentable"
/ enums-over-implicit-proxies discipline (`CLAUDE.md` Type Driven Development).

## Objective

Add an explicit `WeatherSourceRole` enum and a `role` field to
`StationWeatherSource`, migrate the store, set the role explicitly at onboarding,
and make both flow selectors filter on `role` instead of inferring intent from
`extraction_type`. Swiss behavior is preserved exactly; the change unblocks a
correct multi-source (Nepal) dispatch in Plans 081/082.

## Non-goals

- No gateway/Nepal wiring, no `RecapGatewayAdapter` (Plan 081), no dispatch
  generalization (Plan 082 Task 2C — this plan is its prerequisite).
- No change to `extraction_type` semantics or to the `(station_id, nwp_source)`
  uniqueness of a binding.

## Scope — grill-me decisions (locked 2026-07-13)

### 1. Type — `role` is a required enum field, no default

Add `WeatherSourceRole(Enum)` = `FORECAST = "forecast" | REANALYSIS = "reanalysis"`
to `types/enums.py` (mirrors the `WeatherSourceStatus` / `SpatialRepresentation`
lowercase-value convention). Add `role: WeatherSourceRole` to the frozen
`StationWeatherSource` (`types/station.py:77-82`).

**Required, with no default.** There is no sane default: a default would silently
mis-role exactly the Nepal bindings this plan exists to disambiguate, which is the
bug rather than a mitigation of it. A missing `role` must be a construction error,
not a guess.

**Two values suffice — no `BOTH`.** A source that serves both roles does not arise:
snow forecast and snow reanalysis are distinct `nwp_source` strings, hence distinct
bindings under the `(station_id, nwp_source)` primary key.

### 2. Construction-site sweep

`role=` must be added to **every** `StationWeatherSource(...)` call — currently 42
across 22 files: `services/onboarding.py` (2), `store/station_store.py`
(`_row_to_weather_source`), the fakes, and ~30 test fixtures. Because the field is
required and keyword-only, pyright plus failing constructors surface every miss;
no site can be silently skipped.

### 3. Store + migration

Add a `role` column to `station_weather_sources` in `db/metadata.py:164-187`, with
`CheckConstraint("role IN ('forecast', 'reanalysis')")`, mirroring how
`extraction_type` and `status` are declared. Thread it through
`store_weather_source` (values **and** the `on_conflict_do_update` `set_` clause)
and `_row_to_weather_source` (`store/station_store.py:219-249`).

**Migration:** a standard incremental Alembic revision `0030` off the current head
`0029` (`alembic/versions/0029_hindcast_dedup_constraint.py`) — the chain is
committed and continuous. Three steps:

1. `add_column` nullable,
2. backfill `UPDATE station_weather_sources SET role = CASE WHEN extraction_type = 'point' THEN 'reanalysis' ELSE 'forecast' END`,
3. `alter_column ... nullable=False` and add the check constraint.

The backfill is **exact for all current data**, because `POINT`→reanalysis /
non-`POINT`→forecast *is* the implicit rule the code applies today (Swiss:
`camels-ch`/`POINT` reanalysis + `icon_ch2_eps`/`BASIN_AVERAGE` forecast). It is a
faithful materialisation of existing behaviour, not a new policy. New (Nepal) rows
set `role` explicitly and never rely on it.

### 4. Onboarding sets the role explicitly

`services/onboarding.py:357-386` — `camels-ch` binding → `role=REANALYSIS`,
`icon_ch2_eps` binding → `role=FORECAST`. Explicit at the construction site; the
backfill rule is never re-derived here.

### 5. Flow 1 — `_select_nwp_source` becomes a role lookup that can fail loudly

`flows/run_forecast_cycle.py:87-106` currently runs a two-pass heuristic: exact
`icon_ch2_eps` match, then first `BASIN_AVERAGE` binding, then a
`_ICON_NWP_SOURCE` fallback string. **Retire all three passes, the
`_ICON_NWP_SOURCE` fallback, and the now-false docstring.**

Replacement: select the single **active** binding with `role == FORECAST`. Raise
`ConfigurationError` (`exceptions.py:84`) when there is **0** or **more than 1** —
both are station-config faults that must surface at the boundary rather than be
papered over by picking a member of the set. This is what makes the selection
deterministic for a Nepal station carrying two `BASIN_AVERAGE` bindings; the old
code's non-determinism came precisely from tolerating an ambiguous set.

### 6. Flow 6 — `_reanalysis_sources` filters on role

`flows/ingest_weather_history.py:243-252` — add `source.role is
WeatherSourceRole.REANALYSIS` to the existing `nwp_source` name match, so a
forecast binding that happens to share a source name can never be pulled into the
training/reanalysis path.

### 7. Tests

- Swiss round-trip is **unchanged** (regression floor: the existing onboarding →
  Flow 1 → Flow 6 behaviour must be byte-for-byte identical).
- A station with **two `BASIN_AVERAGE` bindings** (one FORECAST, one REANALYSIS)
  resolves each path to the correct source by role — the Nepal shape, testable on
  Swiss infrastructure today.
- A forecast target with **0 FORECAST bindings** raises `ConfigurationError`.
- A forecast target with **2 FORECAST bindings** raises `ConfigurationError`.
- Migration backfill correctness (POINT → reanalysis, non-POINT → forecast).
- Onboarding sets both roles.

## Relationship to 081 / 082

- **Plan 081** (offline adapter) can be *built* in parallel — it does not need this
  field. But its "one adapter, two Protocols" dispatch design is only *correct*
  once this field exists (forecast storage keys off the `role==FORECAST` binding's
  source name; the adapter's `NWP_SOURCE` is the reanalysis identity only).
- **Plan 082 Task 2C** (dispatch implementation) **depends on this plan** — its
  Phase A→B round-trip and `_select_nwp_source`/`_reanalysis_sources` wiring assume
  role-based selection. `082.depends_on` gains `114`.

## Exit gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

## References

- Plan 081 `docs/plans/081-recap-dg-client-integration.md` (dispatch design)
- Plan 082 `docs/plans/082-recap-gateway-operational-readiness.md` (Task 2C)
- Plan 106 §4 (v1 critical-path roadmap — Wave 1 forcing spine)
