---
status: READY
created: 2026-08-31
plan: 224
title: M-A5c — close IMERG's two prerequisites, then measure the volume and stop
scope: Close the two limitations recorded as prerequisites before any IMERG acquisition, then run T1's projection gate to learn the actual retrieval volume. ENDS THERE. NOT the bulk retrieval, NOT a window decision, NOT any comparison against gauges.
depends_on: [211]
blocks: [any IMERG acquisition]
source: docs/design/dhm-precipitation-phase2-recommendation.md § 7.3; PR #229
---

# Plan 224 — M-A5c IMERG prerequisites and the volume measurement

## Status

**READY.** Owner confirmed 2026-08-31.

## ⛔ PROPORTIONALITY IS BINDING

**Two small fixes and one measurement.** The pipeline exists, is merged and has been through four
independent review rounds. No new framework, abstraction layer, config surface or file format.
⛔ **This plan does NOT retrieve the archive.** **Adding length is a cost.**

## ⛔ PER-RUN SCOPE (binding)

- 🔴 **RETRIEVE EXACTLY ONE GRANULE. NOTHING ELSE.** This is the single hardest constraint. ⛔ Not a
  day, not a month, not "a small sample to be safe". The projection is arithmetic on **one** measured
  file. If the retrieval loop looks like it should run again, **stop and report**.
- ✅ **Network: GES DISC HTTPS archive only**, Earthdata credentials from `~/.netrc`.
  ⛔ **Never the Copernicus CDS** — a wrong root once triggered a live ERA5-Land download on this track.
- ⛔ **Publish no bundle.** T1's fixes are to the publisher; ⛔ do not exercise them by publishing.
- ⛔ Never write under `data/dhm_precip/era5_land*`; never delete anything under any `points/` tree, and
  ⛔ **do not touch `data/dhm_precip/tigge/`** — that is 110 MB of retrieved research data.
- ⚠️ **Verify every code reference in this plan against the source before relying on it.** Plan 220 had
  three plan-stated code facts that were wrong; a citation that was never checked reads exactly like one
  that was.
- **Iterate on `tests/unit/scripts/`**, not the full suite (~8 min). Full `tests/unit` **once** at the
  end, measuring your own baseline.
- **Hold at PR.** Patch bump folded into the real commit; stage by explicit path, never `git add -A`.

## Why this plan exists

Plan 211 merged the IMERG Early acquisition and extraction pipeline (PR #229, v0.1.837) and
**deliberately stopped before retrieving anything**. Two things now stand between that and any real
acquisition, and one unknown blocks the decision itself.

## The two prerequisites — safe only while nothing is published

Both were accepted as deferrals **because no bundle exists**. ⛔ **That stops being true the moment the
first granule is retrieved**, which is why they are this plan's first task rather than the retrieval's.

1. **Acquisition gaps are not reconciled against the per-station hourly `granule_count`.** The bundle
   records which granules were missing, and each station-hour records how many granules it had — but
   nothing checks the two agree. ⇒ A bundle could report a gap the series does not reflect, or vice
   versa, and validation would pass.
   ⚠️ The reviewer noted the naïve formulation (105,216 gaps × 52,608 hours per station) is expensive
   and **need not be implemented as a Cartesian product**. ⛔ Do not build the expensive version;
   ⇒ derive the expected `granule_count` per hour from the gap set and compare, or an equivalent that
   is linear in the data.
2. **A published bundle's digest can be orphaned.** The writer protects the permanent acquisition
   record itself (refusing downgrades, detecting checksum disagreement) but **does not check whether a
   published bundle's `acquisition_record_sha256` still resolves**. ⇒ A re-acquisition can leave a
   published bundle naming a record that no longer exists.
   ⛔ This needs the writer to consider published bundles — the cross-layer dependency the original plan
   deliberately avoided. **Keep it minimal**: refuse to replace a record that a published bundle names,
   or fail loudly. ⛔ Do not build a general provenance framework.

## The unknown that blocks the decision

**Nobody knows what the retrieval actually weighs.** The plan projects "hundreds of GB" over 105,216
granules against **870 GB free** — but that is arithmetic on an assumed granule size, not a measurement.
⇒ **T1's projection gate answers it**: retrieve **one** granule, verify the D1 read contract on it,
record its size, multiply, report, **and stop**.

⛔ **If server-side subsetting works, the whole question dissolves** — a subset over the frozen box is
~4,500 cells per granule instead of a global field. The projection must state which route was used.

## Decisions

- **D1 — The prerequisites close BEFORE the probe, not after.** ⛔ Ordering matters: a granule on disk
  is the first step toward a published bundle, and both prerequisites exist to protect published state.

- **D2 — Exactly ONE granule. This plan does not retrieve a window.** The probe is the D1 contract
  verification Plan 211 already specifies. ⛔ Not a day, not a month, not "a small sample to be safe".

- **D3 — The output is a NUMBER and a RECOMMENDATION, not data.** Report: measured granule size, the
  route used (full field or subset), the projected total for the full window **and for one JJAS
  season**, and the free disk at the time. ⇒ The owner decides the window with real figures.

- **D4 — No estimator, comparison or gauge work.** ⛔ Nothing in this plan touches the DHM gauges, the
  timing analysis, or any bundle publication. It closes two holes and measures one quantity.

- **D5 — The IMERG case is NARROW and unchanged.** Per M-A9 §7.3: worth pursuing at **low and mid
  elevation**, where the 20 Group-B gauges can validate it independently; ⛔ **NOT a route to the high
  basins** (passive-microwave retrieval degrades over snow/ice, exactly where our gauges are sparsest);
  ⛔ **not an operational feed** (~4h20m–4h50m measured latency). ⚠️ Early receives Final-derived
  climatological calibration, so **published Final skill figures do not transfer to Early**.
  ⇒ **One thing has changed in its favour and should be recorded, not acted on here:** IMERG Early is
  **half-hourly**, where the IFS timing result is bounded at ±3 h by 6-hourly data. A finer independent
  view of the diurnal cycle is now worth more than it was. ⛔ That is an argument for the *next* plan,
  not scope for this one.

## Tasks

### T1 — close the two prerequisites (depends: nothing)
**In:** the gap/`granule_count` reconciliation (linear, not Cartesian) and the published-digest
protection, both minimal.
**Out:** any retrieval; any new abstraction.
**Verify:** a bundle whose recorded gaps disagree with its per-station `granule_count` is **refused**;
a record that a published bundle's digest names **cannot be replaced**. ⛔ Prove each by reverting —
reintroduce the hole, confirm the test fails, restore.

### T2 — measure the volume, then stop (depends: T1)
**In:** retrieve **one** granule per D2; verify the D1 read contract on it; record its size and the
route; project over 105,216 and over one JJAS season; report against free disk.
**Out:** ⛔ **any further retrieval.** ⛔ Any bundle publication.
**Verify:** exactly one granule exists on disk afterwards; the projection names its route and its
measured basis; ⛔ nothing is written under `data/dhm_precip/era5_land*` and nothing under any `points/`
tree is deleted.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit

The two prerequisites are closed and proven, and the owner has a **measured** retrieval volume with its
route named — enough to choose a window, or to decline. ⛔ **No archive has been retrieved and no bundle
published.**

## Non-goals

The bulk retrieval · choosing a window · any gauge comparison or skill claim · any operational use
(the ~4h20m latency rules it out) · IMERG Final or Late · the high basins (D5) · a provenance framework.
