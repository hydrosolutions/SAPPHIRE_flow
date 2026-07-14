# Nepal v1 — Internal Gap Analysis & Build Plan

> **Status: DRAFT** — internal planning artifact. Plan our own work before writing
> the two collaborator-facing documents. No subagent runs from this until promoted
> to READY.
>
> **Scope: Nepal v1 only.** HSOL operates the **eastern** region; DHM operates the
> **western** region (DHM performs model training and extends the tool to the west).
> The operational end state is **one shared local/on-prem deployment** that both orgs
> use, fed by the **SAPPHIRE Data Gateway** and **Snowmapper**. A **cloud deployment is
> transitional** — HSOL's bootstrap for developing/training east models — and is
> **retained as a staging environment** once production is live at DHM, after the
> initial model migration into the local instance (§decision #1b). **The Swiss
> deployment keeps its own self-extraction path (Flow 1 steps
> 1.2–1.4 active) and is unaffected by everything below.**

## 1. The requirement

HSOL develops and (initially, on the cloud) trains models for the **east** of Nepal.
DHM then operates the **west** on the **same local/on-prem deployment**: they add new
gauges (new shapefiles), get catchments processed automatically, and either **retrain
an HSOL east model** on new western data (cold, per #5) or **train a new model from
zero**. Making this west-extension easy is an explicit goal. Once the local
(production) deployment is live at DHM, the cloud server is **retained as a staging
environment**: HSOL's cloud-trained models are migrated into production, and the
cloud→local path becomes the ongoing **staging → production model-promotion channel**.

**Ownership (project duration):** HSOL owns the local deployment and the HSOL-trained
(east) models; DHM owns their own (west) models in the same instance.

Three upstream parties are involved:

- **SAPPHIRE Data Gateway** — receives basin shapefiles, performs NWP/reanalysis
  grid→basin extraction, delivers forcing. DHM adds new shapefiles here (Gateway v2).
- **Snowmapper** — produces snow forecasts for the **entire country**, delivered via
  the Gateway.
- **Model implementer** — owns the model contract (`ForecastInterface` repo).

We must (a) plan our own build, then (b) produce requirements for the Gateway
developer and the model implementer so they plan accordingly.

## 2. Decisions (from the 2026-06-11 interview)

| # | Branch | Decision | Bucket |
|---|---|---|---|
| 1 | Deployment topology | **One shared local/on-prem deployment** = production (end state). Whole-country AOI, single DB/API. East/west = `station_group` + `regional_basin`, not separate instances. **Cloud = transitional bootstrap, then retained as a staging environment** (not deprecated). | ours |
| 1b | Staging→prod model promotion | Initial bulk migration of HSOL's cloud-trained **model classes + trained artifacts** into local production, then an **ongoing staging→production promotion channel**. Hard requirement: **artifact portability** — deployment-independent artifacts; identical or remapped station/basin IDs across staging and production. | ours |
| 2 | Geometry source-of-truth | **RESOLVED (2026-06-11): SAP3 = full SoT.** DHM uploads geometry to **SAP3** (not the Gateway). SAP3 runs rigorous validation, then **uploads the validated GeoPackage to the Gateway**, which extracts and returns forcing as a wide-format DataFrame correlated to the submitted basin. No `gateway_basin_id` mapping — the Gateway's internal id is not exposed and not stored. **AMENDED 2026-07-14 (Plan 117):** static-attribute *extraction* is no longer necessarily ours — it may be SAP3-side, or delivered by an **adjacent** basin/static extraction tool as a validated artifact package (`04-basin-static-artifact-contract.md`). SAP3 owns validation, import, and provenance either way; the geometry SoT decision is unchanged. | ours + Gateway |
| 3 | Catchment auto-processing | Baselines/flow-regime automatically on new-basin add — **ours**. Static attributes (HydroATLAS/MERIT): **either** SAP3-side extraction **or** an accepted **adjacent** basin/static package (`04-basin-static-artifact-contract.md`) — see #2. **Elevation bands are NOT auto-generated** — see #3b. Pour-point snapping stays **manual**. | ours **or** adjacent tool |
| 3b | Elevation bands | **Demoted to nice-to-have.** When a model needs bands, the **uploaded shapefile must already contain band polygons**; we ingest them into `basins.band_geometries` and reject at onboarding if a model declares `ELEVATION_BAND` but the shapefile lacks them. DEM-based band generation is a future item, not on the critical path. | ours + Gateway |
| 4 | Model unit | **One model *class* → many per-group artifacts.** Model admin activates which artifact serves which `station_group`. Multiple conceptually-different models may be active at once, each with its own artifacts + groups (combination). | ours + modeller |
| 5 | Retrain semantics | **Cold retrain required** in the contract; optional `warm_start_from` slot for capable ML models. | modeller + ours |
| 6 | Snowmapper | **Banded dynamic forcing** (SWE + snowmelt) via the Gateway, declared by the model in `data_requirements`. | Gateway + modeller |
| 7 | Training history | **Gateway back-extracts** full historical ERA5-Land + historical Snowmapper, per band, on demand, for each new basin. Same pipeline as operational forcing. | Gateway |
| 8 | Retrain gate | **Initial onboarding keeps auto-promote.** *Retraining* (Flow 9) requires skill-compare + **human promote** via `PENDING_APPROVAL`. | ours |
| 9 | Access | **Authn + audit mandatory.** Authz coarse except operational **promotion**, scoped by `station_group` ownership (DHM promotes west, HSOL east; all read all). | ours |

**Through-line:** the *banded* path (NWP + Snowmapper forcing, and ingesting band
polygons from the shapefile) recurs across #3b/#6/#7. We do **not** generate bands —
they arrive in the shapefile and the Gateway extracts forcing against them.

## 3. Gap analysis — supported vs. build (Nepal v1)

| Capability | Architecture today | Gap for Nepal v1 |
|---|---|---|
| Shared deployment, whole-country AOI | Flow 0 AOI + `regional_basin` label exist (Plan 024) | None structural; needs whole-country config + station-group layout |
| Staging→prod model promotion (cloud→local) | Single-instance design; no cross-instance model export/import; staging concept exists (Plan 046) | **Build:** export/import of model classes + artifacts; deployment-portable artifacts; station/basin ID remap at import |
| Basin geometry store | `basins` table (PostGIS), `band_geometries` JSONB exists | **Build:** DHM upload + rigorous multi-polygon validation; upload validated gpkg(s) to the Gateway (one gpkg may hold many basins; each polygon keyed by a unique `name`, echoed back — no Gateway id mapping) |
| Static catchment attributes | `exactextract` + attribute derivation exist | **Build (Nepal):** accept a validated basin/static package from the **adjacent** extraction tool (`04-basin-static-artifact-contract.md`) — import + provenance, not extraction. SAP3-side HydroATLAS/MERIT wiring into Flow 0 stays the fallback path and remains the Swiss/v0 route. |
| Elevation bands | `band_geometries` field + `GridExtractor` `ELEVATION_BAND` spatial type specced | **Light build:** ingest band polygons from uploaded shapefile; onboarding check. (Gateway does the extraction.) DEM generation deferred. |
| NWP/forcing extraction | Self-extraction (Flow 1 1.2–1.4) for Swiss | **Nepal: skipped** — Gateway upstream. Consume pre-extracted banded forcing. |
| One class → many group artifacts | `GroupForecastModel` Protocol + `model_artifacts` (group_id) + `ModelCombinationStrategy` types exist | **Build (v0b deferred):** operational `GroupForecastModel` in forecast cycle; per-group artifact activation; pooled combination |
| Retrain on new data | Flow 6 initial training (auto-promote) exists; `PENDING_APPROVAL` status defined but unused; Plan 066 strategy | **Build:** Flow 9 (retrain + skill-compare + human promote). See Plan 066. |
| Snowmapper forcing | none | **Build:** consume banded SWE/snowmelt as dynamic forcing from the Gateway |
| Authn / authz / audit | Deferred (Plan 042); adjustment audit log exists | **Build:** authentication + audit on promotion; promotion scoped by station-group ownership |
| Model contract (ForecastInterface) | External repo; SAPPHIRE-side adapter (v0b) not built | **Build:** ForecastInterface adapter; contract additions (warm_start slot, banded snow features) |

## 4. Build list (ours) — priority order

1. **Forcing consumption from the Gateway (Nepal path)** — ingest pre-extracted banded
   NWP + Snowmapper forcing; skip Flow 1 1.2–1.4 for Nepal config.
2. **Geometry ingest into SAP3 (DHM upload → SAP3 → Gateway)** — DHM-facing upload
   entry (staged: minimal first), rigorous multi-polygon validation, static attributes
   (SAP3-side **or** from an accepted **adjacent** package — `04-…`),
   operational flip on our side, **upload validated gpkg(s) to the Gateway** (one gpkg may
   hold many basins, all of one kind) and consume the wide-DataFrame forcing it returns
   **per polygon** (catchment/band), each column keyed by a unique per-polygon `name`
   (SAP3's convention)
   that the Gateway echoes; SAP3 keeps the basin→(gpkg, `name`) map. **Risk:** single-polygon
   gpkg is untested on the Gateway — prefer the multi-polygon path or test it. (Decision #2
   resolved → unblocked.)
3. **Flow 9 retraining** — `PENDING_APPROVAL` artifact, skill-compare vs incumbent,
   human promote/reject. Builds on Plan 066.
4. **Operational `GroupForecastModel`** — execution in the forecast cycle, per-group
   artifact activation, pooled multi-model combination (deferred v0b pieces).
5. **Authn + audit + promotion scoping** — promotion restricted by station-group
   ownership; audit extends the existing adjustment log.
6. **Staging→prod model promotion** — export/import of model classes + trained
   artifacts cloud→local; deployment-portable artifacts; station/basin ID remap at
   import. Initial bulk migration, then ongoing. Ties to staging infra (Plan 046).
7. **Static-attribute datasets for Nepal** — accept a validated basin/static package from
   the **adjacent** extraction tool (`04-basin-static-artifact-contract.md`): import,
   validation, and provenance. SAP3-side HydroATLAS/MERIT wiring into Flow 0 is the
   fallback and the Swiss/v0 path.
8. **Band-polygon ingest** — read `band_geometries` from the uploaded shapefile;
   reject onboarding if a model declares `ELEVATION_BAND` and bands are absent.
9. *(Nice-to-have, deferred)* DEM-based elevation-band generation.

### Additional build items (2026-06-18 — integration-fit review + gateway answers)

Folded in after the SAP3⇄Gateway integration-fit review and the gateway dev's answers.
Several need careful planning, not a quick patch.

- **Gateway HRU reference in gauge metadata.** The fetch endpoint takes the **gateway HRU
  (gpkg) file name**; SAP3 must store that name with each gauge so it knows which HRU(s) to
  call, and map returned columns back via the per-polygon `name` (extends build item #2).
- **Training-readiness / coverage gate (highest risk).** Decouple onboarding (Flow 5) from
  in-band training (Flow 6): the new-basin first fetch carries a **coverage block** (period
  available per dataset/band); training MUST assert covered-span ⊇ requested period (temporal
  coverage, not just column presence) before starting. Add an `AWAITING_FORCING`/coverage
  concept. Without this, manual back-extraction risks silently training on truncated history.
- **Operational fetch orchestration.** Run **every 3 h**, fetch the *latest available*
  forecast; loop control + 50 perturbed members (`ifs_type` cf/pf) and assemble; concurrent
  fetch (`task.map`) + client-side retry/backoff (the client has none); revisit deploy
  `concurrency_limit=1`.
- **`NWP_DELIVERY` watchdog (staleness model).** Wire an emitter for the defined
  `PipelineCheckType.NWP_DELIVERY`: alert when the *latest available* forecast is staler than
  a configured threshold (≈ 6 h cycle + ~7–8 h open-data delay + margin); thresholds in
  config, not constants. Distinguish stale delivery from a basin out-of-coverage (G18).
- **Canonical variable namespace.** Introduce one `WeatherVariable` source of truth + a
  source-name map (ERA5 `total_precipitation` / IFS `tp` ↔ SAP3 canonical ↔ FI names), and
  **reconcile the two unreconciled adapter-local maps** (`PARAM_GROUPS` in
  `adapters/meteoswiss_nwp.py`, `_FI_UNIT_TO_CANONICAL` in `adapters/forecast_interface.py`).
  *Internal inconsistency — needs investigation + careful planning, not a piecemeal patch.*
- **`forcing_type` handling.** SAP3 self-tags provenance by endpoint (`era5_land_reanalysis`
  → reanalysis, `ifs_forecast` → nwp) — no Gateway tag needed as long as we never use the
  `operational()` lag-fill blend. Add a `forcing_type` field where skill/series keying needs it.
- **gpkg export / DR.** A writer that serialises validated basin + band polygons to a gpkg
  with the `name` keys, reproducible from SAP3 DB state (the manual web upload consumes it).
- **Manual runbooks.** Document the gpkg web-upload and historical back-extraction procedures
  (gateway dev documents their side; G17a).

### Geometry validation (SAP3-owned) — what "rigorous" covers

Per the agreement with the Gateway dev, SAP3 — not the Gateway — does rigorous GeoPackage
**multi-polygon** validation before forwarding (build item #2). It MUST check at least:
container/CRS (GeoPackage, EPSG:4326), geometry type ((Multi)Polygon, 2-D, no
GeometryCollection), **OGC validity** (`ST_IsValid`: no self-intersections, closed rings,
sane orientation, no duplicate/collinear vertices, no slivers), multipart/holes handling,
coordinate extent within AOI, **elevation bands** (within parent basin, non-overlapping,
required `band_id` + elevation attributes), attribute schema, and empty/null rejection —
failing loudly with a specific error.

## 5. Resolved decision — geometry source of truth (#2): SAP3 = full SoT

**Decided 2026-06-11** *(static-attribute clause amended 2026-07-14, Plan 117)*. DHM
uploads basin geometry to **SAP3** (not the Gateway). SAP3 runs rigorous validation,
obtains static attributes (SAP3-side extraction **or** an accepted **adjacent**
basin/static package — `04-basin-static-artifact-contract.md`), assigns
`sapphire_basin_id`, then **uploads the validated GeoPackage to the Gateway** via API; the Gateway extracts and returns
forcing as a wide-format DataFrame correlated to the submitted basin (no reprojection, no
re-snapping → no drift). The Gateway's internal basin id is **not exposed and not stored**
— no `gateway_basin_id` mapping is needed.

**Rationale (total-cost-of-ownership, not just upfront cost):**
- *Cross-deployment consistency* — matches the Swiss pattern (geometry enters SAP, SAP
  validates, SAP is SoT). One shared ingestion/validation/onboarding path across all
  deployments; Nepal only adds the forward-to-Gateway step. The rejected alternative
  (DHM → Gateway → SAP3 pull-by-hash) is a Nepal-only ingestion fork.
- *Minimal Gateway dependency* — narrows Gateway reliance to the one thing only it can
  do (forcing extraction). No dependence on the Gateway dev's (currently very poor)
  validation; the authoritative geometry lives with us, not at the Gateway.
- *Asymmetric risk* — over-investing here is a bounded one-time cost; under-investing
  means re-architecting ingestion mid-deployment across an org boundary in a live flood
  system. For a system HSOL owns operationally, weight the recoverable side.

**Staging (keeps v1.0 lean):** ship SAP3 validation + forward path first (needed
anyway); the DHM-facing upload entry starts minimal (HSOL-assisted or a plain endpoint)
and is polished later.

**Rejected alternative:** DHM → Gateway → SAP3 pull-by-hash (Gateway does a thin
structural gate). Lower upfront build for us, but forks the ingestion path and bets the
data-quality gate on an external team we don't control.

**Contract line-item:** the Gateway MUST extract forcing against the exact geometry
bytes SAP3 provides — no reprojection, no re-snapping.

## 6. Next steps

1. ~~HSOL team resolves decision #2.~~ **Done — SAP3 = full SoT (§5).**
2. Promote this DRAFT to READY.
3. Draft `01-data-gateway-requirements.md` (Gateway developer) and
   `02-forecast-interface-requirements.md` (model implementer → ports to the
   `ForecastInterface` repo). **← in progress.**

## 7. Cross-references

- `docs/architecture-context.md` — Flow 0/5/6/9, Data Gateway notes, artifact scope
- `docs/plans/047-nepal-v1-data-sources.md` — Nepal data-source adapter scope (feeds this)
- `docs/plans/066-train-models-retrain-strategy.md` — retraining strategy (Flow 9)
- `docs/v0-scope.md` §I — v1 compatibility guardrails
