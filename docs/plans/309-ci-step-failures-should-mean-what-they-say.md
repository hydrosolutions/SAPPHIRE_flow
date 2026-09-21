---
status: DRAFT
created: 2026-09-21
plan: 309
title: A transient tool download fails a PR the security gate passed — CI step failures should mean what they say
scope: Make `build-image-and-scan`'s SBOM step distinguish "the tool could not be installed" from "the tool ran and produced nothing", retry the first before deciding anything, and give a genuinely missing SBOM a durable home other than a blocked PR. Explicitly NOT loosening any security gate (Trivy's scan, gate table, SARIF derivation and code-scanning upload are untouched), NOT a blanket `continue-on-error` anywhere, NOT changing what an SBOM contains or the image build, NOT touching the model/forecast pipeline.
depends_on: []
blocks: []
open_decisions: [D1, D2, D3]
source: 2026-09-21 — the syft step failed on PR #286 (run 35613224865, job 106377308103) with a GitHub 504; every count below was measured that day against the last 100 completed `ci.yml` runs via the Actions API, and the workflow/standards citations are line-anchored to the repo at `052327d6`.
---

# Plan 309 — CI step failures should mean what they say

## Status

**DRAFT.** Written after a GitHub outage blocked a PR whose security gate had passed.
⚖️ **Plan number 309 is claimed, not granted** — 302–305 and 309+ are unused in `docs/plans/`
and `docs/plans/archive/`, and nothing in `docs/` refers to a "Plan 309". The owner grants numbers;
if 309 is wanted elsewhere this document renumbers.

**Three decisions are open (D1, D2, D3) and D2 gates the only task that could recreate a
silent-failure class.** Nothing here is implementable until at least D1 and D2 are closed.

## Why this exists

PR #286's `build-image-and-scan` job failed here:

```
[command]/usr/bin/sh …_syft v1.51.1
[debug] http_download(url=https://github.com/anchore/syft/releases/v1.51.1)
[error] received HTTP status=504 for url='https://github.com/anchore/syft/releases/v1.51.1'
[error] unable to find tag=''
##[error]The Syft installer failed to install v1.51.1; see the log above for details
```

**syft was never installed, so it never ran.** The `unable to find tag=''` line is the installer
misreporting a gateway error as a missing version — the tag is fine (`GET
github.com/anchore/syft/releases/tag/v1.51.1` → **200**, re-checked the same hour). The PR touches
`src/sapphire_flow/services/caravan_statics.py`, two store modules and four test modules. It
touches no Dockerfile, no image, no workflow.

Every other check on that run passed, **including the security gate**: `lint`, `dependency-safety`,
`wheel-only-guard`, `Trivy`, `unit` (13m30s) and `integration` (5m10s). The one red mark was an
inventory artifact that could not download its own binary.

🔑 **The protection for exactly this was written — one step too low.** `ci.yml:649-650` explains why
the *upload* is keyed on `sbom-generate` rather than on `build-image`:

> Keyed on sbom-generate, not build-image: a transient sbom-action fault must skip the upload, not
> fail it confusingly via `if-no-files-found: error`.

The upload is protected from a transient SBOM fault. `sbom-generate` itself is not, so the
transient fault fails the whole job and the upload never becomes relevant.

## What is measured

### 1. How often this fires — and the honest number

Last **100 completed `ci.yml` runs** (Actions API, 2026-09-21). **15 failed.** By failing step:

| failing step | runs | what it is |
|---|---|---|
| `Render vulnerability table (gate)` and/or `lint/Trivy filesystem scan` | 7 | the CVE gate **working** — real findings |
| `unit` pytest | 2 | real test failures |
| `Install system deps for cfgrib / rioxarray / exactextract` | 2 | an **apt fetch** — the same transient class |
| `Generate SBOM with syft` | **1** | today |
| no failed job recorded (cancelled / startup failure) | 4 | — |

⚠️ **State this plainly rather than inflate it: the syft step has failed once in 100 runs.** "It
will bite us again" is a forecast, not an observation. What *is* observed is the **class** —
a CI step failing because a third-party artifact could not be fetched — at **3 of 15 failures**,
across two different jobs and two different fetch mechanisms. The plan's case rests on the class.

### 2. What an SBOM is for, per our own standard

`docs/standards/security.md:783`:

> `syft` runs after the CI image build and emits a CycloneDX JSON SBOM … uploaded as a workflow
> artifact on every run. SBOM gives the repo immediate recoverability value: when a future CVE
> lands, the artifact answers "which historical image contains the affected library?".

and `:785` — "Release attachment and registry attestation are **future controls** — deliberately
deferred until an image-publish workflow exists."

**The SBOM is an inventory trail, not a merge control.** No document in `docs/standards/` makes it
a gate. Today's behaviour is therefore stricter than the policy it implements.

### 3. The image a PR run scans is thrown away

`docs/standards/cicd.md:504`: the CI tag `sapphire-flow:ci-${{ github.sha }}` is "purely local to
the CI runner … **discarded when the runner terminates; it is never pushed, tagged for release, or
attached to a registry**." CI runs on `push: [main]` **and** `pull_request` (`ci.yml:3-6`), so the
merge commit produces its own SBOM on `main`.

🔑 **The lineage recoverability actually depends on is the `main` lineage.** A PR run's SBOM
describes an image that no longer exists and was never deployed.

### 4. The reasoning any fix must not undo

`ci.yml:616-621`, on the SARIF upload, from Plan 180:

> **NOT wrapped in a blanket continue-on-error**: a broken upload (bad token, malformed SARIF, path
> typo, licensing lapse) must fail the job loudly — the same silent-failure class this plan exists
> to close, moved one level down.

⛔ **"Just add `continue-on-error`" is therefore ruled out before this plan starts.** Plan 180's
incident had two root causes and the second was an artifact that **nothing read**. A green run that
quietly produced no SBOM is that failure wearing a different hat.

### 5. The repo already has a retry precedent

`live-lindas-weekly-autoretry.yml` retries a transient external failure with a **cap** (12/day), a
**wait matched to the upstream's cadence** (5 min ≈ BAFU's publish cycle), a **scope restriction**
(scheduled runs only — "a manual rerun that fails is an explicit signal, not a transient") and a
**costed rationale** (~$1.42 per incident). Its *shape* is wrong here — it re-dispatches a whole
workflow via `workflow_run` — but its **discipline** is the standard this plan should meet.

## The actual tension

Two failures wear one name, and today's arrangement cannot tell them apart:

| what happened | what it says about the image | what it should do |
|---|---|---|
| syft could not be **installed** — the network never delivered the binary | **nothing.** No measurement was attempted | retry; if it still fails, do not block an unrelated PR |
| syft **ran** and emitted no SBOM, or an invalid one | something real — the image or the tool is wrong | fail loudly |

Neither "always fail" nor "never fail" is correct, which is why this is a plan and not a one-line
patch.

## Tasks

### T1 — retry the install before deciding anything

**Outcome.** A single transient fetch failure no longer reaches the job's conclusion. On the
observed evidence this alone removes the failure mode; the rest of the plan exists for what is
left when a retry does not help.

**In.** `.github/workflows/ci.yml`, the `Generate SBOM with syft` step only.

**Out.** ⛔ No change to `trivy-scan`, `trivy-gate-table`, `trivy-sarif`, the SARIF uploads, the
image build or the scripts smoke-check. ⛔ No `continue-on-error` in this task — T1 retries, it
does not forgive.

**How** depends on **D1**, because `anchore/sbom-action` is a `uses:` step and a `uses:` step
cannot be wrapped in a shell retry loop:

| option | shape | cost |
|---|---|---|
| **(a)** keep the action | a second `uses:` step, identical inputs, `if: steps.sbom-generate.outcome == 'failure'` | duplicated step; the pinned SHA now appears twice and must be bumped in two places |
| **(b)** move to the CLI | the equivalent already recorded at `ci.yml:634` and `cicd.md:620` — `syft sapphire-flow:ci-<sha> -o cyclonedx-json > sbom.cdx.json` — inside a bounded retry loop | we own the install step; loses whatever the action does beyond the CLI, which must be checked, not assumed |

**Verification.** ⚠️ **A retry cannot be verified by watching CI be green** — it is green either
way. Prove it by forcing the first attempt to fail (an unreachable installer URL, or a deliberately
bogus pinned version on a scratch branch) and showing the job completes with an SBOM produced by
the second attempt, and that the run log contains **both** attempts.

### T2 — make the residual failure mean what it is

**Outcome.** After T1's retries are exhausted, a run that produced an image but no SBOM is
classified rather than uniformly fatal.

**Proposed shape, subject to D3.** A `push: main` run **fails** — that is the lineage recoverability
depends on (§3). A `pull_request` run **annotates and continues** — its image is discarded and the
merge commit will produce the real SBOM.

**In.** `ci.yml`, the SBOM steps.

**Out.** ⛔ The classification must key on **what was produced**, not on the step's exit status
alone: "syft ran and emitted an empty or invalid `sbom.cdx.json`" must stay fatal on both event
types. Distinguishing those two is the whole point of the task; a version that only reads
`outcome` has not done it.

**Verification.** Four cases, each forced, not reasoned about: {`main`, PR} × {install failed,
produced an invalid SBOM}. Record the job conclusion for each.

### T3 — a missing SBOM needs a durable home, not an annotation

**Outcome.** A run that legitimately produced no SBOM is visible somewhere a person will later
look, so that T2's relaxation does not recreate the Plan 180 class.

🔴 **This is the task that makes T2 safe, and it is gated on D2.** An annotation on an otherwise
green run is read by nobody; if that is all T2 leaves behind, the plan has converted a loud
false alarm into a silent real one. ⛔ **T2 must not merge ahead of T3.**

**In.** Depends entirely on D2.

**Out.** ⛔ Not another workflow artifact nobody reads — that was the incident's second root cause
(`ci.yml:617-618`).

**Verification.** Force a persistent SBOM failure on `main` and show the signal arrives at its
destination and survives the run — i.e. is still findable a week later without knowing the run id.

### T4 — survey the same exposure elsewhere, and fix nothing blind

**Outcome.** A list of every CI step whose failure would block a PR for a reason unrelated to the
PR, with each classified as gate or infrastructure. **A survey, not a refactor.**

**Starting evidence.** `Install system deps for cfgrib / rioxarray / exactextract` failed **2** of
the last 100 runs (§1) — the same class, a different mechanism, and it has bitten **twice as often
as syft**.

**Out.** ⛔ No step changes under T4. Anything it finds becomes its own task or its own plan, so
that each change is reviewed against its own step's meaning rather than waved through on this
plan's argument.

## Owner decisions

### D1 — keep `anchore/sbom-action`, or move to the pinned syft CLI?

Recommendation: **(a), keep the action.** It is SHA-pinned per `security.md`'s supply-chain policy;
switching to a CLI we install ourselves trades one transient failure for a new pinning obligation.
The duplicated SHA is a real cost — mitigate it with a comment at both sites naming the other.

### D2 — where does a genuinely missing SBOM show up?

The options, honestly costed:

| option | strength | weakness |
|---|---|---|
| open/update a GitHub issue | durable, assignable, survives the run | needs `issues: write`; can generate noise |
| a scheduled job that checks recent `main` runs have `sbom-cyclonedx` artifacts and fails if not | reads the thing we actually care about, catches slow decay | a second moving part; artifacts expire (retention) |
| job-summary annotation only | free | ⛔ **nobody reads it** — this is the Plan 180 class |

Recommendation: **the scheduled check.** It verifies the artifact *exists* rather than that a step
*reported success*, which is the distinction Plan 180 was about. ⚠️ Artifact retention bounds how
far back it can look — establish that number before adopting it.

### D3 — is the PR/main split right?

Alternative: never let the SBOM block **either**, with T3 as the sole signal. Simpler, and defensible
if the scheduled check in D2 is adopted. Recommendation: keep `main` fatal — it costs nothing when
the tool works and keeps a hard failure on the lineage that matters.

## Watch items, not tasks

- ⛔ **Nothing in this plan may weaken a security gate.** Trivy's scan, gate table, SARIF
  derivation and code-scanning upload are out of scope in every task.
- 🪤 **A retry that always fails twice, then continues, looks exactly like success.** T3 is what
  makes that distinguishable. This is the plan's own most likely failure.
- 🪤 **`syft` failed once in 100 runs.** If review wants the stronger case, it is the class (3 of
  15 failures), not this step. Do not let the number drift upward in retelling.
- **`ci.yml:634` and `cicd.md:620` both record the equivalent CLI — and they disagree.** `ci.yml`
  names `sapphire-flow:ci-${{ github.sha }}`, the `cicd.md:620` table names `sapphire-flow:local`. If D1
  picks (b), the real command has to be established rather than copied from either, and both must be
  updated, and `cicd.md:732` and `security.md:783` both describe the step "via `anchore/sbom-action`"
  — four sites, not one.
- **No job depends on `build-image-and-scan`** (`cicd.md:733`: the `e2e` job Plan 064 specified
  was never built). So this job's status blocks a PR only through the branch protection rules —
  worth confirming which checks are actually required before assuming T2 changes anything.

## Exit gates

- D1, D2 and D3 are each closed or explicitly carried, with the carrier named.
- T1's retry is proven by a **forced** first-attempt failure, not by a green run.
- T2's four cases are each forced and their conclusions recorded.
- T3 is merged **no later than** T2, and its signal is shown to survive the run.
- T4 produces a classified list; every change it implies is filed separately.
- Plan 180's reasoning at `ci.yml:616-621` is quoted in the PR description with an explicit
  statement of why this change does not undo it.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"], "decision": "D1",
      "note": "retry the install; on the measured evidence this alone removes the observed failure" },
    { "id": "P2", "tasks": ["T3"], "depends_on": ["P1"], "decision": "D2",
      "note": "the durable signal must EXIST before the relaxation that relies on it" },
    { "id": "P3", "tasks": ["T2"], "depends_on": ["P2"], "decision": "D3",
      "note": "classify the residual failure; sequenced AFTER T3 deliberately" },
    { "id": "P4", "tasks": ["T4"], "parallel_with": ["P1", "P2", "P3"],
      "note": "survey only; produces tasks, changes nothing" }
  ]
}
```

## Changelog

**2026-09-21 — created.** Written the same day the failure occurred, from the run log, the Actions
API over the last 100 runs, and the standards text. **Not yet independently reviewed.**

**2026-09-21 — four line anchors corrected.** The first commit cited `cicd.md` at 461/577/689/690.
Those numbers came from a working tree that had not yet pulled the Plan 307 merge, which added
lines to that file; against the real `main` they are 504/620/732/733 and land on unrelated text.
⭐ Re-measured at the moment of writing, but against the wrong tree — `git fetch` updates the refs,
not the checkout. Every anchor in this document has since been verified by printing the line it
names. See `feedback_measure_at_the_moment_of_acting`.
