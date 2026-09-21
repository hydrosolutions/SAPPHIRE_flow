---
status: DRAFT
created: 2026-09-21
revised: 2026-09-21
plan: 306
title: A basin package's statics are invisible to every aquacast model, and their encodings are asserted by names that lie
scope: Make the statics delivered by a `basin-static-artifact/v1` package reachable by a model that declares `StaticNaming.CARAVAN`, and make each static's encoding a declared, enforced contract rather than a comment in one module. Explicitly NOT the basin package contract's geometry or provenance rules (Plan 120), NOT fixing the Caravan import path (Plan 155/188 — it works, and this plan may REUSE it), NOT onboarding the six DHM stations, NOT retraining any model, NOT the day-boundary/timezone family (252/254/258).
depends_on: []
blocks: []
related: [307]
open_decisions: [D1, D2, D3, D4]
source: 2026-09-21 — measured against the repo at `9dc07915`, the live mac-mini staging database at v0.1.927, the `nepal-dhm-basins` package delivered 2026-09-20, and the owner's `cmal_small` model tree. Every number below was measured on that date, and each says how.
---

# Plan 306 — basin-package statics: the namespace gap, and the encodings nothing declares

## Status

**DRAFT.** Consolidating rewrite 2026-09-21 — see the Changelog. Six independent review rounds
(one Claude, five Codex) were folded in place, which left the document carrying its own
archaeology; the corpus already records that layered corrections are how Plan 252 failed three
rounds on its text rather than its reasoning. This is the rewrite. **Every finding is preserved;
only the superseded wording is gone.** Review coverage and what each round changed are in the
Changelog.

Plan number **306 granted by the owner, 2026-09-21**.

## Why this exists

The `nepal-dhm-basins` package is model-ready on its own terms: it covers **78 of 78** of
`cmal_small`'s declared statics, with no NaNs and no unit conversions. It is nonetheless unusable
by that model as things stand — for a reason that has nothing to do with the data.

## What is measured

### 1. The package covers the model's statics exactly

Measured by loading `static_attributes.parquet` (6 basins × 93 columns) and applying the rename
table in the model's own `harmonization/harmonization_manifest.yaml`:

| | |
|---|---|
| `cmal_small` declared statics | **78** (`models/aquacast/configs/cmal_small.yaml`) |
| covered by package column name directly | 55 |
| covered after the manifest's `source_name → canonical_name` rename | the remaining **23 — 78/78** |
| non-identity conversions among those renames | **0** |
| required features with any NaN across the 6 basins | **0** |

The manifest's static block is one-to-one from HydroATLAS codes to canonical names —
`slp_dg_sav → slope`, `cly_pc_sav → clay_fraction` — every one `conversion: identity`.

🔑 **The repo already holds that same map.** `services/caravan_statics.py::CARAVAN_ALIAS` contains
all 23 aliases; its two newest entries (`forest_fraction`, `permafrost_fraction`) were added
2026-09-04 for `cmal_small`. **The mapping is not the gap.**

### 2. 🔴 The gap: a package-imported basin is in the wrong namespace

- **Every aquacast model opts into strict namespaced resolution.** `models/aquacast/_shim.py:468`
  sets `static_naming: ClassVar[StaticNaming] = StaticNaming.CARAVAN` on the shim base, inherited
  by every subclass. `types/enums.py:139-141` defines that as *"D15's strict `caravan:`-namespaced,
  **no-bare-fallback** resolution rule"*; `resolve_caravan_static_key` implements it — the key
  looked up for `forest_fraction` is `caravan:for_pc_sse`, **with no fallback to the bare name**.
- **The basin-package importer writes bare keys.** `store/basin_importer.py:628` takes the parquet
  row verbatim and writes it to `basins.attributes` with no prefix applied anywhere on the path.

⇒ **A basin imported from a `basin-static-artifact/v1` package resolves ZERO of the 78 statics.**

✅ **Independently confirmed** by a reviewer who traced the whole path rather than its two ends:
the loader preserves the parquet column names, `basin_importer.py:628` copies them unchanged, the
store persists them unchanged, the collision-aware resolver accepts the primary and secondary
**prefixed** keys only, and projection explicitly drops unresolved bare declared names.

⚠️ **It remains a deduction, not an observed failure** — the package has not been imported
anywhere, so nothing has run end-to-end. **T1 exists to prove it red. If T1 comes up green, this
plan is wrong and should be withdrawn.**

🔑 **This is a property of THIS package's key shape, not of the contract.** A compliant package
that already carried `caravan:`-prefixed keys would resolve fine. The defect is that **the contract
does not say which shape it emits and the resolver accepts only one** — so "contract-compliant"
and "usable by our models" are independent facts today. D1 turns on this.

**Confirming context** (live staging DB): the 148 Swiss basins carry 516 attribute keys each,
stored `caravan:`-prefixed by the Plan 155/188 path — written at `store/caravan_import.py:164`,
which namespaces every column on the way in. ⚠️ **The discriminating query is for the bare
HydroATLAS code** (`for_pc_sse`, unprefixed): a package-imported basin would carry it, a
Caravan-imported one would not. *Querying bare canonical names proves nothing — neither path ever
writes one.*

### 3. The encodings: the names lie, and nothing checks them

`CARAVAN_ALIAS` carries one comment, added 2026-09-04:

> NOTE the values are PERCENT (0-100), not 0-1 — aquacast's `*_fraction` naming is misleading, and
> nothing in this pipeline rescales them.

⚠️ **True, but narrower than it looks:** it sits on the two `cmal_small` entries and says "both" —
`forest_fraction` and `permafrost_fraction`. It makes no claim about slope or aridity. **The real
gap is that no per-feature encoding is declared anywhere.**

Measured ranges — Swiss from `basins.attributes` on staging, Nepal from the package parquet:

| feature | Swiss (148 basins) | Nepal (6 basins) |
|---|---|---|
| `for_pc_sse` | 11 – 97 | 8.4 – 45.6 |
| `cly_pc_sav` | 12 – 23 | 12.8 – 15.3 |
| `snd_pc_sav` | 38 – 52 | 49.1 – 61.2 |
| `gla_pc_sse` | 0 – 21.1 | 7.3 – 20.4 |
| `snw_pc_syr` | 9 – 61 | 7.7 – 21.8 |
| `slp_dg_sav` | 14 – 325 | 162.3 – 276.1 |
| `ari_ix_sav` | 128 – 527 | 56.6 – 127.0 |
| **`lka_pc_sse`** | **0 – 362** | 0.19 – 2.38 |

Two things the comment does not say:

- **`slp_dg_sav` and `ari_ix_sav` are not percentages at all** — they are raw HydroATLAS scaled
  integers (a slope of `325` is not 325 degrees). The general rule is "raw HydroATLAS encoding,
  per attribute", not "percent".
- 🔴 **`caravan:lka_pc_sse` exceeds 100 on 8 of the 148 Swiss basins** — 2308 Goldach-Bleiche at
  **362.0**, 2312 Salmsach-Hungerbühl at 350.0, then 157.0, 140.9, 109.0, 109.0, 108.0, 105.0. A
  lake extent of 362% is not a percentage. Misread scale or delivered defect is **not determinable
  from this repo** → D3.

**Nothing validates any of it.** No declared unit on `basins.attributes`, no guard, and the
agreement between the two import paths above holds **by construction, not by contract**.

### 4. The package declares no model requirements — but the field is live

`feature_catalog.json` carries `required_by_models` on each of its 92 features, **empty for all
92**. So §1's coverage is this plan's measurement, not the package's assertion, and a future
package that silently dropped a feature would be accepted and fail at predict time.

⚠️ **That field is consumed by the loader's hold/warn evaluation** — populating it is **not** a
documentation-only act. D4 owns that choice and records what it would actually do.

## Tasks

### T1 — prove the namespace gap red, before changing anything

**Outcome.** A test that imports a `basin-static-artifact/v1` package and asserts that a
`StaticNaming.CARAVAN` model **resolves all 78 declared statics with their expected values**.
Today it fails because none resolves.

⛔ **Assert the end state, never the zero.** A test that *asserts* zero resolution passes today and
**fails once T2 fixes it** — a characterization test pointing the wrong way. Zero-resolved is the
**pre-change diagnostic**, not the assertion. (Same defect, same form, as the one caught twice in
Plan 262's T1 — see `feedback_red_first_must_prove_the_fault`.)

**In.** One test under `tests/unit/` (or `tests/integration/` if a real import is needed).

🔴 **Two different artifacts are called `nepal-dhm-basins`. Do not confuse them:**

| | `tests/fixtures/basin_static/nepal-dhm-basins/` | the 2026-09-20 delivery |
|---|---|---|
| dated | 2026-07-17 | 2026-09-20 |
| basins | **1** (a DHM test basin) | **6** (447, 450, 604.5, 647, 670, 684) |
| parquet | 30,758 bytes | 33,541 bytes |
| `for_pc_sse` | **76.82** | 8.41 – 45.56 |

**Every number in §1 and §3 describes the DELIVERY, not the in-repo fixture** — `76.82` falls
outside §3's quoted range. **Use the in-repo fixture** (right shape, no external file needed) **but
do not expect §1/§3's values from it.**

**Out.** Any production change. Any change to `caravan_statics.py` or `basin_importer.py`. Adding
a log message or error field — that is a production change dressed as test support, and the
criterion below exists so none is needed.

**Verification — the discriminating criterion is a two-path comparison, not error text.** On a
package-imported basin **all 78** declared statics report missing; on a Caravan-imported basin
**the same 78 resolve**. Same model, same declared names, two import paths, opposite outcomes.

⛔ **Do not require the failure to name `caravan:`-prefixed keys — no code emits one on a miss.**
`services/training_data.py:526-531` logs declared canonical names; `caravan_statics.py:524-528`
builds `StaticCoverageGap.missing_statics` from declared names too. The only prefixed key in an
error is the collision raise (`:253-257`), a different failure.

⛔ A failure for any other reason is not red — not a `KeyError` from a missing basin, not a loader
rejection, not a model-construction error.

**Pre-change.** This task IS the pre-change evidence for T2.

### T2 — close the namespace gap (D1 decides how)

**Outcome.** A package-imported basin satisfies a `StaticNaming.CARAVAN` model's declared statics.

**In.** D1's chosen option, whose touched files D1 names per option.

**Out.** Weakening D15's no-bare-fallback rule as a side effect. That rule exists because inference
from the alias table cannot distinguish a Caravan direct name (`area`) from an incumbent model's
own same-named bare attribute (`types/enums.py:142-144`). If D1 lands on relaxing it, that is a
deliberate supersession with its own reasoning, not a convenience.

**Verification.** T1's test goes green for the right reason; the 148 Swiss basins' resolution is
unchanged (re-run whatever covers the Caravan path); and **for any option that prefixes on import,
all three fixtures pass** — bare-key, already-prefixed (no `caravan:caravan:` double prefix), and
**mixed-with-conflict** (collision detected before the normalized dict is built; outcome
independent of column order). `uv run pytest` clean.

### T3 — declare each encoding, and enforce it

**Outcome.** Each static's encoding is **declared per feature**, and an import whose declared
encoding disagrees with what the model expects is **rejected**, not warned about.

**In.** D2's answer, which must name three things: where the declaration lives, which boundary
enforces it, and what rejection looks like (refuse the import, or hold the basin).

⛔ **Documentation alone does not satisfy this Outcome**, and ⛔ **a numeric range guard cannot do
this job** — a `[0, 100]` check accepts a wrongly-rescaled `0.456` exactly as readily as a
legitimate `0.456%`. **Encoding is declared and compared, never inferred from values.** That is
also why §3's agreement between the two paths is evidence they happen to match, not a mechanism.

**Out.** Rescaling anything. ⛔ The model was trained on the raw encoding; converting to 0–1 would
be a 100× error on the percent-valued features and a wrong answer on the scaled ones. This task
makes the convention checkable; it does not change it.

**Verification.** Two tests, which are not the same test:

1. **Value preservation** — a known value survives *both* import paths and reaches the model
   bit-for-bit as delivered.
2. **Encoding rejection** — a package declaring an encoding the model does not expect is refused,
   the refusal naming the feature and both encodings.

⛔ Neither may decide an encoding from the magnitude of the numbers.

### T4 — dispose of the >100 lake percentages

**Outcome.** The 8 Swiss basins in §3 are either explained (a scale we misread, then documented) or
recorded as a data defect with an owner.

**In.** A question to the extractor/modeller — HydroATLAS's definition of `lka_pc_sse`, and whether
the delivered Caravan values carry a scale factor — and the answer, written here.

**Out.** Editing stored values. Excluding the basins.

**Verification.** The disposition is recorded with its source. A defect gets a named follow-on; a
scale is carried into T3's declaration.

## Owner decisions

### D1 — how does a package-imported basin satisfy a `CARAVAN`-naming model?

Four options. Whichever is chosen, **T2's In takes that option's touched files**.

1. **Prefix at import** — `basin_importer` writes prefixed keys. Simple, but the package contract
   is model-agnostic (§4) and this bakes one model family's namespace into a general importer.
   *Touches:* `store/basin_importer.py`, its tests.
2. **Resolve by provenance** — `basins.package_id` already exists; resolution consults it and
   accepts a package-sourced bare key. Keeps the importer neutral. *Touches:*
   `services/caravan_statics.py`, its tests. ⚠️ Weakens D15 — see T2's Out.
3. **The package declares its namespace** — a manifest field, validated by the loader. Cleanest
   contractually; it is an upstream `04-basin-static-artifact-contract` change and the extractor
   must emit it, so it needs coordination outside this repo. *(No scope conflict: the scope line
   excludes that contract's geometry and provenance rules, and a namespace declaration is neither.)*
4. **Extract the prefixing the repo already ships.** `store/caravan_import.py:164` builds exactly
   the required shape. ⛔ **This means extracting the prefixing operation into the package import
   path — NOT invoking the Caravan command**, which is Swiss-specific well beyond its manifest pin
   (`adapters/caravan_attributes.py:25-37,57` accepts only `caravan_camels_ch_` identities;
   `caravan_import.py:157` looks up network `"bafu"`) and would not make T1's package-import test
   green anyway. *Touches:* `store/caravan_import.py` (extract), `store/basin_importer.py` (call),
   their tests.

🔴 **Options 1 and 4 prefix unconditionally, which breaks the case §2 establishes.** A compliant
package may already carry prefixed keys; an unconditional prefixer turns
`caravan:for_pc_sse` into **`caravan:caravan:for_pc_sse`**, which the resolver cannot resolve.
Neither T1 (bare-key fixture) nor the Swiss regression (Caravan path) would catch it.

🔴 **And making the prefixing idempotent is not enough.** "Prefix unless already prefixed" maps
`for_pc_sse` and `caravan:for_pc_sse` onto the **same dictionary key**, so one value silently
overwrites the other during dict construction — before the resolver runs, with **input order
deciding the winner**.

⇒ **Any import-prefixing option must** leave already-prefixed keys untouched, **detect collisions
on the raw column set BEFORE the normalized dict is built**, state its policy (refuse, or a stated
precedence), and verify that policy **independently of column order**, against **three** fixtures:
bare-key, already-prefixed, and mixed-with-conflicting-values. *The third is the only one that
catches a silent overwrite.*

⚠️ **No existing guard covers this.** `_collision_keys` handles a *different* pair —
`caravan:for_pc_sse` and `caravan:forest_fraction`, both prefixed (the raw code and Caravan's own
canonical name). The bare-versus-prefixed collision has no guard at all.

### D2 — where does the encoding declaration live?

**Whatever D2 chooses must supply three things**, because T3 needs all three: the **delivered**
encoding per feature, the **expected** encoding per feature, and the **boundary that compares
them** with a rejection policy. A repo-side table and an upstream catalog field are both viable
carriers of the first two. *A comment carries neither; a range guard compares nothing. Neither is
an option.*

🔴 **The catalog's existing `unit` field is NOT the answer.** Measured on the in-repo catalog:

| feature | catalog `unit` | actual value | verdict |
|---|---|---|---|
| `area` | `km2` | — | correct |
| `for_pc_sse`, `cly_pc_sav` | `%` | 8 – 77 | correct |
| **`slp_dg_sav`** | **`degrees`** | **266.99** | 🔴 **false** — §3 shows a scaled integer, not 267° |
| **`ari_ix_sav`** | **`null`** | 56 – 527 | 🔴 **absent** |

For the two features §3 singles out, the catalog's `unit` is **wrong for one and missing for the
other**. Adopting it would import a false unit wearing the appearance of a declared one — worse
than today's comment, which at least does not claim authority. **Whatever D2 chooses must be
verified against these two features specifically.**

### D3 — are the >100 `lka_pc_sse` values a misread scale or a defect?

Not answerable from this repo. Needs the extractor or the modeller. **T4 obtains it.**

### D4 — how should a coverage gate actually be built?

The obvious answer — "populate `required_by_models` and let the loader check it" — **does not
work, and neither does the obvious repair.**

- `_evaluate_required_static` derives `catalog_required` from the catalog itself
  (`basin_package_loader.py:1237-1239`) and iterates only those entries (`:1372-1378`). **A feature
  dropped from both `feature_catalog.json` and `static_attributes.parquet` is never visited** — the
  one case a coverage gate exists to catch. A self-describing manifest cannot gate its own
  completeness.
- Making the comparison alias-aware does not fix that, because the iteration source is still the
  catalog.
- The membership test is `name in assigned`, comparing `catalog_required`'s **raw** names
  (`for_pc_sse`) against the model's **canonical** names (`forest_fraction`). For all 23 aliased
  features of a CARAVAN model it is false by construction → **warning, never hold**.

⇒ **A working gate must:** (1) obtain the requirement set **independently of the package**, via the
existing `assigned_model_features` resolver; (2) **iterate that set**, not the catalog's, resolving
each requirement against the package values regardless of catalog presence; (3) translate canonical
↔ package names on the way (the T2 mapping). **Keep a test for the deletion case** — it is the only
one that proves the gate is real.

**Two things that make this more than a one-line choice:**

- **(a) The "documentation" option is not inert.** `required_by_models` is consumed by the
  evaluator, so populating it **changes import acceptance today**: for a **direct, unaliased** name
  such as `area`, catalog name and canonical name are the same string, so a missing value becomes a
  **HOLD** — while aliased features still only warn. Populating it yields **inconsistent**
  enforcement. Either put documentation-only requirements where the evaluator does not read them,
  or treat this as the acceptance change it is, and task and test it.
- **(b) The gate needs the naming regime, which the resolver discards.**
  `build_assigned_model_features_resolver` returns `frozenset[str]` — a flat union dropping model
  identity and each model's `StaticNaming`. A `NATIVE` and a `CARAVAN` model can declare the **same
  name** and require **different stored values**; canonical translation cannot recover which is
  which. The gate must carry each requirement's regime through the resolver, with tests for a
  NATIVE assignment, a CARAVAN assignment, and the shared-name case.

⚖️ **So D4 chooses between** a documentation outcome (whose real cost is (a)) and the
assignment-aware gate (whose real cost is (b) plus a task). **D4 is CARRIED** — it does not gate
T1–T3.

## Watch items, not tasks

- **The Swiss path is not broken and must not be disturbed.** The 148 Swiss basins resolve today
  through the `caravan:` prefix written by the Plan 155/188 import.
- **`cmal_small` is `GROUP`-scoped**, so Nepal basins would be assigned as a group, not per
  station — for whoever sequences the DHM onboarding, not for this plan.
- **This plan does not make `cmal_small` runnable for Nepal on its own.** Statics are one input;
  the 30-day forcing window and the daily observation series are separate questions.
- 🔑 **This plan needs the staging mount Plan 307 T1 builds.** Importing a package on staging means
  getting a package **directory** readable inside a read-only-rootfs container.
  `docs/operations/basin-static-importer-runbook.md:54-61` says only `--package-dir <path>` and
  never explains how a host directory reaches that container; the base compose gives
  `prefect-worker` no operator bind. ⚖️ Recorded as `related: [307]`; **whether it becomes a formal
  dependency is the orchestrator's call.**
- **If D1 lands on option 1 or 4, already-imported basins keep their bare keys.** Nothing here says
  whether a re-import or backfill is needed. For the Swiss 148 the question is masked — their keys
  come from the Caravan path and are already prefixed — so **T2's verification would not surface
  the omission.** Any option that changes the written key shape must state what happens to rows
  written before it.

## Exit gates

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

🔴 **D1 and D2 must be ANSWERED before implementation starts — they are not carryable.** T2 is
entirely determined by D1 and T3 by D2. **D3 and D4 are carryable:** D3 is a question T4 asks, and
D4 chooses between two outcomes this plan describes in full.

- D1 and D2 are answered, and the task each determines names the chosen option and its files.
- T1's test asserts the **end state** (all 78 resolve, with expected values), failed before T2 and
  passed after, and its discriminating evidence is the **two-path comparison** — not the text of
  any error message.
- The 148 Swiss basins' static resolution is unchanged — demonstrated, not assumed.
- No stored attribute value was rescaled by this plan.
- T3's two tests both exist, and neither infers an encoding from a value's magnitude.
- For any prefixing option, all three T2 fixtures pass, including mixed-with-conflict.
- D3 and D4 are each answered or explicitly carried, with the carrier named.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"],
      "note": "prove the namespace gap; if this comes up green the plan is withdrawn" },
    { "id": "P2", "tasks": ["T2"], "depends_on": ["P1"], "requires_decision": "D1",
      "note": "close the namespace gap" },
    { "id": "P3", "tasks": ["T3"], "depends_on": ["P2"], "requires_decision": "D2",
      "note": "declare and enforce the encoding" },
    { "id": "P4", "tasks": ["T4"], "parallel_with": ["P1", "P2", "P3"],
      "produces_decision": "D3",
      "deferred_outcome": "if the extractor has not answered when P1-P3 complete, T4 closes as CARRIED with the >100 values recorded as an open data question and this plan named as its carrier",
      "note": "T4's work IS obtaining D3; it cannot require D3 as an entry condition" }
  ]
}
```

## Changelog

**2026-09-21 — consolidating rewrite.** Six review rounds had been folded in place, leaving each
correction sitting beside the wording it replaced. Rewritten per the house rule: superseded text is
deleted, not annotated. No finding was dropped.

Review history, and what each round changed:

| round | reviewer | outcome | what it found |
|---|---|---|---|
| 1 | Codex | NEEDS CHANGES (3 major, 2 minor) | T3 promised enforcement while permitting documentation; D4's premise wrong; exit gates let deciding questions stay open; T1's red inverted; the alias comment overstated |
| 1 | Claude (independent) | NEEDS CHANGES (**1 blocker**, 6 major, 6 minor) | **T1's criterion was unachievable — no code emits a prefixed key on a miss**; two artifacts share the name `nepal-dhm-basins`; the catalog `unit` field is false for `slp_dg_sav`; D4's namespace mismatch; D1 omitted option 4; §2's confirming query was non-discriminating |
| 2 | Codex | NEEDS CHANGES (**1 blocker**, 4 major, 1 minor) | **the round-1 fix left the old criterion standing in the exit gates**; D2 still offered ruled-out options; D4 still missed drop-from-both; D1 option 4 understated; T4's phase required the decision it produces |
| 3 | Codex | NEEDS CHANGES (1 major) | options 1 and 4 would double-prefix an already-prefixed package |
| 4 | Codex | NEEDS CHANGES (1 major) | idempotent prefixing silently collapses two keys into one; the `_collision_keys` citation was wrong |
| 5 | Codex | NEEDS CHANGES (2 major) | D4's "documentation" option is not inert; the gate needs the naming regime the resolver discards |
| 6 | Codex | **APPROVE** | no findings at any severity |

⭐ **The two blockers were the same failure in different clothes: a correction applied to one site
while its twin stood untouched.** It happened three times across this plan and 307 — the exit-gate
criterion, a mount count, and a guard citation. Sweep by **value**, never by site. See
`feedback_correction_notes_do_not_replace_wrong_text` and `feedback_sweep_by_value_not_by_site`.

⭐ **Rounds 3–5 audited the folds, not the plan.** Every finding from round 3 on was a defect in a
previous round's *correction*. That is the expected shape of a converging review, and it is the
argument for this rewrite: a plan patched six times is harder to review than one written once.

**Numbers granted by the owner 2026-09-21: 306 and 307.** (An earlier revision claimed 306 was
"the first unreferenced number"; that was false — 274–299 are unreferenced repo-wide.)
