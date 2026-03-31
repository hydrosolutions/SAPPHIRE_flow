---
status: DRAFT
created: 2026-03-30
scope: documentation sweep — update ~50 station references to ~1000, re-evaluate affected rationales
depends_on: []
---

# 013 — v0 Scale Re-evaluation (~50 → ~1000 Stations)

## Problem

Multiple docs reference "~50 stations" as the v0 scale target. This is outdated —
v0 starts smaller but must scale to ~1000 stations with sub-daily data for planned
large-scale experiments. The "~50 stations" figure drives simplification rationales,
performance budgets, and "acceptable at v0 scale" justifications that need re-evaluation.

## Affected Files

- `docs/v0-scope.md` — line 9 ("~50 stations, single VM"), line 34 (Flow 4 deferral
  rationale), line 50 (partitioning rationale), lines 248/256/284 (performance targets
  and per-step budgets)
- `docs/architecture-context.md` — line 595 (onboarding timing for "50 stations")
- `docs/spec/database-schema.md` — line 10 ("~50 stations")
- `docs/design/v0-flow2-observation-pipeline.md` — lines 61, 385, 387 (query-time
  aggregation viability at "~50 stations")

## Tasks

1. Update all station count references to reflect the scale range (starting smaller,
   scaling to ~1000).
2. Re-evaluate simplification rationales that depend on small scale — particularly:
   - **A1 (no partitioning)**: ~1000 stations × sub-daily × multiple parameters may
     produce significantly more than "a few GB/year." Re-assess whether partitioning
     is still safely deferred.
   - **Flow 4 deferral**: Manual supervision at ~1000 stations is less credible than
     at ~50. Re-assess timeline.
   - **D2 batch write budgets**: 1000 stations × 21 members × 120 timesteps = 2.52M
     rows per forecast cycle (not 126K). Verify COPY performance at this scale.
3. Update performance budgets (§D) for 1000-station target. The 60-second forecast
   cycle target may need revisiting or the budget breakdown needs rebalancing.
4. Check if "single VM" (line 9) still holds at 1000-station scale or if the
   infrastructure section needs updating.

## Urgency

Foundational — affects performance targets and simplification rationales across all
docs. Should be resolved early to avoid building on wrong assumptions.

## Origin

Extracted from plan 011 §G.
