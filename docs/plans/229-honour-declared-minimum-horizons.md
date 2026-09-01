---
status: DRAFT
created: 2026-09-01
plan: 229
title: Let a model run on a shorter weather record when it says it can
scope: Bump ForecastInterface and aquacast together, carry a minimum alongside the desired horizon end-to-end, and accept a short record when the model declares it acceptable. Replaces the temporary shortcut taken in Plan 151 D34.
depends_on: []
blocks: []
source: 2026-09-01 — owner ruling that Plan 151's AT_MOST shortcut was a workaround pending the aquacast update, which has now arrived
---

# Plan 229 — stop refusing forecasts the model could have made

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

This plan makes **one value into two** along a path that already exists, and lets a declaration
already being made reach code already written to consume it. It is deliberately larger than a
dependency bump and deliberately smaller than a redesign.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings to justify a pass.
2. **A finding must name a CONCRETE FAILURE** with `file:line` and a consequence.
3. **Do not propose new apparatus** — no new abstraction layer, no horizon framework, no strategy
   pattern, no new service, no CI job, no telemetry.
4. **Do not widen this into the multi-resolution work** (Plan 153) or reopen Plan 151 beyond
   D34/D10a. Those non-goals are load-bearing.
4b. **Do not propose per-feature minimums.** No model declares divergent ones (§ Scope ruling); a
   previous review round proposed building that machinery and it was rejected as solving a problem
   nobody has.
5. **Do not propose deleting `HORIZON_CEILING_FLOORS`.** D1 explains why: it is the working
   short-record support today.
6. **Do not re-argue whether to do this at all.** The owner ruled on 2026-09-01 that the August
   compromise was a workaround pending exactly this update.
7. **Adding length is a cost.** Prefer deleting to adding; a shorter plan that still passes its exit
   gates is a better plan.

## In plain terms, before the detail

Today our system decides how much future weather data a model needs, demands exactly that much, and
**refuses to run if there is less**. The updated model can now say *"I'd like N days, but I can work
with as few as M."* Nothing on our side listens, so we still refuse.

This plan makes us listen: when the model says a shorter record is acceptable, we run and produce a
correspondingly shorter forecast instead of producing nothing.

## Why now, and why not the cheap version

Plan 151 **D34** deliberately dropped this handling in August, because the axis was *"unreachable
twice over"* — ForecastInterface was pinned at v0.1.19 which had no way to express it, and the
adapter hid the model's declaration. It recorded the limitation as an accepted cost (151 D10a) and
set a **revisit trigger: an FI bump to >= v0.1.20**.

**That trigger has fired.** FI v0.1.20 exists and aquacast v0.1.346 already declares the new
semantics.

**The owner has ruled (2026-09-01) that 151's acceptance was a WORKAROUND pending exactly this, not
a permanent design choice.** That ruling is why this plan exists and why the cheap version — bump
the dependencies, leave the limitation in place — is explicitly rejected: it would convert a
temporary compromise into a permanent one, and the note recording its temporariness would be lost.

**Nothing in the repo said the compromise was temporary.** 151 reads as settled and closed, which is
why a later reader (me, 2026-09-01) mistook it for permanent and recommended the wrong thing. T4
fixes that record.

## What actually has to change

The desired horizon is a **single number** at every hop, so there is nowhere to put a minimum:

| where | today |
|---|---|
| `types/forcing_track.py:85` | `ForcingRequired` carries one horizon per feature |
| `services/track_projection.py:64` | carries that maximum through unchanged |
| `services/track_resolution.py:146` | **rejects** any record shorter than it, before the model is consulted |
| `services/nwp_coverage.py:79` | `assess_future_coverage` takes one `required_steps: int` |
| `services/horizon_semantics.py:44` | `RequiredSteps` carries a single `steps` with no per-feature dimension |
| `adapters/forecast_interface.py:514-521` | collapses per-variable `future_steps` to a scalar max |

And the model's own declaration never arrives: `_model_declared_floor()`
(`services/horizon_semantics.py:74`) reads `input_requirement` off the model, but all three runners
pass the wrapped adapter (`run_station_forecast.py:255`, `:285`; `run_group_forecast.py:389`), which
exposes only a private `_model`.

**A static stand-in is doing this job today and must not be removed carelessly.**
`HORIZON_CEILING_FLOORS` (`types/ids.py:69-71`) hard-codes a floor of 5 for `cmal_pool_pt`, consulted
at `horizon_semantics.py:139`, and runner tests prove it changes acceptance
(`tests/unit/services/test_run_station_forecast.py:1150`). **It is the current short-record support.**

## Scope ruling — a MODEL-WIDE minimum, not a per-feature one

**Measured 2026-09-01:** aquacast sets `min_future_steps=1 if relaxable else None`
(`aquacast/operational/requirement.py:213`) and decides `horizon_semantics` once per model (`:197`)
— **one uniform value across every variable.** No model declares divergent per-variable minimums
today.

Plan 151 **D34**'s revisit trigger required **both** *"an FI bump to >= v0.1.20"* **AND** *"a
per-track-eligible model declaring divergent per-variable `min_future_steps`"*. **Only the first
has fired.**

So this plan honours a **single minimum per model** — which is exactly what
`_model_declared_floor()` already returns (`services/horizon_semantics.py:116`) and what
`assess_future_coverage(..., required_steps: int)` already accepts (`services/nwp_coverage.py:79`).
**Per-feature minimums are explicitly OUT OF SCOPE** until a model declares divergent ones, at
which point 151 D34's second condition fires and it becomes its own plan.

A review round proposed building the per-feature machinery now and called the existing single value
a blocker. It is not a blocker for any declaration that exists — building it would be solving a
problem nobody has, and this plan's proportionality rules forbid it.

## Decisions

- **D1 — Replace the hard-coded table with the model's own declaration; do not delete it blind.**
  `HORIZON_CEILING_FLOORS` becomes the fallback for models that declare nothing, not the primary
  source. Removing it before declarations flow would remove working support — a mistake an earlier
  draft of Plan 227 nearly recommended.

- **D2 — The minimum travels beside the desired horizon; it does not replace it.** Both are needed:
  the desired horizon still drives what we fetch, the minimum only decides what we accept. A single
  number cannot carry both, which is why every row in the table above must change.

- **D3 — Acceptance moves to where the model is known.** Rejection currently happens in resolution
  (`track_resolution.py:146`) before any model is consulted. A record shorter than desired but at or
  above the declared minimum must survive that far.

- **D4 — Short runs must be visibly short, never silently short.** `RequiredSteps` already carries a
  reason *"so callers can log a truncated run distinctly from a full one, rather than silently
  equating them"* (`horizon_semantics.py:45-48`). Honour that: a forecast produced from a short
  record must be recorded as such, in the stored forecast and in the logs.

- **D5a — All THREE runner routes must be covered, and the GROUP route is the one that matters.**
  `cmal_pool_pt` runs as a GROUP model, and the GROUP path has **zero** references to track
  resolution (verified by grep 2026-09-01). An earlier draft targeted the
  carrier/projection/resolution chain only — which would have missed the very route the model
  actually uses. The accept/reject seam common to all three routes is
  `resolve_required_steps()` -> `_model_declared_floor()` -> `assess_future_coverage()`, and that is
  what this plan changes.

- **D5 — This supersedes 151 D34, and says so in both documents.** A plan that quietly contradicts a
  ruling marked "settled, do not reopen" is worse than one that names it.

## Tasks

### T1 — bump the two dependencies together
FI `v0.1.19` → `v0.1.20` and aquacast `1937794c` → `5cbfdbb7`. They cannot resolve independently.
*Exit:* `uv lock` resolves; `uv sync --extra aquacast` installs; `rich` stays at 14.x (the
`>=14,<15` override must be undisturbed).

### T2 — let the model's declaration reach the code that wants it
Expose `input_requirement` through the adapter so `_model_declared_floor()` sees it. Selective
forwarding is the adapter's established pattern (`forecast_interface.py:465`), so this does not
violate its design.
*Exit:* a declared minimum is visible through the wrapper on all three runner paths; proven by a
test that fails today.

### T3 — carry the minimum end-to-end and accept short records
The seam named in D5a, on **all three** runner routes — station legacy, station per-track, and
GROUP. The single-value carriers in the table above are touched only where they actually block that
seam; this plan does **not** rebuild them for per-feature minimums (§ Scope ruling).
*Exit:* a record shorter than desired but at or above the declared minimum **produces a forecast**;
one below the minimum still fails; a model declaring no minimum behaves exactly as today. Red-first.

### T4 — correct the record in Plan 151, and close out Plan 227
Note against D34/D10a that the acceptance was a workaround pending the FI bump, that the trigger has
fired, and that this plan supersedes it.
Also mark **Plan 227 superseded**: it sits BLOCKED awaiting a decision this plan now contains, and
an active blocked plan pointing at an answer given elsewhere is how work gets silently lost.
*Exit:* a reader of 151 cannot mistake the compromise for a permanent design choice, and 227 no
longer reads as awaiting an answer.

## Non-goals

Multi-resolution work (Plan 153) · re-opening anything in 151 beyond D34/D10a · changing what we
fetch (only what we accept) · the deployed image or the mini · onboarding any model.

## Exit gates

- A short-but-acceptable record produces a forecast; a too-short one still fails; no-declaration
  models are unchanged.
- Short runs are recorded as short, not silently equated with full ones.
- `HORIZON_CEILING_FLOORS` still covers models that declare nothing.
- Plan 151 no longer reads as though the limitation were permanent.
