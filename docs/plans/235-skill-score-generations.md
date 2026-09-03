---
status: DRAFT
created: 2026-09-03
plan: 235
title: A skill recompute must replace what it supersedes — generations, not collisions
scope: Give a logical recompute one stable identity, make every consumer read the newest generation, and bound retention. Split out of Plan 228 when an independent design check showed version-bumping alone was unsafe. MUST land before Plan 228's D3 recompute is executed.
depends_on: [228]
blocks: [226, 234]
source: 2026-09-03 — independent design check of Plan 228's proposed recompute fix
---

# Plan 235 — generations, not collisions

## Status

**DRAFT — not reviewed.** Created 2026-09-03 after an independent design check found the simpler
fix unsafe. Every finding below is from that check and cites live code.

## Why this exists

Plan 228 needs a recompute (its D3) to replace 114,987 corrupt skill scores. Two problems block it,
and the obvious fix does not solve them.

**Recomputes are silently discarded.** `eval_period_end` is doing duty as computation identity but
it is only `max(hindcast_step)` (`services/skill/service.py:656-659`). Recompute the same hindcasts
after corrected observations arrive and the new score carries the same key — dropped by
`ON CONFLICT DO NOTHING`. D3 would report success and store nothing.

**Bumping `computation_version` per run does not fix it**, which is why this plan exists rather than
a one-line change:

- It conflates two different things. `computation_version` means *algorithm/schema version* and is
  the authoritative `< 2` invalidity cutoff (`docs/decisions/plan-228-hindcast-skill-resampling.md:19-39`).
  Reusing it as a run counter destroys the ability to distinguish "new algorithm" from "same
  algorithm, corrected inputs".
- A naive `MAX(version)+1` inside each mapped task can collide or fragment one logical recompute
  across several versions.

## What must be true

### D1 — one logical recompute has ONE stable identity

Stable across retries of the same run, different when the inputs differ. Not `eval_period_end`, and
not a counter each task allocates for itself. It belongs in the natural key and drives
latest-generation selection.

### D2 — every consumer reads the newest generation

Six do not today, and each is a live wrong-answer path once generations coexist:

| Consumer | Behaviour |
|---|---|
| `store/skill_store.py:152-167` `fetch_scores_by_regime` | returns every version and freshness state |
| `store/skill_store.py:169-183` `fetch_skill_scores` | returns all versions |
| `api/routes/models.py:125-152` | exposes every historical score and diagram |
| `api/routes/models.py:201-233` skill chart | renders duplicate lead-time points |
| `api/routes/stations.py:361-390` | double-counts, averaging generations together |
| the promotion gate | compensates only partially |

### D3 — supersession is atomic, and covers diagrams

There is no "store the complete new generation, then supersede the previous one" operation.
`mark_stale` updates only overlapping score rows and **cannot mark diagrams at all**
(`store/skill_store.py:185-203`). A half-superseded generation is worse than none.

### D4 — retention is bounded

Each full recompute adds ~115,000 score rows plus diagrams and index entries. Twelve recomputes is
~1.5M rows; nothing prunes them, and `docs/handover/data-flows.md:699` states skill scores are
permanent. **Plans 226 and 234 each end in a full recompute**, so this is not hypothetical. Decide
a retention rule, or state explicitly that unbounded growth is accepted and why.

## 🔴 The trap this plan must close for Plan 226

`compute_skills_task` fetches the **entire 1970–2100 hindcast history** unless `hindcast_run_id` is
supplied (`flows/compute_skills.py:85-116`), then scores every phase cohort (`:133-171`).

**A Plan 226 recompute that omits the run id would publish both the old unanchored and the new
anchored cohorts inside the same newest generation.** Generation identity alone does not prevent
this — the run id must be required on the hindcast→skill handoff. Plan 234 records the same defect
from the artifact-attribution angle (`docs/plans/234-...:53-59`); it is one defect with two
symptoms and must be fixed once, here.

## Sequencing — this is the constraint that binds the family

```
228 (merged, deployed)
   └─ 235  ← MUST land before D3's recompute executes
        ├─ 226  (re-anchors valid_time → full recompute)
        └─ 234  (changes aggregation → full recompute)
```

228 ships without this: the partial unique index makes its migration deployable, and D3's recompute
is gated on the mac-mini regardless. But **no recompute — 228's, 226's or 234's — may run until 235
lands**, or it silently stores nothing.

## Non-goals

- Plan 228's code fix, which is complete and shipping.
- Re-opening the `computation_version < 2` invalidity cutoff. It stays the algorithm-version
  marker; this plan adds a separate generation identity rather than overloading it.
- The FI aggregation work — Plan 234.
- Anchoring — Plan 226.

## Exit gates

To be written at review. Two are fixed:

- A real-PostgreSQL test that runs a recompute **twice** over unchanged hindcasts with changed
  observations, and proves the corrected result both survives and becomes the one that is read.
- A test that a recompute omitting `hindcast_run_id` cannot publish two cohorts into one generation.
