---
status: READY
created: 2026-09-21
revised: 2026-09-21
plan: 306
title: A basin package's statics are invisible to every aquacast model, and their encodings are asserted by names that lie
scope: Make the statics delivered by a `basin-static-artifact/v1` package reachable by a model that declares `StaticNaming.CARAVAN`, and make each static's encoding a declared, enforced contract rather than a comment in one module. Explicitly NOT the basin package contract's geometry or provenance rules (Plan 120), NOT fixing the Caravan import path (Plan 155/188 — it works, and this plan may REUSE it), NOT onboarding the six DHM stations, NOT retraining any model, NOT the day-boundary/timezone family (252/254/258).
depends_on: []
blocks: []
related: [307]
open_decisions: [D3, D4]
source: 2026-09-21 — measured against the repo at `9dc07915`, the live mac-mini staging database at v0.1.927, the `nepal-dhm-basins` package delivered 2026-09-20, and the owner's `cmal_small` model tree. Every number below was measured on that date, and each says how.
---

# Plan 306 — basin-package statics: the namespace gap, and the encodings nothing declares

## Status

**READY — set by the owner on 2026-09-21.** **D1, D2 and D2a are all closed; no open decision
gates any task.** D3 and D4 are carried and gate nothing.

Consolidating rewrite 2026-09-21 — see the Changelog. **Seven independent review passes — one
Claude and six Codex — across six numbered rounds** were folded in place, which left the document
carrying its own
archaeology; the corpus already records that layered corrections are how Plan 252 failed three
rounds on its text rather than its reasoning. This is the rewrite. **Every finding is preserved;
only the superseded wording is gone.** Review coverage and what each round changed are in the
Changelog.

Plan number **306 granted by the owner, 2026-09-21**.

⚖️ **D1 and D2 were closed by the owner on 2026-09-21.** Translate the names **on the way in**,
reusing the operation the repo already ships for the Swiss path; keep the encoding declaration
**in our own code for now**, and ask the extractor to correct and populate the package's own field
so it can take over later.

**All three implementation tasks — T1, T2 and T3 — are implementable.** T4 is a question to the
extractor rather than an implementation task, and carries a deferred outcome. D3 and D4 remain
carried and block nothing.

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

### T2 — close the namespace gap by translating at import (D1, closed)

**Outcome.** A package-imported basin satisfies a `StaticNaming.CARAVAN` model's declared statics.

**In.** ⚖️ **D1 CLOSED — translate on the way in, reusing the operation the repo already ships
(option 4).** So: `store/caravan_import.py` (extract the prefixing operation from its Swiss-only
call site), `store/basin_importer.py` (call it on the package path), and the tests for both.

⛔ **Two things the extraction must carry, both from review and neither optional:**

1. **Leave an already-prefixed key untouched.** A compliant package may arrive already prefixed
   (§2); prefixing it again yields `caravan:caravan:…`, which the resolver cannot resolve.
2. **Detect collisions on the RAW column set, before the normalized dictionary is built.** Simply
   making the prefixing idempotent maps `for_pc_sse` and `caravan:for_pc_sse` onto one key, so one
   value silently overwrites the other with column order deciding the winner. Refuse on conflict —
   a package carrying two shapes for one concept is a package we do not understand.

**Out.** Weakening D15's no-bare-fallback rule as a side effect. That rule exists because inference
from the alias table cannot distinguish a Caravan direct name (`area`) from an incumbent model's
own same-named bare attribute (`types/enums.py:142-144`). **D1 chose option 4, which does not
touch that rule** — so weakening it is simply out of scope here, and any change that would is a
deliberate supersession needing its own reasoning.

**Verification.** T1's test goes green for the right reason; the 148 Swiss basins' resolution is
unchanged (re-run whatever covers the Caravan path); and **all three fixtures pass** — bare-key, already-prefixed (no `caravan:caravan:` double prefix), and
**mixed-with-conflict** (collision detected before the normalized dict is built; outcome
independent of column order). `uv run pytest` clean.

### T3 — declare each encoding, and enforce it

**Outcome.** Each static's encoding is **declared per feature against a versioned package**, and
an import is **rejected** — not warned about — when its encoding disagrees with what the model
expects, when its package version is unknown, or when its contents have changed without its
version changing.

**In.** ⚖️ **D2 CLOSED — a repo-side table, for now.** The expected encoding per feature lives in
this repository, beside `CARAVAN_ALIAS` where the name mapping already lives, and the comparison
happens at the import boundary. An import whose delivered encoding disagrees is **refused**, the
refusal naming the feature and both encodings.

⚖️ **D2a CLOSED — the table is keyed to a VERSIONED package, and any content change without a
version change is refused (owner, 2026-09-21).** A recorded description of a file is only true of
*that* file, so the entry binds to an identity the package already carries.

**What the table records, per `package_id` + extractor version:** the **verified delivered
encoding per feature** — established by us, because the package's own `unit` field is not
trustworthy (see D2) — **and the per-file SHA-256 checksums**, which are copied from the manifest
and serve only to detect drift.

⚠️ *Confirming review 2026-09-21: an earlier wording said "both are already in the package's own
manifest", which contradicted D2 outright — the checksums are, the encodings are not, and their
absence is the whole reason this table exists.*

**What the manifest supplies** is the package's identity and integrity, not its semantics:
`package_id`, `extractor.version` (`0.1.2` for the current delivery) and a `checksums` block
covering every file. That is enough to key and pin the table, so **no change is needed at the
extractor to make the version rule enforceable today**.

**Two ADDITIONAL refusals beyond the encoding mismatch above, and they are different from it and
from each other:**

1. **Unknown `package_id` + version → refuse.** Nothing has described it, so there is nothing to
   compare against, and assuming compatibility is the failure this task exists to prevent.
   ⚠️ **This rule BLOCKS the namespace-repair republish T2's watch item prescribes**, which is by
   definition a new, unregistered id. *(Independent verification 2026-09-21 found the conflict;
   the two were written without being checked against each other.)* **T3 must either ship with a
   registered entry for any repair package, or be sequenced after the repairs.** Do not discover
   this when a repair is refused.
2. **Known version, checksums differ → refuse.** The contents changed without the version
   changing, which is exactly the drift that would otherwise make our recorded description
   silently false.

🔑 **This places one obligation OUTSIDE this repo, accepted by the owner 2026-09-21: the extractor
must bump the package version on ANY content change — including a plain re-run over updated basin
data.** Re-extracting the same basins and keeping the version would be refused by rule 2. That
discipline is what makes the check work, and it must be communicated to the extractor rather than
discovered at the first re-delivery.

⚠️ **Not the package's own `unit` field — yet.** It is the natural long-term home, and once the
extractor corrects and populates it the delivered side becomes self-declared and the table keeps
only the expected side plus the version pin. But measured today it is **wrong for `slp_dg_sav` and
blank for `ari_ix_sav`** (see D2), so adopting it now would import a false statement wearing the
appearance of a declared one. **Ask the extractor to
correct and populate it; switch when it is trustworthy.** That switch is a follow-on, not part of
this plan.

⛔ **Documentation alone does not satisfy this Outcome**, and ⛔ **a numeric range guard cannot do
this job** — a `[0, 100]` check accepts a wrongly-rescaled `0.456` exactly as readily as a
legitimate `0.456%`. **Encoding is declared and compared, never inferred from values.** That is
also why §3's agreement between the two paths is evidence they happen to match, not a mechanism.

**Out.** Rescaling anything. ⛔ The model was trained on the raw encoding; converting to 0–1 would
be a 100× error on the percent-valued features and a wrong answer on the scaled ones. This task
makes the convention checkable; it does not change it.

**Verification.** Four tests, each catching a different failure:

1. **Value preservation** — a known value survives *both* import paths and reaches the model
   bit-for-bit as delivered.
2. **Encoding rejection** — a package whose recorded delivered encoding disagrees with what the
   model expects is refused, naming the feature and both encodings.
3. **Unknown version rejection** — a package whose `package_id` + extractor version has no entry
   is refused, naming the version.
4. **Silent-drift rejection** — a package whose version matches an entry but whose file checksums
   do not is refused, naming the files that differ. *This is the case the whole mechanism exists
   for; a test suite without it proves nothing about drift.*

⛔ None of them may decide an encoding from the magnitude of the numbers.

### T4 — dispose of the >100 lake percentages

**Outcome.** The 8 Swiss basins in §3 are either explained (a scale we misread, then documented) or
recorded as a data defect with an owner.

**In.** A question to the extractor/modeller — HydroATLAS's definition of `lka_pc_sse`, and whether
the delivered Caravan values carry a scale factor — and the answer, written here.

**Out.** Editing stored values. Excluding the basins.

**Verification.** The disposition is recorded with its source. A defect gets a named follow-on; a
scale is carried into T3's declaration.

## Owner decisions

### D1 — ✅ CLOSED, owner 2026-09-21: translate on the way in, reusing what the repo ships (option 4)

**The owner chose to translate at import**, and among the two ways of doing that, to reuse the
existing operation rather than write a second one — **option 4**. Options 1, 2 and 3 were not
taken. *(Confirming review 2026-09-21: an earlier wording of this paragraph kept option 1 as a
fallback "if extraction proves impractical". No task or exit gate carried that fallback, so it
was a contradiction, not an escape hatch. If extraction genuinely proves impractical, that is a
finding to bring back here — not a licence to switch options mid-implementation.)*

The four options as assessed, kept because the reasoning is what makes the caveats binding:

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

⇒ **The chosen option must** leave already-prefixed keys untouched, **detect collisions on the raw
column set BEFORE the normalized dict is built**, and **REFUSE on conflict** — *confirming review
2026-09-21: this sentence still offered "refuse, or a stated precedence" while T2 and the exit gate
already mandated refusal.* A package carrying two shapes for one concept is a package we do not
understand, and a precedence rule would turn that into a silent choice. Verify **independently of
column order**, against **three** fixtures: bare-key, already-prefixed, and
mixed-with-conflicting-values. *The third is the only one that catches a silent overwrite.*

⚠️ **No existing guard covers this.** `_collision_keys` handles a *different* pair —
`caravan:for_pc_sse` and `caravan:forest_fraction`, both prefixed (the raw code and Caravan's own
canonical name). The bare-versus-prefixed collision has no guard at all.

### D2 — ✅ CLOSED, owner 2026-09-21: a repo-side table for now, the package's own field later

**The owner chose to keep the declaration in our own code**, and to ask the extractor to correct
and populate the package's `unit` field so it can become the source later.

**Whatever carries it must supply three things**, because T3 needs all three: the **delivered**
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
than today's comment, which at least does not claim authority. **The repo-side table D2 chose
must be verified against these two features specifically** — they are the ones where a plausible
declaration is wrong.

### D2a — ✅ CLOSED, owner 2026-09-21: version the package, and refuse any content change without a version change

**The question.** D2 put the encoding declaration in our own code and ruled out the package's own
field for now. That settles where the **expected** encoding lives but not what we compare it
*against* — and a description we hold of a file is only true of *that* file. When the extractor
changes how a value is computed, our recorded description silently becomes false, the model
receives numbers in a form it was not trained on, nothing errors, and the forecasts simply shift.

⚖️ **The owner's rule: assign the package a version, and reject any change to its contents that
does not come with a version change.** *(This supersedes the looser "vouch for known sources"
sketch this plan proposed; the owner's framing is the same idea stated more simply, and it is
enforceable with what the package already carries.)*

**Why it works with today's delivery, unchanged.** The manifest already declares `package_id`,
`extractor.version` and a `checksums` block with a SHA-256 per file. Keying our table to
`package_id` + version, and pinning the checksums alongside, makes both halves of the rule
checkable at import: an unrecognised version has no description, and a recognised version whose
files have changed has a description that no longer matches.

🔑 **The obligation this places outside the repo, accepted by the owner:** the extractor must bump
the version on **any** content change, **including a plain re-run over updated basin data**. A
re-extraction that keeps its version will be refused. That is the discipline that makes the check
mean anything, and it must be communicated to the extractor rather than discovered at the first
re-delivery.

⭐ **This is strictly better than what this plan proposed.** The earlier sketch would have accepted
a changed package under a known version, because it vouched for a *producer* rather than for a
*specific artifact*. The owner's rule closes that hole.

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
↔ package names on the way — via the existing `CARAVAN_ALIAS` resolution, **not** via T2. *(Final
review 2026-09-21: an earlier wording called this "the T2 mapping". T2 extracts namespace
**prefixing**; the canonical-to-HydroATLAS alias translation is a different mechanism that already
exists, and pointing an implementer at T2 for it would have sent them to the wrong code.)* **Keep a test for the deletion case** — it is the only
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

- 🔑 **T2 changes the stored key shape for package-imported basins, and that is safe only
  because no NATIVE model reads statics.** Measured 2026-09-21 while implementing: every
  discoverable model declaring `StaticNaming.NATIVE` declares **zero** static features, so none
  can be affected. ⚠️ **That is a fact about today's model set, not a guarantee** — a NATIVE model
  that declared statics and read a package-imported basin would resolve nothing after this change,
  exactly as CARAVAN models did before it. The plan did not anticipate this direction; it is
  recorded here rather than left for someone to rediscover.
- 🔴 **The extraction DID change the Swiss path's behaviour for two input shapes — deliberately,
  and the owner may reverse it.** *Confirming review 2026-09-21 flagged that "extracted unchanged"
  was not accurate.* The inline operation was an unconditional prefix; the shared one is
  idempotent and refuses a mixed source. So:
  - an already-`caravan:`-prefixed column used to become `caravan:caravan:…` and now passes
    through unchanged;
  - a source carrying **both** spellings of one concept used to keep both as distinct keys and is
    now **refused**.
  ⚖️ **For the real Caravan attributes parquet — bare HydroATLAS codes — behaviour is identical,
  and that is the shape the 148 Swiss basins were imported from.** The two deltas apply only to
  inputs the loader permits but has not produced. Both changes are improvements (the old
  double-prefix was unresolvable), but they ARE changes to a live path this plan's watch items say
  not to disturb.
  ⚖️ **CLOSED, owner 2026-09-21: KEEP IT SHARED.** One implementation serves both imports; the
  alternative was two copies that drift, and two imports answering differently on odd input. The
  stricter behaviour stands as a **deliberate, recorded** change to the Swiss path rather than an
  unnoticed side effect — which is the distinction that mattered, not the strictness itself.
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
- 🔴 **D1 chose option 4, so basins imported BEFORE this change keep their bare keys, and
  re-running the same package will NOT repair them.** *Independent review 2026-09-21 required this
  disposition, which an earlier revision demanded and then did not supply.*
  `store/basin_importer.py::_basin_needs_import` treats a basin whose current projection already
  carries this `package_id` as already imported and skips it — by design, for idempotency. So a
  re-run of an unchanged package is a no-op and the bare keys survive.
  🔴 **The remediation an earlier revision gave was WRONG.** *Confirming review 2026-09-21.* It
  claimed D2a's version bump suffices, "because a new version is a different `package_id`". It is
  not: `basin_importer` takes `PackageId(loaded.manifest.package_id)` **directly**, and the
  manifest carries `package_id` and `extractor.version` as **separate fields** (measured —
  `nepal-dhm-basins` at extractor `0.1.2`). Bumping the version leaves `package_id` unchanged, the
  basin is still skipped, and the bare keys survive **silently**. That instruction would have
  failed in exactly the way it was written to prevent.
  ⇒ **Actual remediation: republish under a NEW `package_id`** — deliberately, including for an
  unchanged payload whose only purpose is namespace repair. The mechanism works: an existing basin
  whose stored `package_id` differs takes the **correction** branch and its `attributes` are
  **wholly replaced** with the namespaced shape (no merge, so no half-repaired basin carrying both
  spellings). Nothing dedupes an identical payload under a new id — `package_id` is itself part of
  the canonical fingerprint. Verified independently 2026-09-21, and already exercised end to end by
  `tests/integration/store/test_basin_importer_persistence.py` re-importing as
  `nepal-dhm-basins-v2`.

  🔴 **But "just republish" is NOT the whole instruction, and one of the gaps is created by THIS
  PLAN.** *Independent verification 2026-09-21.*

  1. ⛔ **T3 would REFUSE the repair.** T3 specifies "unknown `package_id` + version → refuse". A
     namespace-repair republish is BY DEFINITION a new, unregistered `package_id`. Once T3 ships,
     the repair is blocked until someone adds a repo-side entry — a code change, review and
     release. **Either sequence any repair BEFORE T3, or register the new id as part of doing it.**
     T3 is unimplemented today, so the conflict is plan-internal and not yet live.
  2. **Who mints the new `package_id` is unarranged.** D2a puts version bumps on the extractor for
     *content* changes; an unchanged payload needing a new id for namespace repair is not covered
     by that obligation and needs an explicit ask.
  3. **A repair is a CORRECTION, not a quiet rewrite.** Every repaired basin gets a new
     `basin_versions` row, supersedes the prior one, and reports `material_change=True` with
     `affected_artifact_ids` — every model artifact trained on the superseded version. Nothing
     auto-invalidates them; the CLI only logs it. **The operator must act on that list.** And ALL
     basins in the republished package are corrected, not only those needing repair.
  4. **A narrower primitive exists:** `store/basin_store.py::merge_namespaced_attributes` is
     additive with no version bump and no `material_change` — at the cost of leaving the bare keys
     in place beside the namespaced ones, and needing a one-off script, since no CLI exposes it for
     the package path.

  **No schema migration is needed** in any of these, and a re-import will not happen as a side
  effect of D2a's versioning rule.
  ⚠️ **Whether any such rows exist is deployment-specific and not determinable from this repo.**
  Measured 2026-09-21: the staging database holds **148 BAFU basins and no package-imported
  basin at all**, so on that host the question is moot today.

## Exit gates

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

✅ **D1 and D2 were answered by the owner on 2026-09-21** — they gated implementation and no longer
do. **D3 and D4 remain carryable:** D3 is a question T4 asks, and D4 chooses between two outcomes
this plan describes in full.

- T2 implements D1's option 4 and carries both of its binding caveats (no double-prefixing;
  collision detection before the normalized dict, refusing on conflict).
- T3 implements D2's repo-side table, and does **not** adopt the package's `unit` field.
- T1's test asserts the **end state** (all 78 resolve, with expected values), failed before T2 and
  passed after, and its discriminating evidence is the **two-path comparison** — not the text of
  any error message.
- The 148 Swiss basins' static resolution is unchanged — demonstrated, not assumed.
- No stored attribute value was rescaled by this plan.
- T3's four tests all exist — value preservation, encoding mismatch, unknown version, and
  checksum drift on a matching version — and none infers an encoding from a value's magnitude.
- All three T2 fixtures pass, including mixed-with-conflict.
- D3 and D4 are each answered or explicitly carried, with the carrier named.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"],
      "note": "prove the namespace gap; if this comes up green the plan is withdrawn" },
    { "id": "P2", "tasks": ["T2"], "depends_on": ["P1"], "decision": "D1 CLOSED 2026-09-21",
      "note": "close the namespace gap: translate at import, reusing the existing operation" },
    { "id": "P3", "tasks": ["T3"], "depends_on": ["P2"],
      "decision": "D2 and D2a both CLOSED 2026-09-21",
      "note": "declare and enforce the encoding from a repo-side table keyed to package_id + extractor version, with checksum pinning" },
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

**After the rewrite, 2026-09-21.** The rewrite was reviewed on its own terms — the earlier APPROVE
covered the superseded text — and returned **APPROVE**, with an explicit round-by-round audit
finding **nothing lost in the rewrite**. Three further confirming passes followed as decisions were
closed, each finding the same defect one level deeper:

| pass | found |
|---|---|
| rewrite review | APPROVE; no finding survived only as a changelog line |
| D1/D2 closure | 4 majors/minors — the decision *summaries* were updated while the task Ins, Outs, Verifications and exit gates that ACT on them kept the open wording; plus an option-1 fallback the author invented that no task carried |
| operative sweep | 1 defect — the author's own "flagged for confirmation" caveat on D2a was itself prose-only, while the phase graph, exit gates and status line authorised T3 unconditionally |
| D2a closure | **APPROVE** — D2a operative at all five sites, no execution path bypassing it |

⭐ **The lesson in its final form: sweep by VALUE and by ROLE.** A decision, correction or caveat
is not applied until the task Ins, Outs, Verifications, exit gates and phase graph that act on it
say the same thing as the prose that reports it. Every post-rewrite finding was an instance of
that one error, including the caveat written to prevent it.

**D2a closed by the owner 2026-09-21** with a rule better than the one this plan proposed — bind
the description to a *versioned artifact* rather than vouching for a *producer*.

**The Swiss call site stays SHARED — owner, 2026-09-21.** T2 extracted the prefixing operation
*from* the Swiss import path, and the extraction is not byte-for-byte behaviour-preserving on two
inputs the Swiss loader permits but has never produced (an already-prefixed column, and a mixed
bare/prefixed source). The owner chose one shared implementation over two copies that drift. The
stricter behaviour on those two inputs is therefore a **deliberate, recorded** change to a live
path, pinned by `TestWhatTheSwissExtractionPreservedAndWhatItChanged` — not an unnoticed side
effect. That distinction was the question; the strictness itself was never in doubt.
