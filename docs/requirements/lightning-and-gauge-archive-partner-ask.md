# Partner data asks — lightning strokes and the hourly gauge archive

**Status: NOT YET SENT.** Owner action. Recorded here because it kept getting lost in conversation.
**Milestone:** M-D4 (`docs/design/dhm-precipitation-milestones.md`). **Raised:** 2026-08-18.

Two separate asks. They will probably go to **different partners**, so they can be sent independently.

---

## Ask 1 — Lightning (stroke-level)

> **What we need**
> Stroke-level lightning detections for **2020–2025**, over **26–31 °N, 80–89 °E**.
>
> Per stroke: **UTC timestamp, latitude, longitude, detection network.**
> Peak current and stroke/flash classification are welcome but **not** required.
>
> Any **one** of **WWLLN**, **GLD360** or **ENTLN** is enough — we do not need all three.

**Which partner.** **WWLLN** is a research consortium and is usually free to academic members — the
likeliest yes, so ask there first. **GLD360** (Vaisala) and **ENTLN** are commercial: what matters is
whether a partner already **holds a licence**, not the price.

**Why, if they ask.** Not for rainfall amounts — for **timing**. Lightning is the only convective-timing
indicator that is **independent of precipitation retrieval**, so it carries neither gauge undercatch nor
the orographic-retrieval error that degrades satellite products over the Himalaya. Timing is precisely
the disputed quantity: ERA5-class models put the Himalayan foothill precipitation peak up to **~12 h**
wrong, and Nepal v1 ships a **3-hourly** product, so a 12 h error is **four timesteps** of displacement.

**Why the box matters.** 26–31 N / 80–89 E is deliberately the **same box as our ERA5-Land acquisition**,
so the data drops straight into the existing elevation banding with no reprojection.

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
