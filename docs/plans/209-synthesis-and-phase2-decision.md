---
status: DRAFT
created: 2026-08-28
plan: 209
title: M-A9 — synthesis, the Phase-2 recommendation, and an IMERG feasibility read
scope: Consolidate M-A6/M-A7/M-A8 into one decision document stating what the DHM precipitation sample can and cannot be used for operationally; state for each Phase-2 option what would have to be true and whether the evidence supports it, explicitly including "no operational use"; and assess whether IMERG is worth acquiring (M-A5b) for operational forecast/nowcast correction. NOT a correction implementation, NOT an IMERG acquisition, NOT a GO decision — the owner decides.
depends_on: [184, 193, 205]
blocks: [M-DEC]
source: docs/design/dhm-precipitation-milestones.md § M-A9
---

# Plan 209 — M-A9 synthesis and the Phase-2 recommendation

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ PROPORTIONALITY IS BINDING

**The deliverable is a document and a recommendation, not software.** M-A9 computes almost nothing
new: M-A6, M-A7 and M-A8 are complete and their numbers are in
`docs/design/dhm-precipitation-milestones.md`. One small feasibility probe is permitted (T2) and
nothing else. **⛔ No correction design, no disaggregator, no acquisition pipeline.** Adding length is
a cost.

## What the three milestones established

**M-A6 — ERA5-Land's error has a shape, and the shape is consistent across every elevation band.**
It is **wetter than the gauge** averaged over all retained hours and **less intense** on the hours the
gauge calls wet; the intensity deficit shrinks from **+2.22 mm/h** below 700 m to **+0.43 mm/h** above
3,000 m. ⛔ Recorded as a pattern, never an attribution: a 0.1° cell (~110 km²) that rains lightly and
often where a point gauge rains hard and rarely produces the same signature with no model error at all
(D5 — characterised, not decomposed). ERA5-Land **never once reached DHM's 60 mm/1 h warning level**
against these 26 stations.

**M-A7 — tail behaviour transfers between stations everywhere EXCEPT the Lesser Himalaya transition.**
Leave-one-out prediction error within 25 %: 0.889 below 700 m, **1.000** at 700–2,000 m, **0.000** at
2,000–3,000 m, **1.000** above 3,000 m. Inside that failing band the q99/q50 ratio spans **9.56 to
32.00**. The diurnal hypothesis is **half** confirmed: the southern-margin early-morning peak
reproduces (~04:45 NPT below 700 m), but the three upper bands sit together just after midnight with
**no gradient among them** — a step change at the margin, not the monotonic shift the exploratory
`r = −0.486` implied.

**M-A8 — one clean elevation signal, and one unidentifiable confound.** Within Group B (20 stations at
**fixed** reporting resolution, 2,080 m of relief) q99 wet-hour intensity correlates **−0.697** with
elevation. That is the cleanest elevation result the track has, clean *because* resolution is held
constant rather than adjusted for. The **between**-group contrast is unidentifiable: Group A spans
2,490–3,700 m and Group B 67–2,147 m with **no overlap**, so "instrument" and "altitude" are the same
sentence in this sample.

**And the answer to the question that enabled the Pyramid work is NO.** The apparent rain-phase
gradient is **−52.49 %/km** (1.5 °C screen) but is an **upper bound in magnitude** on any true decline
— unheated gauges, catch efficiency falls with wind, wind exposure rises up a transect — and the
confound is **not identifiable from these data**. A usable precipitation lapse rate for elevation-band
forcing correction needs the convection-permitting route (**OD-10**), not this track.

## Decisions

- **D1 — The recommendation is written against the operational question, not the research one.** The
  document states, for each candidate use, **what would have to be true** and whether the evidence
  supports it — **including "no operational use"**, which is a permitted and possibly correct outcome
  (milestone Exit). ⛔ It does not hedge every option into "more research needed".

- **D2 — Every quoted number carries the conditions it was published with.** Plan 184 Exit 2 binds
  here too: no magnitude is quoted without its retained-hour `n` and its sub-freezing mass fraction;
  no M-A7 figure without its adequacy designation; no gradient without its period and its upper-bound
  sign. ⛔ **A synthesis is exactly where companions get stripped**, and this decision exists because
  that is the failure mode of every summary document.

- **D3 — The four candidate uses are named and assessed separately.** They fail and succeed for
  different reasons and must not be collapsed:
  1. **Gap-filling the gauge record** (use ERA5-Land where DHM is missing);
  2. **Bias-correcting ERA5-Land forcing** with a gauge-derived adjustment;
  3. **Elevation-band forcing correction** (the lapse-rate use);
  4. **Operational ingest of DHM real-time observations** for forecasting.

- **D4 — Use 3 is already answered NO by M-A8, and the document says so plainly.** ⛔ Do not re-open it
  or soften it into "promising but uncertain".

- **D5 — Use 4 carries a separate, non-technical blocker.** The owner's own read (2026-08) is that
  DHM's operational API delivers 10-/15-minute data with little or no QC, through the same API as
  water level, whose shape we have never seen. **This sample is already QC'd by DHM** and still
  contains false-zero runs to 52 days, a stuck-high sensor at 1,728 mm/day, and 46 sentinel values at
  one station. ⇒ The document states the **quality precedent**, not a capability claim.

- **D6 — The IMERG assessment is a LITERATURE + LATENCY read, not an acquisition.** M-A5b (IMERG
  acquisition + extraction) has **no plan written**. M-A9 decides only whether writing one is
  justified. ⛔ **Do not acquire IMERG in this plan.**

- **D7 — The IMERG read must separate the three runs, because only one is operationally usable.**
  IMERG **Early** ≈ 4 h latency (half-hourly, rapid response), **Late** ≈ 14 h, **Final** ≈ 3.5 months
  and gauge-calibrated. ⛔ **A characterisation done on Final says nothing about operational skill**,
  because Final is the one we could never use in real time — and M-A5b as currently written specifies
  **IMERG Final**. That mismatch is the first thing the document must surface.

- **D8 — The known Himalayan limitation is stated up front, because it lands exactly where we need
  help.** Published validation over Nepal finds satellite products correlate well in the **southern
  and middle hills** and poorly over the **northern high mountains**, and underestimate mean annual
  precipitation. Passive-microwave retrieval degrades over snow- and ice-covered surfaces (weaker
  scattering signatures, supercooled-liquid masking), and **IMERG deliberately RETAINS microwave
  estimates over snow/ice within 60°N–S rather than masking them** — so the high basins return numbers
  whose conditions make them least trustworthy. ⇒ **IMERG is strongest where our gauge network is
  already densest and weakest where it is sparsest.** That asymmetry is the finding, and it decides
  the recommendation rather than decorating it.

- **D9 — No pre-registered verdict on IMERG** (vision D8). T1 states the conditions; the owner decides
  whether M-A5b is worth writing.

## Tasks

Two tasks, two phases.

### T1 — the synthesis and recommendation document (depends: nothing new)
**In:** one document under `docs/design/` consolidating M-A6/M-A7/M-A8 against D3's four candidate
uses, each with what would have to be true and whether the evidence supports it, every number carrying
its companions (D2); the IMERG assessment per D6–D8 including the **Final-vs-Early mismatch in M-A5b's
current text**; and an explicit recommendation on whether to write an M-A5b plan.
**Out:** any correction design; any acquisition; any GO/NO-GO decision (the owner's, M-DEC).
**Verify:** no code, so no test. Instead: **every number in the document is traceable to a milestone
fold or to `ma6_run.py`/`ma7_run.py`/`ma8_run.py` output**, and a reviewer can regenerate any of them
with one command. ⛔ A number that cannot be traced is a defect.

### T2 — the IMERG latency/coverage feasibility probe (depends: nothing new)
**In:** a small, read-only probe answering three questions the literature cannot: **(a)** is IMERG
Early actually retrievable for our window and box, **(b)** what is its observed latency in practice,
and **(c)** what fraction of its retained values over our 26 station cells fall in the snow/ice-flagged
regime D8 describes. Report counts, never a skill claim.
**Out:** any extraction pipeline, any archive publication, any comparison against the gauges (that is
M-A5b's and a later milestone's work).
**Verify:** the probe runs read-only, publishes **no bundle**, and its output is a table of counts.
⛔ **Do not write into `data/dhm_precip/`.**

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T2"], "parallel": false},
    {"id": "phase-2", "tasks": ["T1"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

*T2 precedes T1 deliberately: the recommendation should be written knowing whether IMERG Early is
actually retrievable here, not assuming it.*

## Exit

A written recommendation stating, for each of D3's four candidate uses, what would have to be true and
whether the evidence supports it — explicitly permitting "no operational use"; every number carrying
the conditions it was published with and traceable to a regenerable source; the IMERG assessment
separating Early/Late/Final by latency, naming the Final-vs-Early mismatch in M-A5b's current text, and
stating the southern-hills-strong / high-mountain-weak asymmetry; and a recommendation on whether an
M-A5b plan is worth writing. **The owner decides (M-DEC); this plan does not.**

## Non-goals

Any correction, adjustment, disaggregation or downscaling design · IMERG acquisition or extraction
(M-A5b) · the students' independent findings (deferred by the owner, 2026-08-28) · re-opening use 3,
the lapse rate (D4) · any GO/NO-GO decision · recomputing M-A6/M-A7/M-A8 numbers.
