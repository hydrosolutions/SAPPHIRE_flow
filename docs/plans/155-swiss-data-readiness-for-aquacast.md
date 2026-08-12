---
status: DRAFT
created: 2026-08-12
plan: 155
title: Swiss data readiness for aquacast — HydroATLAS basin package, static aliasing, gap-free forcing depth
scope: Make the Swiss station set satisfy `cmal_pool_PT`'s declared input contract. Three data problems, one owner, one discipline — a Swiss HydroATLAS basin package (our Swiss basins carry CAMELS-CH attributes and NONE of the 50 statics PT declares), the Caravan↔HydroATLAS alias map re-derived against that package, and 210 days of GAP-FREE daily discharge + precipitation + temperature per station. Split out of Plan 152 (its T1, T1b, T1c) because it is a data workstream with a different discipline and owner from the integration engineering, and because it GATES Plan 152's go/no-go spike.
depends_on: [130]
blocks: [152]
supersedes: []
---

# Plan 155 — Swiss data readiness for aquacast

## Status
**DRAFT.** Split out of **Plan 152** (tasks T1, T1b, T1c) on 2026-08-12.

**Read Plan 152 first** for shared context: the selected artifact (`cmal_pool_PT`), its verified
contract, the owner decisions (D1–D12), and § *What the Swiss run can and cannot tell us*. This plan
deliberately does **not** duplicate that material — duplication is what produced the drift Plan 152
spent three review rounds correcting.

**Why it is separate:** these three problems are **data acquisition and shaping**, owner-driven, with
a different skillset from the packaging and integration engineering. And **T1c gates Plan 152's T0c**
— the go/no-go spike cannot hand-shape real Swiss inputs until the statics exist.

**Note to reviewers — this plan is one of a FAMILY (152 / 155 / 156 / 157).** Shared context (the
selected artifact and its verified contract, owner decisions D1–D13, and what the Swiss run can and
cannot prove) lives in **Plan 152 only, by design** — the siblings reference it rather than
duplicating it, because duplication is what produced the drift Plan 152 spent three review rounds
correcting. **"This plan does not explain X" is NOT a finding if X is in 152.** Do flag it if a
statement here CONTRADICTS 152, or if this plan depends on something no plan in the family owns.

## Objective

Make Switzerland able to feed PT at all. Switzerland is not a convenience here — **it is the only
track**: Nepal is out of reach (no operational DHM access, Plan 152 D2), so there is no fallback if
this fails.

PT's verified contract (Plan 152 T0.0): daily only, **210-day lookback**, 15-day horizon (a **5-day**
variant is being produced — Plan 152 G13), `future_dynamic = [precipitation, mean_temperature]`,
`past_dynamic = [discharge]`, **50 statics**, `max_nan=0`.

## The three problems

### G12 — Swiss basins carry no HydroATLAS statics (the blocker)
Swiss `Basin.attributes` come from the **CAMELS-CH** attribute row
(`adapters/camelsch_adapter.py:214`); no Swiss basin *package* has ever been imported; and the
HydroATLAS codes (`slp_dg_sav`, `gla_pc_sse`, `pre_mm_syr`, `glc_pc_s*`) appear in **exactly one file
in the repo** — the Nepal fixture (`tests/fixtures/basin_static/nepal-dhm-basins/`). Verified by grep.

So the shortfall is **not 21 renames**: it is a large fraction of PT's 50 statics, including all 22
`glc_pc_s*` land-cover classes and the HydroATLAS-coded soil/topography set, which have **no
CAMELS-CH counterpart under any alias**. `_static_inputs`
(`adapters/forecast_interface.py:1059-1071`) raises on every name it cannot find.

### G8 — the alias map, and why it must be re-derived
aquacast declares statics in **Caravan** names; a HydroATLAS package carries raw codes. Against the
**Nepal** fixture, 29 of the pooled 51 match exactly and 22 are a pure rename (**21 of which PT
needs** — it omits `degree_of_regulation`). **That map was derived against Nepal and must not be
assumed to carry over** — the same over-generalisation produced G12.

### G15 — `mean_temperature` vs `temperature`: a NAME mismatch nobody owns
aquacast declares **`mean_temperature`**; SAP3's canonical forcing names are
**`{"precipitation", "temperature"}`** (`config/deployment.py:132`), and the MeteoSwiss reanalysis
adapter emits `temperature`. So compatibility reports **both past and future forcing missing** under
the name this plan uses, and the operational read would look for a series that does not exist.

**Plan 157 scopes the UNIT boundary (`mm/day`) but not the NAME boundary** — this fell between the
siblings. **It belongs in the shim, with the units**: expose canonical `temperature` in the outward
FI requirement and translate internally to aquacast's `mean_temperature`. Recorded here because T3
must audit **stored `temperature`**, not a non-existent `mean_temperature` series. *(Assigned to Plan
157; tracked here so the dependency is visible from the data side.)*

### G1 — 210 days of GAP-FREE daily history
`max_nan=0` on every past variable. But `_missing_value_count`
(`adapters/forecast_interface.py:666-672`) counts **nulls in existing rows**; a completely **absent
day** carries no null and **escapes the gate**, and the past-dynamic read
(`services/operational_inputs.py:468-489`) does not resample onto or complete a cadence grid. So the
real requirement is **calendar completeness**, and the real risk is a silent truncation.

Separately, `operational_inputs` only **warns** on a short lookback (`:443-466`) — an under-fed
window degrades silently rather than announcing itself.

## T0 — Freeze the candidate manifest FIRST (review finding, 2026-08-12)

**The Swiss station list is not the aquacast candidate list.** Our onboarding manifest includes
**lake stations that cannot run a discharge model at all** — `docs/deployment/dress-rehearsal-2026-04-21.md`
§F7 records that Murten (**station 2004**) "was onboarded as a row but never marked
`station_operational` because all three registered models target `discharge` — Murten only has
`water_level`". The CAMELS-CH adapter emits `water_level` for such stations
(`adapters/camelsch_adapter.py:98`) and `discharge` only where `discharge_vol` exists (`:48-65`).

**Derive the candidate manifest from PT compatibility** — at minimum `forecast_targets` and
`measured_parameters` containing `discharge` — sweep the whole set for further non-discharge
stations, and **freeze the resulting count**. Every coverage figure in T1/T2/T3 is a fraction of
*that* denominator, not of the onboarding manifest. Getting this wrong inflates every readiness
percentage in this plan.

## Tasks

### T1 — Swiss HydroATLAS basin package: produce and import (closes G12)
**The hardest prerequisite, and it gates Plan 152's T0c.**

**The two halves have very different costs, and the first should be settled before scoping:**
- **Producing the package** (`basins.gpkg` + `static_attributes.parquet` + `feature_catalog.json`
  over the Swiss basin polygons) is the expensive half — and **we do not hold the extraction
  tooling**: `extract_hydroatlas.py` / `hydroatlas.py` are Gateway-side (Plan 117 cites them as prior
  art; nothing matches in this repo). **The Gateway already produced the Nepal package, so a Swiss
  package is plausibly a REQUEST rather than a build.** Establish which first — it is the difference
  between a ticket and a data workstream.
- **Importing it** is cheap and already built: `cli/import_basin_package.py` plus the Plan 120
  loader, which validates `feature_catalog.json` against the parquet columns.

**Exit gate:** every one of PT's **50** declared statics resolves, to a **non-null, finite** value
(`math.isfinite`) — note `_is_missing` (`services/basin_package_loader.py:1390`) rejects only `None`
and NaN, so **infinities pass it** and it is not a finiteness definition — for every station in the
T0 manifest. **`area` must be present** or the area-based `m³/s ↔ mm/day` conversion fails at predict.

**Red-first:** the Swiss candidate set resolves **0 of the 22 `glc_pc_s*`** statics today (proves
G12); after import, all 50 resolve.

### T2 — Caravan↔HydroATLAS static alias map (closes G8)
**Depends on T1** — re-derive the map against the **Swiss** package, do not port the Nepal map
unchecked.

**Approach — additive aliasing.** Emit the Caravan aliases as **additional columns** alongside the
HydroATLAS canonicals when building the static frame. Purely additive: our canonical namespace and
the `feature_catalog.json` / `required_by_models` seam (`services/basin_importer.py:185`) are
untouched, `_static_inputs` selects only what the model asks for so extra columns are inert, and —
importantly — **it needs no change to `adapters/forecast_interface.py`**, avoiding the Plan 151 and
Plan 157 collisions. *(Rejected: renaming at import, which churns our canonical namespace; or an
alias layer inside the FI adapter, which collides with 151's T2.)*

**The projection must cover the COMPATIBILITY path, not just the frame (review finding — this was a
hole in the original design).** Onboarding derives each station's available statics from the **raw**
`basin.attributes` key set (`flows/onboard_model.py:262-263`) and compatibility then subtracts PT's
**Caravan-named** `req.static_features` from that raw set
(`services/model_onboarding.py:251-252`). **So onboarding fails even if every frame is projected
correctly** — the check never sees the aliases. Training performs its missing-static check before
constructing the projected frame too. The projection must therefore operate on **attribute/key sets**
as well as frames, and be applied in both onboarding-compatibility paths and ahead of training's
check.

**Define alias collision semantics.** If a package ever carries both the raw code and the canonical
Caravan name, equal finite values are accepted and **conflicting values fail loudly** naming station,
alias, canonical name and both values. Never silently overwrite a package value.

**Red-first — the RED assertion is the positive one, and it must be asserted at BOTH boundaries:**
1. **compatibility** — PT reports zero missing statics for a Swiss station (fails today: the raw key
   set contains no Caravan names);
2. **frame** — a HydroATLAS-named static frame resolves all of PT's declared statics (fails today:
   `_static_inputs` demands exact declared names).
*(The converse — that an unaliased frame raises — already passes and is a characterization
assertion, not the gate. `_static_inputs` success alone is insufficient evidence.)*

### T3 — Gap-free 210-day forcing depth (closes G1)
Extend stored historical forcing and past-target coverage so every target station carries the full
daily lookback at **every** operational issue. Audit **gap structure, not depth**: leading, trailing
and internal gaps, duplicate and off-grid stamps, for discharge, precipitation and temperature.
Depends on Plan 130 (READY, unimplemented) for the MeteoSwiss reanalysis tail — **land it rather than
duplicating it**.

Additionally harden the silent-degradation hole: a station with insufficient lookback must yield an
**explicit assignment failure**, not a warning plus a forecast
(`services/operational_inputs.py:443-466`). **Red-first** — today it warns and continues.

*(Touches `services/operational_inputs.py`, which Plan 151 also edits — sequence after it or accept a
rebase.)*

## Phase dependency graph

```json
{
  "phases": [
    { "id": "T1", "tasks": ["T1"], "parallel": false },
    { "id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"] },
    { "id": "T3", "tasks": ["T3"], "parallel": false }
  ]
}
```

T3 is independent of the package work and is the long pole — start it first, in parallel with
establishing whether T1 is a request or a build.

## Non-goals
- Anything aquacast-specific beyond satisfying PT's declared contract (see Plan 152).
- Sub-daily / hourly depth — PT is daily-only; hourly returns with Plan 153.
- Nepal data readiness — out of reach (Plan 152 D2).

## Risks
- **T1 may be a data workstream rather than a ticket.** If the Gateway cannot produce a Swiss
  package, extraction over the Swiss polygons is comparable in cost to the radiation ingest Plan 152
  deferred as too expensive. **Establish this before committing.**
- **Gap structure may be worse than depth suggests, and there is NO safety net.** BAFU has
  documented blackout and weekly-publishing fragility; `max_nan=0` is unforgiving. **There is no
  fallback chain on the GROUP path** (Plan 152, under T5): a failed station goes dark for this model.
  Worse, the granularity is **inconsistent** — a `max_nan` violation drops one station
  (`adapters/forecast_interface.py:752-767`) but a future-coverage shortfall returns `{}` for the
  **whole group** (`services/run_group_forecast.py:406`). **T3 must size the question that actually
  matters: how often does ONE station take the entire Swiss group dark?** *(Note the repo comment at
  `run_group_forecast.py:381-383` claims "the fallback chain still runs, mirroring the STATION path"
  — that comment is wrong and seeded this error; worth correcting when the file is next touched.)*

## References
- `docs/plans/152-aquacast-pooled-model-integration.md` — parent; artifact contract, decisions,
  and what the Swiss run can and cannot prove.
- `docs/plans/130-temperature-reanalysis-live-tail.md` — READY, unimplemented; T3 dependency.
- `docs/plans/117-basin-static-artifact-architecture.md` / Plan 120 — the package format + importer.
