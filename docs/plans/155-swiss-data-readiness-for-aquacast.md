---
status: READY
created: 2026-08-12
plan: 155
title: Swiss data readiness for aquacast — HydroATLAS basin package, static aliasing, gap-free forcing depth
scope: Make the Swiss station set satisfy `cmal_pool_PT`'s declared input contract. Three data problems — (T1) IMPORT Caravan's published CAMELS-CH statics, namespaced under a `caravan:` prefix per D15, covering 148 of the 149 discharge stations; (T2) the Caravan↔HydroATLAS alias map derived against that import; (T3) 210 days of GAP-FREE daily discharge + precipitation + temperature per station. NOTE (2026-08-13): T1 was originally scoped as a Swiss HydroATLAS EXTRACTION; T0a proved the attributes already exist, published, and that the residual gap is ZERO basins, so the extraction and the `static-attrs-nepal` appliance are OUT OF SCOPE. Split out of Plan 152 (its T1, T1b, T1c) because it is a data workstream with a different discipline and owner from the integration engineering, and because it GATES Plan 152's go/no-go spike.
depends_on: [130]
blocks: [152]
supersedes: []
---

# Plan 155 — Swiss data readiness for aquacast

## Status
**READY** (owner, 2026-08-13). Split out of **Plan 152** (tasks T1, T1b, T1c) on 2026-08-12.

**T1 + T2 IMPLEMENTED (2026-08-13) — then REVIEWED and found NOT MERGEABLE.** The inventory below
is accurate and useful; **its two DEVIATION claims are SUPERSEDED** by the review section that
follows. Specifically: the collision guard is **not** "forward-compatible defensive code", it is
**dead by construction** (a single-valued resolver can never trip it), and the narrowing rationale
**conflates two distinct collision cases** — bare-CAMELS-CH-vs-Caravan (which D15 settles: Caravan
wins) and raw-code-vs-canonical-name *within* the Caravan namespace (which the plan requires to fail
loudly and which remains undetected). **D16 now supplies the missing piece the implementation guessed
at.** Read the review section before the deviations.

- **T1/T1b** — `adapters/caravan_attributes.py` (pure parquet parsing, sanitises
  NaN/Inf to `None`), `store/basin_store.py::PgBasinStore.merge_namespaced_attributes`
  (the dedicated additive operation T1b calls for — JSONB `||` merge, no new
  `basin_versions` row, no `material_change`, guarded to reject any key without the
  `caravan:` prefix), and `store/caravan_import.py::import_caravan_attributes`
  (end-to-end orchestration joining on `(network, code)` identity).
- **T2** — `services/caravan_statics.py`: `resolve_caravan_static_key` (D15's rule),
  `available_declared_static_keys` (the compatibility-boundary projection),
  `project_declared_static_attributes` (the frame-boundary projection, additive —
  original keys untouched, model-declared bare names added/overridden). Wired into
  all five call sites that used to read `basin.attributes`/`non_null_static_keys`
  directly: `flows/onboard_model.py::_validate_compatibility_task`,
  `services/model_onboarding.py::onboard_model`'s own compatibility step,
  `services/training_data.py::assemble_station_training_data` (both the missing-static
  gate and the frame build), `services/hindcast.py::_load_static_attributes`,
  `services/operational_inputs.py::assemble_station_operational_inputs`. No change to
  `adapters/forecast_interface.py` (confirmed, per the plan's design constraint).
- **T1's PROVENANCE fields (structured table in "Provenance of the Swiss parquet")
  are captured as a returned `CaravanImportProvenance` dataclass, NOT persisted into
  `basin_static_packages`** (deviation — see below).
- **DEVIATION — T2 "collision semantics" narrowed.** The plan's "Define alias
  collision semantics" text ("conflicting values fail loudly") reads, taken literally
  across ALL of PT's 50 statics, as being in tension with T1's own red test (resolving
  `area` must succeed and return Caravan's value, not raise) — CAMELS-CH's bare `area`
  and Caravan's `caravan:area` will differ for essentially every station, so a
  blanket "raise on any bare-name disagreement" rule would make `area` (and the other
  6 colliding names) permanently unresolvable, contradicting the plan's own exit gate.
  Implemented instead: `project_declared_static_attributes` always resolves a
  model-DECLARED name from its `caravan:` source (D15's explicit "no bare fallback" —
  Caravan wins, full stop, for names the model itself asks for); the collision guard
  is kept only for the ambiguity a fixed, one-way `CARAVAN_ALIAS` table cannot itself
  rule out (two different `caravan:` source keys resolving to the same declared name
  with differing values) — currently unreachable given the map's shape, so it is
  forward-compatible defensive code, not a tested behaviour. Flagging for owner
  confirmation rather than silently picking an interpretation.
- **DEVIATION — provenance not persisted to `basin_static_packages`.** The plan's
  provenance table (`source_datasets[].name/version/purpose`, `extractor_name`,
  `extractor_version`) is designed around `PackageManifest`/the full basin-PACKAGE
  write pipeline (`store/basin_importer.py::import_basin_package`) — geometry,
  correction branch, `material_change`, immutable fingerprint. T1b's additive merge is
  deliberately NOT that pipeline (see `merge_namespaced_attributes`'s docstring), so
  there is no natural `package_id`/`basin_static_packages` row for it to write without
  either (a) reusing `basin_static_packages` for a shape it wasn't designed for, or
  (b) building new schema — neither of which the plan's T1/T2 exit gates require.
  `import_caravan_attributes` returns a `CaravanImportProvenance` (source dataset name/
  version/purpose, extractor name/version) as a value the CALLER is responsible for
  recording; persisting it durably is follow-on work, natural to fold into T0b/T1c.
- **T0b, T3 remain out of scope for this pass**, per plan.
- **Real import not yet run.** `import_caravan_attributes` is built and tested against
  fixtures; running it against the real `/Users/bea/Downloads/data.parquet` over the
  live 148-station T0a manifest — and asking the modeller for the confirmed Caravan
  release version before doing so (plan's own guidance) — is an operational step for
  after this PR merges, not part of this implementation.

**Implementation scope on flipping READY = T1 + T2 only.** T0a is **done** (result below). **T3 is
DEFERRED and must NOT be implemented in this pass:** it depends on Plan 130 (READY but
*unimplemented*) for the MeteoSwiss reanalysis tail, and it edits
`services/operational_inputs.py`, which **Plan 151 is actively rewriting in a parallel session** —
implementing it now buys a guaranteed conflict on a file another session owns. T0b remains blocked on
Plan 157's shim.

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

## ⚠️ Post-implementation review — T1+T2 landed but are NOT mergeable (2026-08-13)

`/implement` (`wgfj4zqk7`) committed T1+T2 on `feat/plan-155-caravan-statics`
(`33112b2` + version bump `ecaf401`, worktree `../sapphire-plan155`) and then **ESCALATED**: its
verifier could not observe pytest finish, so `rounds: 0` — **the Codex review loop never ran**. An
independent Codex pass was run manually afterwards. It found a **BLOCKER**, and every finding below
was **confirmed by execution**, not accepted on assertion.

### BLOCKER — D15's "no bare-name fallback" is inverted at BOTH boundaries
This is the exact silent failure D15 exists to prevent, and it is live for `area`.

- **Frame path.** `project_declared_static_attributes` seeds `projected = dict(attributes)`
  (`services/caravan_statics.py:123`) and, when the `caravan:` source key is absent, `continue`s
  (`:126`) — leaving the bare legacy key in place. Executed:
  `project_declared_static_attributes({"area": 123.0}, ["area"])` → `{"area": 123.0}`. A
  Caravan-declaring model asking for `area` is handed **CAMELS-CH's** value, which rescales every
  discharge through the `m3/s <-> mm/day` conversion.
- **Compatibility path.** `available_declared_static_keys` is correct in isolation (returns
  `frozenset()` above), but all three callers **union the raw bare keys back in** —
  `non_null_static_keys(attrs) | available_declared_static_keys(...)`
  (`flows/onboard_model.py:274`, `services/model_onboarding.py:1275`, `services/training_data.py:250`).
  So compatibility reports `area` available when Caravan's is missing, and the gate that should catch
  the frame defect passes instead.

**The underlying design gap:** the code applies Caravan resolution to *every* model's declared names,
so it cannot simultaneously give PT no-bare-fallback and let an incumbent CAMELS-CH model resolve its
own bare `area`. It currently resolves that conflict silently in the incumbent's favour, which breaks
PT. **A fix needs an explicit notion of "this model is Caravan-declaring"** — that concept does not
exist yet and is not in this plan. Owner decision required before the fixer round.

### MAJOR — T2 collision semantics are not implemented (the guard is dead code)
`resolve_caravan_static_key` is **single-valued** (`:74`), so the guard at `:130-138` can never fire:
one declared name yields exactly one source key, hence one value. Executed — with
`{"caravan:slp_dg_sav": 11.0, "caravan:slope": 99.0}` and declared `slope`, it **silently returns
11.0** where the plan requires a loud failure. The importer *will* store both keys, since it prefixes
every input column (`store/caravan_import.py:63`).
**Latent, not live:** **0 of 21** alias pairs ship both names in the real parquet (verified). But the
implementer's characterisation — "unreachable given the map's shape", "forward-compatible" — is
wrong and creates false confidence: it is unreachable *by construction*, and no change to the map can
make it fire. Also, equal **infinities** are accepted (no finiteness check).

### MAJOR — provenance is ephemeral, and the release cannot even be supplied
`CaravanImportProvenance` carries every agreed field including `extractor_name="hsol"`, but it is
constructed *after* the writes and merely **returned** (`store/caravan_import.py:73`); nothing
persists it. **This inverts the advice recorded above.** There is no fingerprint and no immutability
guard, so a re-import with *different* source data is silently undetectable — and `basin_store.py:165`
merges with JSONB `||`, overwriting existing `caravan:*` values without a trace. Worse, the import API
accepts only `extractor_version` (`:34`), so even once the modeller confirms the Caravan release
**there is no parameter to pass it through** — `source_dataset_version` always takes its placeholder
default.

### MAJOR — T1's exit gate is neither enforced nor genuinely tested
The importer takes no frozen manifest and iterates only rows present in the parquet
(`store/caravan_import.py:49`), so it cannot detect a manifest station missing from the file, and it
marks a station matched even when required values sanitised to `None` (`:64`). The "all fifty" test
builds **25 synthetic values all hard-coded to `1.0`** (`tests/unit/services/test_caravan_statics.py:156`)
— it proves neither the 50 names nor any real coverage.

### MAJOR — test soundness is weaker than reported
The stash-based proofs for the *wiring* changes (`training_data.py`, `onboard_model.py`) are
legitimate red-first evidence. But every test in a **newly added module** fails against the parent
commit at **collection** (`ImportError`), which is not "failing for the right reason". No test covers
the non-empty legacy-`area` case (`:90` passes `{}`, hitting the early return), the raw-vs-canonical
collision, `_static_inputs` itself, or the hindcast/operational projection paths.

### What is sound
The 21-entry alias table matches the plan; prefix-only storage is guarded; the differing-`area`
helper test is genuinely non-coincidental; and the flow/training positive tests do fail under the old
raw-key behaviour. Full suite, ruff and the pyright ratchet all pass.

**Status: hold at branch, do NOT open a PR.** The blocker needs the Caravan-declaring-model decision
first.

## Implementation notes (read before writing code)

**Source data:** `/Users/bea/Downloads/data.parquet` — 296 rows x 216 cols, `gauge_id` =
`caravan_camels_ch_<BAFU code>`. A Caravan **passthrough**, not a re-extraction (see the provenance
section).

**The seven colliding names** — `area`, `p_mean`, `frac_snow`, `high_prec_freq`, `high_prec_dur`,
`low_prec_freq`, `low_prec_dur` — are **all among PT's 50**, which is why D15's no-fallback rule is
load-bearing rather than pedantic. `area` is the dangerous one: it drives the `m3/s <-> mm/day`
conversion, so silently taking CAMELS-CH's value rescales every discharge.

**Test soundness is the gate, not a formality.** Every locking test must fail against the pre-change
code **for the right reason** — prove it by running it, do not assert it in prose.
- **T1's red test**: resolving PT's `area` for a Swiss station returns **Caravan's** value, and the
  fixture's two `area` values are asserted to **differ**, so the test cannot pass by coincidence.
  Removing the namespacing must make it fail.
- **T2's red assertion is the POSITIVE one at both boundaries** (compatibility reports zero missing
  statics; a HydroATLAS-named frame resolves all of PT's declared statics). The converse — that an
  unaliased frame raises — **already passes today**, so it is characterization, not a gate.

**Data-reading gotcha:** CAMELS-CH CSVs ship **unit-suffixed headers** (`discharge_vol(m3/s)`), so an
exact-name match against a raw CSV read silently finds nothing and returns a uniform empty result
that looks like a finding. `camelsch.timeseries.load_basin_timeseries` strips the units — use the
loader. (This is why `adapters/camelsch_adapter.py:48`'s exact match is correct as written.)

**Version bumping:** every code commit folds in `uv run bump-my-version bump patch` plus a tag.
**Other sessions are consuming versions concurrently** — if a version is already claimed, bump again.

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
- **T0a — ✅ DONE, 2026-08-13 (executed).** Result below.

#### T0a result — the frozen manifest is **149 discharge stations**, and the extraction gap is **ONE basin**

Run over all 169 configured `basin_ids` (`config.toml`) using **two independent signals that agree
perfectly**: observed discharge loaded through *the adapter's own loader*
(`camelsch.timeseries.load_basin_timeseries`, not a raw CSV read — see the note below), and
CAMELS-CH's authoritative `water_body_type` column.

| | count | |
|---|---|---|
| configured `basin_ids` | **169** | the onboarding manifest |
| `water_body_type == lake` | **20** | **out of scope** (owner, 2026-08-13) — zero observed discharge, all 20 |
| `water_body_type == stream` | **149** | **← the frozen T0a denominator**; no other type exists in the set |
| streams Caravan covers | **148 / 149** | **99.3 %** |
| streams Caravan does NOT cover | **1** | `2446` |

The 20 lakes are exactly the 20 lake-typed gauges in the whole 169 — the sweep T0 mandates found no
further non-discharge station hiding among the covered 148. Each of the 20 has **0** observed
discharge values and ~14 610 water-level values, so the two signals never disagree. They are:
`2004` Murten/Murtensee, `2017` Zug, `2021` Ponte Tresa, `2025` Brunnen, `2027` St-Prex, `2028`
Genève-Sécheron, `2031` Unterägeri, `2066` St. Moritz, `2073` Silvaplana, `2074` Brissago, `2081`
Pfäffikon, `2088` Sarnen, `2097` Meisterschwanden, `2101` Melide-Ferrera, `2118` Murg/Walensee,
`2137` Gelfingen, `2168` Sempach, `2208` Ligerz, `2484` Lauerz, `2642` Neuchâtel.

**Caravan covers none of the 20 lakes and 148 of the 149 streams** — i.e. its exclusion rule is
essentially "no lakes", plus one stream.

**✅ The single survivor was DROPPED (owner, 2026-08-13). The manifest is 148 stations and the extraction gap is ZERO.** `2446`
**Gampelen-Zihlbrücke** is typed `stream` and has 13 832 observed discharge values, so it passes T0a
mechanically. But it is the **Zihlkanal** — the regulated outflow canal carrying Lac de Neuchâtel
into Bielersee, part of the Jura water correction — and its "catchment" is **2695.5 km²**, i.e. the
entire upstream lake system. Its discharge is a **regulation decision, not a rainfall-runoff
response**, which is the one hydrological regime a pooled rainfall-runoff model cannot represent, and
is the most plausible reason Caravan excluded it while keeping every other Swiss stream.

**Decision: `2446` is dropped; the extraction gap is ZERO.** Standing up a Swiss
HydroATLAS extraction — rebuilding three region-scoped datasets, including an ERA5-Land cube that
needed a 128 GiB EC2 host — to serve **one regulated canal whose physics the model cannot capture**
fails this plan's own test that the cost "is justified for ~20 basins only if those basins matter
operationally". If the owner wants `2446` forecast anyway, it is a candidate for a different model
class, not a reason to build the extraction pipeline.

**Consequence: T1's `Only if a genuine gap survives T0a` branch does NOT fire.** The
`static-attrs-nepal` appliance and the Swiss BasinATLAS/ERA5-cube work are **out of scope for Plan
155**. This removes the largest single cost item in the plan.

**Method note (worth keeping).** A first pass read the CSVs directly and reported all 21 as having
neither discharge *nor* water level — a uniform result that was a **parsing artefact**, not a
finding: CAMELS-CH ships unit-suffixed headers (`discharge_vol(m3/s)`), so an exact-name match hits
nothing. `camelsch.load_basin_timeseries` strips the units, which is why
`adapters/camelsch_adapter.py:48`'s exact `"discharge_vol" not in df.columns` is **correct as
written** — checked, not assumed, and not a bug. The rule that caught it: a classification where
every input lands in the same bucket is a failed instrument until proven otherwise.

- **T0b — confirm by compatibility, AFTER T2 and Plan 157's shim:** re-run the freeze through real PT
  compatibility and reconcile against T0a. A divergence is a finding, not a silent update.

Sweep the whole set for further non-discharge stations and **freeze the resulting count**. Every coverage figure in T1/T2/T3 is a fraction of
*that* denominator, not of the onboarding manifest. Getting this wrong inflates every readiness
percentage in this plan.

## Tasks

### T1 — Import Caravan's CAMELS-CH attributes (closes G12 for 148/149 discharge gauges)
**MAJOR SIMPLIFICATION (2026-08-13).** This task was scoped as a Swiss HydroATLAS extraction —
BasinATLAS download, regional clip, MERIT HFX, an ERA5-Land cube on a 128 GiB EC2 host with the
`temenos` toolchain. **Almost none of that is needed.** The attributes already exist, published by
Caravan, and are the **same ones `cmal_pool_PT` was trained on** — which is a correctness
requirement, not a convenience: attributes derived a different way are a distribution shift the model
never saw.

**Verified against the delivered parquet (296 rows × 216 cols, `caravan_camels_ch_<BAFU code>`):**
- **All 50 of PT's declared statics are present** — 29 under PT's own names, 21 under their raw
  HydroATLAS codes (all 21 confirmed present).
- **148 of our 149 DISCHARGE gauges are covered (99.3 %) — see T0a; the other 20 of the 169 are lakes and out of scope.** *(PT's own training subset covered only 70 —
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

**Storage shape — DECIDED (D15/A): namespaced inside `Basin.attributes` under the `caravan:`
prefix**, resolved as `caravan:` + (alias where the parquet ships a HydroATLAS code), with **no
bare-name fallback**.

**Onboarding steps:**
1. **Join on identity** — `caravan_camels_ch_<code>` → `(network="bafu", code=<code>)`. 148/169 match;
   the rest are T0a's problem.
2. **Load additively — and the mechanism does NOT exist yet; build it (2026-08-13).** The package
   importer's only update path is the correction branch, and
   `basin_store.update_basin_from_package` replaces `attributes`, geometry and area **wholesale**,
   sets `material_change=True` and returns an affected-artifact set
   (`store/basin_importer.py:810-833`) — exactly what T1 must not do. Merging in the caller does not
   help: it still runs the correction branch and still flags the incumbent artifacts T6 exists to
   compare against.

   **Add a dedicated additive operation** that unions namespaced keys into `Basin.attributes`
   **without** superseding the basin version or flagging artifacts. This is honest rather than a
   loophole: D15 guarantees the `caravan:` keys are **disjoint** from every existing key, so the
   operation cannot change any value an incumbent artifact was trained on — it is not a correction
   and must not be recorded as one.

   **Guard it structurally:** the operation must **reject any key lacking the `caravan:` prefix**, so
   it is incapable of modifying an incumbent attribute even by mistake. That guard is what makes
   skipping the supersede/flag machinery sound; without it this is just an unaudited write path.
3. **Resolve PT's 50 through the Caravan namespace only** — no silent fallback to a CAMELS-CH value
   of the same name. A missing Caravan attribute must fail loudly, not quietly resolve to a
   different derivation.
4. **Column set — store all 216 under the prefix.** A namespaced key cannot collide, so breadth
   costs only rows, and the next model needing a different subset does not force a re-import.
   Storing only PT's 50 is a defensible, reversible alternative; the namespacing itself is not.

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

**Resolution is gated on D16's model declaration.** `project_declared_static_attributes` and
`available_declared_static_keys` must take the model's declared static namespace and apply the strict
no-fallback rule **only** under `caravan`. Two concrete requirements the first implementation
violated:
1. **No bare-name fallback under `caravan`** — the projection must NOT leave a bare legacy key
   standing in for a declared name whose `caravan:` source is absent, and the compatibility callers
   must NOT union the raw bare key set back in for such a model (`flows/onboard_model.py:274`,
   `services/model_onboarding.py:1275`, `services/training_data.py:250`). A missing Caravan attribute
   is a **loud failure**.
2. **`native` models keep today's behaviour exactly** — bare keys, raw key set, no projection.

**Define alias collision semantics.** If a package ever carries both the raw code and the canonical
Caravan name, equal finite values are accepted and **conflicting values fail loudly** naming station,
alias, canonical name and both values. Never silently overwrite a package value. **The check must
look up BOTH keys** — a guard driven by a single-valued resolver is dead code and cannot detect the
case (the first implementation shipped exactly that). Require **finite** equality: equal infinities
must not pass.

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

## Provenance of the Swiss parquet — it is a Caravan PASSTHROUGH, not a re-extraction

**Established by execution, 2026-08-13.** aquacast's own attribute doc states that the FAO
Penman-Monteith PET variants "that Caravan also publishes are **not** computed — they need a separate
PET model", and its per-region coverage table lists seven regions (`nepal`, `camels_ind`, `lamah`,
`camels_nz`, `camels_us`, `camelsh`, `kaz`) — **`camels_ch` is not among them**.

The delivered parquet (296 rows × 216 cols, `gauge_id` = `caravan_camels_ch_*`) **does** carry
`pet_mean_FAO_PM`, `aridity_FAO_PM`, `moisture_index_FAO_PM`, `seasonality_FAO_PM`. Since the
extraction pipeline provably does not produce those, the Swiss file is **curated Caravan-published
values carried through** — the same treatment the doc records for `kaz` ("curated Caravan values
kept, validated, not overwritten").

**Three consequences:**

1. **This is good for us.** The values are canonical published Caravan, exactly reproducible from a
   citable release — there is no bespoke derivation to document or re-run. It confirms T1's
   import-don't-extract framing.
2. **The provenance string T1 writes must say so** — cite the Caravan release, *not* aquacast's
   extraction scripts, which did not produce these numbers.
3. **The Swiss import cannot double as the DHM worked example.** DHM's path is a genuine extraction
   (as `nepal`'s 11 gauges were); ours is a download. The operator guide still needs its own basis.

**Provenance is STRUCTURED, not a free-text string — and it is IMMUTABLE (owner decision, 2026-08-13).**
`PackageManifest` (`types/basin_package.py:50-62`) already separates the three facts we were trying to
compress into one token: `source_datasets[].{name,version,purpose}` says *what dataset*, and
`extractor_name`/`extractor_version` say *who produced the package*. So Sandro is recorded — just in
the field that means "who", not welded onto the dataset name:

| field | value | why |
|---|---|---|
| `source_datasets[].name` | `caravan` | the dataset, matching D15's `caravan:` attribute prefix |
| `source_datasets[].version` | `unconfirmed@delivered-2026-08-13` | honest and **stays true**; see the immutability warning |
| `source_datasets[].purpose` | `attributes` | |
| `extractor_name` | `hsol` (owner, 2026-08-13) | ← **the deliverer**: the organisation, not a person or a tool version, so it stays true as staff and tooling change |
| `extractor_version` | the aquacast commit, if obtainable | |

**Not `caravans`.** It sits one character from the `caravan:` prefix D15 pins, in the same system, so
a later reader cannot tell whether the two are the same thing or deliberately different — and
`name` is the field someone will try to match against a published Caravan release, which a
deliverer-encoded name makes unmatchable.

**⚠️ IMMUTABILITY MAKES A LATE CORRECTION EXPENSIVE.** `name`/`version`/`purpose` all feed the
canonical package fingerprint (`types/basin_package.py:248-249`), and a differing fingerprint under
the same `package_id` raises `BasinPackageRejectedError` — "a content change requires a NEW
package_id" (`store/basin_importer.py:316-372`). So filling the release in later is **not an edit**;
it is a new package and a re-import of all 148 basins, through the correction branch that sets
`material_change=True` and flags incumbent artifacts (`:810-833`).

**Therefore: ask the modeller for the Caravan release BEFORE running T1's import** — it is one
message, and it is much cheaper than the re-import. The placeholder above exists only so a blocked
T1 can still proceed; it is deliberately phrased so it never becomes *false*, only *less precise*.
If the answer arrives after import, do the re-import **before T6**, while no artifact yet depends on
the attributes.

## Decisions

- **D16 — how does resolution know a model is Caravan-declaring? — RESOLVED (owner, 2026-08-13):
  the MODEL declares it.** This is the fix for the review BLOCKER above; it is a prerequisite for the
  fixer round, not an optional refinement.

  A model declares its static-naming convention as a **class attribute**, exactly like the existing
  `model_tier` / `alert_eligibility` pattern that `discover_models` already reads and enforces
  (`services/model_registry.py:61-76`). Resolution then branches on the declaration instead of
  guessing:

  | declaration | resolution for a declared static `X` |
  |---|---|
  | `native` (**the default**) | the bare `X` — **every incumbent is byte-for-byte unchanged** |
  | `caravan` | `caravan:` + (`alias[X]` or `X`) **ONLY**; a missing key is a **loud failure** |

  **The default must be `native`**, so the strict regime is opt-in and no existing model changes
  behaviour when this lands. Plan 155 owns the resolution branch; **Plan 159's shim sets the flag on
  the aquacast model**, which is the natural owner since it already binds PT's config at import time.

  **Why the alternatives lose.** *Infer from the alias map* cannot work: the 29 **direct** statics —
  including `area`, the dangerous one — are textually indistinguishable from an incumbent's own
  names, so inference fails on exactly the case that matters. *Translate in the shim* is clean but
  moves the fix into an external repo that does not exist yet, so Plan 155 could not close. *Strip
  the colliding bare keys* breaks D15's explicit promise that incumbents keep their inputs untouched
  — an incumbent on a Caravan-imported basin would lose its `area` entirely.

- **D15 — how do Caravan attributes coexist with CAMELS-CH's? — RESOLVED (owner, 2026-08-13):
  option (A), namespace them inside the existing `Basin.attributes` dict.** Smallest change, no
  schema work, incumbents untouched, one uniform resolution rule for all 50 of PT's statics.

  **Prefix: `caravan:`.** A colon cannot appear in any real attribute name (all `[a-z0-9_]+`), so
  collision with a source attribute is **structurally impossible** rather than merely unlikely —
  `caravan_` would not have that property.

  **The resolution rule, in full:** a PT static `X` resolves to `caravan:` + (`alias[X]` where the
  parquet ships a HydroATLAS code, else `X`) — 21 aliased, 29 direct. **There is NO fallback to a
  bare `X`.** A missing Caravan attribute must fail loudly; falling back would hand PT a CAMELS-CH
  value of the same name and a different derivation — the exact silent failure this decision exists
  to prevent, and the one that would rescale every discharge through `area`.

  *(Rejected: (B) a per-source attribute table keyed by `(basin, source, name)` — cleanest
  long-term and makes provenance first-class, but schema + migration + store + read path; revisit
  when a THIRD attribute source appears, at which point (A)'s prefix convention is the natural thing
  to migrate. (C) store only PT's 50 and overwrite the 7 collisions — silently changes the
  incumbents' inputs.)*

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
