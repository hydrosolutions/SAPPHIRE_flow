---
status: DRAFT
created: 2026-09-21
plan: 310
title: The SBOM retry schedule is safe only while the image build stays fast — check the remaining job time before each wait
scope: Add a remaining-job-time admission check before each SBOM download retry wait in `build-image-and-scan`, so a slower image build cannot push the last attempt past the job timeout. Explicitly NOT changing the retry count or the wait lengths (Plan 309 D1 closed those as (b)), NOT touching any security gate, NOT relaxing the SBOM requirement, NOT the execute or enforcement steps except where they read the admission outcome.
depends_on: [309]
blocks: []
open_decisions: [D1]
source: 2026-09-21 — carried openly from Plan 309's exit gates, which recorded this as NOT IMPLEMENTED. The timings below were measured on run 106387452121 and are the same ones Plan 309 §3 used.
---

# Plan 310 — the retry schedule needs an admission check, not just a schedule

## Status

**DRAFT, filed at the owner's request 2026-09-21** immediately after Plan 309 T1 merged (PR #287),
so the gap does not survive only as a line in a completed plan's exit gates. **Not reviewed.**

⚖️ **Plan number 310 is claimed, not granted** — unused in `docs/plans/` and `docs/plans/archive/`,
and nothing in `docs/` refers to a "Plan 310".

## Why this exists

Plan 309 shipped three syft download attempts with 4- and 6-minute waits (D1, option (b)), each
attempt bounded by `timeout-minutes: 3`. Its own exit gates record the gap:

> ⚠️ **NOT IMPLEMENTED.** The plan called for checking remaining job time before each wait; the
> shipped version uses the fixed (b) schedule. … **Carried as a known gap, not silently dropped.**

**The schedule is safe because of a measurement, not because of a guarantee.** From run
`106387452121`:

| | |
|---|---|
| job start → SBOM step start | **2 min 49 s** (of which the image build is **1 min 42 s**) |
| `timeout-minutes` on the job | **30** |
| ⟹ budget when the chain begins | **~27 min**, ~26 usable after reserve |
| worst case for (b), every attempt stalling to its 3-min bound | **~19 min** |

19 against 26 is comfortable — **at 2m49s of prelude**. The margin is ~7 minutes of *prelude*, not
of retrying.

## The failure, stated precisely

🔴 **A job-level timeout CANCELS.** Every `!cancelled()` step is skipped, so overrunning produces
**no enforcement step, no failure message and no upload** — a bare cancellation. That is the exact
outcome Plan 309 exists to prevent, reached by a different route.

So a slower prelude does not degrade gracefully. It converts the plan's central deliverable — a red
check that *explains itself* — into a red check that explains nothing.

⚠️ **What would have to change for this to fire:** the pre-SBOM steps growing from ~2m49s to
**≳9 minutes** while a syft outage is also in progress and every attempt stalls to its bound. The
image build is the dominant term and is cache-sensitive; a cold cache, a base-image change or a new
heavy dependency are all ordinary events. ⛔ **This has not been observed** — it is a bound
computed from one measurement, exactly as Plan 309's schedule was.

## Tasks

### T1 — admit each wait against the remaining time

**Outcome.** Before each retry wait, the workflow computes the time left in the job budget and
skips the remaining attempts when a further wait plus a bounded attempt plus the reserve would not
fit. The enforcement step then reports **exhaustion by budget** distinctly from exhaustion by
attempts, because they mean different things to an operator.

**In.** `.github/workflows/ci.yml`, the SBOM chain's wait steps and the enforcement step's message.

**Out.** ⛔ No change to the attempt count or wait lengths — Plan 309 D1 closed those. ⛔ No change
to any Trivy step, the image build, or the SBOM requirement. ⛔ No change to the `!cancelled() &&
steps.build-image.outcome == 'success'` guards on any step; **every new step carries them too**, or
it will skip underneath a red CVE gate (Plan 309's pass-3 finding, and the reason
`tests/unit/tooling/test_trivy_gate_observability.py` exists).

**Verification.**
- A forced run with an artificially slowed prelude shows the later attempts **skipped by admission**
  and the enforcement step still running with its message. ⚠️ **Not a run that merely passes** —
  the skip must be observed.
- The existing contract tests extend to the new steps, and the new behaviour is **mutation-tested**:
  removing the admission check must fail a test. *(Plan 309's mutation run is the model — including
  its lesson that a mutant which never applied looks exactly like one that survived, so each patch
  asserts its anchor matched.)*

## Owner decision

### D1 — where does the deadline come from?

| option | how | cost |
|---|---|---|
| **(a)** compute from a timestamp captured at job start | a first step records `date +%s`; each wait compares against it and the known `timeout-minutes` | the 30 is then written in two places and can drift from the job's own setting |
| **(b)** raise `timeout-minutes` instead and keep the fixed schedule | one-line change | ⛔ does not fix it — it moves the cliff, and trades feedback latency on a genuinely broken build |
| **(c)** drop the third attempt when the prelude was slow | simplest conditional | coarse; discards coverage on exactly the runs that are already unlucky |

Recommendation: **(a)**, with the duplicated 30 read from a single workflow-level `env:` that the
job's `timeout-minutes` also uses, so there is one source of truth rather than two constants.

## Watch items

- 🪤 **Every new step needs the two guards.** A step added without `!cancelled() &&
  steps.build-image.outcome == 'success'` silently skips under a red CVE gate.
- 🪤 **A job timeout cancels; a step timeout does not.** The whole point is to never reach the
  former.
- **This is a bound from one measurement, not an observed failure.** Say so; do not let it drift
  into "CI times out" in retelling.

## Exit gates

- D1 closed.
- The admission skip is **observed** on a forced slow-prelude run, not inferred.
- The enforcement message distinguishes budget exhaustion from attempt exhaustion.
- `tests/unit/tooling/test_trivy_gate_observability.py` covers the new steps, and at least one
  mutant that disables the admission check is **killed** — with the patch asserting its anchor
  matched.
- `security.md` and `cicd.md:518` remain unchanged; this plan relaxes nothing.

```json
{
  "phases": [
    { "id": "P1", "tasks": ["T1"], "decision": "D1",
      "note": "admission check before each wait; every new step carries the two guards" }
  ]
}
```

## Changelog

**2026-09-21 — created** at the owner's request, from Plan 309's carried exit gate. Not reviewed.
