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

**READY — PARTIALLY MERGED. T1 + T1b + T2 are on `main`; T0b and T3 are not built.**
*(Bookkeeping corrected 2026-08-27: this block previously read a bare `READY` plus "Committed on
`feat/plan-155-caravan-statics` (hold-at-PR)", which had been true and then stopped being true. The
branch merged and the header was never updated, so the plan read as unstarted while half of it was
live in production. Nothing about the plan changed — only the record of it.)*

| task | state | evidence |
|---|---|---|
| **T0a** freeze the candidate manifest | ✅ **DONE** (executed 2026-08-13) | recorded at § T0 below |
| **T1** import Caravan CAMELS-CH attributes | ✅ **MERGED** | `aca60e8` (PR #151) — `store/caravan_import.py`, `adapters/caravan_attributes.py` |
| **T1b** onboarding design (no clobbering) | ✅ **MERGED** | same PR |
| **T2** Caravan↔HydroATLAS alias map + D15/D16 static resolution | ✅ **MERGED** | same PR — `services/caravan_statics.py` |
| **T0b** | ❌ **NOT BUILT** | "T0b, T3 remain out of scope for this pass, per plan" (§ below) |
| **T3** gap-free 210-day forcing depth (G1) | ❌ **NOT BUILT** | as above; depends on **Plan 130** (READY, unimplemented) |

**What merging T1/T2 did NOT do — and this is the operationally important part.** It shipped the
*machinery* to import Caravan statics; it did not *run* an import and it did not add an operator
entrypoint. Measured on the mac mini 2026-08-27: **0 basins carry a `caravan:`-prefixed key** and
`basin_static_packages` holds **0 rows**. Because `cmal_pool_pt` declares
`StaticNaming.CARAVAN` (`models/aquacast/_shim.py:467`) — strict namespaced resolution, no bare-name
fallback — it currently resolves **none** of its ~50 statics on that deployment. Closing that is
**Plan 188**, not this plan.

**Fixer round 3 (2026-08-14)** addressed round-2's BLOCKER (manifest-scoped exit gate) and its 4
majors (atomicity, compatibility/frame resolver divergence, test surface, docs) — see that section
below for detail. Rounds 3 and 4 are in the merged history (`bc8f5d1`, `de150f4`).

**Independent review status — 2026-08-14: THREE passes have run, the last a GO/NO-GO over the whole
branch.** The round-3 fixes came back **no blockers, no majors** (4 minors, all closed in `2ab2dc8`).
The GO/NO-GO found **no code blocker**, and specifically confirmed the two things that most needed an
outside check: the diff is **inert for existing NATIVE models** (training and hindcast paths
unprojected, FI naming survives adaptation, the forecast cycle supplies every assigned model), and
**neither accepted deferral is PR-blocking** — the unbound gate sets and the unpersisted provenance
gate the later *real operational import*, not the merge of a currently-unused capability. Its NO-GO
was **staleness only**: the branch had fallen behind `origin/main`, and this paragraph still claimed
no review had run. Both are now addressed.

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

**Status (superseded by the two fixer rounds below): hold at branch, do NOT open a PR.** The blocker
needs the Caravan-declaring-model decision first.

### Fixer round (2026-08-13) — BLOCKER + MAJORs addressed per D16

Implemented D16 (the model declares `static_naming`, `types/enums.py::StaticNaming`, default
`NATIVE`) and re-fixed T2's collision guard and T1's exit-gate checkability:

- **BLOCKER fixed at both boundaries.** `project_declared_static_attributes` now explicitly `pop`s
  a stale bare legacy key when its `caravan:` source is absent (was `continue`, leaving it standing).
  All five call sites (`flows/onboard_model.py`, `services/model_onboarding.py`,
  `services/training_data.py`, `services/hindcast.py`, `services/operational_inputs.py`) now branch on
  `declared_static_naming(model)`: a `CARAVAN`-declaring model resolves ONLY through the alias/direct
  rule (no union with the raw bare key set); a `NATIVE` model (every incumbent, unchanged) keeps the
  pre-155 raw-key-set/frame behaviour byte-for-byte. Proved sound by reverting each fix in place,
  confirming the new locking tests fail RED for the right reason, then restoring (not committed).
- **T2 collision guard fixed.** `project_declared_static_attributes` now checks BOTH an aliased name's
  raw-HydroATLAS-code key and its bare Caravan-name key (`_collision_keys`) when a delivered package
  carries both; equal FINITE values resolve silently, differing values or equal infinities raise
  `ConfigurationError` naming station, both keys and both values (`_values_agree`).
- **T1 exit gate made checkable.** `services/caravan_statics.py::verify_static_coverage` implements
  "every station resolves all of a model's declared statics to non-null, FINITE values" (`math.
  isfinite`, not just `is_missing_static_value`) as a reusable function over a `{station_code:
  attributes}` manifest, returning per-station `StaticCoverageGap`s.
- **Provenance/manifest gaps partially closed.** `import_caravan_attributes` gained
  `source_dataset_version` (threads the modeller-confirmed release into `CaravanImportProvenance`,
  previously unreachable) and `expected_codes` (`CaravanImportResult.missing_from_manifest` surfaces a
  T0a-manifest station absent from the parquet entirely — previously invisible to `matched_codes`/
  `unmatched_codes`, which only ever see codes the parquet contains). Durable persistence of
  provenance into `basin_static_packages` remains deliberately out of scope (needs new schema — see
  the original T1 deviation note above; still natural to fold into T0b/T1c).
- **Real import still not run** — same operational follow-on as before (ask the modeller for the
  confirmed Caravan release first).

Exit gates: ruff check + format, pyright ratchet (no new errors vs baseline), and the full `caravan`
test suite (unit + DB-backed integration) green.

### Fixer round 2 (2026-08-13) — real-FI-boundary BLOCKER + fallback-chain superset MAJOR addressed

An independent Codex pass over that round found the D16 fix itself incomplete at the REAL production
boundary, plus a new gap the round didn't examine. Addressed:

- **BLOCKER fixed — `static_naming` now survives `ForecastInterfaceAdapter`.** Discovery wraps every
  real FI model in `ForecastInterfaceAdapter` (`adapters/forecast_interface.py`), which forwards
  NOTHING by default (no `__getattr__` passthrough — see its `config_hash` property). Codex traced the
  path and found "raw model = caravan, adapted model = native": every one of the five D16 call sites
  branches on `declared_static_naming(model)`, but `model` downstream is always the ADAPTED instance,
  so a real Caravan-declaring FI model silently resolved as NATIVE. `services/model_registry.py`'s
  `_assert_model_classification_declared` — already the place `model_tier`/`alert_eligibility` are
  copied from the raw model onto the adapted one at discovery time — now copies `static_naming`
  identically. Locking test builds a real `_CaravanFakeFIModel` satisfying the FI Protocol, wraps it
  through the real `discover_models()` path (not a bare fake), and asserts the RETURNED adapted
  instance resolves `CARAVAN` (`tests/unit/services/test_model_registry.py::
  test_static_naming_survives_the_fi_adapter_boundary`).
- **MAJOR fixed — malformed `static_naming` now raises instead of silently downgrading.**
  `declared_static_naming` used `isinstance(..., StaticNaming) else NATIVE`, so a plausible near-miss
  (the plain string `"caravan"`) silently defaulted to NATIVE — and a test explicitly locked that
  silent-failure shape. A sentinel now distinguishes ABSENT (defaults to NATIVE, unchanged) from
  PRESENT-but-invalid (raises `ConfigurationError`, matching `_declared_model_tier`/`_declared_
  alert_eligibility`'s own pattern). The old locking test was inverted to assert the raise; a
  `static_naming = None` case is covered too.
- **MAJOR fixed — the fallback-chain/superset gap the first fixer round didn't examine.**
  `assemble_station_operational_inputs` shares ONE static frame across every model assigned to a
  station (`requirements_override` unions `static_features` across the whole fallback chain —
  `flows/run_forecast_cycle.py`), but the round-1 fix gated Caravan resolution on a single
  representative model (`first_model`) while resolving against that cross-model superset — silently
  handing a co-assigned model's Caravan-declared name the OTHER model's derivation for the identical
  bare name (`area` again: CAMELS-CH's value handed to a Caravan-declaring model, or vice versa).
  New `services/caravan_statics.py::resolve_shared_static_frame` scopes resolution PER assigned model:
  a name only a CARAVAN-declaring model asks for is projected through the alias/direct rule; a name
  only a NATIVE model asks for is left untouched; the SAME bare name declared under DIFFERING regimes
  by two co-assigned models raises `ConfigurationError` naming the station and the name(s) rather than
  silently resolving one value for both. `run_forecast_cycle.py` now threads `assigned_models` through
  as `static_naming_models`; `assemble_station_operational_inputs` defaults to `[model]` when the
  caller has no broader assignment set (the GROUP path). This is currently LATENT (no model in `src/`
  declares `StaticNaming.CARAVAN` yet) but is the designed, expected shape once PT is deployed
  alongside a NATIVE fallback per the project's linreg>ML>conceptual priority pattern.
- **MAJOR fixed — T1's exit gate wired into the import itself, made sound.** `verify_static_coverage`
  previously looked up only the primary key directly (bypassing T2's collision resolution entirely)
  and accepted `bool`/`str` as "finite" (`isinstance(True, int)` is `True` in Python). It now resolves
  through `project_declared_static_attributes` (collision-aware; a `ConfigurationError` from a raw
  collision is surfaced as a per-station `StaticCoverageGap.collision_error`, not swallowed) and a
  dedicated `_is_finite_numeric` that explicitly rejects `bool`/`str`. `import_caravan_attributes`
  gained an opt-in `required_static_names` parameter: when supplied, every manifest station must
  match, bind to a basin, AND clear `verify_static_coverage` before the function returns —
  `ConfigurationError` naming every shortfall — making the exit gate the operational import's own
  gate rather than a disconnected script. (No production caller exists yet because the real Caravan
  import itself has not run — same operational follow-on as before; the modeller's confirmed release
  is still pending. `required_static_names` is deliberately a parameter, not a hardcoded 50-name
  constant baked into this module, since the concrete PT static-name set is aquacast's own contract,
  not this module's.)
- **MAJOR fixed — changed-value re-import is now a loud failure, not a silent overwrite.**
  `merge_namespaced_attributes` used JSONB `||`, which silently prefers the incoming value on a
  changed re-import with no trace. It now fetches the basin's current attributes first; a key already
  present with a DIFFERING value raises `ConfigurationError` naming every conflict (existing vs.
  incoming) BEFORE any write is issued — an identical-value replay is still a no-op success. A
  durable, persisted source-version record and artifact-invalidation-on-genuine-revision remain
  deliberately out of scope (needs new schema — the plan's T1 deviation note; still natural to fold
  into T0b/T1c); this closes the SILENT half of the finding at the per-key level.
  `CaravanImportProvenance` also gained `content_fingerprint` (a stable SHA-256 of the parsed parquet
  rows), a durable content identity distinguishing "identical replay" from "genuinely different source
  data" ahead of any persisted lineage table existing.
- **minor fixed — the `prefix` bypass.** `merge_namespaced_attributes`'s `prefix` parameter is gone;
  the `"caravan:"` guard is hardcoded, so there is no longer any argument a caller could pass to
  defeat it (previously `prefix=""` made every key, including a bare `"area"`, pass the check).

Test soundness: every new/changed locking test above was proved to fail against the pre-fix code for
the right reason (fix stashed, test kept, run RED, fix restored) — see the fixer's own record for the
exact commands; not re-narrated here to avoid the doc drifting from what was actually run.

Exit gates re-run and green: ruff check + format, pyright ratchet (no new errors vs baseline), and the
full `caravan`-scoped unit + DB-backed integration test suite (including the new tests above).

**Status: fixer round 2 complete, but NOT ready for PR — this section previously claimed otherwise
and was wrong.** An independent round-2 review (see "Round-2 review" above) found the loop **stalled**
with **1 blocker + 4 majors** outstanding. What round 2 genuinely closed is the D15/D16 no-bare-fallback
blocker — verified by execution, including the co-assigned-differing-regimes case. What remains open is
the **T1 exit gate**, which is unusable in both directions (it raises on the 148 permanently
out-of-scope source rows, or validates nothing at all), plus atomicity under AUTOCOMMIT, the
compatibility-vs-frame resolver divergence, and the thin test surface. **Read the round-2 section for
the specified fix before making any change.**

## Round-2 review — D16 BLOCKER fixed; a NEW blocker, loop STALLED (2026-08-13)

The fixer ran 2 rounds and **stalled** ("a fix failed to reduce the blocker+major count"), leaving
1 blocker + 4 majors. Branch `784c566`. Verified by execution, not accepted on report.

### ✅ The original D15/D16 blocker IS fixed
`resolve_shared_static_frame` gates on D16's `StaticNaming`, executed on all four cases:
CARAVAN + `caravan:area` absent → `None`, **not** the leaked `123.0`; NATIVE → `123.0`, unchanged;
CARAVAN + present → Caravan's `500.0`; two co-assigned models declaring the same name under
**differing** regimes → `ConfigurationError`. That last case is a genuine subtlety the fixer found on
its own (a shared frame must not resolve one model's name under another's regime) and is a real
improvement over what D16 specified.

### 🔴 NEW BLOCKER — the T1 exit gate is unusable in BOTH directions
`store/caravan_import.py:137` raises on `if unmatched or no_basin or missing_from_manifest or
coverage_gaps`, where `unmatched` collects **every parquet row with no configured station**.
Measured against the real inputs: the parquet holds **296** codes, `config.toml` holds **169**
stations, **148** overlap — so **148 rows are permanently out of scope** and land in `unmatched` on
every run. Therefore:
- supply `required_static_names` → the gate **always raises**, even on a flawless 148-station import;
- omit it → the import **returns success with no validation at all**.

There is no third path, and tests lock both behaviours.

**Root cause — a scope conflation, and the plan is not ambiguous here.** T1's exit gate is
**manifest-scoped**: *"every station in the T0a manifest resolves all 50 of PT's statics"*. The
implementation made it **parquet-scoped**. A parquet code with no configured station is not a
failure — the file legitimately covers 296 Swiss gauges of which only ours are relevant, **by
design**. Fix:
1. **Gate on the manifest, never on the source file.** Failures = manifest stations *missing from the
   parquet*, *bound to no basin*, or *with coverage gaps*. Source-only codes are **reported, never
   fatal**.
2. **Make the operational entrypoint require `expected_codes` + `required_static_names`**, so the
   always-skip path cannot be taken by accident.
3. **Preflight, then write** (see the atomicity major below) — resolve every binding and coverage
   check *before* the first mutation.

### Remaining majors (unverified by me beyond the blocker; carried from the review)
- **Atomicity** — writes happen inside the row loop while validation runs after it, and the repo's
  production connection is **AUTOCOMMIT**, so a gate failure can leave a **partially applied**
  import. The canonical basin importer explicitly refuses this transaction shape; this one does not.
- **Compatibility and frame resolution disagree** — `available_declared_static_keys` checks only the
  primary raw-code key while `project_declared_static_attributes` will accept the secondary canonical
  key alone, so a station can pass compatibility and then raise at frame build (or the reverse).
  One resolver must serve compatibility, projection and coverage.
- **Tests still under-prove the surface** — the "all fifty" test exercises 25 names; no Caravan test
  reaches the real `model_onboarding`, `hindcast`, `operational_inputs`, or FI `_static_inputs`
  boundary, so removing a production wiring call could leave the suite green.
- **MINOR** — `docs/touchpoint-maps.md:482` still documents a caller-supplied `prefix` parameter that
  round 2 removed; it contradicts line 659 and the shipped signature.

## Round-3 review — loop reported CONVERGED (0/0), independent Codex disagrees (2026-08-14)

The workflow converged after 3 rounds (5 fixer commits, HEAD `9fe14b7`, full suite **3410 passed**).
An independent Codex pass over the FINAL state returned **not mergeable**. Note `codexFailedRounds: 1`
— one of the loop's own rounds ran without its Codex pass, and rounds 4-5 landed *after* the
implementer wrote its report, so its evidence covers `bc8f5d1`, not HEAD. **Every item below was
checked against the code; two of Codex's severities are corrected.**

### ✅ Verified genuinely fixed (by execution)
- **D15/D16 resolution** — CARAVAN + missing `caravan:area` → `None`, never the leaked `123.0`;
  NATIVE unchanged; CARAVAN + present → Caravan's value; differing co-assigned regimes raise.
- **The T1 exit gate** — now manifest-scoped, and **its locking test discriminates**: reverting
  `manifest_unmatched` → `unmatched` makes `test_an_out_of_manifest_unmatched_code_does_not_raise`
  **fail** while `test_a_manifest_unmatched_code_raises` still passes. `run_operational_caravan_import`
  requires both gate parameters, so the always-skip path is closed by the signature.
- **Atomicity** — `require_real_transaction` refuses AUTOCOMMIT before any read or write.

### ⬇️ Codex's BLOCKER — real, but NOT live; downgrade to MAJOR (hardening)
An empty/omitting `static_naming_models` makes `resolve_shared_static_frame` see no Caravan
declarations and return raw attributes — executed: `[]` yields `area=123.0` where `[caravan_model]`
yields `500.0` (`services/operational_inputs.py:574`). **But the invariant holds by construction at
the only caller**: `first_model = models.get(assembly_assignment.model_id)` (`:2076`, guarded at
`:2077`) and `assigned_models` is every resolving model of the same `sorted_assignments` (`:2093`),
so the invoked model is always in the list. It is a **service-contract gap reachable only by a future
caller**, not a live data-corruption path. Fix cheaply: have the service reject a
`static_naming_models` that omits the invoked model.

### ✅ Codex's MAJORs — both real, and both now CLOSABLE
They share one root: **no caller supplies the AUTHENTIC manifest and the AUTHENTIC 50 statics.**
`expected_codes`/`required_static_names` need only be non-empty, and the "all fifty" test uses
**22 invented placeholder names** — the test's own comment concedes it is "a committed, minimal
golden list standing in for PT's real 50-static declared contract" because "the real declared name
list lives in the external `cmal_pool_PT` artifact, not this repo".

**That premise no longer holds — the real list was extracted this session:**
`cmal_pool_PT/config.yaml` → `static_features`, **exactly 50 names**, splitting **21 aliased / 29
direct** against `CARAVAN_ALIAS` — an independent confirmation that the 21-entry alias map is right.
**Commit those 50 as a fixture** and the vacuous-test major closes and the exit gate becomes real
rather than nominal.

### MINORs
- **Value admissibility is not shared.** `_resolve_declared_value` rejects only missing values, so an
  infinity/string/bool under a `caravan:` key is "available" and gets projected, while
  `verify_static_coverage` would reject it as non-finite. Compatibility, frame and coverage agree on
  *key selection* but not on *validity* (`services/caravan_statics.py:241,283,507`). Matters because
  `area` is among the statics.
- **`services/training_data.py:253`** omits `station_code`, so a T2 collision reports
  `station '<unknown>'` — contrary to T2's "naming station, alias, canonical name and both values".

## Round-3 findings — DRIVEN DIRECTLY, four fixes + the contract vendored (2026-08-14)

Applied by hand rather than via another `/implement` cycle (the loop had just declared convergence
while these were open, so a fourth run risked repeating that). **Every fix has a locking test proven
to FAIL against the pre-fix code**, by reverting the fix and re-running.

1. **PT's REAL 50-static contract is now VENDORED** —
   `tests/fixtures/reference/cmal_pool_PT_static_features.json`, extracted from the artifact's
   `config.yaml :: static_features`. This replaces a golden list that padded 28 confirmed names with
   **22 invented `direct_static_NN` placeholders**, which proved cardinality rather than the
   contract. The test now follows the fixture, and the "one missing direct static" case **derives** a
   real direct name instead of hard-coding a placeholder that could vanish silently.
2. **The `static_naming_models` bypass is closed structurally** (`services/operational_inputs.py`) —
   the invoked `model` is now always part of the resolution set (`[model, *(static_naming_models or
   ())]`) rather than being *replaced* by the caller's list. An `[]` or a list built from a different
   assignment set previously made the resolver see no CARAVAN declaration and hand the model raw bare
   `area`. Uniting beats raising here: it cannot fail closed on a legitimate caller, and a genuine
   regime disagreement still raises via the existing differing-regimes guard.
3. **Admissibility is now shared** (`services/caravan_statics.py::_resolve_declared_value`) — it
   applies `_is_finite_numeric`, so an infinity/string/bool under a `caravan:` key is no longer
   reported "available" and projected while `verify_static_coverage` would reject it. `area` is among
   the 50, so a non-finite value there corrupted the `m3/s <-> mm/day` conversion.
4. **`services/training_data.py` threads `station_code`** into `available_declared_static_keys`, so a
   T2 collision names the station instead of reporting `'<unknown>'`. All call sites now pass it.

### ✅ End-to-end confirmation the vendored contract unlocked
Run against the delivered parquet: **all 50 of PT's declared statics resolve to a column the file
actually ships** (50/50, none unresolvable), splitting exactly **21 aliased / 29 direct** against
`CARAVAN_ALIAS` — an independent confirmation of the alias map T1b derived separately from the
modeller's `static_attributes.md`. Both facts are locked as tests, including that the 50 resolve to
**distinct** keys (an alias collapsing two declared names onto one column would feed a model the same
value twice).

### ⚠️ DELIBERATELY NOT FIXED — the gate's sets are still caller-supplied
Codex's remaining MAJOR stands: `expected_codes` / `required_static_names` need only be non-empty,
so a caller passing 147 stations or one static still "passes". **Not fixed here because the
authoritative runtime source of the 50 is the model's own `data_requirements.static_features`, and
the operational caller that would supply it does not exist yet** — the plan's standing "real import
not yet run" follow-on. The substantive half is closed: the *tests* now pin the real contract instead
of placeholders. **Binding the operational entrypoint to the model's declared statics and the frozen
T0a manifest belongs with that follow-on**, and should be done before the first real import.
### Fixer round 3 (2026-08-14) — round-2's BLOCKER + 4 majors addressed

- **BLOCKER fixed — the T1 exit gate is now manifest-scoped, never parquet-scoped.**
  `import_caravan_attributes` intersects `unmatched`/`no_basin`/coverage checks with the caller's
  `expected_codes` MANIFEST before deciding whether to raise; a parquet row with no configured
  station (out-of-scope, e.g. 148 of the real delivery's 296) is reported but never fatal.
  `required_static_names` without `expected_codes` now raises `ConfigurationError` immediately,
  before any read or write, closing the "always-skip" accidental-no-op path. Locked in
  `tests/integration/store/test_caravan_import.py` (`test_an_out_of_manifest_unmatched_code_does_not_raise`,
  `test_a_manifest_unmatched_code_raises`, `test_required_static_names_without_expected_codes_raises_immediately`)
  — proved RED against the pre-fix code (stashed, ran, confirmed genuine assertion failures, restored).
- **MAJOR fixed — atomicity.** `store/_helpers.py::require_real_transaction` (extracted from
  `basin_importer.py`'s own guard, now shared by both) refuses to run `import_caravan_attributes` at
  all — before the parquet is even read — unless `basin_store.connection` (a new public
  `PgBasinStore.connection` property) is inside a real, non-AUTOCOMMIT, already-open transaction.
  `TestTransactionGuard` (mirroring `test_basin_importer_persistence.py`'s own) and
  `test_exit_gate_failure_after_a_successful_merge_rolls_back_on_caller_rollback` (mirroring
  `TestPackageAtomicity`) prove both the refusal and the end-to-end rollback. Proved RED
  (stash/run/restore).
- **MAJOR fixed — compatibility/frame resolver divergence.** `available_declared_static_keys` and
  `project_declared_static_attributes` now share one collision-aware resolver
  (`_resolve_declared_value`), so a station carrying only the secondary (bare-Caravan-name) key for
  an aliased static resolves identically at both boundaries. Locked directly
  (`test_agrees_with_the_frame_when_only_the_secondary_key_is_present`), proved RED.
- **MAJOR partially addressed — test surface.** New direct-boundary tests reach
  `services/hindcast.py::_load_static_attributes` (new file, `test_hindcast_caravan_statics.py`) and
  `services/operational_inputs.py::assemble_station_operational_inputs` (including the
  `static_naming_models` fallback-chain differing-regime raise). The duplicated D16 compatibility
  loop in `flows/onboard_model.py::_validate_compatibility_task` and
  `services/model_onboarding.py::onboard_model`'s Step 1 was extracted to one shared function
  (`services/caravan_statics.py::resolve_available_static_keys_for_stations`), removing the
  divergence risk and adding direct unit coverage of the extracted logic. **Not fully closed**: no
  test reaches the real, full `onboard_model()`/`model_onboarding.py` pipeline end-to-end (the
  extracted-and-shared function is unit-tested instead — a deliberate scope call given the pipeline's
  heavy fixture cost), and no test reaches `adapters/forecast_interface.py::_static_inputs` directly
  (covered only transitively via `assemble_station_operational_inputs`/`assemble_station_training_data`
  producing an already-resolved frame). Residual risk for a future pass.
- **MINOR fixed** — `docs/touchpoint-maps.md`'s stale `prefix`-parameter reference corrected, plus new
  bullets documenting the shared resolver and the manifest-scoped exit gate as contracts that must
  not change silently.

Exit gates: `ruff check` + `ruff format --check` clean on every changed file (12 pre-existing
alembic-migration `E501`s untouched); pyright ratchet 428 ≤ baseline 459 (one transient new error in
the extracted `resolve_available_static_keys_for_stations`, fixed via `typing.cast` before the final
run); full unit suite (2753 passed) and full integration suite (591 passed, 7 pre-existing skips)
green.

### Fixer round 4 (2026-08-14) — an independent Codex diff review's BLOCKER + 3 majors addressed

An independent Codex pass over round 3's committed diff found the round-2 "make the operational
entrypoint require `expected_codes` + `required_static_names`" fix item was never actually
implemented (round 3 only closed the manifest-scoping half), plus 3 majors and 2 minors. Verified by
execution, not accepted on report.

- **BLOCKER fixed — the operational exit gate is no longer optional or bypassable.**
  `import_caravan_attributes` still defaults both gate parameters to `None` and accepts empty
  frozensets, so calling it with both omitted (or both empty) wrote data and returned success with no
  validation at all — a test explicitly locked that silent-success shape. Rather than tighten the
  flexible internal function (several of ITS OWN tests deliberately omit/narrow these parameters to
  exercise the reporting-only paths in isolation — `unmatched_codes`/`stations_without_basin`/
  `missing_from_manifest` without raising), a new `run_operational_caravan_import` is now THE
  production entrypoint: `expected_codes`/`required_static_names` are ordinary no-default
  keyword-only parameters (omitting either is a `TypeError` at the call site — Python itself refuses
  the "both omitted" bypass) and additionally checked non-empty at runtime before any read or write
  (closing the "empty sets validate nothing" variant of the same bypass). `import_caravan_attributes`
  stays as the documented, explicitly-internal building block; do not call it directly from a
  production flow. Locked in `tests/integration/store/test_caravan_import.py::
  TestRunOperationalCaravanImport` (5 tests: both TypeError-on-omission cases, both
  ConfigurationError-on-empty cases, and a full round-trip proving the gate still passes/fails
  correctly through the wrapper) — proved RED against the pre-fix code (stashed, ran, confirmed the
  silent-success test as the failure signature, restored).
- **MAJOR fixed — the collision guard now enforces "both present ⇒ both finite numeric ⇒ equal", not
  "filter nulls first, then maybe compare".** `_resolve_declared_value` used to filter out a
  missing/null candidate BEFORE checking whether both the primary and secondary key existed, so a
  delivered-but-null primary value alongside a valid secondary value silently resolved to the non-null
  one instead of raising — exactly the silent pick T2's guard exists to forbid. Presence is now
  `key in attributes` (a delivered-but-null column still counts as present and participates in the
  agreement check); usability is checked only after any collision is resolved or ruled out.
  `_values_agree` additionally requires BOTH operands to pass `_is_finite_numeric` (numeric,
  non-boolean, finite) before comparing equality, so two equal strings or a `True == 1` bool/int pair
  no longer silently "agree" (only finiteness of a float was checked before). Locked directly in
  `tests/unit/services/test_caravan_statics.py::TestCollisionSemantics` (null/valid, NaN/valid,
  string/string, bool/int) — proved RED.
- **MAJOR fixed — the changed-value replay guard's check/update race.** `merge_namespaced_attributes`
  read current attributes with a plain `SELECT`, then wrote unconditionally: two concurrent imports
  could both observe "no existing key" and the second's write would silently win. The `SELECT` is now
  `SELECT ... FOR UPDATE`, locking the basin row for the remainder of the caller's transaction, so a
  concurrent second caller blocks until the first commits, then re-reads the NOW-current row before
  comparing. Locked with a REAL two-connection concurrency test
  (`tests/integration/store/test_caravan_import.py::TestMergeNamespacedAttributesConcurrency`,
  mirroring `test_station_group_store.py`'s own pattern): two threads, each on its own connection,
  attempt DIFFERING values for the same new key synchronized via a barrier — exactly one wins, the
  other raises `ConfigurationError`, and the persisted value matches only the winner. Proved RED
  (without the fix, both threads observed no conflict and either could silently win with no trace,
  failing the "exactly one raises" assertion).
- **MAJOR fixed — the "all fifty" test now genuinely covers 50 names, not 25.** The prior test
  exercised 21 aliases + 4 direct-name samples. **SUPERSEDED 2026-08-14 — the golden list is no
  longer synthetic.** An interim version padded the 28 confirmed names with 22 placeholder
  `direct_static_NN` entries because "the real list lives in the external artifact, not this repo";
  that premise no longer holds. PT's **real** contract is now **vendored** at
  `tests/fixtures/reference/cmal_pool_PT_static_features.json` (from
  `cmal_pool_PT/config.yaml :: static_features`), and `PT_FIFTY_STATICS` reads it. The raw list is
  asserted to be 50 entries **and** 50 unique (asserting the frozenset alone would let a 51-entry
  fixture with one duplicate read as "exactly fifty"), the converse test **derives** a real direct
  name rather than hard-coding a placeholder, and the 50 are additionally verified to split exactly
  21 aliased / 29 direct and to resolve to **distinct** keys.
- **minor fixed — `network` is no longer a caller-suppliable parameter.** `import_caravan_attributes`
  hardcodes `"bafu"` — Caravan's CAMELS-CH parser only ever understands a `caravan_camels_ch_*` gauge
  id, so a non-"bafu" `network` argument could previously attach Swiss attributes to an unrelated
  station sharing the same code.
- **minor fixed — compatibility-time collision errors now name the real station.**
  `available_declared_static_keys` takes an optional `station_code`, forwarded from
  `resolve_available_static_keys_for_stations`'s per-station loop (`station.code`), so a conflicting
  alias at onboarding no longer reports `<unknown>`. Locked directly
  (`test_a_compatibility_time_collision_names_the_station`).
- **minor NOT fixed — real production-wiring end-to-end coverage (deliberate scope call, unchanged
  from round 3).** The review flagged that `services/model_onboarding.py:1269` and
  `flows/run_forecast_cycle.py:2146` are not exercised by a full plain-Python `onboard_model()` /
  fallback-chain forecast-cycle test, so deleting either one-line wiring call could leave the existing
  suite green. Both call sites forward into the SAME already-directly-tested shared function
  (`resolve_available_static_keys_for_stations` / `resolve_shared_static_frame`), which narrows the
  actual blast radius of an accidental deletion to "the wiring call itself", not the resolution logic.
  Building a full `onboard_model()` or `run_forecast_cycle.py` end-to-end test carries the heavy
  fixture cost round 3 already declined to pay for the identical reason. Left as documented residual
  risk rather than attempted here — flag if a future change touches either call site.

Test soundness: every locking test above for a correctness/bug fix was proved to fail against the
pre-fix code for the right reason (fix stashed, test kept, run RED, fix restored).

Exit gates re-run and green: `ruff check` + `ruff format --check` clean; pyright ratchet 428 ≤
baseline 459 (unchanged); full `caravan`-scoped unit + DB-backed integration suite (97 passed).

### Fixer round 5 (2026-08-14) — an independent Codex diff review's MAJOR addressed

An independent Codex pass over round 4's committed diff found one major: the collision guard's
"both present ⇒ must agree" rule (round 4's fix) treated two present keys that are BOTH
`None`/`NaN` as a *conflict*, not as "this static is simply missing" — raising
`ConfigurationError` with the factually wrong message `"resolves to differing values ... (None)
and ... (None)"`. This is reachable whenever a delivered Caravan package carries both the raw
HydroATLAS column and the bare canonical-name column for an aliased static, and neither has data
for a given basin — distinct from round 4's tested "one null, one valid" case. Left uncaught, it
would crash `onboard_model()`'s Step 1 (`resolve_available_static_keys_for_stations` has no
`try/except` around it) and, via `verify_static_coverage`, mark **every** declared static for the
station missing (`collision_error`) rather than just the one affected static.

- **MAJOR fixed — "both present" no longer implies a collision when both present values are
  independently missing.** `_resolve_declared_value` now short-circuits with a
  `all(is_missing_static_value(...) for key in present_keys)` check, immediately after the
  presence check and before the agreement loop: if every present candidate is itself missing, the
  function falls through to `_NO_CANDIDATE` — mirroring the no-present-keys case — instead of
  entering the agreement loop at all. The existing "one null, one genuinely valid" raise (round
  4's fix, via `_values_agree`/`_is_finite_numeric`) is unchanged; only the all-present-and-all-
  missing case is now exempted. Locked directly in
  `tests/unit/services/test_caravan_statics.py::TestCollisionSemantics::
  test_both_keys_present_and_both_null_resolves_to_missing_not_a_collision` (plus a one-null-
  one-NaN variant) and `TestVerifyStaticCoverage::
  test_both_keys_present_and_both_null_is_a_plain_missing_gap_not_a_collision` (asserts the exit
  gate reports a plain `missing_statics` gap for only the affected static, with
  `collision_error is None`, not a whole-station collision blowout) — proved RED against round 4's
  code (fix stashed, tests kept, all 3 failed with the wrong-message `ConfigurationError` /
  everything-missing shape, restored).

Test soundness: proved (fix stashed, the 3 new locking tests run RED against the pre-fix code,
fix restored). Exit gates re-run and green: `ruff check` + `ruff format --check` clean; pyright
0 errors on the changed file; `tests/unit/services/test_caravan_statics.py` (52 passed).

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
