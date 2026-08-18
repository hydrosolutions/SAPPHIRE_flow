---
status: DRAFT
created: 2026-08-18
revised: 2026-08-18
plan: 183
title: The forcing canonicalisation seam — make a second NWP source safe to add
scope: Give the forcing contract a real consumer so unit and accumulation semantics are enforced rather than reimplemented per adapter; read the CRS instead of overwriting it; make ForcingResolution able to express sub-daily. Explicitly NOT building any downscaling (OD-10), NOT writing a DHM adapter, NOT changing existing adapter behaviour.
depends_on: []
blocks: [OD-10, M-A5b]
source: docs/design/dhm-precipitation-milestones.md
---

# Plan 183 — the forcing canonicalisation seam

## Status
**DRAFT.** Not for implementation until the owner confirms. **Not scheduled** — recorded because the
findings are actionable and would otherwise be lost inside a precipitation research document.

## Why this exists, and why it is filed HERE

Owner decision **OD-10**: SAPPHIRE Flow will **not** build convection-permitting downscaling. Nepal's
DHM has parallel projects improving their own forecasts and may couple a downscaled product into this
system later. What we owe is a **forcing interface that accepts an externally produced product without
redesign** — the seam is what makes someone else's success usable, and it is the cheapest available fix
for the ~12 h Himalayan diurnal phase error, which is structural in every parametrised-convection model
we can obtain.

A review on 2026-08-18 asked whether that seam exists. **It does not.** Findings were first written into
`docs/design/dhm-precipitation-milestones.md` under OD-10 — **the wrong home**: they are architecture
defects, and nobody building an adapter would look in a precipitation research doc. This plan is where
they belong.

## The findings — all verified against the code, not inferred

| # | Finding | Evidence |
|---|---|---|
| **F1** | **No shared canonicalisation layer.** Variable renaming, unit conversion and **precipitation de-accumulation** are adapter-PRIVATE if-chains. `types/forcing_schema.py` declares the canonical contract and has **ZERO consumers** | `adapters/meteoswiss_nwp.py:157-177`; grep for `forcing_schema` outside its own module returns nothing |
| **F2** | **`ForcingResolution` has only `DAILY`** — the one place the forcing contract is written down **cannot express the 3-hourly product v1 promises** | `types/forcing_schema.py:25-26` |
| **F3** | **The grid extractor OVERWRITES the CRS rather than reading it** — `.rio.write_crs("EPSG:4326")`. A projected or rotated-pole grid is **silently mis-georeferenced**, surfacing much later as "polygon(s) outside grid extent". No regridding exists anywhere in the repo | `preprocessing/exact_extract_grid_extractor.py:98`, error at `:158-161` |
| **F4** | **No adapter registry at all** — `adapters/__init__.py` is **0 bytes**. Adding a source is a build, not an extension | `wc -c` = 0 |
| **F5** | **A provider assumption lives in `types/`, not an adapter** — `HORIZON_CEILING_FLOORS` pins 5 steps because *"5 = MeteoSwiss ICON-CH2-EPS's 120 h"*; the comment itself calls it *"a PROVIDER assumption, not a modelling judgement"* | `types/ids.py:63-72` |

## D1 — F1 is the priority, because it is the one that fails SILENTLY

F2–F5 fail **loudly**: a missing enum member, a CRS error surfacing as an empty extraction, a missing
registry, a wrong horizon. You find out.

**F1 does not.** A second adapter must reimplement the accumulation/unit contract with nothing
enforcing it, and a **rate-vs-accumulation mismatch produces plausible, wrong forcing rather than an
error** — then models are trained against it and the error is baked in.

**This is not hypothetical for this repo.** ERA5-Land's accumulation convention was stated **wrongly**
in Plan 171's first draft and was settled only by a real CDS call (2026-08-17). **IMERG, the next source
queued (M-A5b), is a RATE product (mm/hr)** — exactly the mismatch waiting to happen, against an
existing accumulation-shaped pipeline.

⇒ **Smallest change that most reduces risk: give `forcing_schema` a real consumer.** Make the canonical
unit/accumulation contract something an adapter must *satisfy* rather than something it may *ignore* —
**before a second forecast source exists**, because retrofitting it after models are trained means
retraining.

## D2 — F3 is the same failure in a different place: assume rather than assert

`.rio.write_crs()` **asserts a CRS onto** the data instead of **reading** what the file declares. Fix it
with F1, not separately: **read the CRS; reproject to EPSG:4326 if it differs; raise a typed error if
the file declares none.** Never overwrite. This is the same discipline the track learned three times
against CDS — verify against the artefact, not against the assumption.

## Non-goals

Building downscaling (OD-10 — explicitly someone else's lane) · writing a DHM adapter (there is no
product yet; specifying against an unseen format is the mistake this track has made three times) ·
changing existing adapter behaviour (the seam must be additive — existing sources keep working
byte-identically) · fixing F5's horizon assumption, which is the modeller's declaration to replace.

## Sequencing note

**This plan gates M-A5b (IMERG).** If IMERG is built before F1, it will hand-roll the rate→accumulation
conversion in a private if-chain — the third adapter to do so, and the first where the units differ from
the pipeline's assumption. Doing F1 first makes IMERG the plan that *validates* the seam.
