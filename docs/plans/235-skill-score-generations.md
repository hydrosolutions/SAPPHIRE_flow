---
status: DRAFT
created: 2026-09-03
plan: 235
title: A skill recompute must replace what it supersedes — generations, not collisions
scope: Give a logical recompute one stable identity, make every consumer read the newest generation, and record retention growth. Split out of Plan 228 when an independent design check showed version-bumping alone was unsafe. MUST land before Plan 228's D3 recompute is executed.
depends_on: [228]
blocks: []
source: 2026-09-03 — independent design check of Plan 228's proposed recompute fix
---

# Plan 235 — generations, not collisions

## Status

**DRAFT — not reviewed.** Created 2026-09-03 after an independent design check found the simpler
fix unsafe. Every finding below is from that check and cites live code.

## ⛔ BINDING ON THIS PLAN **AND ON ITS REVIEW** — read before changing anything

### 1. Do not over-engineer

**A finding that SHRINKS this plan is worth more than one that adds.** Precedent in this very
family: Plan 222 was inflated from 5 tasks to 8 by review rounds and had to be cut back by a
dedicated proportionality pass; Plan 226 was tripled from 186 to 526 lines by a `plan` round whose
entire output was **discarded**. Both had guards. Do not make this the third.

This plan has already had one independent review and was rewritten under it. It is deliberately
pitched at *what must be true* (D1–D4, D2b) plus tasks — **not** at implementation choices.

**Out of bounds — reject if proposed:**

- Choosing between a generation **table** and a generation **column**. Deliberately open; the
  observable contract in D2b is what matters.
- Pruning or retention machinery. D4 is a **documentation decision** and stays one.
- New tasks, phases, abstractions or plug-in points beyond T1–T5.
- Anything owned by another plan in the line (see §2).
- Rewriting historical administrative views to hide older generations.

**"No findings" is a complete and valuable review.** Do not manufacture findings to justify a pass.

### 2. 🔗 THIS PLAN CANNOT BE CHANGED IN ISOLATION

It sits in a **six-plan line that was jointly reviewed twice** specifically so the plans could not
diverge. The agreed order:

```
228 (in PR #246) → 229 → 230 → 235 → { 226 , 234 }
```

| Plan | Owns | Do not move work into 235 from here |
|---|---|---|
| **228** | the shipped P1/P2/D4 fix, migrations 0051 + 0052's partial indexes | its D3 (mark + recompute) is gated on 235 |
| **229** | declared minimum horizons, the FI version bump | ⛔ BLOCKED — two internal contradictions |
| **230** | bounding the hindcast issue-time window | must select a **subset** of the existing grid |
| **226** | daily-model `valid_time` anchoring | depends on 235 |
| **234** | FI-declared aggregation, channel-keyed | depends on 235 |

**If you change this plan in a way that touches any of those boundaries, you MUST say so
explicitly** — naming the other plan, the line you would change in it, and why the split still
holds. A change that silently re-allocates ownership between plans is the exact failure two joint
reviews were run to prevent. **Do not edit the other plans**; report the required change instead.

Facts already established by those joint reviews — do **not** re-derive or contradict:

- `computation_version` means **algorithm version** and is the authoritative `< 2` invalidity
  cutoff. It is not a run counter.
- ForecastInterface v0.1.20 leaves the aggregation contract unchanged, so 229 may land before 234.
- Run/artifact attribution belongs **solely** to this plan.
- No recompute *or marking* — 228's, 226's or 234's — may run until this plan lands.

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

**There are NINE in-scope readers, and the codebase already has THREE mutually inconsistent rules
for "what is current"** *(corrected 2026-09-03 — an earlier count of eight was wrong; reader 9 was
missed by an audit that grepped the store's call sites instead of every reflected-table access.
Re-run the audit as `reflected.tables.get("skill_scores"|"skill_diagrams")` plus direct
`skill_scores.c`/`skill_diagrams.c` access before treating any list as exhaustive.)*:

| # | Reader | Path | Filters by version today? |
|---|---|---|---|
| 1 | `store/skill_store.py:45` `fetch_latest_scores` | store | ✅ yes |
| 2 | `store/skill_store.py:67` `fetch_latest_diagrams` | store | ✅ yes |
| 3 | `store/skill_store.py:89` `fetch_scores_by_regime` | store | ❌ no |
| 4 | `store/skill_store.py:106` `fetch_skill_scores` | store | ❌ no |
| 5 | `services/model_onboarding.py` — the **promotion gate**, via reader 4 | service | ❌ no |
| 6 | `api/routes/models.py:126` scores | **reflected table, raw SQL** | ❌ no |
| 7 | `api/routes/models.py:141` diagrams | **reflected table, raw SQL** | ❌ no |
| 8 | `api/routes/stations.py:363` station summary | **reflected table, raw SQL** | ❌ no |
| 9 | `api/routes/models.py:177` **skill-chart endpoint** (`model_skill_chart_json`) | **reflected table, raw SQL** | ⚠️ **`freshness == "current"` (`:213`)** — a THIRD mechanism |

**Three incompatible definitions of "current" ship today**: `max(computation_version)` (readers 1–2),
no filter at all (3, 4, 6, 7, 8), and `freshness == "current"` (9). Unifying them is this plan's
work; a generation identity layered on top of three disagreeing rules would produce a fourth.

**Deliberately OUT of scope**: `api/routes/dashboard.py:209` also reads `skill_scores` through a
reflected table, but it is the administrative retention/freshness breakdown the Non-goals keep
historical. Named here so the exclusion is a decision rather than another oversight.

⚠️ **Readers 6–9 bypass the store entirely**, reading reflected tables with raw SQL. Any fix applied
only at the store leaves the whole API serving mixed generations.

### D2b — the publication contract, decided HERE not at implementation time

*(First review: the plan stated WHAT must be true but left three semantics open that cannot be
deferred — each changes observable behaviour.)*

1. **Precedence is algorithm version FIRST, then generation.** Publication time alone is unsafe:
   migration 0052 deliberately permits a rolled-back image to write `computation_version = 1`
   (`alembic/versions/0052_...py:76-91`), so a *later* invalid v1 generation could otherwise
   supersede a correct v2 one. Read the highest eligible algorithm version, then its newest
   generation.
2. **Replacement scope is the recompute's own domain.** A station/parameter-scoped recompute must
   not hide unrelated parameters, models or stations merely by being globally newest.
3. **Input identity must be defined, because observations are MUTABLE.** `ON CONFLICT DO UPDATE`
   (`store/observation_store.py:37-60,97-121`) means "stable across retries" and "different when
   inputs differ" cannot both hold on their own. Define either an input snapshot/fingerprint or an
   ingestion-quiescence rule. The table-vs-column choice stays open; this does not.

### D3 — supersession is atomic, and covers diagrams

There is no "store the complete new generation, then supersede the previous one" operation.
`mark_stale` updates only overlapping score rows and **cannot mark diagrams at all**
(`store/skill_store.py:122-140`). A half-superseded generation is worse than none.

**🔴 The missing piece the first review found — a generation-level COMPLETENESS GATE followed by ONE
atomic publication.** Every expected score/diagram partition must finish successfully *before* the
new generation becomes visible; an empty, failed or partial generation must leave the previous
publication untouched. Without it a partially successful 115,000-row fan-out half-replaces the
scores, which is worse than either outcome.

Today nothing prevents that: a task can return empty before publishing any diagnostic
(`flows/compute_skills.py:109-120`), scores and diagrams are separate writes (`:65-71`), and
production runs **AUTOCOMMIT**, committing each statement independently (`flows/_db.py:78-85`).

**Two operational constraints come with it**: physical marking needs explicit transaction wiring
against that autocommit default, and `sapphire_worker` currently holds **INSERT only** on both skill
tables (`docker/bootstrap-roles.sql:180-181`) — so either a privilege change or an append-only
publication record is required.

### D4 — retention: the EXISTING permanent policy is accepted, no machinery added

Each full recompute adds ~115,000 score rows plus diagrams and index entries; nothing prunes them,
and `docs/handover/data-flows.md:699` already states skill scores are permanent. *(Family review,
2026-09-03: adding pruning machinery here would be scope growth.)* **Accept the existing policy and
record the growth rate** — a documentation statement, no implementation task, no pruning machinery.
Historical administrative views (the dashboard's retention/freshness breakdown) stay historical and
are deliberately NOT swept into latest-generation filtering.

⚠️ `data-flows.md:699` makes **scores** permanent but says nothing about **diagrams** — so this
decision *establishes* diagram retention rather than restating it. Say so explicitly.

## 🔴 The trap this plan must close for Plan 226

`compute_skills_task` fetches the **entire 1970–2100 hindcast history** unless `hindcast_run_id` is
supplied (`flows/compute_skills.py:85-116`), then scores every phase cohort (`:133-171`).

**And it is not the only handoff** *(corrected at first review, 2026-09-03)*:

| Path | State |
|---|---|
| `flows/compute_skills.py:85,106-116` | unscoped **only when the optional id is omitted** — the training and model-onboarding callers already pass it (`flows/train_models.py:624-677`, `flows/onboard_model.py:1096-1148`) |
| `flows/compute_skills.py:226-234` combined scoring | **unconditionally unscoped** |
| `services/onboarding.py:157-163` station onboarding | **unconditionally unscoped** |

**This plan owns all three**, or the defect moves to whichever was left out.

**A Plan 226 recompute that omits the run id would publish both the old unanchored and the new
anchored cohorts inside the same newest generation.** Generation identity alone does not prevent
this — the run id must be required on the hindcast→skill handoff. Plan 234 records the same defect
from the artifact-attribution angle (`docs/plans/234-honour-declared-aggregation-end-to-end.md:80-85`); it is one defect with two
symptoms and must be fixed once, here.

## Sequencing — this is the constraint that binds the family

```
228 (ships now — deployable migration + the shipped P1/P2/D4 fix)
   └─ 235  ← MUST land before ANY of 228's D3 executes
        ├─ 226  (anchoring)   ─┐ no hard order between these two;
        └─ 234  (aggregation) ─┘ both may be DEVELOPED before 235, not landed
```

**The gate covers ALL of D3, not only the recompute** *(family review, 2026-09-03 — the earlier
wording gated the recompute alone)*. Plan 228's D3 says mark first, then recompute; but marking
before 235 lands would strip every current, trustworthy result **without publishing a replacement**,
because supersession is not yet atomic and cannot touch diagrams at all
(`store/skill_store.py:122-140`). Mark and replace must happen as one operation, and that operation
is this plan's.

226 and 234 may be **developed and held at PR** before 235, but must not land or deploy while live
onboarding, hindcast or skill flows could exercise them. There is no demonstrated hard order between
226 and 234 once 235 is in.

## Phase graph

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2", "T3"], "parallel": false, "depends_on": ["phase-1"]},
    {"id": "phase-3", "tasks": ["T4"], "parallel": false, "depends_on": ["phase-2"]},
    {"id": "phase-4", "tasks": ["T5"], "parallel": false, "depends_on": ["phase-3"]}
  ]
}
```

## Tasks

### T1 — the generation identity and its migration

Add a generation identity per D1 and D2b, alongside `computation_version` (which keeps its meaning
as **algorithm** version — do not overload it).

**Migration constraints, all from the first review:**
- Do **not** collapse or tighten 0052's `computation_version < 2` legacy indexes; 0052 documents why
  that fails against existing NULL-artifact rows (`alembic/versions/0052_...py:59-72`).
- The new column must start **nullable** under the one-release rollback rule
  (`docs/standards/cicd.md:184-198`) — and a raw nullable column in a unique index lets an
  old-image write with `generation = NULL` bypass uniqueness entirely. Define explicit
  NULL/baseline-generation semantics.
- Give existing rows deterministic baseline read semantics, **including any v2 rows written between
  Plan 228 and this plan**.

### T2 — all eight readers select the newest eligible generation

Per D2's table. Readers 6–8 are raw reflected-table SQL in the API and need their own fix; a
store-only change leaves them broken.

### T3 — run scoping on all three handoffs

Per the trap section: `compute_skills.py` (make the id required rather than optional), combined
scoring, and station onboarding.

### T4 — completeness gate and atomic publication

Per D3. A generation becomes visible only when every expected partition has succeeded; a partial or
failed generation leaves the previous publication untouched. Resolve the AUTOCOMMIT and
INSERT-only-privilege constraints here.

### T5 — documentation

Record the retention decision (D4, documentation only, explicitly covering diagrams) and the
publication contract (D2b), so whoever executes Plan 228's D3 can see the rules.

## Non-goals

- Plan 228's shipped fix; anchoring (226); FI aggregation (234).
- Pruning or retention machinery — D4 is a documentation decision.
- Overloading `computation_version` as a run counter.
- Rewriting historical administrative views to hide older generations.
- Re-opening the `computation_version < 2` invalidity cutoff. It stays the algorithm-version marker;
  this plan adds a separate generation identity rather than overloading it.

## Exit gates

- A real-PostgreSQL test that runs a recompute **twice** over unchanged hindcasts with changed
  observations, proving the corrected result both survives and becomes the one that is read.
- A real-PostgreSQL test that a **partial** generation — some partitions failing — leaves the
  previous publication intact and visible. This is the gate the first review found missing; without
  it a half-replaced 115,000-row recompute passes.
- A test that a recompute omitting the run id cannot publish two cohorts into one generation.
- A migration test upgrading a database seeded with 0052-shaped rows, including NULL-artifact
  legacy rows and v2 rows written before this plan.
- A test that an old-image write with a NULL generation cannot bypass uniqueness.
- All eight readers covered, including the three raw-SQL API paths.
- `uv run pytest tests/unit` green, reporting **pytest's own** exit status.

## After this plan

Plan 228's D3 becomes executable: mark ~115,000 scores superseded and recompute, as one operation
that either lands completely or changes nothing.
