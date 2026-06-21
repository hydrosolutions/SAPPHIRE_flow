# SAPPHIRE Data Gateway — Integration Requirements (Nepal v1)

> **Status: AGREED (2026-06-18)** — baseline agreed with the Data Gateway developer.
> The Nepal v1 interface is deliberately minimal: **only operational forcing fetch is an
> automated API; geometry registration and historical back-extraction are manual/supervised
> for v1.0** (see the §1 table). A few items remain pending test/confirmation (§8). This
> supersedes the earlier elaborate draft; the separate HTTP-contract doc (`04-…`) was
> scrapped as over-engineered against this agreement.
> **Audience:** SAPPHIRE Data Gateway development team.
> **Scope: Nepal v1 only.** The Swiss deployment does not use the Gateway and is out of
> scope. Throughout, **SAP3** = the SAPPHIRE Flow forecasting system (HSOL/DHM).
>
> Requirement keywords **MUST / SHOULD / MAY** are used in the RFC-2119 sense.

## 1. Context & the agreed division

In Nepal v1, SAP3 runs as one shared on-prem production deployment (HSOL east, DHM west)
plus a cloud staging instance. The Gateway sits upstream and does **grid → basin forcing
extraction** (NWP, reanalysis, Snowmapper). SAP3 does **not** extract gridded forcing
itself in Nepal.

**Agreed division (with the Gateway developer):**
- **SAP3 owns basin geometry and its validation.** Each `sapphire_basin_id` is associated
  with a GeoPackage. DHM uploads geometry to SAP3; SAP3 performs **rigorous GeoPackage
  multi-polygon validation** + static-attribute extraction.
- **SAP3 uploads the validated GeoPackage to the Gateway via API; the Gateway extracts and
  returns the forcing as a wide-format pandas DataFrame**, **one series per polygon** in the
  gpkg (a gpkg may hold several catchments and/or elevation-band polygons). Operational +
  historical.

So the Gateway is **not** asked to validate geometry — it processes a GeoPackage SAP3 has
already validated, extracting against exactly those bytes. **Addressing:** the forcing-fetch
endpoint takes the **name of the gateway HRU (gpkg) file**; SAP3 therefore stores that HRU
file name alongside each gauge's metadata so it knows which HRU(s) to fetch. Within an HRU,
each returned series is keyed by the per-polygon **`name`** attribute SAP3 set (§2 G5), which
SAP3 maps back to its `(gauge, band)`. The Gateway exposes no internal id; SAP3 stores no
Gateway-id mapping.

```
ONBOARDING (per basin)
  SAP3 ──validated gpkg (upload)──▶ GATEWAY       response correlated to the
        (Gateway: minor validation,                submitted basin; no shared
         extract against exact bytes)              basin-id namespace

OPERATIONAL (every cycle)                HISTORICAL (on demand, per new basin)
  GATEWAY ──forcing (wide DataFrame)──▶ SAP3   GATEWAY ──historical forcing──▶ SAP3
  NWP + Snowmapper, basin & per-band           ERA5-Land + historical Snowmapper, per-band
```

### What we need as an API — automated vs manual

SAP3 already detects missing data on its own side: the watchdog (`ops/watchdog.py`, Flow 4,
`PipelineCheckType.NWP_DELIVERY`) raises an ops alert when forcing doesn't arrive. So the
Gateway does **not** need to expose health/status to us. Only one interaction must be a
fully automated API; the rest can be manual for v1.0.

| Interaction | Frequency / actor | Programmatic API? |
|---|---|---|
| **Fetch operational forcing** | every 3 h, automated | **Required** — SAP3 runs every 3 h and pulls the latest available forcing (outbound HTTPS, API key). |
| Register basin geometry (validated gpkg) | per onboarding, supervised | **Manual web upload available now**; a programmatic upload client MAY be added later (not a priority). |
| Trigger historical (ERA5/Snowmapper) back-extraction | onboarding/retraining, supervised | **Manual for v1.0** (operator triggers on the Gateway); programmatic async API is a later enhancement. |
| Gateway health / liveness | on SAP3 alert, human | **Not required.** SAP3's watchdog detects absence; a DHM sysadmin checks the Gateway directly. Needs only a human-checkable surface on the Gateway + a runbook on our side. |

## 2. Geometry intake (onboarding)

SAP3 sends a GeoPackage it has already validated; the Gateway processes it as-is. Rigorous
multi-polygon validation is **SAP3's responsibility** (see `00-internal-gap-analysis.md`).

- **G1.** SAP3 submits basin geometry via the Gateway's API as **GeoPackage (`.gpkg`)**,
  CRS **EPSG:4326**, geometry **(Multi)Polygon**, 2-D, OGC-valid.
- **G2.** A basin MAY include **elevation-band polygons** (one feature per band, tagged
  with `band_id`). The Gateway MUST NOT generate bands itself (see §4).
- **G3.** The Gateway MUST extract against the **exact geometry bytes** SAP3 submits — no
  reprojection, simplification, re-snapping, or buffering (guarantees no drift).
- **G4.** The Gateway performs **only minor structural validation** (file readable /
  parseable). On a parse failure it MUST return a clear error.
- **G5.** A submitted gpkg MAY contain **several polygons** (multiple catchments and/or
  band polygons). Each feature/polygon must have a unique `name` attribute (text,
  lowercase). A feature can have basin polygons and/or band-elevated polygons.
- The naming convention is up to the SAP3 implementer.

#### Gateway-side validation
1. GeoPackage format only
2. Readable and non-empty
3. CRS must be EPSG:4326
4. Layer name must start with a letter or underscore — e.g. "polygons" recommended, "00003" rejected.
5. At least one polygon feature
6. Each feature must carry attribut called "name" (text, lowercase)
7. attribute "name" values must be unique across all features

## 3. Operational forcing delivery

- **G7.** For every forecast cycle, the Gateway MUST deliver, for all registered basins,
  the forcing variables required by the active forecast models:
  - **NWP:** ECMWF IFS ENS (51 members). Variable list driven by model
    `data_requirements` — at minimum total precipitation and 2 m temperature; the full
    list is to be fixed with the model implementer (see `02-forecast-interface-…`).
  - **Snowmapper:** snow water equivalent (SWE) and snowmelt.
- **G8.** Forcing MUST preserve the **ensemble members** (not pre-reduced to a mean) so
  SAP3 can run ensemble forecasts.
- **G9.** Spatial granularity MUST be **basin-average and, where bands are defined,
  per elevation band** (§4).
- **G10.** Forecasts follow the ECMWF IFS cycles (00/06/12/18 UTC) and become available on
  the Gateway **~7–8 h after production time**. SAP3 does **not** wait on a per-cycle
  deadline: it runs the forecast **every 3 h** and **fetches the latest available** forecast
  each run. The watchdog (§7) therefore checks **staleness of the latest available forecast**,
  not per-cycle lateness.
- **G11.** Within the returned wide-format DataFrame, each value MUST be identifiable by feature attribute "name" value
  (via the G5 key), variable, valid time, lead time, and ensemble member.

## 4. Elevation-band extraction

- **G12.** The gateway extracts all the features in the geometry, whether they are basin polygons or band-elevated polygons. From gateway's perspective, they are all the category and only unique distinction is the feature's attribute "name".


### Projection / CRS (zonal extraction correctness)

- **G13a.** The Gateway MUST NOT **destructively resample the raster** to match the
  polygons. It MUST extract on each source's **native grid**.
- **G13b.** **Web Mercator (EPSG:3857) MUST NOT be used for area-weighting** — it is
  conformal, not equal-area, with latitude-dependent area distortion that biases
  basin-average and (especially) per-band weighting for large Himalayan basins. If a
  single common CRS is unavoidable, it MUST be an **equal-area** CRS appropriate to Nepal.
- **G13c.** CRS/grid handling MUST be **identical across all sources** (IFS, ERA5-Land,
  Snowmapper) and across **historical and operational** extraction, so residual bias is
  consistent between training and inference. *(SAP3's own path extracts on the native
  EPSG:4326 grid via `exactextract` — matching that is ideal.)*
- **Resolved (2026-06-18).** The Gateway uses **EPSG:4326 throughout** (rasters + geometry),
  matching SAP3's `exactextract` path — no EPSG:3857 in area-weighting. G13a–c satisfied.

## 5. Historical back-extraction (training data)

This is the requirement most easily overlooked and is **load-bearing**: without it, DHM
can add a basin but cannot train or retrain a model there.

- **G14.** On registration of a new basin (or on explicit request from SAP3), the Gateway
  MUST be able to **back-extract the full multi-year historical forcing series** for that
  basin/band:
  - **Reanalysis:** ERA5-Land (hourly, multi-variable).
  - **Historical Snowmapper:** SWE/snowmelt over the same period.
- **G15.** Historical extraction MUST use the **same pipeline, variables, spatial
  granularity, and output format** as operational forcing (G7–G12), so a model trained on
  history and run operationally sees consistent inputs.
- **G16.** Historical output MUST be distinguishable from operational forcing (a
  `forcing_type` / provenance tag of `reanalysis` vs `nwp`) so SAP3 can interpret skill
  correctly.
- **G17a (trigger — manual for v1.0).** Back-extraction is on-demand and long-running
  (minutes–hours) but **rare and supervised** (onboarding/retraining only). For v1.0 it MAY
  be a **manual operation**: an operator triggers it on the Gateway, then SAP3 fetches the
  result once available (same wide-DataFrame, per-polygon format — G11/G15). A **documented
  procedure** is required.

## 6. Coverage / Area of Interest

- **G18.** The Gateway holds the AOI and the gridded sources. On geometry intake, the
  Gateway SHOULD report whether the basin lies **within NWP / reanalysis / Snowmapper
  coverage**, and flag (not silently truncate) any basin partly outside coverage.
- **G18a (readiness via coverage block).** Because back-extraction is manual with no status
  API, the **first fetch** for a newly back-extracted basin carries a **coverage block** (the
  period actually available per dataset/band). SAP3 reads it to gate training: training MUST
  NOT start until the covered span ⊇ the requested training period. This is SAP3's readiness
  signal in lieu of a programmatic status API (ties to G17a).

## 7. Reliability, signalling & failure modes

- **G20.** SAP3 already detects late/missing delivery on its own side (watchdog Flow 4,
  `NWP_DELIVERY` check), so the Gateway is **not** required to expose a programmatic
  health/status API. Instead it MUST provide a **human-checkable status surface** (dashboard
  or logs) a DHM sysadmin can inspect, and an agreed expected per-cycle **delivery
  deadline** so SAP3's watchdog threshold is calibrated. *(SAP3 side: a runbook tells the
  sysadmin where to check when an alert fires.)*
- **G22.** Because the Gateway manages the NWP archive in Nepal, SAP3 will **not** run its
  own NWP gap-recovery (Flow 11). The Gateway therefore SHOULD define how it handles and
  reports its own archive gaps.

### v1 known limitation — no tenant isolation (accepted)

- **G23 (accepted limitation).** For v1.0 the Gateway authenticates with a single shared
  API key and does **not** partition tenants: any caller holding the key can read any
  registered basin's forcing (HSOL-east and DHM-west are not isolated on the Gateway). This
  is **consciously accepted for v1** given the shared on-prem deployment and the rare,
  supervised write/registration path. SAP3 keeps the key secret and does not expose the
  Gateway to external consumers. Revisit if multi-tenant read isolation becomes a
  requirement.

## 8. Open questions for the Gateway developer

1. **Forcing payload layout** — the response is a wide-format pandas DataFrame via API;
   confirm how ensemble members, lead times, and bands are encoded as columns/rows, and
   that members are preserved (not pre-reduced). Answer: **yes**.
2. **Response correlation** (G5) — confirm the per-polygon key SAP3 supplies is echoed in
   the returned DataFrame, so each series maps back to `(sapphire_basin_id, band_id)`.
Answer: the geometry's feature attribute "name" value is considered as unique identifier.
3. **Single-polygon gpkg** — the multi-polygon (several-catchment) return path is the
   tested one; a gpkg with a **single** polygon is **untested**. Confirm it works, or
   whether SAP3 must always batch ≥2 polygons per upload. Answer: **most probably yes, we need to test it**.
4. **Geometry submission API** — exact endpoint, auth, payload, max basin/band counts per
   submission. Answer: **Manual web upload is available now (web interface). A programmatic
   upload client MAY be added later — not a priority. Max basin/band counts undefined; no
   issues so far.** SAP3 addresses an HRU by its **gpkg file name** at fetch time (§1).
5. **Historical back-extraction — manual procedure** (§5, G17a) — for v1.0, who triggers
   ERA5/Snowmapper back-extraction on the Gateway and how, and typical turnaround. (A
   programmatic trigger is a possible later enhancement, not required now.) Answer: **yes, we'll document the procedure**.
6. **Variable list** — confirm the NWP/Snowmapper variable set once the model implementer
   fixes `data_requirements`. Answer: **yes, but not a hard requirement because the gateway will support any variable set**.
7. **Snowmapper coverage** — country-wide, but are there elevation/spatial gaps relevant
   to specific basins? Answer: **not a question for the Gateway**.
8. **Versioning** — how are changes to the Gateway's extraction (e.g. NWP model upgrades)
   communicated, so SAP3 can tag forecasts/skill accordingly? Answer: **G16 gives a hint**.
9. **CRS / projection** (G13a–c) — what CRS/grid does the Gateway extract in today? Does it
   resample rasters, or extract on the native grid with reprojected polygons? Is Web
   Mercator involved anywhere in the area-weighting? Answer: **Resolved — EPSG:4326
   throughout** (rasters + geometry); matches SAP3's `exactextract` path, no EPSG:3857 in
   area-weighting (see G13 resolution).

## 9. What we explicitly do NOT need from the Gateway

- Rich shapefile/geometry validation (SAP3 owns it).
- Basin geometry as a source of truth (SAP3 owns it; the Gateway holds a copy for
  extraction only).
- A **programmatic health/status API** — SAP3 detects missing data itself; a
  human-checkable surface suffices (G20).
- A **programmatic async job API for historical back-extraction** in v1.0 — a manual
  trigger is acceptable (G17a).
- Any hydrological modelling, QC of observations, or alerting — all SAP3-side.

---

*Derived from `00-internal-gap-analysis.md` (decisions #1, #1b, #2, #3b, #6, #7). Pairs
with `02-forecast-interface-requirements.md`, which fixes the variable/feature list that
§3/§5 here depend on.*
