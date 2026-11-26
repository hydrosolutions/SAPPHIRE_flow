---
status: F1–F4 COMPLETE for release 0.1.1197; source execution remains DRAFT
created: 2026-11-25
owner: deployment orchestrator
baseline: efe2613a
parent: 2026-11-22-operational-deployment-recipe.md
---

# Preserve deployment identity across model initialization

## Finding and scope

The private inventory runner compares its complete observed identity before and
after execution. A verified local CPU-wheel experiment completed the actual model
constructors and ForecastInterface declarations within the existing resource limits,
then stopped at `identity_drift`. An observation-only follow-up proved that the
process environment was the sole changed component. Exactly two keys were added
inside the one real constructor/declaration call:

| Key | Expected initialization value from the inspected dependency |
| --- | --- |
| `KMP_DUPLICATE_LIB_OK` | `True` |
| `KMP_INIT_AT_FORK` | `FALSE` |

No other key changed, appeared or disappeared. Image, source, configuration
selectors and job remained identical. The diagnostic recorded no values, so the
implementation must still check the actual values against this table. Installed
scikit-learn 1.9.0 contains the corresponding `setdefault` calls; the diagnostic
establishes the call interval, not the exact writer instruction.

This plan changes only the private inventory helper and its qualification tests.
It does not modify application model/FI behavior, set environment variables,
preload imports, change a resource limit, or enable deployment execution. Normal
packaging, lockfile and CI work belong to the CI/CPU-build owner. B1/B2 source
inventory and reconstruction/commissioning remain DRAFT.

## Proposed behavior

Keep the full original identity check. Admit a narrowly defined initialization
transition at the existing selected-declarations call, then require that exact
resulting environment for the rest of the run:

1. Immediately before the call, the bounded current environment must equal the
   first observed environment. Drift before construction still refuses.
2. Invoke the original qualified selected-declarations path once, under its
   existing constructor guard. Only a successful, nonempty qualified projection
   can supply an initialization checkpoint; failures never supply one and retain
   their original classification. An empty projection cannot authorize drift.
3. Immediately after the call, every previously present key/value must be
   unchanged. The only permitted additions are absent keys from the table, with
   their exact case-sensitive values. Either, both or neither may be added.
   Unexpected values, new names, removals and changes to existing values refuse.
   A mismatch stops qualification: do not change the table during implementation
   to make the observed runtime pass; return the changed evidence for plan review.
4. Carry the resulting bounded environment in the existing internal readback.
   At finalization compare every original identity component, using this
   checkpoint only as the expected environment. Any subsequent change, including
   removal or modification of an accepted key, refuses. No checkpoint means no
   exception to exact original equality.

The exception is available only for the qualified selected model stack in the
admitted runtime, and only when `sklearn` is absent from `sys.modules` immediately
before the call and present immediately after it. Its post-call module origin
must match the admitted initializer. Inspect these facts without importing early.
An already imported library permits only exact environment equality. Admission
verifies the dependency version and inspected
initializer fingerprint without importing the dependency early; a different
dependency implementation requires requalification. The current inspected
`sklearn/__init__.py` SHA-256 is
`ce663dfdb31bdbd4292166527e43a4f861b31b445d2ee894e725e82c1e738f78`.
This is a local compatibility exception, not a general OpenMP setting or an
environment-variable prefix exemption. It does not authorize new model code to
set arbitrary process state.

Use the existing typed `ObservedIdentity` and `Readback` boundaries; a single
internal checkpoint field is sufficient unless implementation demonstrates a
need for more. Do not change the FI signature or add a configurable allowlist.
Keep environment snapshots in memory. Closed reports and diagnostics never emit
values, value hashes, arbitrary key names, URLs or free-form import errors.
Use a closed indication such as `init_transition_accepted` to distinguish an
accepted transition from unchanged environment and from refusal.

## Milestones and acceptance

| Milestone | Deliverable and concrete touchpoints | Exit criteria |
| --- | --- | --- |
| F1 — transition guard | Private helper `identity.py`, `report_builder.py::collect_readback`, `runner.py::inventory`, and their corresponding tests. Existing source/evidence directories are immutable; dispatch records identify a fresh private implementation directory. | Exact transition and checkpoint behavior above; no extra constructor call; original typed identity, packet membership, SQL/transaction and resource checks preserved. Focused tests, lint, formatting and types pass. |
| F2 — independent patch review | One relevant reviewer checks the final helper diff, evidence handling and qualification adapter. Full report stays private. | Findings resolved with regression evidence before any runtime execution; new helper/bundle hashes are bound through the existing packet/controller admission. The census-helper pin changes only if that helper actually changes. |
| F3 — synthetic runtime qualification | Existing CPU-wheel build/provenance adapter, isolated initializer-value confirmation, and supervised base, mixed and declared-only fixtures; the model itself remains installed code. | The initializer confirms both fixed expected values; base cases pass; mixed produces its expected scoped PASS; declared-only produces the expected policy HOLD with completed real declaration and explicit omission evidence. Identity/import/resource HOLD is not an acceptable substitute. Preserve negative drift checks and verify cleanup. |
| F4 — maintained-image handoff | CI/CPU-build owner supplies the reviewed clean build and exact image/package/source evidence. Deployment owner reruns installed-runtime qualification against that image. | Local derivative evidence is labelled experimental. Source execution stays blocked until the intended runtime and separate source packet meet their existing gates. No release publication or activation is authorized by this plan. |

Meaningful F1 tests cover:

- Unchanged environment; each allowed addition alone; both additions; pre-existing
  keys retained exactly. Wrong values/case, changed existing keys, removals and an
  unrelated added key must refuse. Include pre-existing empty/different values
  retained unchanged, and wrong key-name case.
- Changes before the declaration interval and after its checkpoint must refuse,
  even when the final value would otherwise look like an allowed initialization.
- Missing/failed/empty projection and missing checkpoint cannot authorize drift.
  A changed dependency fingerprint, image, job, selector, source file or membership
  still refuses. Oversized/incomplete snapshots remain failures.
- Already imported, missing or wrong-origin library cannot authorize additions.
  Admission must leave `sys.modules` unchanged. No failed import emits its text.
- The original exception and failure classification remain intact. Each model is
  constructed once. Secret sentinels are absent from reports, proof, logs and
  captured failure output; no assertion renders a raw environment dictionary.

For F3, run the affected full private helper regression suite after final changes.
Confirm the two exact initialization values in one disposable synthetic subprocess
on the admitted image, using a minimal synthetic environment with neither key
present. Do not inherit or print host environment values. Import only the admitted
initializer, assert the two literal values, and emit closed matches/mismatch
indications. A mismatch stops; do not amend the table or continue qualification.
This is separate from the inventory child: no preloading, environment mutation or
extra constructor call occurs in that child. Then run the real installed scenarios.
Reuse existing database/cancellation evidence
where unchanged; repeat affected integration cases if the patch alters their
boundary. Keep AS 2 GiB, CPU 30/35 seconds, wall 55 seconds, SQL/row/byte caps,
read-only worker-role transaction, rollback and process reaping unchanged.
Use only synthetic local state and cached, hash-verified inputs. Remove owned
containers, networks and candidate image, verify the original parent survives,
and report retained build cache without pruning unrelated Docker resources.
Any eventual repository code PR requires the full suite after its final change.

## CI coordination and documentation

The CPU trial used the release 0.1.1191 application source with Torch 2.10.0+cpu,
torchvision 0.25.0+cpu, Aquacast 0.1.396 and FI 0.2.10, on Linux/amd64 under an
ARM Docker engine. It proved constructor/declaration completion, not inference,
trained-artifact compatibility, native performance or current-main qualification.
Keep existing security pins and known dependency exceptions; FlashRNN independently
requires Triton. The trial changed only two wheels and retained CUDA distributions
and old layers, so its image size/build time do not measure a clean CPU build.
The unit-test matrix uses Python 3.12; its CPU-wheel availability and sizes must be
checked separately from the Python 3.14 release image.

The CI owner may work independently on `pyproject.toml`, `uv.lock`, `Dockerfile`
and applicable workflow/package documentation. Measure cold and warm dependency
installation, image build/scan and relevant test jobs on the same native runner
class and source revision. Preserve required tests and distinguish download-byte
savings from elapsed time. Coordinate the chosen CPU/GPU/platform policy before
changing default deployment capabilities. Deployment owns the private guard and
its runtime evidence; neither owner changes the other's files without coordination.
The maintained-image handoff must include the installed scikit-learn version,
initializer file origin and SHA-256. Each new image requires identity admission;
a dependency change may also require a new fingerprint qualification. A rebuild
does not necessarily change this Python source hash: verify it in each image
rather than assuming either sameness or a platform-specific difference.

Record implementation outcomes in this plan and its parent. The administrator
guide `docs/deployment/operational-comparison.md` needs no new setting or workaround;
update its supported-runtime instructions only after a maintained image qualifies.
Full reviews, private paths, agency inputs and detailed diagnostics stay outside Git.

```json
{"phases":[{"id":"F1","status":"COMPLETE","depends_on":["independent_plan_review"]},{"id":"F2","status":"COMPLETE","depends_on":["F1"]},{"id":"F3","status":"COMPLETE_LOCAL_EXPERIMENT","depends_on":["F2"]},{"id":"F4","status":"COMPLETE_RELEASE_0.1.1197","depends_on":["F3","maintained_cpu_image"]}],"source_execution":"DRAFT","implementation_admission":"owner_authorized_F1_to_F4","ci_handoff_evidence_spec":"CI coordination and documentation"}
```

One independent Claude plan review completed. Its two findings were incorporated:
bind the exception to the first admitted import within the call, and stop on any
value/name mismatch with an isolated value confirmation before qualification.
The owner subsequently authorized F1–F3 implementation and local synthetic
qualification. Patch review must finish before runtime execution. F4 still needs
the maintained-image handoff; source execution and live activation are not admitted.

## Implementation and local qualification

F1 adds the bounded environment checkpoint to the existing private identity and
readback types. It accepts only the specified first-import additions; every
pre-existing value and every other identity component remain subject to exact
comparison. The original declaration function runs once. Failed declarations
retain their original classification. Checkpoints are absent from object repr
and closed reports; the private proof records only a transition-accepted marker.

**166 affected checks pass:** 43 new checks plus all 123 previously qualified
current-helper cases. The independent patch reviewer matched the historical
case names and verified that none were omitted, checked the final file hashes,
and found no blocking issues. Ruff, formatting and strict changed-module types
pass. Exploratory predecessor tests targeting earlier helper APIs and a separate
database fixture are recorded privately as incompatible runs, not counted as
passes. Earlier database/cancellation evidence is reused only for unchanged
boundaries; the actual worker-role snapshot and finalization path ran again below.

On the same admitted CPU-wheel derivative of release 0.1.1191:

| Check | Observed result |
| --- | --- |
| Isolated initializer | Both fixed values matched; only a closed `MATCH` result was emitted. |
| Base | All eight existing cases matched, including drift and resource refusals. No initialization transition was accepted. |
| Mixed | Expected scoped PASS; ordinary targets covered, declared-grid omission retained, both real declarations completed, transition accepted. |
| Declared-only | Expected `HOLD requirements_unknown`, with completed real declaration and explicit omission; transition accepted. This is a policy-case qualification, not a complete inventory PASS. |

The mixed child took 9.882 seconds wall / 9.668 seconds CPU; declared-only took
9.384 / 9.056 seconds. These are whole-child emulated timings, not native or CI
performance. The 2-GiB AS ceiling, 30/35-second CPU limits, 55-second wall limit,
SQL/row/byte limits and constructor guard were unchanged. Controller readback
verified the exact changed helper bundle, image, source, configuration and job;
the snapshots rolled back and worker connections and processes were cleaned up.

Independent cleanup readback found no owned containers or networks and confirmed
the candidate image/tag absent and original parent layers unchanged. Four untagged
intermediate images from this run remain as local build cache. No live inputs,
provider calls, trained artifacts, inference, source database or live settings
were used. Repository application code, packaging and CI were not changed.

F1–F3 are complete for this local experimental runtime. F4 must qualify the
maintained image supplied by the CI/CPU-build owner before the intended runtime
can be admitted. B1/B2 and reconstruction/commissioning remain DRAFT.

## Maintained-image qualification — 2026-11-26

Release 0.1.1197, source `1b6e2236`, was independently verified against the owner's
original produced/delivered receipts from build run `45785745222`. Source CI was
reused; delivery gates ran, but the release-version suite was not rerun. No image
was relabelled, application file replaced or execution limit widened.

The first native Linux amd64 attempt passed all eight base cases, then refused a
stale private registry implementation pin. The merged display-label change was
reviewed; its whole-file hash matched both the released Git source and installed
image. A fresh private packet updated that one pin and its aggregate digest while
preserving all original evidence. One independent patch review found no blockers;
all 183 affected checks, Ruff and strict changed-module types passed.

The second isolated run matched all ten expected outcomes across base, mixed and
declared-only scenarios. Mixed produced its scoped PASS; declared-only retained
the expected `HOLD requirements_unknown` after successful real declaration and
explicit omission. Every child was reaped, identities checked and database
backends closed. All owned containers/networks and both temporary servers, disks,
interfaces, security groups, keys and access rules were removed with readback.
No live application, job, database or operational secret was changed by this test.

F4 is complete for this exact maintained image. The source has since advanced to
release 0.1.1199 with changed declared-grid rating inputs and schema; the old omission
qualification is not transferable by updating hashes. The
[source-admission plan](2026-11-26-source-inventory-admission.md) separates its
collector, current-route qualification and source execution gates. B1/B2 and
reconstruction/commissioning remain DRAFT.
