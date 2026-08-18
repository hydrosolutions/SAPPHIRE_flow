# Partner data asks — lightning strokes and the hourly gauge archive

**Status: NOT YET SENT.** Owner action. Recorded here because it kept getting lost in conversation.
**Milestone:** M-D4 (`docs/design/dhm-precipitation-milestones.md`). **Raised:** 2026-08-18.

Two separate asks. They will probably go to **different partners**, so they can be sent independently.

---

## Ask 1 — Lightning: ⚠️ NO LONGER A PARTNER ASK. Use NASA ISS-LIS.

**Superseded 2026-08-18. WWLLN is not available to us; there is no budget for GLD360/ENTLN.**
**Neither matters — NASA's data is free with an Earthdata login the owner already holds.**

### Primary: ISS-LIS (Lightning Imaging Sensor on the ISS)

| | |
|---|---|
| Period | **2017-03-01 → 2023-11-16** (V3 reprocessed, whole mission) |
| Coverage | **±55° latitude** — Nepal (26–31 °N) comfortably inside |
| Overlap with our window | **2020–2023** |
| Accuracy | ~3 km location, <2 ms timing |
| Detection | **TOTAL lightning (IC + CG), optical, from above** — does not miss intracloud the way CG-only ground networks do |
| Access | **Free**, NASA Earthdata / GHRC DAAC, HDF-4 + netCDF-4 |

**Secondary, free, ready-made: the LIS/OTD gridded climatologies.** From TRMM-LIS + OTD, these include a
published **diurnal** climatology product. It predates our window, but **a diurnal cycle is a stable
climatological feature** (the same argument that lets Pyramid's 1994–2023 record inform our 2020–2025
one), so it is a legitimate cross-check on any profile we derive.

### ⚠️ The binding limitation — sampling, not access

**ISS-LIS SAMPLES; it does not MONITOR.** In low Earth orbit it views a given point only during
overpasses of ~90 seconds. The ISS orbit **precesses**, which is exactly what makes it usable for a
*diurnal* climatology — it eventually samples all local times — but there is **no continuous time
series at a point**.

⇒ **The question that decides whether this works: are there enough overpasses over 26–31 N / 80–89 E in
JJAS to populate 24 hour-bins per elevation band?** Checkable immediately on download, and it should be
the **first** thing checked — before any analysis is built on it. If bins are too sparse, fall back to
the LIS/OTD gridded diurnal climatology, which trades currency for sample size.

**No geostationary alternative covers Nepal well:** GOES-GLM is Americas-only; MTG-LI sits at 0° so
Nepal is at its extreme limb; FY-4 LMI (~105 °E) would see Nepal but is not openly accessible.

### What this changes

**M-D4 stops being blocked on a partner.** It becomes a small acquisition we can run ourselves — same
shape as M-A4/ERA5-Land. **Do not draft an acquisition plan until the data is in hand**; this track has
specified against documentation three times and been wrong every time.

---

## Ask 2 — Hourly precipitation gauge archive

> **What we need**
> The **full archive period available** — not a live feed.
>
> Per station: **name/ID, latitude, longitude, ELEVATION**, hourly precipitation, and **any QC flags**
> the archive carries.

**Which partner.** **ICIMOD** and **DHM** are the obvious routes. DHM is documented as operating
**≥63 hourly AWS**; we currently hold **26**.

**Elevation is not optional.** The whole analysis is elevation-banded — a station without elevation
cannot be used at all.

**⚠️ Long series, not real time.** DHM's public rainfall portal serves real-time aggregates only.
Collecting from it now yields **a couple of months before delivery**, and every use we have — diurnal
climatology, seasonality — needs **years**. If a partner offers live API access instead of an archive,
that is **the wrong shape** for this and worth saying so politely.

---

## What we do NOT need

- Radar. Nepal's one C-band radar (Surkhet, 2019, ~200 km) does not usefully cover our central/eastern
  basins; Palpa and Udaipur are planned only. **Not on the critical path** (OD-9).
- Any downscaled NWP product *as a deliverable from us* — DHM has parallel projects for that, and we
  have deliberately chosen not to duplicate it (OD-10). If one of those projects produces something, we
  want to **couple to it**, which is a different conversation.
