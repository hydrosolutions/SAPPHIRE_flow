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

### G8 — the alias map, now AUTHORITATIVE
aquacast declares statics in **Caravan** names; the attributes parquet ships the raw **HydroATLAS**
codes. The mapping is no longer inferred: it is published in the modeller's own
`aquacast/docs/static_attributes.md`, whose table gives `code → canonical type` for every attribute.

Checked against PT's 50: **29 direct + 21 aliased = 50, zero unresolved.** The 21:
`slope←slp_dg_sav`, `stream_gradient←sgr_dk_sav`, `lake_fraction←lka_pc_sse`,
`air_temperature←tmp_dc_syr`, `precip_annual←pre_mm_syr`, `pet_annual←pet_mm_syr`,
`aet_annual←aet_mm_syr`, `aridity_index←ari_ix_sav`, `climate_moisture_index←cmi_ix_syr`,
`snow_cover←snw_pc_syr`, `snow_cover_max←snw_pc_smx`, `glacier_fraction←gla_pc_sse`,
`cropland_fraction←crp_pc_sse`, `pasture_fraction←pst_pc_sse`, `clay_fraction←cly_pc_sav`,
`silt_fraction←slt_pc_sav`, `sand_fraction←snd_pc_sav`, `soil_organic_carbon←soc_th_sav`,
`soil_water_content←swc_pc_syr`, `karst_fraction←kar_pc_sse`, `irrigated_fraction←ire_pc_sse`.

*(This independently confirms the map previously derived from the Nepal fixture — same count, same
pairs. The earlier warning to re-derive it stands as method; the answer happened to hold.)*

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

**Two-stage, because a compatibility-derived freeze is CIRCULAR** (seam review, 2026-08-12).
Running PT compatibility needs a live model object — which needs Plan 157's shim installed
(`services/model_registry.py:78-85`) — and it subtracts PT's **Caravan-named** statics from the raw
`basin.attributes` key set (`services/model_onboarding.py:251-252`), which cannot pass until T1 and
T2 land. But T1/T2/T3's gates are denominated in the manifest. So:
- **T0a — provisional manifest, NOW, from station data alone:** stations whose `forecast_targets` and
  `measured_parameters` contain `discharge`, excluding lake/`water_level`-only stations. This needs
  no model object and unblocks T1/T2/T3.
- **T0b — confirm by compatibility, AFTER T2 and Plan 157's shim:** re-run the freeze through real PT
  compatibility and reconcile against T0a. A divergence is a finding, not a silent update.

Sweep the whole set for further non-discharge stations and **freeze the resulting count**. Every coverage figure in T1/T2/T3 is a fraction of
*that* denominator, not of the onboarding manifest. Getting this wrong inflates every readiness
percentage in this plan.

## Tasks

### T1 — Import Caravan's CAMELS-CH attributes (closes G12 for 148/169 gauges)
**MAJOR SIMPLIFICATION (2026-08-13).** This task was scoped as a Swiss HydroATLAS extraction —
BasinATLAS download, regional clip, MERIT HFX, an ERA5-Land cube on a 128 GiB EC2 host with the
`temenos` toolchain. **Almost none of that is needed.** The attributes already exist, published by
Caravan, and are the **same ones `cmal_pool_PT` was trained on** — which is a correctness
requirement, not a convenience: attributes derived a different way are a distribution shift the model
never saw.

**Verified against the delivered parquet (296 rows × 216 cols, `caravan_camels_ch_<BAFU code>`):**
- **All 50 of PT's declared statics are present** — 29 under PT's own names, 21 under their raw
  HydroATLAS codes (all 21 confirmed present).
- **148 of our 169 configured gauges are covered.** *(PT's own training subset covered only 70 —
  Sandro filtered by basin QC and discharge sufficiency, so his training list understates what
  Caravan publishes.)*
- **21 gauges have no Caravan row:** `2004 2017 2021 2025 2027 2028 2031 2066 2073 2074 2081 2088
  2097 2101 2118 2137 2168 2208 2446 2484 2642`.

**ADD — do NOT overwrite the existing BAFU attributes (owner decision, 2026-08-13).** Our Swiss
`Basin.attributes` today are the **CAMELS-CH release** passed through verbatim
(`adapters/camelsch_adapter.py:239-254`). Caravan's are a **different derivation** of similar
concepts. Overwriting is actively harmful: the importer's correction branch replaces attributes,
geometry and area wholesale, sets `material_change=True` and returns an affected-artifact set
(`store/basin_importer.py:810-833`) — **invalidating the incumbent artifacts T6 exists to compare
against**. Keep both, clearly namespaced: incumbents keep their inputs, PT gets the ones it was
trained on, and a material disagreement between the two becomes diagnostic rather than silent.

**The residual gap is ~21 gauges, not 169 — and shrinks further.** Lake / water-level-only stations
are **out of scope for these attributes** (owner, 2026-08-13): they feed deep-learning **runoff**
models, and a lake station cannot run a discharge model at all (`2004`, Murten — see T0a and
`docs/deployment/dress-rehearsal-2026-04-21.md` §F7). **T0a must classify the 21 before any
extraction is scoped.**

**Only if a genuine gap survives T0a** does the extraction appliance apply — `hydrosolutions/static-attrs-nepal`,
a no-code appliance emitting a `basin-static-artifact/v1` ZIP from gauge lat/lons. Note it needs
three **region-scoped** datasets (MERIT HFX, a HydroATLAS clip, an ERA5-Land cube), all currently
Nepal/Pfaf-L2-45; a Swiss run means rebuilding them, and the ERA5 cube alone required a 128 GiB EC2
host (a 64 GiB one was OOM-killed). **That cost is justified for ~20 basins only if those basins
matter operationally.**

**Exit gate:** every station in the T0a manifest resolves all 50 of PT's statics to non-null, finite
values (`math.isfinite` — note `_is_missing`, `services/basin_package_loader.py:1390`, rejects only
`None` and NaN, so infinities pass it). **`area` must be present** or the area-based `m³/s ↔ mm/day`
conversion fails at predict.

**Red-first:** a Swiss station resolves **0** of PT's 50 statics today (the CAMELS-CH namespace
carries none of them); after import + aliasing, all 50 resolve.

### T1b — ONBOARDING DESIGN: how Caravan attributes land without clobbering ours

**The collision is real and it hits exactly the attributes PT needs.** The delivered parquet
(296 × 216) breaks down as: 6 identity, 192 HydroATLAS codes (incl. the 48 per-class
`glc/pnv/wet_pc_s*`), 8 Caravan climate indices under **bare names**, 2 `_ERA5_LAND`, 4 `_FAO_PM`,
and 4 other (`dis_m3_*`, `area_fraction_used_for_aggregation`).

CAMELS-CH attributes are the union of its own CSVs merged on `gauge_id`
(`camelsch/attributes.py:30-70`) — and it uses the **same classic CAMELS names**. So these overlap
by name with **different derivations and different values**:

`area`, `p_mean`, `frac_snow`, `high_prec_freq`, `high_prec_dur`, `low_prec_freq`, `low_prec_dur`

**All 7 are among PT's 50.** `area` is the dangerous one: it drives the area-based `m³/s ↔ mm/day`
conversion, so taking CAMELS-CH's value where PT was trained on Caravan's **silently rescales every
discharge** — passing every non-null/finite gate on the way through.

A flat merge into `Basin.attributes` is therefore **not** an option: it either overwrites ours or is
overwritten by ours, and both are silent.

**Onboarding steps (once D15 picks the storage shape):**
1. **Join on identity** — `caravan_camels_ch_<code>` → `(network="bafu", code=<code>)`. 148/169 match;
   the rest are T0a's problem.
2. **Load additively** — never through the importer's correction branch, which replaces attributes,
   geometry and area wholesale and flags incumbent artifacts (`store/basin_importer.py:810-833`).
3. **Resolve PT's 50 through the Caravan namespace only** — no silent fallback to a CAMELS-CH value
   of the same name. A missing Caravan attribute must fail loudly, not quietly resolve to a
   different derivation.
4. **Decide the column set** — PT's 50, or the full 216. The full set costs little and spares a
   re-import for the next model; the 50 is minimal and auditable.

**Red-first:** with both sources loaded, resolving PT's `area` for a Swiss station returns
**Caravan's** value, not CAMELS-CH's — and the two are asserted to differ in the fixture, so the test
cannot pass by coincidence. Removing the namespacing makes it fail.

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
    { "id": "T0a", "tasks": ["T0a"], "parallel": false },
    { "id": "T1",  "tasks": ["T1"],  "parallel": false, "depends_on": ["T0a"] },
    { "id": "T2",  "tasks": ["T2"],  "parallel": false, "depends_on": ["T1"] },
    { "id": "T3",  "tasks": ["T3"],  "parallel": false, "depends_on": ["T0a"] },
    { "id": "T0b", "tasks": ["T0b"], "parallel": false, "depends_on": ["T2", "plan-157"] }
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
