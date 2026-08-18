---
name: standing
description: Produce the project standing review — measure the repo, the plan corpus, CI and the live staging host, verify every claimed-vs-measured drift finding, then author a ranked verdict and republish the standing artifact. Use when the user asks for project status, "where are we", a prioritisation review, or before any planning round.
---

Produce a **standing review**: what is proven, what is thin, what is missing, and what
to do next. It exists because the plan index drifts from the repo, and the repo drifts
from the live deployment — and prioritising from a drifted index sends work to the wrong
place. Wave 2 stayed at zero for months that way.

## 1. Measure first, read second

```bash
uv run python tools/standing_snapshot.py --out /tmp/standing.json
```

Roughly 90 s. It reports repo state, the plan corpus by status, GitHub CI and open PRs,
live counters from the staging host, and a **DRIFT** section — claimed-vs-measured
discrepancies. Add `--tests` (~11 min) when the verdict will make a claim about suite
health; otherwise cite the last known run and say when it was.

If the host is unreachable, run the LAN control **before** concluding anything about it:

```bash
ping -c2 192.168.1.125   # a known-good LAN peer
```

A blind observer and a dead host produce identical evidence. Can't reach any peer → the
problem is this machine's Local Network permission, not the mini.

## 2. Verify every finding before it reaches the page

The drift checks are **heuristics that produce claims, not facts**:

- `shipped-but-unarchived` matches commit subjects — confirm the code is really on main.
- `stranded-plan` compares plan numbers — confirm the branch is genuinely unmerged work
  and not a superseded or deliberately abandoned line.
- `shipped-never-exercised` proves a row count is zero — decide whether that is a gap or
  simply not yet configured for this deployment.

Read the cited code, commit, or branch before folding a finding in. A finding is a claim.

## 3. Author the verdict — do not template it

The snapshot is generated; the judgement is written fresh each run. Rank by **lead time
and blocking power**, not by size or by how recently something was touched. Weigh it
against the locked wave order in `docs/plans/106-v1-critical-path-roadmap.md` — and say
so explicitly when attention has drifted away from that order, because that is the
failure this review exists to catch.

Structure that has worked:

1. **Vital signs** — live counters, stated as measured, with the date.
2. **Proven** — running in production, with the evidence. Green CI is not proof; a
   production row count is.
3. **Thin ice** — shipped but unexercised, or proven only at a scale that hides the risk.
4. **Gaps** — split *blocked on other people* from *buildable now, simply not built*.
   The second list is the one that should sting.
5. **Recommendation** — a short ordered list, each row carrying why-now and rough shape.

Two habits worth keeping: name the thing nobody is working on, and state plainly when a
capability exists in code but has never run for real. Both were the useful findings the
first time.

## 4. Publish

Republish to the **same artifact** so it reads as a living page rather than a pile of
one-offs:

**https://claude.ai/code/artifact/cea7ab7f-0bee-4468-8966-64344b82f46e**

Pass that as `url`, keep the title and favicon stable, and carry an explicit "as measured
on <date>" stamp. Then relay the three or four conclusions in chat — the artifact is the
record, the message is the handoff.

## Cadence

Weekly, and **before any planning round**. Also whenever someone asks where the project
stands. The mechanical half (duplicate plan numbers, shipped-but-unarchived, stranded
READY plans) is cheap enough to run any time you are about to trust the plan index.

## Not this

- Do not let the ranking become a template. If the verdict reads the same as last week
  while the snapshot changed, the ranking is not being re-authored.
- Do not report a drift finding you have not confirmed.
- Do not turn it into a metrics dashboard. The counters exist to support a judgement, not
  to replace one.
