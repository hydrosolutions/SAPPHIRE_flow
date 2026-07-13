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
Needs grill-me → WF1 (plan-review) → WF2.

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

## Scope (to be hardened in grill-me)

1. **Type** — add `WeatherSourceRole(Enum)` = `FORECAST | REANALYSIS` to
   `types/enums.py`; add `role: WeatherSourceRole` to the frozen
   `StationWeatherSource` (`types/station.py`). Parse-don't-validate at the boundary.
2. **Store** — add a `role` column to `station_weather_sources` with a migration;
   update `store_weather_source` / `_row_to_weather_source`
   (`store/station_store.py:233-248`, `:219-231`).
   **Backfill rule (grill-me to confirm):** existing rows are unambiguous under the
   current Swiss scheme — `POINT` → `REANALYSIS`, non-`POINT` (`BASIN_AVERAGE` etc.)
   → `FORECAST`. This is exact for current data because that IS the implicit rule
   today; new (Nepal) rows set `role` explicitly.
3. **Onboarding** — `services/onboarding.py:357-386` sets `role=REANALYSIS` on the
   forcing-source binding (`camels-ch`) and `role=FORECAST` on the ICON binding.
4. **Flow 1** — `_select_nwp_source` (`flows/run_forecast_cycle.py:87-106`) filters
   `role==FORECAST` (deterministic); drop the reliance on `extraction_type` /
   first-match for intent.
5. **Flow 6** — `_reanalysis_sources` (`flows/ingest_weather_history.py:243-252`)
   filters `role==REANALYSIS` in addition to the source-name match.
6. **Tests** — Swiss round-trip unchanged; a station with two `BASIN_AVERAGE`
   bindings (forecast + reanalysis) resolves each path deterministically by role;
   migration backfill correctness; onboarding sets roles.

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
