---
status: DRAFT
created: 2026-07-15
plan: 115b3
parent: 115b
title: Validation gate — our derived forcing vs CAMELS reference + the live-tail residual
scope: A GO/NO-GO analysis gate before the reader flip. No production change.
depends_on: [115b2]
blocks: [115b4]
---

# Plan 115b3 — Validation gate

> **Design source: [Plan 115b](115b-weather-flow6-reachability.md) §8** — the reference-comparison design,
> the named confounds, and the owner tolerance decisions. Carries **phase 4**.

## Status

**DRAFT.** Third chunk (115b1 → 115b2 → **115b3** → 115b4). Independent Codex review before READY.

## What this is — and why it is its own plan

Phase 4 is a **gate, not a deliverable**: it does **not** change production. It runs *after* the backfill
(115b2) and *before* the reader flip (115b4), and its output is the **go/no-go** decision on whether our
self-derived series is a faithful continuation of CAMELS. Isolating it as its own plan makes the gate an
explicit, reviewable checkpoint rather than a step someone rolls through.

## The comparison (115b §8) — a whole-pipeline REFERENCE comparison, confounds named

CAMELS-CH's forcing (`RhiresD`/`TabsD`/`SrelD`, Höge et al. 2023) and ours share products, but the
comparison is **not** a clean attributable control — named confounds: grid vintage/resolution (CAMELS 2 km
vs open-data 1 km), reprocessing, and (NEEDS-EXTERNAL-CHECK) whether CAMELS' shipped shapefiles are the exact
masks it aggregated forcing over. The **biggest** confound is likely gone — our basins ARE CAMELS' shipped
geometries (`camelsch_adapter.py:262-301` → stored `onboarding.py:240-248`) — but confirm before leaning on it.

### Tolerance gates (owner-locked, 115b §8)

**PRECIPITATION** — per-basin relative bias of the **1981-2020 TOTAL**:
```
rel_bias = (Σours − Σcamels) / Σcamels     per basin
  ≤ 5%  → pass ;  > 5%  → FLAG ;  > 20% → ESCALATE to owner (NOT an auto-stop)
```
**TEMPERATURE** — per-basin absolute error in °C (percent banned near 0):
```
pass ⟺ |mean_bias| ≤ 0.5 °C AND rmse ≤ 1.0 °C ;  FLAG if either exceeds ;  ESCALATE if |mean_bias|>1.0 or rmse>2.0
  (thresholds owner-to-confirm on first results)
```
**NON-GATING DIAGNOSTICS** (reported, never thresholded): per-season totals, per-event maxima, wet-day
RMSE — this is where the 2 km-vs-1 km grid effect legitimately lives; large per-*event* discrepancies are
physically expected, never a gate trip. **>20% on the TOTAL escalates but never auto-stops** — a large
whole-period bias may be a legitimate grid-change consequence and must be **explained**, not shrugged off.

## Tasks (phase 4)

- **4A — basin-mean comparison 1981-2020** (precip `RhiresD` + temp `TabsD`, our polygons vs CAMELS').
- **4B — tolerance report** (per-basin bias/RMSE + the seasonal/intensity diagnostics; apply the gates).
  *(4B depends on 4A.)*
- **4C — fetch the RprelimD/RhiresD overlap window.** The clean live-tail comparison has **no data in our
  DB** (audit: only `camels-ch`) and the backfill spans are disjoint by construction — so this is a
  SEPARATE one-off STAC fetch of a recent window where BOTH products are served (RprelimD retained ~2
  months; RhiresD republishes over it with its lag). *(Live probe 2026-07-15: overlap ~16 days and moving —
  accumulate over several monthly cycles; a single grab is only ~2 weeks of paired days.)*
- **4D — live-tail residual** (`RprelimD` vs `RhiresD` over 4C's overlap: same pipeline, polygons, grid,
  vintage → **the one genuinely attributable number** in the plan; the honest uncertainty on the ~8-week
  preliminary window). *(4D depends on 4C.)*

## Tests

- The validation experiment is a gate, but its **outputs are pinned**: a regression test asserts our
  basin-mean derivation is stable against a small committed fixture (so the gate itself doesn't silently drift).
- Tolerance-gate logic: precip relative-bias and temperature mean-bias+RMSE classify pass/flag/escalate
  correctly against synthetic inputs at the boundaries.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-4",
      "name": "Reference comparison vs CAMELS + clean live-tail measurement (a GATE)",
      "tasks": ["4A-basin-mean-comparison-1981-2020", "4B-tolerance-report", "4C-fetch-rprelimd-rhiresd-overlap-window", "4D-live-tail-residual"],
      "parallel": false,
      "task_depends_on": {"4B-tolerance-report": ["4A-basin-mean-comparison-1981-2020"], "4D-live-tail-residual": ["4C-fetch-rprelimd-rhiresd-overlap-window"]},
      "note": "4A||4C may start together; 4B after 4A; 4D after 4C.",
      "depends_on": ["plan-115b2"]
    }
  ]
}
```

## Exit gate — this plan is DONE when the gate is EVALUATED, not merely coded

```bash
uv run ruff check src/ tests/ && uv run pyright src/ && uv run pytest
```
Plus: **run 4A–4D on staging and record the per-basin results.** If precip TOTAL rel-bias >5% or temp
exceeds threshold on any basin, it is **FLAGGED and explained** before 115b4 proceeds; >20% / large temp
**escalates to the owner**. 115b4 (the flip) does **not** start until this gate's result is recorded and,
if flagged, dispositioned.

## Provenance

Extracted from Plan 115b (phase 4), 2026-07-15.
