---
status: DRAFT
created: 2026-09-21
revised: 2026-09-21
plan: 309
title: A transient tool download reds a PR the security gate passed — CI step failures should mean what they say
scope: Make `build-image-and-scan`'s SBOM step distinguish "the tool could not be installed" from "the tool ran and produced nothing", retry the first, and give a genuinely missing SBOM a durable home before anything is relaxed. Includes the `security.md` / `cicd.md` amendments the relaxation requires. Explicitly NOT loosening any security gate (Trivy's scan, gate table, SARIF derivation and code-scanning upload are untouched), NOT a blanket `continue-on-error`, NOT changing what an SBOM contains, NOT the image build, NOT the model/forecast pipeline.
depends_on: []
blocks: []
open_decisions: [D0, D1, D2, D3]
source: 2026-09-21 — the syft step failed three times on PR #286 (run 35613224865, jobs 106377308103 / 106382760290 / 106384504421) between 14:38 and 14:57 UTC. The run census in §1 is reproducible from the script quoted there; every line anchor was verified by printing the line it names, against `main` at `e943f515`.
---

# Plan 309 — CI step failures should mean what they say

## Status

**DRAFT — reviewed once (Codex, 2026-09-21: NEEDS CHANGES, 1 blocker + 6 major + 2 minor), fully
rewritten in response.** Not re-reviewed. ⛔ Not implementable: **D0, D1, D2 and D3 are all open**, and
D2 gates the only task that keeps the rest safe.

⚖️ **Plan number 309 is claimed, not granted.** 302–305 and 309+ are unused across `docs/plans/`
and `docs/plans/archive/`, and nothing in `docs/` refers to a "Plan 309". The owner grants numbers.

## Why this exists

PR #286's `build-image-and-scan` job failed here, and then twice more on manual re-run:

```
[debug] http_download(url=https://github.com/anchore/syft/releases/v1.51.1)
[error] received HTTP status=504 for url='https://github.com/anchore/syft/releases/v1.51.1'
##[error]The Syft installer failed to install v1.51.1
```

**syft was never installed, so it never ran.** The `unable to find tag=''` line that follows in the
log is the installer misreporting a gateway error as a missing version. The PR touches one service
module, two store modules and four test modules. It touches no Dockerfile, no image, no workflow.

Every other check passed, **including the security gate**: `lint`, `dependency-safety`,
`wheel-only-guard`, `Trivy`, `unit` (13m30s) and `integration` (5m10s).

🔑 **The protection for exactly this was written — one step too low.** `ci.yml:649-650` explains why
the *upload* is keyed on `sbom-generate` rather than on `build-image`:

> Keyed on sbom-generate, not build-image: a transient sbom-action fault must skip the upload, not
> fail it confusingly via `if-no-files-found: error`.

The upload is protected from a transient SBOM fault. `sbom-generate` itself is not, so the fault
fails the whole job and the upload never becomes relevant.

## What is measured

### 1. The run census — method first, because the first attempt at this was wrong

⚠️ **`gh api .../runs?per_page=100&status=completed` returned two different samples minutes apart**
(one windowed 07-15→08-27, one 09-08→09-21). It is not a stable "last 100 runs". The census below
uses `gh run list --workflow=ci.yml --limit 100`, sorted by `createdAt`, each run counted **once**,
classified by the failing step of its failing job:

**Window 2026-09-08 → 2026-09-21. 100 runs: 69 success, 15 failure, 14 cancelled, 2 blank.**

| classification | runs | run ids |
|---|---|---|
| Trivy CVE gate — **real findings, the gate working** | 5 | `35443833160` `35442246061` `35442193346` `35442146136` `34956159161` |
| no failed step recorded (cancelled / startup failure) | 5 | `35327473488` `35327404938` `35327361632` `35327309605` `35098330581` |
| `pytest` — real test failures | 2 | `35323564071` `35263408377` |
| **apt fetch** — `Install system deps for cfgrib / rioxarray / exactextract` | 2 | `34579391806` `34572420036` |
| **syft install** — this incident | 1 | `35613224865` |
| | **15** | reconciles with the 15 above |

*(A first pass at this table said 7 and 4 in the first two rows and did not sum to 15. Independent
review caught the arithmetic; the numbers above are re-derived, deduplicated by run, and carry
their ids so anyone can check them.)*

**The two honest framings, which pull opposite ways:**

- **Rate:** the syft step has failed in **1 of 100** runs. Low.
- **Class:** "a CI step failed because a third-party artifact could not be fetched" is **3 of 15**
  failures — two jobs, two fetch mechanisms, and the apt one has bitten **twice as often** as syft.
- ⛔ **3 of 15 is not "this change prevents 20% of failures."** It is the share of *failures*, not
  of runs, and this plan addresses only the syft member of the class directly.

### 1b. The outage outlived a retry — which is the decisive observation

| attempt | job | time (UTC) | failed on |
|---|---|---|---|
| original | `106377308103` | 14:38:56 | `504` on `…/releases/v1.51.1` |
| re-run 1 | `106382760290` | 14:52:57 | the identical `504`, 16 min later |
| re-run 2 | `106384504421` | 14:57:35 | `504` on `…/download/v1.51.1/syft_1.51.1_checksums.txt` |
| re-run 3 | `106387452121` | ~15:05 | **succeeded** |

From a developer machine at 14:55 the same hour, the tag page, the installer's own
`releases/v1.51.1`, and the `syft_1.51.1_linux_amd64.tar.gz` asset **all returned 200**, and
githubstatus.com reported **All Systems Operational**.

🔑 **Three failures across 19 minutes, then recovery — the artifact reachable from here
throughout.** That is why this is a plan and not a retry: an in-job retry on a seconds-to-minutes
timescale would not have absorbed it, and the developer-side 200s are exactly the evidence that
would otherwise have talked us out of the problem.

⚠️ **One outage is one sample**, and it bounds rather than characterises the fault: ~19 minutes of
failures, recovered within 27.

🔴 **A retry's wait is NOT paid on healthy runs** — the second attempt is conditional on the first
having failed, so the wait costs nothing whenever syft installs. *(Independent recommendation pass,
2026-09-21: an earlier revision of this section claimed the wait is "paid on every run". That is
simply wrong, and it was the load-bearing argument against a generous retry.)*

**Which changes what is affordable, measured:** `build-image-and-scan` declares
`timeout-minutes: 30` (`ci.yml:478`) and the job completes in **~3 minutes** when healthy
(`106387452121`: 15:02:29 → 15:05:49). That leaves roughly **27 minutes of headroom** for retries
inside the existing timeout — which would have covered this outage **with no margin at all**
(first attempt 14:38, recovery ~15:05 = 27 minutes).

⛔ **Still do not set the wait from this one sample.** The LINDAS precedent's 5 minutes came from a
*measured upstream publish cycle*; nothing analogous exists here, and an outage one minute longer
than this one exhausts the whole budget.

### 2. What the standards actually say — including the part against this plan

`security.md:783`: syft "emits a CycloneDX JSON SBOM … uploaded as a workflow artifact **on every
run**", for recoverability — "when a future CVE lands, the artifact answers 'which historical image
contains the affected library?'". `:785`: release attachment and registry attestation are "future
controls — deliberately deferred … The SBOM artifact **on every CI run** is the v0 baseline."

🔴 **And `cicd.md:518` lists "SBOM generation" inside the CI *gate* tier**, alongside integration
tests, image builds and Trivy.

⚠️ **So this plan proposes a real narrowing of a stated control, not merely a correction of an
over-strict implementation.** *(Independent review, major: an earlier revision argued "no document
makes it a gate", which `cicd.md:518` contradicts, and inferred permission from the absence of a
named merge rule.)* "Every run" is the written baseline. **T5 exists because changing behaviour
without changing that text would leave the repo's own standard describing something untrue.**

### 3. What a PR run's SBOM describes

`cicd.md:504`: the CI tag is "purely local to the CI runner … discarded when the runner terminates;
it is never pushed, tagged for release, or attached to a registry."

⚠️ **This applies to `main` runs too.** No CI image is ever deployed; the mini builds its own via
`docker compose --build`. So the `main` SBOM is **not** an inventory of a shipped artifact either —
it is a per-commit reconstruction from the same Dockerfile. *(Review, major: an earlier revision
leaned on a deployed-image equivalence it never demonstrated.)* The `main` lineage is still the
better one to protect, because it is per-commit, contiguous and survives the branch — but the
argument is continuity, not provenance of a deployed image, and D3 should be decided on that.

### 4. Nothing is actually blocked — measured, and it cuts both ways

`GET /repos/hydrosolutions/SAPPHIRE_flow/branches/main/protection` → **404 "Branch not protected"**,
and `/rulesets` is empty. **There are no required status checks.** A red `build-image-and-scan` does
not prevent a merge; the owner merges by hand.

⚠️ **So "a GitHub outage blocks a PR" is false, and this plan does not claim it.** The real cost is
smaller and still worth fixing: a red check that means nothing erodes the signal the owner merges
on, and the repo's rule that the suite must pass before merge becomes a judgement call about which
red is the real one. *(Review, minor: the earlier revision asserted the merge-blocking effect
without checking.)*

### 5. The reasoning any fix must not undo

`ci.yml:616-621`, on the SARIF upload, from Plan 180:

> **NOT wrapped in a blanket continue-on-error**: a broken upload (bad token, malformed SARIF, path
> typo, licensing lapse) must fail the job loudly — the same silent-failure class this plan exists
> to close, moved one level down.

⛔ **"Just add `continue-on-error`" is ruled out before this plan starts.** Plan 180's incident had
two root causes and the second was an artifact that **nothing read** (`ci.yml:617-618`).

🔑 **The distinction this plan relies on:** a **scoped** `continue-on-error` on one attempt of a
retry, *followed by a mandatory enforcement step that decides the job's fate*, is not blanket
forgiveness — the decision still happens, once, explicitly. A `continue-on-error` with **no**
enforcement behind it is precisely what Plan 180 forbids. T1 and T2 are only safe together.

### 6. The repo's retry precedent

`live-lindas-weekly-autoretry.yml` retries a transient external failure with a **cap** (12/day), a
**wait matched to the upstream's cadence** (5 min ≈ BAFU's publish cycle), a **scope restriction**
(scheduled runs only — "a manual rerun that fails is an explicit signal, not a transient") and a
**costed rationale**. Its shape is wrong here; its discipline is the standard to meet.

## The actual tension

Two failures wear one name, and today's arrangement cannot tell them apart:

| what happened | what it says about the image | what it should do |
|---|---|---|
| syft could not be **installed** | **nothing.** No measurement was attempted | retry; if it persists, do not red an unrelated PR — but leave a durable trace |
| syft **ran** and emitted no SBOM, or an invalid one | something real about the image or the tool | fail loudly, on any event |
| anything else (unknown) | unknown | ⛔ **fail loudly.** Unknown is not transient |

## Tasks

### T1 — retry the install, with a design that actually works

**Outcome.** A momentary fetch failure does not reach the job's conclusion, and the retry's
mechanics are specified rather than assumed.

🔴 **The obvious shape does not work, and review proved it before implementation.** A second step
gated on `if: steps.sbom-generate.outcome == 'failure'` **never runs**: a step `if` without a status
function carries an implicit `success()`, and the job is already in a failure state. Adding
`!cancelled()` lets it run but does **not** erase the first step's failure — the job still fails.

**The working shape:**

| | |
|---|---|
| attempt 1 | `continue-on-error: true` — so its `conclusion` is `success` and the job continues, while its `outcome` records `failure` |
| attempt 2 | `if: steps.sbom-1.outcome == 'failure'`, same pinned action and inputs, also `continue-on-error: true` |
| **enforcement** | a normal step, **no** `continue-on-error`, that applies T2's classification and fails the job when it should. **This is what keeps §5's property.** |
| upload | `if:` must select **whichever attempt produced the file** — today's condition requires the *first* to have succeeded, so a successful retry would still skip the upload |

**In.** `.github/workflows/ci.yml`, the SBOM steps only.

**Out.** ⛔ No change to `trivy-scan`, `trivy-gate-table`, `trivy-sarif`, either SARIF upload, the
image build or the scripts smoke-check. ⛔ `continue-on-error` appears **only** on retry attempts
that an enforcement step adjudicates — never on the enforcement step, never on a step with nothing
behind it.

**Verification.** ⚠️ **A bogus pinned version fails deterministically on both attempts and proves
nothing** — it tests the wrong fault. Inject an **attempt-specific** fault (e.g. a first attempt
pointed at an unreachable mirror, the second at the real one) and show: both attempts appear in the
log, the job concludes success, and the SBOM uploaded is the second attempt's.
⚠️ **Effectiveness in production stays unmeasured until observed.** §1b shows this outage would
have survived a short retry; T1 is cheap insurance for the momentary case, not the fix.

**Open:** the retry's wait — see §1b. The observed outage ran ~19 minutes and recovered within 27,
against ~27 minutes of headroom under the existing `timeout-minutes: 30`. The wait costs nothing on
a healthy run, so the real constraint is the timeout, not the common case. ⛔ Do not copy the LINDAS
5 minutes, and do not treat one sample as a cadence.

### T2 — classify the residual failure by what actually happened

**Outcome.** After T1's attempts, the enforcement step decides on **evidence of stage**, not on an
exit status.

🔴 **Exit status cannot tell install from execution.** *(Review, major.)* syft can install and then
die before writing anything; an install can fail for a bad version, a permissions problem or a
failed integrity check — none of them transient. The step must record, explicitly, **whether syft
was installed** and **whether it ran**, and the enforcement step reads that.

**The cases, and all five must be covered:**

| | `pull_request` | `push: main` |
|---|---|---|
| installed, ran, valid SBOM | pass | pass |
| **never installed** (fetch failed both attempts) | annotate + continue, **and** emit T3's signal | **fail** (subject to D3) |
| **ran, produced no file** | ⛔ **fail** | ⛔ **fail** |
| **ran, produced an empty or invalid file** | ⛔ **fail** | ⛔ **fail** |
| anything else / unclassifiable | ⛔ **fail** | ⛔ **fail** |

**"Invalid" must be defined in the task, not left to the reader** — at minimum: parses as JSON, is
CycloneDX, and has a non-empty component list. An unparseable or zero-component SBOM is a finding,
not an inventory.

**Out.** ⛔ A version that keys on `outcome` alone has not done this task. ⛔ Unknown failures are
never forgiven.

**Verification.** Force each of the five rows on both event types and record the job conclusion for
each. Ten forced cases, none reasoned about.

### T3 — a durable signal, covering the failures that are actually forgiven

**Outcome.** Every run that T2 lets pass without an SBOM produces a trace that outlives the run and
reaches a named person.

🔴 **Gated on D2, and it must cover PR runs.** *(Review, major: the previous draft forgave PR
failures but proposed a monitor that checked only `main`, so the exact runs being forgiven were the
ones nothing watched.)* Two requirements, both testable:

1. **Coverage** — the signal fires for a forgiven **PR** run, not only for a failing `main` run.
2. **A consumer** — a red scheduled workflow is not delivery unless someone is named who reads it.
   ⛔ "Another red run in the Actions tab" does not satisfy this.

**Out.** ⛔ Not another workflow artifact nobody reads — the incident's second root cause
(`ci.yml:617-618`). ⛔ Not a job-summary annotation as the only signal.

**Verification.** Force a forgiven PR run and a persistent `main` failure; show the signal arrives
at its destination in both cases and is still findable a week later without knowing the run id.

### T4 — survey the same exposure elsewhere, and fix nothing blind

**Outcome.** A list of every CI step whose failure would red a PR for a reason unrelated to the PR,
each classified gate or infrastructure. **A survey, not a refactor.**

**Starting evidence.** `Install system deps for cfgrib / rioxarray / exactextract` — 2 of 15
failures (§1), the same class by a different mechanism, and more frequent than syft.

**Out.** ⛔ No step changes under T4. Each finding becomes its own task or plan, reviewed against
its own step's meaning.

### T5 — amend the standards the relaxation contradicts

**Outcome.** `security.md` and `cicd.md` describe the behaviour that actually ships.

**In.** `security.md:783`/`:785` — "on every run" becomes the *intent* plus the stated exception and
its compensating control. `cicd.md:518` — "SBOM generation" is qualified where it sits in the gate
list. `cicd.md:732` — the step description follows T1's shape.

**Out.** ⛔ T5 does not merge before T2/T3; a standard describing a control that does not exist yet
is the mirror of the defect this plan is fixing.

**Verification.** A reader of `security.md` alone can state correctly when a CI run may complete
without an SBOM and what catches it.

## Owner decisions

### D0 — should this plan exist at its current size? *(raised by the review, not by the author)*

🔴 **The option the plan never offered.** *(Independent recommendation pass, 2026-09-21.)* Bounded
retry (T1) + a clearer failure message, **keeping the SBOM fatal on both events** — no classifier,
no exception policy, no compensating notifier, no standards amendment. T2, T3 and T5 all disappear.

**The case for it, now stronger than when the plan was written:**

- the retry wait is free on healthy runs (§1b), and ~27 minutes of headroom exists inside the
  current timeout — enough to have absorbed this outage, barely;
- **1 affected run in 100**, with **no mechanical merge block** (§4);
- every part of T2/T3/T5 is a new moving part with an owner cost, on a 3–4 person team with a
  Nepal v1.0 deadline in **October 2026**.

**The case against:** a retry that exhausts still reds the job for a reason unrelated to the
change, and §1b shows a slightly longer outage does exactly that. D0 accepts that residue.

⚖️ **This is the first decision, and it subsumes the other three** — if D0 is "keep it small",
D2 and D3 become moot and D1 shrinks to a mechanics question.

### D1 — keep `anchore/sbom-action`, or move to the pinned syft CLI?

**Two recommendations, and they conflict — the owner picks.**

| | choice | reasoning |
|---|---|---|
| author | **keep the action** | SHA-pinned per `security.md`'s supply-chain policy; self-installing a CLI trades one transient failure for a new pinning obligation. Cost: the pinned SHA appears at two sites and must be bumped in both |
| independent reviewer (medium confidence) | **the pinned CLI** | the action "exposes no documented outputs" for the download / verify / install / execute stages, so **T2's stage evidence would have to be inferred from logs**. Pin version *and* expected checksum. Buys observability, not immunity from outages |

⚖️ **The reviewer's argument is the stronger one *if* T2 survives D0** — stage evidence is exactly
what T2 needs and log-scraping for it is fragile. If D0 keeps the plan small, T2 disappears and
this reverts to a plain mechanics question where the author's answer holds.
⚠️ If (b) is chosen instead, `ci.yml:634` records the real equivalent
(`syft sapphire-flow:ci-<sha> …`). `cicd.md:620`'s `sapphire-flow:local` is the **local** equivalent
column and is correct as it stands — *(review, minor: an earlier draft called this a
contradiction; it is not)*.

### D2 — where does a genuinely missing SBOM show up, and who reads it?

| option | strength | weakness |
|---|---|---|
| open/update a GitHub issue | durable, assignable, has an addressee by construction | needs `issues: write`; can generate noise |
| a scheduled job checking recent runs produced `sbom-cyclonedx` | reads the artifact rather than a step's self-report; catches slow decay | a second moving part; artifact **retention** bounds the lookback; needs a named reader |
| annotation only | free | ⛔ **the Plan 180 class** |

**Two recommendations, and the reviewer's is better — recorded as such.**

| | choice | reasoning |
|---|---|---|
| author | the scheduled check, extended to PR runs | verifies the artifact *exists* rather than that a step *reported success* |
| independent reviewer (**high** confidence) | **an assigned GitHub issue**, opened/updated per incident | "the plan recommends a **detector** as though it were a **destination**". A scheduled check answers *whether*; an issue answers *who acts*. One issue per incident, updated with affected commits and runs — not one per retry |

⚖️ **These compose rather than compete, which the plan got wrong**: verify the artifact's presence,
*then* open or update an assigned issue. The open question is no longer which mechanism but **who
it is assigned to** — the reviewer proposes the IT specialist who owns CI, with the owner as
fallback.
⚠️ If the scheduled check is adopted at all, establish the artifact retention period first; it sets
the maximum lookback.

### D3 — does a missing SBOM fail `main`?

§3 weakened the original argument: `main`'s CI image is discarded too, so this is about **continuity
of a per-commit record**, not about a deployed artifact. Two defensible answers — keep `main` fatal
(costs nothing while the tool works), or forgive both and rely wholly on D2's monitor (simpler, one
mechanism instead of two). Recommendation: **keep `main` fatal**, but decide it on continuity. The independent reviewer agrees
(medium confidence) and adds a condition the plan had not stated: ⚠️ **a red `main` run is not
continuity unless someone owns *recovering* the missing record** — re-running to regenerate it, or
explicitly recording the gap as unrecoverable. Without that, the fatal signal preserves an
obligation nobody discharges.

## Watch items, not tasks

- ⛔ **Nothing here may weaken a security gate.** Trivy's scan, gate table, SARIF derivation and
  code-scanning upload are out of scope in every task.
- 🪤 **A retry that always fails twice and then continues looks exactly like success.** T3 is what
  makes that distinguishable — this is the plan's own most likely failure mode.
- 🪤 **Two true numbers that pull opposite ways** (§1): rate 1/100, class 3/15. Quote both or
  neither, and never as "prevents 20% of failures."
- 🪤 **`gh api ...runs?status=completed` is not a stable sample** (§1). Any future census uses
  `gh run list` and reports its window.
- **`main` has no branch protection** (§4). If required checks are ever added, T2's PR/main split
  changes meaning and this plan must be re-read.
- **No job depends on `build-image-and-scan`** — `cicd.md:733` records that Plan 064's `e2e` job was
  never built.

## Exit gates

- 🔴 **D0 is CLOSED FIRST.** It subsumes the rest: a "keep it small" answer retires T2, T3, T5, D2
  and D3 outright, and no other gate below applies. Nothing starts before D0.
- **D1 and D3** are closed or explicitly carried, with the carrier named.
- 🔴 **D2 is CLOSED — not carried.** T2 may not merge while D2 is open; carrying D2 defers T2.
  *(Review, major: the previous "closed or explicitly carried" wording let the safeguard decision
  stay open while the relaxation shipped.)*
- T1's retry is proven by an **attempt-specific** injected fault, not a bogus version and not a
  green run.
- T2's **ten** forced cases (five classifications × two event types) each have a recorded conclusion.
- T3 is **merged and demonstrated operational** — signal delivered for a forgiven PR run and for a
  persistent `main` failure, with its reader named — **before** T2's relaxation is enabled.
- T5 has landed, so no standard describes behaviour that does not ship.
- T4 produces a classified list; every change it implies is filed separately.
- Plan 180's reasoning at `ci.yml:616-621` is quoted in the PR description, with an explicit
  statement of why scoped-retry-plus-enforcement does not undo it (§5).

```json
{
  "phases": [
    { "id": "P0", "tasks": [], "decision": "D0 must be CLOSED FIRST",
      "note": "scope gate: a 'keep it small' answer deletes P2-P4 and leaves only P1 and P5" },
    { "id": "P1", "tasks": ["T1"], "depends_on": ["P0"], "decision": "D1",
      "note": "retry the install — cheap, and MEASURED INSUFFICIENT alone: the same 504 recurred over 19 min" },
    { "id": "P2", "tasks": ["T3"], "depends_on": ["P1"], "decision": "D2 must be CLOSED",
      "note": "the durable signal must exist AND be demonstrated before the relaxation that relies on it" },
    { "id": "P3", "tasks": ["T2"], "depends_on": ["P2"], "decision": "D3",
      "note": "classify by stage evidence; sequenced after T3 deliberately" },
    { "id": "P4", "tasks": ["T5"], "depends_on": ["P3"],
      "note": "amend the standards last, so they describe what shipped" },
    { "id": "P5", "tasks": ["T4"], "parallel_with": ["P0", "P1", "P2", "P3", "P4"],
      "note": "survey only; produces tasks, changes nothing" }
  ]
}
```

## Changelog

**2026-09-21 — created**, the same day the failure occurred.

**2026-09-21 — four line anchors corrected.** The first commit cited `cicd.md` at 461/577/689/690 —
numbering from a checkout that had not pulled the Plan 307 merge. ⭐ `git fetch` updates the refs,
not the working tree. Every anchor is now verified by printing the line it names.

**2026-09-21 — T1's premise falsified within the hour.** The plan opened saying a retry "alone
removes the failure mode" on one observation; a re-run 16 minutes later hit the identical 504, and a
third 5 minutes after that. ⭐ One observation did not license a claim about the failure's
*duration*, and the plan made one anyway.

**2026-09-21 — rewritten after independent review** (Codex: NEEDS CHANGES — 1 blocker, 6 major,
2 minor). Rewritten rather than patched, per the house rule that layered corrections are how Plan
252 failed three rounds on its text. What review changed:

| finding | what it was | what it changed |
|---|---|---|
| **blocker** | the recommended retry **cannot work** — `if: outcome == 'failure'` carries an implicit `success()` and never runs; and T1 forbade the scoped `continue-on-error` its own design needs | T1 now specifies the working shape, including the upload condition, and §5 states why scoped-retry-plus-enforcement is not blanket forgiveness |
| major | exit status cannot distinguish install from execution failure; "invalid SBOM" undefined; "ran but produced no file" missing | T2 rewritten around **stage evidence**, five cases, unknown ⟹ fatal |
| major | the compensating control excluded the very failures being forgiven (PR runs), and named no consumer | T3 now requires PR coverage **and** a named reader, both verified |
| major | the plan understated the policy change — `cicd.md:518` **does** list SBOM generation in the gate tier, and `main` CI images are discarded too | §2 and §3 corrected; **T5 added** to amend the standards |
| major | the census rows summed to 16 against 15 failures, with no dedup rule | §1 re-derived by run with ids and method; the unstable-API trap recorded |
| major | retry effectiveness asserted, not measured; a bogus version fails deterministically on both attempts | T1's verification requires an **attempt-specific** fault; effectiveness declared unmeasured |
| major | exit gates let D2 stay "carried" while the relaxation shipped | D2 must be **CLOSED**; T3 must be **demonstrated operational** before T2 |
| minor | two citation claims were wrong — `cicd.md:620`'s `:local` is the local-equivalent column, and `security.md:783` does not mention the action | both removed |
| minor | "blocked PR" unverified | §4: `main` has **no branch protection**; the claim is withdrawn and the real, smaller cost stated |

⭐ **Five of the nine findings were the same error: asserting where I could have measured.** The
branch protection, the run census, the retry semantics, the `:local` tag and the standards text were
each checkable in seconds, and four of the five came out against the plan.

**2026-09-21 — the outage ended.** The fourth attempt at the same job succeeded (`106387452121`),
and PR #286 went fully green. Total span of the incident ~19 min of failures, recovered within 27.
Folded into §1b and T1 as a **bound, not a cadence** — the temptation it creates is to set the
retry's wait from a single sample, which is the same error as the falsified premise two entries up.

**2026-09-21 — independent recommendation pass, and it disagreed with the author twice.** Asked for
a recommendation on each open decision rather than another review, the reviewer chose **against**
the plan's own recommendation on D1 (the pinned CLI, because the action exposes no documented
stage outputs and T2 needs stage evidence, not log parsing) and on D2 (an **assigned issue**, on the
ground that the plan "recommends a detector as though it were a destination" — artifact verification
answers *whether*, an assigned issue answers *who acts*, and they compose rather than compete). It
agreed on D3 while noting the justification is incomplete without someone owning **recovery** of a
missing record.

⭐ **It also found a load-bearing factual error**: the claim that a retry's wait is "paid on every
run". A conditional second attempt runs only after a failure. Corrected in §1b and T1 — and the
correction cuts against the plan, because it makes the cheap option better than the plan argued.

**D0 added** as a result: the plan never offered "do the small thing and stop", and on the measured
evidence that deserves to be the first decision rather than an unstated assumption.
