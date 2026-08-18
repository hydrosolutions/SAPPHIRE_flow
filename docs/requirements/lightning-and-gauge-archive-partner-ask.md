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

### ✅ FEASIBILITY MEASURED 2026-08-18 — access works; the SCIENCE case is weaker than the access case

Probed with the owner's Earthdata login (`~/.netrc`, `urs.earthdata.nasa.gov`). **Everything technical
works.** The limits are scientific.

| Check | Result |
|---|---|
| Earthdata credentials | ✅ work — CMR search and GHRC download both authenticate |
| ISS-LIS **netCDF** granules | ✅ readable with our existing stack (`h5py` — netCDF4 is HDF5 underneath). Flat layout; flashes are `lightning_flash_lat` / `_lon` / `_TAI93_time` |
| ⛔ LIS/OTD **climatology** (`.hdf`) | **DOWNLOADED (672 MB) BUT UNREADABLE.** Magic `0e031301` = **HDF4**, not HDF5. `h5py` cannot open it, and our GDAL/`rasterio` build has no HDF4 driver. Would need `pyhdf` + the HDF4 C library — a **system** dependency, not just a wheel |
| Granule volume | **3,554 granules per JJAS season** matching our box (~1,777 netCDF), ~2.4 MB each ⇒ **~4 GB/season, ~17 GB for 2020–2023**. Granules are per-ORBIT, so a spatial filter matches the orbit but you still download the whole file |
| **Flash density (measured)** | 5 granules → **748 flashes globally → 6 in 26–31 N / 80–89 E (0.80 %)** |

**Extrapolated yield: ~2,100 flashes per JJAS season in-box, ~8,500 for 2020–2023.**

### ⚠️ The honest verdict: adequate for the FOOTHILLS, probably too sparse for GROUP A

- Across **24 hour-bins** alone: ~350 flashes/bin — **workable**.
- Across **24 hours × elevation bands** — which is the entire point — it falls to **tens per bin**, and
  the distribution is heavily weighted to the Terai.
- **Compounding physical problem: lightning is a proxy for CONVECTIVE precipitation, and high-altitude
  Himalayan precipitation is largely SNOW, which produces very little lightning.** The method is
  therefore weakest **exactly where Group A's unresolved anomaly sits** (2,860–3,700 m).

⇒ **Lightning can validate the documented FOOTHILL phase error** (Hunt et al.: foothills peak ~0300 IST
while ERA5 puts it mid-afternoon) — which is a real and useful result, since that is the error M-A7's
correction must fix. **It cannot settle the high-altitude question.** M-A10's co-located gauge pair
remains the instrument for that.

**Cost/benefit before committing:** ~17 GB of transfer yields a few hundred KB of in-box flashes — a
~0.005 % yield — because the granules are per-orbit and cannot be server-side subsetted through the
plain download route. **Worth it for the foothill validation; not worth it if the high-altitude band was
the motivation.** Owner call.

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
