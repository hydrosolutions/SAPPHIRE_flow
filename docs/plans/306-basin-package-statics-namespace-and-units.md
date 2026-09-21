---
status: DRAFT
created: 2026-09-21
plan: 306
title: A basin package's statics are invisible to every aquacast model, and their units are asserted by a name that lies
scope: Make the statics delivered by a `basin-static-artifact/v1` package reachable by a model that declares `StaticNaming.CARAVAN`, and make the percent-not-fraction unit convention an enforced contract rather than a comment in one module. Explicitly NOT the basin package contract's geometry or provenance rules (Plan 120), NOT the Caravan import path itself (Plan 155/188, which works), NOT onboarding the six DHM stations (a separate plan), NOT retraining any model, NOT the day-boundary/timezone family (252/254/258).
depends_on: []
blocks: []
open_decisions: [D1, D2, D3, D4]
source: 2026-09-21 — measured against the repo at `9dc07915`, the live mac-mini staging database at v0.1.927, the `nepal-dhm-basins` package delivered 2026-09-20, and the owner's `cmal_small` model tree. Every number below was measured on that date; each measurement names how it was taken.
---

# Plan 306 — basin-package statics: the namespace gap and the unit that is not what its name says

## Status

**DRAFT — not reviewed.** Written at the owner's instruction after the `*_fraction` / percent
question surfaced while checking whether `cmal_small` can run on the six DHM basins. The
investigation found a second, larger problem sitting in front of the units one, and both are
recorded here because they share a single seam: `basins.attributes`.

⚠️ **Plan number 306 needs the owner's confirmation — the owner grants plan IDs.** 🔴 *The stated
rationale was false and is withdrawn.* An earlier revision claimed 306 was "the first unreferenced
number". Measured (independent review 2026-09-21): `docs/plans/` holds 270–273 then 301,
`docs/plans/archive/` tops out at 300, and **274–299 are unreferenced repo-wide**. 306 is merely
*a* free number, chosen badly. Plan 307 inherited the same false rationale and is corrected there
too.

## Why this exists

The `nepal-dhm-basins` package is model-ready on its own terms: it covers **78 of 78** of
`cmal_small`'s declared static features with no NaNs. It is nonetheless, as things stand, unusable
by that model — for a reason that has nothing to do with the data.

## What is measured

### 1. The package covers the model's statics exactly

Measured 2026-09-21 by loading `static_attributes.parquet` (6 basins × 93 columns) and applying
the rename table in the model's own `harmonization/harmonization_manifest.yaml`:

| | |
|---|---|
| `cmal_small` declared statics | **78** (`src/sapphire_flow/models/aquacast/configs/cmal_small.yaml`) |
| covered by package column name directly | 55 |
| covered after the manifest's `source_name → canonical_name` rename | **the remaining 23 — 78/78 total** |
| non-identity conversions among those renames | **0** |
| required features with any NaN across the 6 basins | **0** |

The manifest's static block is a one-to-one map from HydroATLAS codes to the model's canonical
names — `slp_dg_sav → slope`, `cly_pc_sav → clay_fraction`, and so on, every one
`conversion: identity`.

🔑 **The repo already holds that same map.** `services/caravan_statics.py::CARAVAN_ALIAS` contains
all 23 aliases, and its two most recent entries (`forest_fraction`, `permafrost_fraction`) were
added on 2026-09-04 specifically for `cmal_small`. The mapping is not the gap.

### 2. 🔴 The gap: a package-imported basin is in the wrong namespace

Two facts, both read from the repo at `9dc07915`:

- **Every aquacast model opts into strict namespaced resolution.**
  `models/aquacast/_shim.py:468` declares `static_naming: ClassVar[StaticNaming] = StaticNaming.CARAVAN`
  on the shim base class, so every subclass inherits it. `types/enums.py:139-141` defines that as
  *"D15's strict `caravan:`-namespaced, **no-bare-fallback** resolution rule"*, and
  `services/caravan_statics.py::resolve_caravan_static_key` implements it: the key looked up for
  `forest_fraction` is `caravan:for_pc_sse`, and **there is no fallback to the bare name**.
- **The basin-package importer writes bare keys.** `store/basin_importer.py:628` takes the
  package's parquet row verbatim — `attributes = dict(_require_static_attributes(...))` — and
  writes it to `basins.attributes` with no prefix applied anywhere on the path.

⇒ **A basin imported from a `basin-static-artifact/v1` package resolves ZERO of the 78 statics for
`cmal_small`.** Every one would be reported missing.

⚠️ **This is a deduction from two measured facts, not yet an observed failure** — the Nepal package
has not been imported anywhere, so nothing has run end-to-end. **T1 exists to prove it red before
anything is changed.** If T1 comes up green, the rest of this plan is wrong and should be withdrawn.

✅ **Independently confirmed 2026-09-21** by a reviewer who traced the whole path rather than the
two endpoints: the loader preserves the parquet column names, `basin_importer.py:628` copies them
unchanged, the basin store persists them unchanged, the collision-aware resolver accepts the
primary and secondary **prefixed** keys only, and projection explicitly drops unresolved bare
declared names. That is a stronger confirmation than the two facts above, and it is still not a
substitute for T1.

🔑 **Scope correction from the same review: this is a property of THIS package's key shape, not of
the contract.** A `basin-static-artifact/v1` package that already carried `caravan:`-prefixed keys
would resolve fine. The defect is that the contract does not say which shape it emits and the
resolver accepts only one — so "the package is contract-compliant" and "the package is usable by
our models" are independent facts today. D1 should be read with that in mind.

Confirming context, measured on the live staging DB 2026-09-21: the 148 Swiss basins carry **516**
attribute keys each, and their statics are stored `caravan:`-prefixed (`caravan:for_pc_sse` etc.)
by the Plan 155/188 Caravan path — written at `store/caravan_import.py:164`, which namespaces every
column on the way in.

⚠️ **An earlier revision offered a non-discriminating query as confirmation.** *Independent review
2026-09-21 (major).* It reported that querying the bare **canonical** names (`forest_fraction`,
`clay_fraction`, …) returns 0 basins — but **neither path ever writes a bare canonical name**: the
package writes bare HydroATLAS codes, the Caravan path writes prefixed ones. That query returns 0
whether or not the importer prefixes, so it confirmed nothing. **The discriminating query is for
the bare HydroATLAS code** (`for_pc_sse` with no prefix), which is what a package-imported basin
would carry and a Caravan-imported one would not.

### 3. The units: the name says fraction, the value is a percent — and sometimes not even that

`CARAVAN_ALIAS` already carries a warning, in a comment added 2026-09-04:

> NOTE the values are PERCENT (0-100), not 0-1 — aquacast's `*_fraction` naming is misleading, and
> nothing in this pipeline rescales them.

⚠️ **That comment is accurate but narrower than it looks, and an earlier revision of this plan
overstated it.** *Independent review 2026-09-21 (minor).* It sits on the two entries added for
`cmal_small` and speaks of "both" — `forest_fraction` and `permafrost_fraction`. It does **not**
claim that slope or aridity are percentages, and it is not a general statement about the alias
table. **The real gap is that there is no per-feature encoding declaration anywhere** — which is
what makes the two features below surprising rather than obvious.

Whatever its scope, it is the whole of the enforcement. Measured ranges confirm both sides agree
today — Swiss from `basins.attributes` on staging, Nepal from the package parquet:

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

Two things this table says that the comment does not:

- **`slp_dg_sav` and `ari_ix_sav` are not percentages at all** — they are HydroATLAS scaled
  integers (a slope of `325` is not 325 degrees). "Percent (0-100)" describes *some* of these
  features; the general rule is "raw HydroATLAS encoding, whatever that is per attribute".
- 🔴 **`caravan:lka_pc_sse` exceeds 100 on 8 of the 148 Swiss basins** — 2308 Goldach-Bleiche at
  **362.0**, 2312 Salmsach-Hungerbühl at 350.0, then 157.0, 140.9, 109.0, 109.0, 108.0, 105.0. A
  lake extent of 362% is not a percentage. Whether this is a HydroATLAS scale we have misread or a
  defect in the delivered values is **not determinable from this repo** (D3).

**Nothing validates any of it.** There is no declared unit on `basins.attributes`, no range guard,
and the agreement between the two import paths in the table above holds by construction rather
than by contract.

### 4. The package declares no model requirements

`feature_catalog.json` carries a `required_by_models` field on each of its 92 features. It is
**empty for all 92**. The 78/78 coverage in §1 is this plan's measurement, not the package's
assertion — so a future package that silently drops a feature would be accepted by the loader and
fail at predict time.

## Tasks

### T1 — prove the namespace gap red, before changing anything

**Outcome.** A test that imports a `basin-static-artifact/v1` package and asserts that a
`StaticNaming.CARAVAN` model **resolves all of its declared statics, with the expected values**,
against the resulting `basins.attributes`. Today it fails because zero resolve; after T2 it
passes. Zero-resolved is the **pre-change diagnostic**, recorded in the failure message — it is
**not** what the test asserts.

🔴 **The obvious wording is inverted and must not be used.** *Independent review 2026-09-21
(minor).* A test that *asserts* zero resolution passes today and **fails after T2 fixes it** — a
characterization test pointing the wrong way. This is the same defect, in the same form, that was
caught twice in Plan 262's T1; see `feedback_red_first_must_prove_the_fault`. Assert the
end state, let the current failure be the red.

**In.** One test under `tests/unit/` (or `tests/integration/` if a real import is needed).

🔴 **There are TWO different artifacts called `nepal-dhm-basins`, and the plan must not let an
implementer confuse them.** *Independent review 2026-09-21 (major).*

| | `tests/fixtures/basin_static/nepal-dhm-basins/` | the 2026-09-20 delivery |
|---|---|---|
| dated | 2026-07-17 | 2026-09-20 |
| basins | **1** (a DHM test basin) | **6** (447, 450, 604.5, 647, 670, 684) |
| parquet | 30,758 bytes | 33,541 bytes |
| `for_pc_sse` | **76.82** | 8.41 – 45.56 |

⛔ **Every number in §1 and §3 of this plan describes the DELIVERY, not the in-repo fixture**, and
`76.82` falls outside the range §3 quotes. An implementer reaching for "the existing reference
fixture" gets figures that contradict this plan. **T1 should use the in-repo fixture** — it is the
right shape for the contract and needs no external file — **but must not expect §1/§3's values
from it.**

**Out.** Any production change. Any change to `caravan_statics.py` or `basin_importer.py`. Adding
a new log message or error field (see the Verification note — that would be a production change
dressed as test support).

**Verification.** The test's assertion is the **end state**: all 78 declared statics resolve with
their expected values. Today it fails because none resolves.

🔴 **The discriminating criterion is a comparison between the two import paths, NOT the text of an
error message.** *Independent review 2026-09-21 (blocker): an earlier revision required "the
failure names the missing `caravan:`-prefixed keys" — and no code emits a prefixed key on a miss.*
`services/training_data.py:526-531` logs `missing=sorted(missing_attrs)` where `missing_attrs =
declared_names - available`, i.e. **declared canonical names** (`forest_fraction`), and
`services/caravan_statics.py:524-528` builds `StaticCoverageGap.missing_statics` from `name for
name in declared` — canonical again. The only place a `caravan:` key appears in an error is the
collision raise (`:253-257`), which is a **different failure**. An implementer following the old
criterion had to either invent a log message — which this task's own Out forbids — or write a test
that passes for the wrong reason.

**So the criterion is:** on a package-imported basin **all 78** declared statics report missing,
while on a Caravan-imported basin **the same 78 resolve**. Same model, same declared names, two
import paths, opposite outcomes — that is what makes it discriminating.

⛔ A failure for any other reason is not red — not a `KeyError` from a missing basin, not a loader
rejection, not a model-construction error. See the T1 lesson recorded in Plan 262, where a red
test was specified wrongly twice, in opposite directions, and both wordings looked like careful
discipline.

**Pre-change.** This task IS the pre-change evidence for T2.

### T2 — close the namespace gap (D1 decides how)

**Outcome.** A package-imported basin satisfies a `StaticNaming.CARAVAN` model's declared statics.

**In.** Whichever of D1's options the owner takes.

**Out.** Removing or weakening D15's no-bare-fallback rule as a side effect of an unrelated change.
That rule exists because inference from the alias table cannot distinguish a Caravan direct name
(`area`) from an incumbent model's own same-named bare attribute (`types/enums.py:142-144`) — if D1
lands on relaxing it, that is a deliberate supersession with its own reasoning, not a convenience.

**Verification.** T1's test goes green for the right reason; the 148 Swiss basins' existing
resolution is unchanged (re-run whatever currently covers the Caravan path); **and, for any
option that prefixes on import, all three fixtures pass** — bare-key, already-prefixed (no
`caravan:caravan:` double-prefix), and mixed-with-conflict (the collision is detected before the
normalized dict is built, and the outcome does not depend on column order). *Round 3 (major):
neither T1's bare-key package nor the Swiss Caravan-path check would catch double-prefixing.
Round 4 (major): nor would either catch a silent overwrite.* `uv run pytest` clean.

### T3 — make the unit contract explicit and checked

**Outcome.** Each static's encoding is *declared* per feature, and an import whose declared
encoding disagrees with what the model expects is **rejected**, not warned about.

**In.** D2's answer, which must name three things: **where the declaration lives**, **which
boundary enforces it**, and **what rejection looks like** (refuse the import, or hold the basin).

🔴 **Documentation alone does not satisfy this task's Outcome.** *Independent review 2026-09-21
(major): the previous wording promised that violations "fail loudly" while permitting a comment as
the minimum deliverable — a task that cannot deliver what it claims.*

⛔ **And a numeric range guard cannot do this job.** A `[0, 100]` check accepts a wrongly-rescaled
`0.456` exactly as readily as a legitimate `0.456%`. **Encoding must be declared and compared,
never inferred from the values** — which is also why the agreement measured in §3 is evidence that
the two paths happen to match today, not a mechanism that keeps them matching.

**Out.** Rescaling anything. ⛔ **The model was trained on the raw encoding; converting to 0–1
would be a 100× error on the percent-valued features and a wrong answer on the scaled ones.** This
task makes the convention checkable, it does not change it.

**Verification.** Two tests, which are not the same test:

1. **Value preservation** — a known value survives *both* import paths (Caravan and package) and
   arrives at the model bit-for-bit as it was delivered.
2. **Encoding rejection** — a package declaring an encoding the model does not expect is refused,
   with the refusal naming the feature and both encodings.

⛔ Neither test may decide the encoding by looking at the magnitude of the numbers.

### T4 — dispose of the >100 lake percentages

**Outcome.** The 8 Swiss basins in §3 are either explained (a scale we misread — then documented)
or recorded as a data defect with an owner.

**In.** A question to the extractor/modeller — HydroATLAS's own definition of `lka_pc_sse` and
whether the delivered Caravan values carry a scale factor. The answer, written here.

**Out.** Editing the stored values. Excluding the basins.

**Verification.** The disposition is recorded with its source. If it is a defect, it gets a named
follow-on; if it is a scale, T3's declaration carries it.

## Owner decisions

**D1 — how does a package-imported basin satisfy a `CARAVAN`-naming model?** Four options, each
with a different blast radius. ⚠️ **Whichever is chosen, T2's In must then name the files it
touches** — an implementer cannot start from "whichever option the owner takes".

  1. **Prefix at import** — `basin_importer` writes `caravan:`-prefixed keys. Simple, but the
     package contract is model-agnostic (§4) and this bakes one model family's namespace into a
     general importer. *Touches:* `store/basin_importer.py`, its tests.
  2. **Resolve by provenance** — `basins.package_id` already exists on the table; resolution could
     consult it and accept a package-sourced bare key. Keeps the importer neutral; adds a branch
     to the resolver. *Touches:* `services/caravan_statics.py`, its tests. ⚠️ This weakens D15's
     no-bare-fallback rule, which T2's Out flags as needing its own reasoning.
  3. **The package declares its namespace** — a manifest field, validated by the loader. Cleanest
     contractually, but it is an **upstream `04-basin-static-artifact-contract` change** and the
     extractor must emit it, so it needs coordination outside this repo. ⚠️ *Round 2 (minor): an
     earlier note said this "widens the scope" because the scope line excludes the package
     contract. It does not — the scope line excludes that contract's **geometry and provenance
     rules**, and a namespace declaration is neither. The upstream-coordination cost is real; the
     scope conflict was not.*
  4. 🔑 **Extract the prefixing the repo already ships.** *Added by independent review 2026-09-21
     (major).* `store/caravan_import.py:164` already builds exactly the required shape —
     `{f"{CARAVAN_PREFIX}{col}": val for col, val in raw_attrs.items()}`.
     🔴 *Round 2 (major): "reuse `scripts/import_caravan_attributes.py` as a second import step"
     understates this badly.* That path is Swiss-specific well beyond its manifest pin:
     `adapters/caravan_attributes.py:25-37,57` accepts only `caravan_camels_ch_` identities, and
     `store/caravan_import.py:157` looks up network `"bafu"`. Running it as a separate command
     also would not make T1's package-import test green, because T1 imports a **package**.
     ⇒ **Option 4 means extracting the one-line prefixing operation into the package import path**,
     not invoking the Caravan command. *Touches:* `store/caravan_import.py` (extract),
     `store/basin_importer.py` (call it), their tests. If instead a second import step is wanted,
     that is a different option and must carry identity parsing, network handling, sequencing and
     its own verification.

🔴 **Options 1 and 4 both prefix unconditionally, and that is a defect once §2's correction is
taken seriously.** *Round 3 (major).* `caravan_import.py:164` builds
`{f"{CARAVAN_PREFIX}{col}": val …}` with no check on `col`. §2 now says explicitly that a
contract-compliant package **may already carry `caravan:`-prefixed keys** — and feeding such a
package through an unconditional prefixer yields **`caravan:caravan:for_pc_sse`**, which the
resolver cannot resolve. ⚠️ **T1 uses a bare-key package and the Swiss regression check uses the
Caravan path, so neither would catch it.**

⇒ **Any import-prefixing option must:** leave an already-prefixed key untouched, define what
happens when a package carries **both** shapes for one concept, and be verified against package
fixtures that include the mixed case.

🔴 **And the obvious "just make the prefixing idempotent" fix is not sufficient.** *Round 4
(major).* An idempotent comprehension — prefix `col` unless it already starts with the prefix —
maps **`for_pc_sse` and `caravan:for_pc_sse` onto the same dictionary key**, so one value silently
overwrites the other during dict construction, before the resolver ever runs, with **input order
deciding which survives**. ⇒ **Collision detection must happen BEFORE the normalized dictionary is
built**, on the raw column set, and the chosen policy (refuse, or a stated precedence) must be
verified **independently of column order**.

⛔ *Round 4 also corrected a citation this plan got wrong:* an earlier revision said
`_collision_keys` "contemplates exactly that pair". It does not — its pair is
**`caravan:for_pc_sse` and `caravan:forest_fraction`** (both prefixed: the raw code and Caravan's
own canonical name), which is a *different* collision. The bare-versus-prefixed collision this
option would introduce has **no existing guard at all**.

⇒ **Fixtures required: three, not two** — bare-key, already-prefixed, and **mixed with conflicting
values for one concept**. Separate bare-only and prefixed-only fixtures both pass against a
silently-overwriting implementation, which is precisely why the third is the one that matters.

⚠️ **The scope line's "(Plan 155/188, which works)" reads as "irrelevant" and should not.** That
path is the one mechanism in the repo that already produces the key shape this plan needs. It is
out of scope as a thing to *fix*; it is squarely in scope as a thing to *reuse*.

**D2 — where does the unit live?** ⚠️ *Round 2 (major): the previous list offered "a comment" and
"a runtime range guard" as standalone options, both of which T3 explicitly rules out — so
answering D2 would not have made T3 implementable.* They are removed.

**Whatever D2 chooses must supply three things**, because T3 needs all three: the **delivered**
encoding per feature, the **expected** encoding per feature, and the **boundary that compares
them** with a rejection policy. A repo-side table and an upstream catalog field are both viable
carriers of the first two; a comment carries neither, and a range guard compares nothing.

🔴 **The catalog's existing `unit` field is NOT the answer, and an earlier revision wrongly
recommended it.** *Independent review 2026-09-21 (major).* Measured on the in-repo catalog:

| feature | catalog `unit` | actual value | verdict |
|---|---|---|---|
| `area` | `km2` | — | correct |
| `for_pc_sse`, `cly_pc_sav` | `%` | 8–77 | correct |
| **`slp_dg_sav`** | **`degrees`** | **266.99** | 🔴 **false** — §3 shows this is a scaled integer, not 267 degrees |
| **`ari_ix_sav`** | **`null`** | 56–527 | 🔴 **absent** |

So for the two features §3 singles out, the catalog's `unit` is **wrong for one and missing for
the other**. Adopting it as the declaration would import a false unit under the appearance of a
declared one — worse than today's comment, which at least does not claim to be authoritative.
Whatever D2 chooses must be verified against these two features specifically.

**D3 — are the >100 `lka_pc_sse` values a misread scale or a defect?** Not answerable from this
repo. Needs the extractor or the modeller.

**D4 — how should a coverage gate actually be built?** `required_by_models` exists on all 92
features and is empty, so the obvious answer is "populate it and let the loader check it".

🔴 **That does not work, and the reason is worth keeping.** *Independent review 2026-09-21
(major).* `services/basin_package_loader.py::_evaluate_required_static` derives `catalog_required`
from the catalog itself, so it iterates only over features **still present**. A feature dropped
from *both* `feature_catalog.json` and `static_attributes.parquet` disappears from the check
entirely — the one case a coverage gate exists to catch. A self-describing manifest cannot gate
its own completeness.

⚙️ **The hook for a working gate already exists** — `assigned_model_features:
Callable[[BasinRecord], frozenset[str]] | None`, built in production by
`services/basin_importer.py::build_assigned_model_features_resolver`, which unions
`model.data_requirements.static_features` over every ACTIVE assignment covering the basin.

🔴 **But that hook is currently comparing two different namespaces, so it can never hold for an
aliased feature.** *Independent review 2026-09-21 (major), verified:* `catalog_required` is built
from `entry.name` — the **raw catalog/parquet name** (`for_pc_sse`) — while `assigned` contains
the model's **canonical** names (`forest_fraction`). The test is `if name in assigned`, so for all
**23 aliased features of a CARAVAN model it is false by construction**, and the outcome is a
WARNING, never a HOLD.

⇒ **A working gate needs three things, and the third is the one that actually catches a deletion.**
🔴 *Round 2 (major): "populate `required_by_models` and make the comparison alias-aware" still
misses the drop-from-both case.* `catalog_required` is derived from **surviving catalog entries**
(`:1237-1239`) and the loop iterates only those (`:1372-1378`), so a feature deleted from both
files is never visited — alias-aware or not. The gate must:

1. obtain the requirement set **independently of the package** (the model's declared statics, via
   the existing `assigned_model_features` resolver);
2. **iterate that set**, not the catalog's, resolving each requirement against the package values
   regardless of whether the catalog still mentions it;
3. translate canonical ↔ package names on the way (the T2 mapping).

⚠️ And keep a test for the deletion case specifically — it is the one a self-describing manifest
can never catch, and therefore the only one that proves the gate is real.

⚖️ **So D4 is a choice between two things, not one:** populate `required_by_models` as
*documentation* (cheap, no gate), or build the assignment-aware gate through
`assigned_model_features` (a real task, with tests for the drop-from-both case). If the gate is
wanted, this plan gains a task; if not, the weaker outcome is recorded deliberately rather than
assumed.

## Watch items, not tasks

- **The Swiss path is not broken and this plan must not disturb it.** The 148 Swiss basins resolve
  correctly today through the `caravan:` prefix written by the Plan 155/188 import.
- **`cmal_small` is `GROUP`-scoped**, so the Nepal basins would be assigned as a group, not per
  station — relevant to whoever sequences the DHM onboarding, not to this plan.
- **This plan does not make `cmal_small` runnable for Nepal on its own.** Statics are one input;
  the 30-day forcing window and the daily observation series are separate questions.
- 🔑 **This plan needs the staging mount Plan 307 T1 builds, and neither plan said so.**
  *Independent review 2026-09-21 (minor).* Importing a package on the staging host means getting a
  package **directory** readable inside a read-only-rootfs container.
  `docs/operations/basin-static-importer-runbook.md:54-61` says only `--package-dir
  /path/to/nepal-dhm-basins` and never explains how a host directory reaches that container; the
  base compose gives `prefect-worker` no operator bind. 307 T1 creates exactly that mount. Both
  plans carried `depends_on: []`. ⚖️ **Whether that becomes a formal dependency or stays a noted
  overlap is the orchestrator's call** — it is recorded here rather than decided.
- **If D1 lands on option 1 (prefix at import), already-imported basins keep their bare keys.**
  *Independent review 2026-09-21 (minor).* Nothing in this plan says whether a re-import or a
  backfill is needed. For the Swiss 148 the question is masked — their keys come from the Caravan
  path and are already prefixed — so **T2's verification would not surface the omission**. Any
  option that changes the written key shape must state what happens to rows written before it.

## Exit gates

```bash
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

🔴 **D1 and D2 must be ANSWERED before implementation starts — they are not carryable.**
*Independent review 2026-09-21 (major): the previous gate let any of D1–D4 be "explicitly
carried", while T2 is entirely determined by D1 and T3 by D2. A plan whose gate permits its own
deciding questions to stay open cannot be implemented.* D3 and D4 **are** carryable: D3 is a
question for the extractor, and D4 chooses between a documentation outcome and an extra task.

- D1 and D2 are answered, and the task each determines names the chosen option.
- T1's test asserts the **end state** (all 78 declared statics resolve, with their expected
  values), was failing before T2 and passing after. Its discriminating evidence is the
  **two-path comparison** — all 78 missing on a package-imported basin, the same 78 resolving on a
  Caravan-imported one — **not** the text of any error message.
  🔴 *Independent review round 2, 2026-09-21 (blocker): the previous bullet still required the
  pre-change failure to "name the missing `caravan:`-prefixed keys" — the exact criterion T1's own
  correction had just removed as unachievable. Correcting the task and leaving the gate standing
  documented the contradiction instead of fixing it; see
  `feedback_correction_notes_do_not_replace_wrong_text`.*
- The 148 Swiss basins' static resolution is unchanged — demonstrated, not assumed.
- No stored attribute value was rescaled by this plan.
- T3's two tests both exist, and neither infers an encoding from the magnitude of a value.
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

⚠️ *Independent review 2026-09-21 (minor): `docs/workflow.md:32` requires a closing JSON
dependency graph and this plan had none.*
