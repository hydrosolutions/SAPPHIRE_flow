---
status: DRAFT
created: 2026-09-21
revised: 2026-09-21
plan: 309
title: A transient tool download reds a PR the security gate passed — retry it, and say so when it still fails
scope: Retry the SBOM step's tool install inside `build-image-and-scan`, and when the retries are exhausted, fail with a message that says what actually happened. The SBOM stays mandatory on every run, so no control is relaxed and no standard changes. Plus a read-only survey of the same exposure elsewhere. Explicitly NOT touching any security gate (Trivy's scan, gate table, SARIF derivation, code-scanning upload), NOT a blanket `continue-on-error`, NOT changing what an SBOM contains, NOT the image build, NOT the model/forecast pipeline.
depends_on: []
blocks: []
open_decisions: [D1]
source: 2026-09-21 — the syft step failed three times on PR #286 (run 35613224865, jobs 106377308103 / 106382760290 / 106384504421) between 14:38 and 14:57 UTC and recovered on the fourth (106387452121, 15:05). The run census in §1 is reproducible by the method stated there; every line anchor was verified by printing the line it names, against `main` at `672c8df5`.
---

# Plan 309 — CI step failures should mean what they say

## Status

**DRAFT.** Two independent passes so far — a review (NEEDS CHANGES: 1 blocker, 6 major, 2 minor)
and a recommendation pass that **disagreed with the author on two of three decisions and found a
load-bearing factual error**. This document is the reconciliation of both.

⚖️ **It is about a third of its previous size.** The reviewer's strongest point was not any single
finding — it was that the plan was **too big for its evidence**. Reconciling on the measured facts
removed three of its five tasks, two of its four decisions, and the standards amendment those
required. What is left is a retry and an honest error message.

⚖️ **Plan number 309 is claimed, not granted.** 302–305 and 309+ are unused across `docs/plans/`
and `docs/plans/archive/`; nothing in `docs/` refers to a "Plan 309". The owner grants numbers.

## Why this exists

PR #286's `build-image-and-scan` failed here, then twice more on manual re-run:

```
[debug] http_download(url=https://github.com/anchore/syft/releases/v1.51.1)
[error] received HTTP status=504 for url='https://github.com/anchore/syft/releases/v1.51.1'
##[error]The Syft installer failed to install v1.51.1
```

**syft was never installed, so it never ran.** The `unable to find tag=''` line that follows is the
installer misreporting a gateway error as a missing version. The PR touched one service module, two
store modules and four test modules — no Dockerfile, no image, no workflow. Every other check
passed, **including the security gate**.

🔑 **The protection for exactly this was written — one step too low.** `ci.yml:649-650` explains why
the *upload* is keyed on `sbom-generate` rather than `build-image`:

> Keyed on sbom-generate, not build-image: a transient sbom-action fault must skip the upload, not
> fail it confusingly via `if-no-files-found: error`.

The upload is protected. `sbom-generate` is not, so the fault fails the job and the upload never
becomes relevant.

## What is measured

### 1. The run census — method first, because the first attempt at this was wrong

⚠️ **`gh api .../runs?per_page=100&status=completed` is not a stable sample** — two calls minutes
apart returned windows 07-15→08-27 and 09-08→09-21. The census below uses
`gh run list --workflow=ci.yml --limit 100`, sorted by `createdAt`, **each run counted once**,
classified by the failing step of its failing job.

**Window 2026-09-08 → 2026-09-21. 100 runs: 69 success, 15 failure, 14 cancelled, 2 blank.**

| classification | runs | run ids |
|---|---|---|
| Trivy CVE gate — **real findings, the gate working** | 5 | `35443833160` `35442246061` `35442193346` `35442146136` `34956159161` |
| no failed step recorded (cancelled / startup failure) | 5 | `35327473488` `35327404938` `35327361632` `35327309605` `35098330581` |
| `pytest` — real test failures | 2 | `35323564071` `35263408377` |
| **apt fetch** — `Install system deps for cfgrib / rioxarray / exactextract` | 2 | `34579391806` `34572420036` |
| **syft install** — this incident | 1 | `35613224865` |
| | **15** | reconciles |

*(A first pass said 7 and 4 in the first two rows and summed to 16 against 15 failures. Review
caught the arithmetic; the above is re-derived per run and carries ids.)*

**Two honest framings that pull opposite ways:**

- **Rate:** the syft step has failed in **1 of 100** runs.
- **Class:** "a CI step failed because a third-party artifact could not be fetched" is **3 of 15**
  failures — two jobs, two mechanisms, and the apt one has bitten **twice as often**.
- ⛔ Neither is "this change prevents 20% of failures". 3/15 is a share of *failures*, not of runs.

### 2. The outage outlived a retry, and then ended

| attempt | job | time (UTC) | result |
|---|---|---|---|
| original | `106377308103` | 14:38:56 | `504` on `…/releases/v1.51.1` |
| re-run 1 | `106382760290` | 14:52:57 | identical `504`, 16 min later |
| re-run 2 | `106384504421` | 14:57:35 | `504` on `…/download/…/syft_1.51.1_checksums.txt` |
| re-run 3 | `106387452121` | 15:02→15:05 | **succeeded** |

Throughout, from a developer machine: the tag page, the installer's `releases/v1.51.1` and the
`linux_amd64.tar.gz` asset all returned **200**, and githubstatus.com was **green**. ⚠️ A local
probe would have argued this problem out of existence.

⚠️ **These four points bound the outage; they do not describe it.** The last observed failure was at
14:57:35 and the SBOM step succeeded at 15:05:19–15:05:39, so recovery happened somewhere in a
**~8-minute window we never sampled**. Measured from the first attempt, that is **between +18.6 and
+26.6 minutes**. *(Third review pass, major: an earlier revision treated this as a continuous
27-minute outage and drew conclusions from it.)* Nothing here establishes that the fault was
continuous, and no retry schedule below can be judged against a duration we did not measure.

### 3. What a retry actually costs — both measurements came out in its favour

🔴 **The wait is not paid on healthy runs.** A second attempt is conditional on the first failing,
so the wait costs nothing whenever syft installs. *(An earlier revision claimed it is "paid on every
run"; that was wrong, and it was the load-bearing argument against a generous retry.)*

🔴 **Runner minutes are free.** `hydrosolutions/SAPPHIRE_flow` is a **public** repository, so
GitHub-hosted runner time is not billed. The LINDAS precedent's costed rationale (~$1.42/incident)
does not transfer.

**So the only real constraint is the job timeout**, and it must be computed from where the SBOM
step actually starts, not from the job's total duration. Per-step timings from the healthy run
`106387452121`:

| | |
|---|---|
| job start → SBOM step start | **2 min 49 s** (15:02:30 → 15:05:19) — checkout, buildx, image build (1m42s), smoke-check, Trivy |
| the SBOM step itself | **20 s** (15:05:19 → 15:05:39) — install *and* scan *and* write |
| upload + post-steps + job complete | **8 s** |
| `timeout-minutes` | **30** (`ci.yml:478`) |

⟹ **~27 minutes remain when the SBOM step begins**, of which ~1 minute must be reserved for
enforcement, upload and the post-steps: **~26 minutes usable for retrying**.

🔴 **A job-level timeout CANCELS the job.** Every `!cancelled()` step is skipped, so overrunning the
budget produces **no enforcement step, no failure message and no upload** — the outcome this plan
exists to prevent, reached by a different route. *(Third review pass, major.)* The job timeout
therefore cannot be the retry controller; each attempt needs its own bound (T1).

### 4. Nothing is mechanically blocked — and it cuts both ways

`GET /repos/hydrosolutions/SAPPHIRE_flow/branches/main/protection` → **404 "Branch not protected"**;
`/rulesets` is empty. **No required status checks.** A red `build-image-and-scan` does not prevent a
merge; the owner merges by hand.

⚠️ **So "a GitHub outage blocks a PR" is false and this plan does not claim it.** The real cost is
smaller and still worth fixing: a red check that means nothing erodes the signal the owner merges
on, and the rule that the suite must pass before merge becomes a judgement about which red is real.

### 5. The reasoning this plan must not undo

`ci.yml:616-621`, from Plan 180, on the SARIF upload:

> **NOT wrapped in a blanket continue-on-error**: a broken upload … must fail the job loudly — the
> same silent-failure class this plan exists to close, moved one level down.

⛔ **"Just add `continue-on-error`" is ruled out before this plan starts**, and the incident's second
root cause was an artifact that **nothing read** (`ci.yml:617-618`).

🔑 **The reconciled design never triggers that rule.** A scoped `continue-on-error` on a retry
*attempt*, adjudicated by a mandatory enforcement step, is not forgiveness — the decision still
happens, once, explicitly, and the answer is still "fail".

⚠️ **But "nothing is forgiven, so nothing can go silent" is too strong, and the plan no longer
claims it.** *(Third review pass, minor.)* Three paths still end with no SBOM and no message: the
**condition trap** in T1 (fixed there, and it is why that fix is not cosmetic), a **cancelled run**
or a **job-timeout cancellation** (§3), and a **failed image build**, where the SBOM step is
correctly skipped. What this design guarantees is narrower and worth stating exactly: **no run
completes successfully without an SBOM.** Gaps from cancellation and upstream failure remain, and
they are visible as red or cancelled runs rather than as green ones.

### 6. What the standards say — and why they now need no change

`security.md:783`/`:785` make "an SBOM artifact **on every CI run**" the v0 baseline, and
`cicd.md:518` lists SBOM generation inside the CI **gate** tier.

✅ **This plan keeps both requirements in force** and so needs no amendment. The earlier draft
proposed forgiving PR runs, which would have contradicted both; that task is gone. The only
documentation touch left is a one-line update to `cicd.md:732` describing the retry.

⚠️ **Preserving a requirement is not the same as fulfilling it.** *(Third review pass, minor.)*
Failing a run does not make "an artifact on every run" literally true — a cancelled run, a job
timeout or a failed image build still leaves a gap (§5). This plan keeps the obligation loud rather
than discharging it, and **T1's exit gate names who is expected to act** (below) so that "re-run
it" has an owner rather than being a mechanism nobody is responsible for invoking.

## The reconciliation

Two independent passes, one author. Where they landed:

| decision | author's original | independent recommendation | **reconciled** |
|---|---|---|---|
| **scope** — classifier + exception policy + notifier + standards amendment, or just a retry? | the large version | "**the plan omits the strongest low-cost alternative**: bounded retry, clearer failure reporting, fatal on both events. One run in 100, no mechanical merge block … weak evidence for introducing a classifier, exception policy and compensating notification system before October" | 🔑 **the small version.** Adopted |
| **D1** — `anchore/sbom-action` or the pinned syft CLI? | keep the action | **the CLI** — "exposes no documented outputs" for the install/execute stages, so stage evidence would be scraped from logs | 🔑 **neither — a third option the reviewer then supplied.** See below |
| **D2** — where does a missing SBOM show up? | a scheduled artifact check | **an assigned issue**: "the plan recommends a **detector** as though it were a **destination**" | ⛔ **retired.** Nothing is forgiven, so there is nothing to compensate for |
| **D3** — fail `main`? | yes | yes, **and someone must own recovering the record** | ⛔ **retired.** Fatal on *both* events; recovery is "re-run the job", which is what happened today |

**D1 — I got this wrong twice, and the third pass supplied the answer.** My reconciliation argued
the reviewer's case for the CLI was "entirely contingent on the classifier", so deleting the
classifier restored the action. 🔴 **That was motivated reasoning and the reviewer said so.**
Deleting the classifier did *not* delete the need for stage evidence: **T1 still prescribes a
message** that names the download path as the cause, and T1's own "syft ran but wrote no file" case
means that message can be **false**. A single opaque step cannot tell an operator which happened.

🔑 **The resolution is neither of the two options the plan offered.** `anchore/sbom-action` ships a
**`download-syft` sub-action at the same SHA we already pin**, whose declared output is `cmd`, "a
reference to the Syft command" — verified at
`anchore/sbom-action/download-syft@3ad7283…/action.yml`. So:

| step | stage it establishes |
|---|---|
| `uses: anchore/sbom-action/download-syft@3ad7283…` | **install** — this step's outcome *is* the install verdict, and it is the step the retry wraps |
| `run: ${{ steps.dl.outputs.cmd }} …` | **execution** — a separate outcome, and a separate message |

Stage separation, the same pinned SHA, **no new supply-chain obligation and no second pin to
maintain** — which was the whole of my argument for keeping the action. It also makes the retry
sharper: we retry the *download*, which is the thing that failed, not the scan.

**What survives from the retired decisions**, because the insight outlives the task:

- 🔑 **"A detector is not a destination."** Verification answers *whether*; an assignee answers *who
  acts*. Recorded here for the next time a compensating control is proposed — see the watch items.
- 🔑 **A red run is not continuity unless someone owns recovery.** Under this design recovery is a
  re-run, and §2 shows that works — but the principle is why the larger design needed an owner and
  this one does not.

## Tasks

### T1 — retry the install, then fail honestly

**Outcome.** A fetch failure that clears within the job's headroom never reaches the job's
conclusion. One that does not still fails the job — with a message saying what happened.

🔴 **The obvious shape does not work, and review proved it before implementation.** A step gated on
`if: steps.sbom-generate.outcome == 'failure'` **never runs**: a step `if` without a status function
carries an implicit `success()`, and the job is already failed. Adding `!cancelled()` lets it run
but does **not** erase the first failure.

**The working shape:**

🔴 **And a second condition trap, which the third pass caught and which is worse.** The existing
step carries `if: ${{ !cancelled() && steps.build-image.outcome == 'success' }}` (`ci.yml:642`) —
deliberately, so the SBOM is still produced when **Trivy's gate has already failed the job**. A
retry gated only on `steps.sbom-1.outcome == 'failure'` silently drops both guards. After a real
CVE finding the job is already red, so **every retry and the enforcement step would skip**, and an
SBOM failure would vanish behind an unrelated red check with no retry and no message.

⟹ **Every attempt, every wait, the enforcement step and the upload must carry
`!cancelled() && steps.build-image.outcome == 'success'`** in addition to their own gate.

**The working shape** (with D1's `download-syft` split):

| | |
|---|---|
| install attempts | `uses: …/download-syft@<pinned>`, each `continue-on-error: true` so `conclusion` is `success` and the job continues while `outcome` records `failure`; attempt *n+1* gated on `!cancelled() && steps.build-image.outcome == 'success' && steps.dl-<n>.outcome == 'failure'` |
| per-attempt bound | 🔴 **`timeout-minutes` on each attempt.** Without it one stalled download consumes the whole budget and the job is **cancelled**, skipping enforcement, message and upload (§3). A healthy install+scan takes **20 s**, so a 3-minute per-attempt bound is ~9× headroom |
| waits | between attempts, from D1's schedule. Free on healthy runs; free in runner minutes |
| execute | `run: ${{ steps.dl-<n>.outputs.cmd }} …` — a **separate** step, so "ran and failed" is a distinct outcome from "never installed" |
| **enforcement** | a normal step — **no** `continue-on-error`, but **with** the two guards above — that keys on the **artifact**: if there is no valid `sbom.cdx.json`, **fail the job** and emit the message. This is what preserves §5 |
| upload | the `if:` must select **whichever attempt produced the file**. Today's condition requires the *first* to have succeeded, so a successful retry would skip the upload |
| reserve | the schedule must leave ~1 minute for enforcement + upload + post-steps inside the ~26 usable minutes (§3) |

**The failure message is a deliverable, not a nicety**, and ⛔ **it must not name a cause it has not
established.** *(Third review pass, major: the previous wording prescribed "could not obtain its
tool" unconditionally, which is false in this task's own "ran but wrote no file" case.)* It reports
whichever of the two stages actually failed — the install attempts' outcomes and the execute step's
outcome are both available, by D1's split — and says plainly that a download-path failure is **not**
a finding about the image and that a re-run is the first response. If neither stage failed and the
file is still absent or invalid, it says *that*, rather than guessing.

⚠️ **This is the only thing standing between a red check and a wrong conclusion**, now that the
classifier and the notifier are gone. It is the plan's main deliverable, not a trimming.

**In.** `.github/workflows/ci.yml` (the SBOM steps only); `docs/standards/cicd.md:732` (one line).

**Out.** ⛔ No change to `trivy-scan`, `trivy-gate-table`, `trivy-sarif`, either SARIF upload, the
image build or the scripts smoke-check. ⛔ `continue-on-error` appears **only** on retry attempts an
enforcement step adjudicates. ⛔ No event-dependent behaviour — PR and `main` are treated alike.
⛔ No change to `security.md` or `cicd.md:518`; this plan relaxes nothing.

**Verification.**

- ⚠️ **A bogus pinned version fails deterministically on every attempt and proves nothing.** Inject
  an **attempt-specific** fault — first attempt at an unreachable mirror, later ones at the real
  one — and show all attempts in the log, the job concluding success, and the uploaded SBOM coming
  from the attempt that worked.
- Force a **persistent** failure and show the job **fails**, with the message present in both the
  log and the job summary.
- Force "syft ran but wrote no file" and show the job **fails** — the enforcement step keys on the
  artifact, not on the attempts' outcomes.
- 🔴 **Force a Trivy gate failure and an SBOM failure in the same run**, and show the retries and
  the enforcement step **still run**, the message still appears, and a recovered SBOM still uploads
  — while the job stays red for the CVE. This is the condition trap above, and nothing else
  catches it.
- Force a **stalled** attempt and show the per-attempt `timeout-minutes` fires, the remaining
  attempts and the enforcement step still run, and the job is **not** cancelled.
- ⚠️ **Effectiveness in production is unmeasured until observed**, and §2 says why it cannot be
  computed: the outage's recovery point was never sampled.

### T2 — survey the same exposure elsewhere, and fix nothing blind

**Outcome.** A list of every CI step whose failure would red a PR for a reason unrelated to the PR,
each classified gate or infrastructure. **A survey, not a refactor.**

**Starting evidence.** `Install system deps for cfgrib / rioxarray / exactextract` — 2 of 15
failures (§1), same class, different mechanism, more frequent than syft.

**Out.** ⛔ No step changes. Each finding becomes its own task or plan, reviewed against its own
step's meaning rather than waved through on this plan's argument.

## Owner decision

### D1 — how many attempts, and how long between them?

⚠️ **Read the D1 discussion in § The reconciliation first** — *what* to retry is settled (the
pinned `download-syft` sub-action). This is only *how often*.

**The budget, from §3:** ~26 minutes usable, per-attempt bound 3 minutes, ~1 minute reserved.
**Offsets below are ABSOLUTE, measured from the first attempt's start** — not successive waits.
*(Third review pass, major: the previous table mixed the two notations, so (b) read as either 12 or
17 minutes of coverage and (c), read as successive waits, started its last attempt at +39 — past
the job timeout.)*

| option | attempt starts (absolute) | last attempt ends by | coverage | reserve left |
|---|---|---|---|---|
| **(a)** 2 attempts | 0, +2 | +5 | ~2 min | ~21 min |
| **(b)** 3 attempts | 0, +4, +10 | +13 | ~10 min | ~13 min |
| **(c)** 4 attempts | 0, +5, +12, +20 | +23 | ~20 min | ~3 min |

🔴 **What the evidence does and does not say.** §2 bounds this outage's recovery to **+18.6 to
+26.6 minutes** from the first attempt — an 8-minute window we never sampled.

- **(b) would not have covered it.** Its last attempt ends at +13, and +18.6 was still failing.
- **(c) is genuinely undetermined.** Its last attempt runs at +20; recovery was after +18.6 and
  before +26.6. It succeeds if recovery fell in that 1.4-minute sliver, and fails otherwise.
- ⛔ **No option is shown to cover it**, and ⛔ **"covers the common case" has no measured support** —
  this is one sample of one outage.

Recommendation: **(b)**, as a **policy choice, not an evidence-backed one** — it absorbs a short
blip at a wall-clock cost a developer will tolerate, and leaves real reserve. (c) buys ~10 more
minutes of coverage for ~10 more minutes of waiting on a broken run and cuts the reserve to ~3
minutes; that is defensible if the owner weights coverage over feedback latency, and the honest
statement is that this single sample does not decide between them.

⚠️ **The alternative nobody has costed: raise `timeout-minutes`.** It is the only way to cover an
outage of §2's length, and its price is slower feedback on a genuinely broken build. Not
recommended, but it should be rejected deliberately rather than by omission.

⛔ **Do not copy the LINDAS 5 minutes as if it were derived.** Its number came from a *measured*
upstream publish cycle; nothing analogous exists here.

## Watch items, not tasks

- ⛔ **Nothing here may weaken a security gate.** Trivy's scan, gate table, SARIF derivation and
  code-scanning upload are out of scope in every task.
- 🔑 **If a future plan proposes forgiving a failure, it needs a destination, not a detector** —
  verification answers *whether*, an assignee answers *who acts*. This plan avoids the question by
  forgiving nothing; the next one may not be able to.
- 🪤 **Two true numbers that pull opposite ways** (§1): rate 1/100, class 3/15. Quote both or
  neither, never as "prevents 20% of failures".
- 🪤 **`gh api ...runs?status=completed` is not a stable sample** (§1). Use `gh run list` and report
  the window.
- **`main` has no branch protection** (§4). If required checks are added, the cost of a spurious red
  changes and this plan should be re-read.
- **The repo is public, so runner minutes are free** (§3). If it ever goes private, D1's arithmetic
  acquires a cost term it does not have today.
- **No job depends on `build-image-and-scan`** — `cicd.md:733` records that Plan 064's `e2e` job was
  never built.

## Exit gates

- **D1** is closed, with the chosen schedule recorded **as absolute offsets** and the per-attempt
  `timeout-minutes` stated, and with the raise-the-job-timeout alternative explicitly rejected.
- T1's retry is proven by an **attempt-specific** injected fault — not a bogus version, not a green
  run.
- T1's failure cases each have a recorded job conclusion: transient-then-recovered, persistent,
  ran-but-no-file, **stalled attempt** (per-attempt timeout fires, job not cancelled), and
  🔴 **SBOM failure underneath an already-failed Trivy gate** (retries and enforcement still run).
- The failure message is shown to report the **stage that actually failed**, on at least the
  install-failed and the ran-but-no-file cases — never a fixed cause.
- **A named person owns re-running a job whose SBOM is missing** (§6). One line in `cicd.md`; not a
  notifier, not a new workflow.
- The failure message is shown present in **both** the job log and the job summary.
- `security.md` and `cicd.md:518` are **unchanged**, and that is verified rather than assumed — if
  either needed changing, the plan has silently grown back into the version this one replaced.
- Plan 180's reasoning at `ci.yml:616-621` is quoted in the PR description with an explicit
  statement of why scoped-retry-plus-enforcement does not undo it (§5).

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"], "decision": "D1",
      "note": "retry the pinned download-syft sub-action, execute syft separately, then enforce on the ARTIFACT; guards !cancelled() + build-image success on every step, per-attempt timeout-minutes; guarantees only that no run completes SUCCESSFULLY without an SBOM" },
    { "id": "P2", "tasks": ["T2"], "parallel_with": ["P1"],
      "note": "survey only; produces tasks, changes nothing" }
  ]
}
```

## Changelog

**2026-09-21 — created**, the day the failure occurred.

**2026-09-21 — four line anchors corrected.** `cicd.md` was cited at 461/577/689/690 — numbering
from a checkout that had not pulled the Plan 307 merge. ⭐ `git fetch` updates refs, not the working
tree. Every anchor is now verified by printing the line it names.

**2026-09-21 — T1's premise falsified within the hour.** The plan opened claiming a retry "alone
removes the failure mode" on one observation; a re-run 16 minutes later hit the identical 504, and a
third 5 minutes after that. ⭐ One observation did not license a claim about the failure's
*duration*.

**2026-09-21 — rewritten after independent review** (NEEDS CHANGES: 1 blocker, 6 major, 2 minor).
The blocker: the recommended retry **could not work** — `if: outcome == 'failure'` carries an
implicit `success()` and never runs, and T1's own Out forbade the scoped `continue-on-error` its
design required. Four further findings were cases of asserting where measuring was possible, and
**all four came out against the plan**: `main` has no branch protection; the census summed to 16
against 15; `cicd.md:518` *does* put SBOM generation in the gate tier; `cicd.md:620`'s `:local` tag
is the local-equivalent column, not a contradiction.

**2026-09-21 — reconciled, and descoped by roughly two thirds.** A recommendation pass disagreed on
two of three decisions and found a load-bearing error: the claim that a retry's wait is "paid on
every run" — a conditional attempt runs only after a failure. Measuring the consequence
(`timeout-minutes: 30` vs a ~3-minute healthy job ⟹ ~27 minutes of free headroom) and then measuring
one more thing the plan had never checked (**the repo is public, so runner minutes are free**) made
the cheap option clearly better than the plan had argued.

**Removed:** the failure classifier, the PR/main exception policy, the compensating notifier, the
standards amendment, and decisions D2 and D3 with them. **Kept:** a retry with working mechanics, a
mandatory enforcement step, an honest failure message, and the survey. **D1 reconciles to the
author's answer only because the reviewer's argument for the CLI was contingent on the classifier
that is now gone** — stated explicitly so that reopening the scope reopens D1.

⭐ **The reviewer's most valuable finding was not on the list of findings.** It was that the plan was
too big for its evidence — 1 failure in 100 runs, no mechanical merge block, a 3–4 person team and
an October deadline. Every individual finding could have been folded while leaving that wrong.

**2026-09-21 — third independent pass on the reconciliation: NEEDS CHANGES (4 major, 1 minor).**
Reviewing the descope rather than the version it replaced, and it found that the reconciliation had
introduced faults of its own:

| finding | what it was |
|---|---|
| **major** | a retry gated only on `steps.sbom-<n>.outcome == 'failure'` **drops the existing `!cancelled() && build-image == 'success'` guards** (`ci.yml:642`), so an SBOM failure underneath an already-red Trivy gate would skip every retry *and* the enforcement step — vanishing behind an unrelated red check |
| **major** | 🔴 **D1's reconciliation was motivated reasoning.** Deleting the classifier did not delete the need for stage evidence: T1 still prescribes a message naming the download path, which its own "ran but wrote no file" case makes false. The reviewer then supplied a third option neither of us had: the **`download-syft` sub-action at the SHA we already pin**, which outputs `cmd` — stage separation with no new pin |
| **major** | no **per-attempt** timeout, so one stalled download consumes the budget and the **job-level timeout cancels**, skipping the message and the upload entirely — the failure this plan exists to prevent, by another route |
| **major** | D1's schedule table **mixed absolute offsets with successive waits**: (b) read as either 12 or 17 minutes, and (c) as successive waits started its last attempt at +39, past the timeout. And the four observations **bound** the outage rather than describing it — recovery fell in an 8-minute window that was never sampled |
| minor | "nothing is forgiven, so nothing can go silent" is too strong — cancellation, job timeout and a failed image build all still leave gaps; and "re-run it" names a mechanism, not an owner |

⭐ **Two of the four majors were defects in the descope itself, not survivals from the larger plan.**
Making a plan smaller is a change like any other, and it earns its own review rather than inheriting
the previous one's approval. ⭐ And the D1 finding is the sharper lesson: I reconciled *toward my own
original answer* and built a contingency argument to justify it. The reviewer, whose position I was
characterising, said the characterisation was convenient — and was right.
