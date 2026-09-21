---
status: READY
created: 2026-09-21
revised: 2026-09-21
plan: 309
title: A transient tool download reds a PR the security gate passed — retry it, and say so when it still fails
scope: Retry the SBOM step's tool install inside `build-image-and-scan`, and when the retries are exhausted, fail with a message that says what actually happened. The SBOM stays mandatory on every run, so no control is relaxed and no standard changes. Plus a read-only survey of the same exposure elsewhere. Explicitly NOT touching any security gate (Trivy's scan, gate table, SARIF derivation, code-scanning upload), NOT a blanket `continue-on-error`, NOT changing what an SBOM contains, NOT the image build, NOT the model/forecast pipeline.
depends_on: []
blocks: []
open_decisions: []
source: 2026-09-21 — the syft step failed three times on PR #286 (run 35613224865, jobs 106377308103 / 106382760290 / 106384504421) between 14:38 and 14:57 UTC and recovered on the fourth (106387452121, 15:05). The run census in §1 is reproducible by the method stated there; every line anchor was verified by printing the line it names, against `main` at `672c8df5`.
---

# Plan 309 — CI step failures should mean what they say

## Status

**READY — set by the orchestrator 2026-09-21, after four independent passes and the owner's
decision on D1.** ✅ **D1 is CLOSED: option (b)** — three download attempts, with **4-minute and
6-minute waits after a failure**. No decision is open; T1 and T2 are both implementable.

⚠️ **The fourth pass's findings are folded but that fold is itself unreviewed** — passes 3 and 4
each found the previous fold introduced defects. The owner's judgement, taken deliberately, is that
the remaining uncertainty is in **workflow mechanics a real CI run settles faster than a fifth
reader**. T1's verification is therefore the gate, not more prose review.

| pass | outcome | what it changed |
|---|---|---|
| 1 — review | NEEDS CHANGES (1 blocker, 6 major, 2 minor) | the recommended retry **could not work**; four claims asserted where measurement was possible, **all four wrong** |
| 2 — recommendations | disagreed on **2 of 3** decisions | found the load-bearing error (a retry's wait is not paid on healthy runs), and argued the plan was **too big for its evidence** |
| 3 — review of the reconciliation | NEEDS CHANGES (4 major, 1 minor) | **two of the four majors were defects in the descope itself**, including a retry that would skip entirely underneath a red Trivy gate; supplied the D1 answer neither party had |
| 4 — review of that fold | NEEDS CHANGES (4 major, 2 minor) | **all four majors created by the fold**: the `download-syft` split never propagated to the artifact contract, the new execute step was left unbounded, a schedule option was physically impossible, and two counterfactuals were written directly beneath the disclaimer saying they could not be |

⚖️ **The plan is about a third of its original size.** Pass 2's strongest point was not any single
finding — it was that the plan was too big for its evidence. Reconciling on the measured facts
removed three of five tasks, two of four decisions, and the standards amendment those required.
What is left is a retry and an honest error message.

⭐ **Making a plan smaller is a change like any other**, and pass 3 proved it earns its own review
rather than inheriting the previous approval. ⭐ **Pass 4 then proved the same of the fold.** The
substance has been stable since the descope; what keeps failing is the editing — which is the
argument for reviewing this document once more before it is built, and for keeping it small.

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
| re-run 1 | `106382760290` | 14:52:57 | identical `504`, **14 min 01 s** later |
| re-run 2 | `106384504421` | 14:57:35 | `504` on `…/download/…/syft_1.51.1_checksums.txt` |
| re-run 3 | `106387452121` | 15:02→15:05 | **succeeded** |

Throughout, from a developer machine: the tag page, the installer's `releases/v1.51.1` and the
`linux_amd64.tar.gz` asset all returned **200**, and githubstatus.com was **green**. ⚠️ A local
probe would have argued this problem out of existence.

⚠️ **These four points bound the outage; they do not describe it.** The last observed failure was at
14:57:35 and the SBOM step ran 15:05:19–15:05:39, so recovery happened somewhere in a **~8-minute
window we never sampled**. Measured from the first attempt (14:38:56), that is **after +18.65 min
and no later than +26.72 min** — the upper bound is the *end* of the successful step, the last
instant by which the download must have worked, so it is the conservative one. *(Third review pass, major: an earlier revision treated this as a continuous
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
  re-run — §2 shows that works — but ⚠️ **a mechanism is not an owner**, and the exit gates require
  one. *(Fourth review pass, minor: this bullet previously said the design "does not" need an owner
  while the exit gate demanded one — a contradiction in the operational contract.)* What the small
  design avoids is a **notifier**, not the responsibility.

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

**The working shape** (with D1's `download-syft` split). 🔴 **The split changes what each step
produces, and that has to propagate** — *(fourth review pass, major: the previous table still said
"upload whichever attempt produced the file", which describes the old design. A **download** attempt
produces a command path, not an SBOM.)*

| | |
|---|---|
| download attempts | `uses: …/download-syft@<pinned>`, each `continue-on-error: true` so `conclusion` is `success` and the job continues while `outcome` records `failure`; attempt *n+1* gated on `!cancelled() && steps.build-image.outcome == 'success' && steps.dl-<n>.outcome == 'failure'` |
| per-attempt bound | 🔴 `timeout-minutes` on **each** attempt. Without it one stalled download consumes the budget and the job is **cancelled**, skipping enforcement, message and upload (§3). A rejected download fails in ~0–11 s and a healthy one plus the scan took 20 s, so a 3-minute bound is generous |
| admission | ⚠️ before each wait, check the **remaining job time** rather than trusting D1's offsets — §3's 2m49s pre-SBOM figure is one measurement, not a floor, and a slower image build shrinks the budget |
| waits | after a failed attempt, per D1. Free on healthy runs; free in runner minutes |
| **execute — once** | `run: ${{ steps.dl-<k>.outputs.cmd }} scan …`, where *k* is the **first attempt that succeeded**. Install success is not artifact readiness, so this is a separate step with its **own `timeout-minutes`** — 🔴 *(fourth pass, major: the previous shape bounded the downloads and left the new execute step unbounded, recreating the cancellation path it had just closed)* |
| **enforcement** | a normal step — **no** `continue-on-error`, but **with** the `!cancelled() && build-image success` guards — keyed on the **artifact**: if there is no valid `sbom.cdx.json`, **fail the job** and emit the message. This is what preserves §5 |
| upload | gated on the **execute** step plus artifact validation — not on any download attempt |
| reserve | the schedule must leave time for execute + enforcement + upload + post-steps inside the budget (§3) |

⛔ **Must be preserved when bypassing the parent action** — all verified in its source at the pinned
SHA (`src/github/SyftGithubAction.ts`):

| | |
|---|---|
| `SYFT_CHECK_FOR_APP_UPDATE: "false"` | the parent sets it (line 127); `download-syft` does **not**. Omitting it adds an **outbound update check** to the very step whose problem is outbound network calls |
| image reference | `sapphire-flow:ci-${{ github.sha }}` |
| format and output | `-o cyclonedx-json`, written to `sbom.cdx.json` |
| artifact name | `sbom-cyclonedx` |


**The failure message is a deliverable, not a nicety**, and ⛔ **it must not name a cause it has not
established.** *(Third review pass, major: the previous wording prescribed "could not obtain its
tool" unconditionally, which is false in this task's own "ran but wrote no file" case.)* It reports
whichever of the two stages actually failed — the install attempts' outcomes and the execute step's
outcome are both available, by D1's split — and says plainly that a download-path failure is **not**
a finding about the image and that a re-run is the first response. If neither stage failed and the
file is still absent or invalid, it says *that*, rather than guessing.

⚠️ **This is the only thing standing between a red check and a wrong conclusion**, now that the
classifier and the notifier are gone. It is the plan's main deliverable, not a trimming.

**In.** `.github/workflows/ci.yml` (the SBOM steps only); `docs/standards/cicd.md:732` (the step description) **and `:620`** (the workflow table row, which still names the parent action and its local equivalent — the split changes both). *(Fourth review pass, minor: limiting the edit to one line left the table describing a design that no longer ships.)*

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
- Force a **stalled download** and a **stalled execute** — two cases, not one — and show the
  per-step `timeout-minutes` fires in each, the enforcement step still runs, and the job is **not**
  cancelled.
- Force a run with **reduced starting headroom** (a slower image build) and show the admission check
  skips the later attempts rather than overrunning into cancellation.
- Show the produced SBOM is **byte-equivalent in shape** to the parent action's — same components,
  same format — and that `SYFT_CHECK_FOR_APP_UPDATE=false` is set on the execute step.
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

### D1 — ✅ CLOSED, owner 2026-09-21: option (b), three attempts with 4- and 6-minute waits

⚠️ **Read the D1 discussion in § The reconciliation first** — *what* to retry is settled (the
pinned `download-syft` sub-action). This is only *how often*.

**The budget, from §3:** ~26 minutes usable from the first download attempt. ⚠️ **That figure is
one measurement, not a floor** — it assumes the pre-SBOM steps take 2m49s, and a slower image build
shrinks it. *(Fourth review pass, major.)* So the schedule is expressed as **waits after a failed
attempt**, and T1 requires admission to check the **remaining** job time before each wait rather
than trusting these offsets.

**Notation.** Each row gives the wait inserted *after* a failed attempt. A rejected download fails
in ~0–11 s (§2), so in the realistic case the cumulative wait *is* the elapsed offset; a **stalled**
attempt instead consumes up to its 3-minute bound, which is why ⛔ **absolute offsets alone are not
a schedule** — an earlier table gave option (a) attempts at 0 and +2 with a 3-minute bound, which is
impossible, since steps run sequentially and attempt 1 may still be running at +2.

| option | waits after a failure | elapsed when the last attempt starts (fast-failure case) | worst case, all attempts stalling |
|---|---|---|---|
| **(a)** 2 attempts | 2 min | ~2 min | ~8 min |
| **(b)** 3 attempts | 4, 6 min | ~10 min | ~19 min |
| **(c)** 4 attempts | 5, 7, 8 min | ~20 min | ~32 min — ⛔ **exceeds the budget** |

🔴 **(c) does not fit once stalls are counted.** It fits only on the assumption that failures stay
fast, which is exactly the assumption a per-attempt bound exists to reject. Either drop it, or pair
it with the admission check so its later attempts are skipped when the remaining time is gone.


🔴 **What the evidence does and does not say — and the answer is "almost nothing".** §2 bounds
recovery to **after +18.65 and no later than +26.72 minutes**, an ~8-minute window nobody sampled.

⛔ **No schedule's historical outcome can be computed from that**, and an earlier revision of this
section computed two anyway, immediately beneath the disclaimer saying it could not. *(Fourth review
pass, major.)* Specifically:

- **"(b) would not have covered it" is unsupported.** A failure at +18.65 does not mean the service
  was down at +10; it may have worked between observations.
- **"(c) succeeds only if recovery precedes +20" is unsupported.** Its attempt *runs* until its
  bound, and a download makes requests throughout, so it can succeed on a recovery after its start.

**What the failure timings do establish** is the schedule's *notation*. The three failing SBOM steps
took **1 s, 11 s and 0 s** (`106377308103`, `106382760290`, `106384504421`) — a rejected download
fails fast. So in the realistic case the attempt offsets are the cumulative waits, and the
per-attempt bound below binds only on a **stall**.

⚖️ **CLOSED as (b), owner 2026-09-21.** Three attempts, waits of **4 and 6 minutes** after a
failed attempt, per-attempt `timeout-minutes: 3`. Taken as a **policy choice with no evidence behind
it** — three attempts across ~10 minutes absorbs a short blip at a wall-clock cost a developer will
tolerate, fits the budget even if every attempt stalls (~19 min worst case against ~26), and leaves
real reserve. ⛔ **Neither this outage nor any other tells us whether (b) or (c) would have helped**;
the plan does not pretend otherwise, and (c) was rejected because it overruns on stalls, not because
it was shown to be worse.


⚠️ **The alternative nobody has costed: raise `timeout-minutes`.** It buys coverage for a longer
outage at the price of slower feedback on a genuinely broken build. ⛔ It is *not* established as
"the only way to cover an outage of §2's length" — that claim rested on the continuity assumption
this section just retired. Reject it deliberately rather than by omission.


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

- ✅ **D1 closed (b)** — three attempts, waits 4 and 6 min, per-attempt `timeout-minutes: 3`. The
  raise-the-job-timeout alternative is rejected deliberately: it trades feedback latency on a
  genuinely broken build for coverage of an outage length we never measured.
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
    { "id": "P1", "tasks": ["T1"], "decision": "D1 CLOSED 2026-09-21 — (b): 3 attempts, waits 4+6 min, per-attempt timeout 3 min",
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

**2026-09-21 — fourth pass on the fold: NEEDS CHANGES (4 major, 2 minor), all four majors created by
the fold itself.**

| finding | what it was |
|---|---|
| **major** | the `download-syft` split **did not propagate through the artifact contract** — a *download* attempt yields a command path, not an SBOM, so "upload whichever attempt produced the file" described the deleted design. Also: bypassing the parent loses `SYFT_CHECK_FOR_APP_UPDATE=false` (set at `SyftGithubAction.ts:127`), adding an **outbound update check** to the step whose problem is outbound calls |
| **major** | the timeout fix bounded the downloads and **left the new execute step unbounded** — a stalled scan still reaches job cancellation and skips the message. And 2m49s is one measurement, not a floor: a slower image build shrinks the budget, so admission must check **remaining** time |
| **major** | D1's option (a) — attempts at 0 and +2 under a 3-minute bound — is **impossible**; steps are sequential. The table now gives **waits after a failure**, and on that basis **(c) overruns** when attempts stall |
| **major** | 🔴 **the continuity assumption survived directly beneath its own disclaimer.** The section said these observations bound rather than describe the outage, then computed two counterfactuals from them. Both are withdrawn; **no schedule's historical outcome is knowable** |
| minor | `14:38:56 → 14:52:57` is **14m01s**, not 16 minutes |
| minor | the doc scope missed `cicd.md:620`, and the reconciliation said the design needs no recovery owner while the exit gate required one |

**Confirmed sound by that pass:** the guards now reach T1's requirements, verification, exit gates
and phase graph; the narrower success guarantee is present; `cmd` is an executable path usable in
`run:`; both action paths pin the same syft version; the parent's own artifact upload is already
disabled. And explicitly: *"the reduced implementation scope is appropriate — these fixes do not
require restoring the classifier, notifier or exception policy."*

⭐ **Four passes, and the third and fourth each found that the previous FOLD introduced new defects.**
The plan's substance has been stable since the descope; what keeps failing is the editing. The
fourth pass's sharpest finding is the clearest case: a disclaimer was written saying the evidence
could not support counterfactuals, and two counterfactuals were written underneath it. **Stating a
limit is not the same as observing it.**

## Execution record — T1, 2026-09-21

Implemented on `feat/plan-309-sbom-retry`, **PR #287**, v0.1.943. Five forced-fault CI runs, each
injecting a specific fault into the real workflow and then reverted:

| run | fault injected | result |
|---|---|---|
| happy path | none | ✅ attempt 1 succeeded in 2 s, waits and attempts 2–3 **skipped**, enforcement passed with **27,087 components**, upload succeeded, job green in 3m06s |
| **A** `35620778764` | attempt 1 pinned to a bogus syft version; 2–3 real | ✅ attempt 1 404'd, the warning fired, **attempt 2 downloaded the real syft**, resolve fell through to its `cmd`, execute + enforce + upload passed, **job green** |
| **B** `35621394667` | all three attempts bogus | ✅ three failures, `installed=false`, execute **skipped**, enforcement **failed**, upload skipped, **job red**, annotation: *"syft could not be DOWNLOADED after 3 attempts… a fault on the download path, NOT a finding about the image"* |
| **C** `35621971390` | syft real, scan targets a nonexistent image | ✅ enforcement **failed** naming the **scan** stage: *"…downloaded and RAN, but the scan failed… Do not assume a re-run fixes it"* |
| **D** `35622426919` | scan succeeds but writes elsewhere | ✅ third message fired: *"…scan reported success, but sbom.cdx.json is missing… needs a human look rather than a re-run"*. 🔴 **And it found a defect — see below** |
| **E** `35623093354` | a step fails **before** the chain, simulating a red CVE gate | 🔑 **the decisive one.** Job **red** from the injection, yet downloads, resolve, execute, enforcement **and upload** all ran and succeeded — the `!cancelled() && build-image == 'success'` guards hold |

🔴 **Run D found a defect in this plan's own design.** The upload had been gated on the *execute*
step. syft can exit 0 having written nothing, so `sbom-generate.outcome == 'success'` while no file
exists — the upload then ran and failed on `if-no-files-found: error`, **adding a confusing second
error beside the real one**. That is precisely what `ci.yml:649-650` was written to avoid, and this
plan reintroduced it one step over. Fixed: the upload is gated on the **enforcement** step, keeping
`!cancelled()` so the SBOM still uploads when the CVE gate has failed the job (run E proves that
still works).

⭐ **A fifth prose review would not have found it.** Four passes examined this design; a forced fault
found the defect in one run. That is the argument for the owner's call to build rather than review
again — recorded because the next plan will face the same choice.

**Exit gates: 6 of 8 proven, 2 not attempted.**

| gate | status |
|---|---|
| D1 closed as (b), raise-the-timeout rejected deliberately | ✅ |
| retry proven by an **attempt-specific** fault, not a bogus version throughout | ✅ run A |
| transient-then-recovered / persistent / ran-but-no-file conclusions recorded | ✅ A / B / D |
| SBOM failure under an already-red gate: retries + enforcement still run | ✅ run E |
| message reports the **stage that actually failed**, never a fixed cause | ✅ B, C and D each produced a different, correct message |
| `security.md` and `cicd.md:518` unchanged | ✅ verified — the diff touches `cicd.md:620` and `:732` only |
| **stalled** download / **stalled** execute → per-step timeout fires, job not cancelled | ⚠️ **NOT ATTEMPTED.** The `timeout-minutes` are declared and the mechanism is standard, but no run forced a stall |
| **reduced starting headroom** → admission skips later attempts | ⚠️ **NOT IMPLEMENTED.** The plan called for checking remaining job time before each wait; the shipped version uses the fixed (b) schedule. Worst case is ~19 min against ~26, so it fits **provided the pre-SBOM steps stay near 2m49s**. A materially slower image build could push the last attempt past the job timeout — and a job timeout **cancels**, skipping the message. **Carried as a known gap, not silently dropped.** |

### The gate I missed, and what caught it

🔴 **`tests/unit/tooling/test_trivy_gate_observability.py` encodes this workflow's contract, and I
never looked for it.** Plan 207 built it to hold exactly the property run E proved — the SBOM steps
must not inherit the implicit `success()` that follows a gate failure — by *evaluating* the `if:`
expressions against representative scenarios rather than pattern-matching them. My restructure broke
**six** of its assertions. **CI caught it; I did not.**

⭐ **I swept the workflow and the standards and stopped there.** The repo's own rule is to sweep by
value *and by role*, and a test that encodes a contract is one of those roles. `grep -rl` for the
step ids I was renaming would have found it in seconds.

**What the tests now hold**, after being rewritten rather than patched around:

- the evaluator understands `steps.<id>.outputs.<name>` — Plan 309's chain gates the scan on it —
  and resolves an output of a step that never ran to the empty string, as real Actions does;
- `TestSbomSurvivesAGateFailure` walks the **whole seven-step chain in order**, each condition
  evaluated against the outcomes accumulated so far, so a *skipped* step's outcome feeds the next
  condition the way the runner does. Plan 207's property is preserved **and widened**: the retries
  and the enforcement step must survive a red job too, not just the first attempt;
- `TestSbomRetryChain` holds Plan 309's own properties, including the run-D defect.

**Mutation-tested — 6 mutants, 6 killed**, each by exactly one test with the other 33 still passing:

| mutant | killed by |
|---|---|
| drop the guards from a retry wait step | `test_gate_failed_the_retries_still_run_too` |
| remove `continue-on-error` from attempt 2 | `test_enforcement_is_the_only_step_that_can_fail_the_job` |
| add `continue-on-error` to the enforcement step | same |
| remove the scan's `timeout-minutes` | `test_every_attempt_and_the_scan_are_individually_bounded` |
| gate the upload on the scan again (the run-D defect) | `test_upload_is_gated_on_enforcement_not_on_the_scan` |
| re-enable the syft update check | `test_the_scan_keeps_the_parent_actions_update_check_disabled` |

⚠️ **The first attempt at this mutation run was worthless and looked fine.** Three of the six
"mutants" never applied — a shell-quoting slip left a literal `\$` in the search string — and the
suite passed, which reads identically to a surviving mutant. The patches are now applied by a script
that **asserts its anchor matched**. See `feedback_red_first_must_prove_the_fault`.
