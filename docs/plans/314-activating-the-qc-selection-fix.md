---
status: DRAFT
created: 2026-09-23
plan: 314
title: Activating the QC selection fix — the canary, the loss gate, and what can actually revert it
scope: The ACTIVATION of Plan 272's selection fix on a live deployment — enabling the `QC_UNCHECKED` write per station, the loss threshold that aborts the rollout, and the artefact that reverts a selection change which ships unconditionally. NOT the selection fix itself, NOT the `QC_UNCHECKED` status or its consumer policy, NOT the paired evaluation harness, NOT the zero-rule telemetry — all Plan 272.
depends_on: [272 phase-1]
blocks: []
related: [264, 269]
open_decisions: [E1, E2]
source: 2026-09-23 — split out of Plan 272 on the owner's instruction, after two independent gates found that 272's code half was executable end to end while its rollout half was not. Every claim below was measured against `main` at `8ac5fa48`.
---

# Plan 314 — activating the QC selection fix

⚠️ **The plan number 314 is PROVISIONAL until the owner grants it.** 302–305 were granted in
prose and never written, so the sequence is not a reliable allocator.

🔴 **E1 and E2 must close BEFORE Plan 272 lands, not after.** E1(b) and E1(c) would change 272's
own tasks, and E2 names the quantity 272's D7 policy compares — so `depends_on` is 272's
**measurement phase**, not the whole plan. *(An earlier draft depended on all of 272, which put
both decisions after the work they govern.)*

## Status

**DRAFT.** ⛔ Only the orchestrator sets READY.

🛑 **PREMISE SUSPENDED for this iteration — owner, 2026-09-23** (`docs/v1-scope.md` § QC posture).
This plan exists to make activation safe on a live system. The owner has since recorded that
**this is a test deployment, the data is not used operationally until QC is fine-tuned, and flags
may be deleted, re-established and changed freely in the meantime.** The risk the canary, the
automatic abort, the loss threshold and the revert artefact were designed to bound **does not
exist in that setting.**

⇒ **Do not build this plan as written.** What survives, and is worth keeping, is small:
- the record of WHY the flag cannot revert a selection change (§ What is measured);
- **E1(a)** — a scoped `UPDATE` plus the pre-tagged image reverts everything, at any point, for
  one runbook line: that is the whole revert story for a test deployment;
- the rollback anchor naming trap (`rollback-272`, not the shared name every upgrade reuses).

**This plan becomes relevant again when the data becomes operational.** Until then it is a
placeholder for that moment, not work to schedule.

⚠️ **High-risk work** (`docs/workflow.md`): it changes scientific behaviour on the live Swiss
deployment, so it needs a relevant independent review in ADDITION to the ordinary Claude/Codex
pair, on the exact state that will land.

## Why this plan exists, separately

Plan 272 diagnoses and fixes the selection defect: a configured QC rule is unreachable when the
inferred cadence matches nothing, and the data is then recorded as having PASSED quality control
with zero rules run. That fix is a code change with a paired evaluation harness, and two
independent review gates agreed it is buildable from 272 alone.

🔴 **Its ACTIVATION is not, and could not be made so by rewording.** Six review rounds converged
on the same place, because the rollout design rests on an artefact that does not exist:

> **The flag gates the `QC_UNCHECKED` WRITE only. The selection change ships unconditionally**
> — 272's T2 deletes the `< 2 rows ⟹ 1 h` fallback and its T6 explicitly rejects keeping the old
> path in `src`. So **disabling the flag cannot restore the old verdicts**, and the newly
> selected rules keep producing `QC_FAILED`/`QC_SUSPECT` after a "revert".

An automatic abort that reverts the flag therefore does not stop the thing it measures. That is
E1, and it is a design decision, not a wording fix — which is why it now has its own plan.

## What is measured

**1. The flag cannot revert selection.** `config/deployment.py:146` carries the Plan 235
two-release flag precedent. 272's C4 adopts it for the `QC_UNCHECKED` write. Nothing in 272
puts the selection arithmetic behind it, and 272's T6 records "a flag keeping the old path in
`src`" as a **rejected** alternative.

**2. The compatibility image, as currently defined, cannot serve as the revert artefact.**
272's T2b item 10 defines it as "an image that *understands* `QC_UNCHECKED` but never writes
it", and 272's phase note forbids shipping T2b without T2. ⇒ The only buildable image carrying
that understanding is the phase-2 release, **which contains the selection change**. Redeploying
it changes nothing the abort measures.

**3. D7's baseline is not persisted, so it cannot be queried.** The abort compares a
per-station, per-cycle `QC_FAILED`/`QC_SUSPECT` fraction. Measured: `_run_qc_task` returns
per-station counts that `flows/ingest_observations.py:756-758` sums into in-memory fleet
totals, and `store/observation_store.py:143-150` `update_qc` persists a verdict with **no QC
cycle identifier**. ⇒ No stored row carries a cycle key, and the per-cycle fraction exists
nowhere a query can reach.

**4. The host has no image registry.** `docs/standards/cicd.md:506` — images are local-only and
the Sunday prune protects only `^rollback($|-)` tags. Image revert here is a procedure, not a
button, and it needs both compose overlays plus the exported build tokens.

## Owner decisions

### E1 — 🔴 What artefact reverts a selection change that ships unconditionally?

This is the question 272 never answered, and the reason this plan exists.

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Rewrite the rows, then redeploy the pre-272 image.** One scoped statement — `UPDATE observations SET qc_status='qc_passed' WHERE qc_status='qc_unchecked'` — then redeploy the anchor tagged before the upgrade. | Reverts selection AND the status write, **at any point**. 🔑 The only thing that makes the old image raise is the stored VALUE: `qc_status` is plain `sa.Text` under a CHECK constraint that does not list `qc_unchecked` (`db/metadata.py:526-533`), and `QcStatus(row["qc_status"])` (`store/observation_store.py:318`) is what chokes. Rewriting the value restores legibility. **No artefact, no second build, one runbook line.** ⚠️ The rewritten rows keep T0's `-norules` sentinel in `qc_rule_version`, so they stay identifiable afterwards — the revert does not re-hide the defect. |
| **(b)** | **Build a genuine compatibility image**: understands `QC_UNCHECKED`, does NOT carry T2's selection change. | A real revert at any point, at the cost of maintaining a second build. ⚠️ *An earlier draft priced this as "requires 272's T2 and T2b to be separable, which the phase note forbids" — that was wrong: the phase note forbids shipping a WRITING release without T2, and this image never writes.* |
| **(c)** | **Put the selection change behind the flag after all** — the alternative 272's T6 rejected. | A true behaviour rollback, at the cost of the duplicate path in `src` that 272 exists to remove. ⛔ **Only available BEFORE 272 lands**, and no task here delivers it — choosing it re-opens 272's T2 and T6. |
| **(d)** | **Accept fleet-wide selection exposure, with no automatic selection rollback** — the paired harness as the pre-activation evidence. | ⛔ **The canary CANNOT bound this.** The canary scopes the status write only; selection reaches every station the moment the image deploys, so a small station set buys nothing here. *(An earlier draft recommended this on the premise that selection "is not revertible" — (a) shows it is.)* |

**Recommendation: (a).** It adds no artefact, changes no other plan, and — unlike the earlier
recommendation of (d) — it actually works after the first `QC_UNCHECKED` row, which is when a
revert is most likely to be wanted. ⚠️ **It requires the anchor to be tagged before the upgrade
and to survive**, which is T2's job. 272's paired harness remains the pre-activation evidence
either way; this is the escape hatch behind it.

### E2 — What does D7's abort actually compare, given the baseline is not stored?

Per § What is measured (3), the per-station per-cycle fraction is not queryable.

| | option | cost |
|---|---|---|
| **(a)** ⭐ | Compare the **stored per-station verdict fraction over a NAMED WINDOW** — e.g. rows whose `timestamp` falls in the last N hours — stated explicitly as a proxy for cycle-level loss. | Queryable today, no new mechanism. ⚠️ **The window is not optional**: `observations` carries no QC timestamp (`db/metadata.py:537-543`) and `update_qc` writes none, so an unwindowed reading is whole-history and can never cross the threshold. Coarser than per-cycle, and re-examination (272's T2b item 8) moves rows in and out while they stay in the checked window. |
| **(b)** | Emit per-station counts from the ingest loop, which already holds them. | Exact. A code change belonging to a task here. ⚠️ It cannot be compared against a 272 baseline, because 272's measurement runs before any code from this plan exists — choosing (b) means the baseline is measured here too. |
| **(c)** | Drop the automatic abort; make the canary a human gate on the paired harness. | Removes the control. ⛔ 272's D7 rejected "turn it on and watch" for a measured reason: a skipped forecast raises no alarm today. |

**Recommendation: (a)**, with the proxy named in the runbook. ⚠️ Whichever is chosen, T1's
baseline in Plan 272 must measure the SAME quantity the abort compares, or the comparison is
between two different populations.

## Tasks

### T1 — Settle the revert artefact, and write it down

**Outcome.** E1 is closed, and the activation checklist names the artefact that reverts a
selection change, when it is available, and what happens after it is not.

**In.** The decision, recorded here; the runbook entry in `docs/operations/`; and, if E1 closes
on (b), the change to Plan 272's phase graph that makes T2 and T2b separately shippable.

**Out.** ⛔ Any code change under `src/`. ⛔ Re-opening 272's T6 rejection unless E1 closes on (c).

**Pre-change.** N/A — a decision and its record.

**Verification.** The checklist states the artefact and its availability window; a reader can
answer "what do I do if the canary aborts after the first `QC_UNCHECKED` row" from the document.

### T2 — The flag, the canary and the abort

**Outcome.** The `QC_UNCHECKED` write can be enabled per station, reverted in seconds without a
rebuild, and aborts itself when the measured loss exceeds D7's threshold.

**In.**
- **Enabling Plan 272 T2b's write gate — this task does NOT build it.** 272 T2b In item 12
  ships it, defaulting `False`, with the write it gates.
- 🔑 **State the field's SHAPE, because a bool cannot do the canary.** The Plan 235 precedent
  (`config/deployment.py:146`) is `bool`, but this rollout enables 2–3 stations first, so the
  setting must carry a station set (or a bool PLUS a station list) — ⛔ *an earlier draft
  specified a bool and then required per-station enablement, which two implementers would build
  two ways, and the bool build would pass the test while being incapable of the canary.*
  ⛔ Neither shape gates the selection change — § What is measured (1).
- **Per-station scoping** for the canary: `stations.network` exists and the QC loop already
  iterates per `(station_id, parameter)`. Enable on 2–3 BAFU stations, watch one full forecast
  cycle (`0 */6 * * *`), then widen.
- **The enable path**: `config/overlays/mac-mini.toml`, a host bind mount into `prefect-worker`,
  `prefect-worker-ingest` and `api` (`docker-compose.macmini.yml:46, 52, 76`, `:ro`).
  ⚠️ Edit it **in place** — a new inode leaves the container reading the old content.
- **The abort**, per E2: the measured fraction, the comparison, and the revert action E1 settles.
- **Retained-row behaviour after the flag is disabled** — rows already written `QC_UNCHECKED`
  stay written; say so, and say what re-enables them (272's T2b item 8).
- 🔴 **The calculated-station re-check, which came here with C9** (272 T2b's *Accepted
  exception* makes it a precondition): if any calculated station exists in the deployment, its
  dependent stations join the blast-radius assessment **before the flag is enabled**. Measured
  inert today — `config.toml` declares no `[[stations]]` — so this is a re-check against the
  live deployment, not an assumption.
- **The rollback anchor, tagged BEFORE the upgrade**:
  `docker tag sapphire-flow:${OLD} sapphire-flow:rollback-272` — ⛔ *not the shared `rollback-backup` name, which `docs/standards/cicd.md:317` reuses for every upgrade, so any unrelated deploy during the widening would destroy this anchor; `rollback-272` still matches the `^rollback($|-)` protect pattern* — the host has no registry
  (`docs/standards/cicd.md:506`) and the Sunday prune protects only `^rollback($|-)` tags.

**Out.** ⛔ The paired harness (272's T6). ⛔ The selection fix, the status, the consumer policy
and the telemetry (all 272). ⛔ The `qc_rule_version` bump and the watchdog probe — those ship
with the code in 272, not with activation.

**Pre-change.** A RED test proving the flag gates the write: with it `False` a zero-rule group
stores no `QC_UNCHECKED` row, with it `True` the same group does. ⚠️ **This can only be written
once 272's T2b has landed** — before that there is no symbol to exercise, and a test failing on
a missing symbol proves nothing (272 § the same standard).

**Verification.**
- With the flag `False`, no `QC_UNCHECKED` row is written.
- Enabling for one station changes that station and no other.
- A simulated loss above the threshold aborts **and performs E1's revert action** — ⛔ *not
  merely reverting the flag, which § What is measured (1) proves does not stop the population
  the abort counts; an implementation that reverts only the flag passes a weaker wording and
  reproduces the exact defect this plan was split out to fix.* Under E1(a) the revert is a row
  rewrite plus an image redeploy, so the abort **halts ingest and pages an operator** rather
  than performing it in-flow; state which, and verify the halt.
- The rollback anchor exists before the upgrade begins.

### T3 — Activate, and record what it did

**Outcome.** The fix is enabled across the Swiss fleet, or it is not and the reason is written
down.

**In.** Running 272's T6 paired harness as an activation precondition — on a degraded pinned
range where the retained history holds one, and on 272's constructed repairable case where it
does not. Then the canary, then the widening. **Deleting 272's T6 harness and its frozen
snapshot when the rollout completes** is a checklist item: a frozen copy of a deleted code path
that outlives its rollout becomes a second implementation nobody remembers is there.

**Out.** ⛔ Changing any threshold value to make activation pass.

**Pre-change.** N/A — activation is an operational procedure. ⚠️ *The harness deletion below is
not: it removes a committed script and its entry in the Dockerfile's curated COPY list, so it
lands as an ordinary code change with the suite green.*

**Verification.** The per-station enablement sequence, the measured loss at each step against
D7's threshold, and the terminal state, all recorded in this plan — **and** the harness script
and its frozen snapshot are absent from `scripts/` and from the Dockerfile's COPY list, with the
suite passing.

## Explicitly out of scope

- **The selection fix, the `QC_UNCHECKED` status, its 13-consumer policy, the zero-rule
  telemetry and the paired harness** — Plan 272. This plan turns that work on; it does not
  change it.
- **The threshold VALUES for Nepali stations** — Plan 268 D14.
- **`rate_of_change`'s gap-blindness** — Plan 313.
- **A watchdog alarm for a station whose forecast is skipped for missing input** — recorded by
  272's D7 as a follow-on, still unowned.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"], "parallel": false },
    { "id": "phase-2", "tasks": ["T2"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T3"], "depends_on": ["phase-2"] }
  ]
}
```
