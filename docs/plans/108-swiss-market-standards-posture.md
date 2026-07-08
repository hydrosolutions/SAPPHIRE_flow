# Plan 108 — Swiss market standards posture

**Status:** DRAFT
**Type:** v1+ standards / market-readiness plan (docs-first, low priority; no code)
**Owner:** Bea (marti@hydrosolutions.ch)
**Created:** 2026-07-08
**Priority:** Low
**Earliest:** v1+ / post-v1.0, unless a Swiss partner procurement makes one item contractual
**Relates to:** `docs/architecture-context.md`, `docs/standards/security.md`,
`docs/standards/logging.md`, `docs/standards/cicd.md`, `docs/standards/wmo.md`

> This plan deliberately keeps the Swiss standards posture out of the v1.0 Nepal
> critical path. It turns four Swiss partner-facing standards into explicit v1+
> decision gates and docs tasks, without implying that SAPPHIRE Flow should become
> a native GIS server, drinking-water risk-management suite, or certified W12 tool.

## 0. Architecture escalation

No immediate architecture change is required to document and plan for these four
standards.

Escalate before implementation if any partner asks for one of these stronger
claims:

1. **Native OGC service hosting** (`WMS`, `WFS`, `SOS`, SensorThings, OGC API
   Features, OGC API EDR) from SAPPHIRE Flow itself. That changes deployment,
   API surface, auth, caching, observability, and possibly the PostGIS schema.
   Preferred default: expose stable internal REST/PostGIS contracts and place an
   OGC gateway such as GeoServer or pygeoapi at the boundary.
2. **Native INTERLIS import/export or round-trip validation**. That requires an
   explicit geodata exchange boundary, model selection, CRS policy, validation
   tooling, and acceptance tests. Preferred default: spike existing Swiss tooling
   (`ili2db`, `ilivalidator`, GDAL/FME bridges) before writing any parser/exporter.
3. **SVGW W12-aligned drinking-water risk workflows**. That is product scope, not
   just technical compliance. Preferred default: document non-applicability unless
   the product is used for municipal drinking-water self-control or groundwater
   protection risk management.
4. **Formal nFADP/DSG compliance statement for authenticated users, logs, or
   partner operations**. That does not require a new architecture by itself, but
   it must be tied to auth/RBAC/audit, retention, backup, breach-notification, and
   hosting decisions before any public claim is made.

## 1. Evidence baseline

**INTERLIS**

Swiss geodata modelling and exchange standard; legally anchored in Swiss
geoinformation legislation since 2008. The practical baseline remains INTERLIS
2.3, with 2.4 metamodel material available. Source: <https://www.interlis.ch/en>.

Planning interpretation: relevant for cantonal GIS exchange, not a v1.0 blocker.
Treat as an export/import capability gate, not a core runtime dependency.

**SVGW W12**

SVGW lists W12 as the 2023 guideline for good procedural practice in drinking-water
supplies, with related certification rules W102/W103. Source:
<https://www.svgw.ch/shopregelwerk/vollstaendige-%C3%BCbersicht-svgw-regelwerk/>.

Planning interpretation: conditional relevance only. Do not build W12 workflows
unless municipal drinking-water self-control enters product scope.

**OGC standards**

Swiss federal geodata services publish WMS and related services, with EPSG:2056/LV95
and EPSG:4326 among supported CRS options. OGC now points implementers toward OGC
API Features for modern feature access, while WFS remains reliable but older. SOS
remains valid for sensor observations; SensorThings is the modern IoT/sensor API
option. Sources: <https://docs.geo.admin.ch/visualize-data/wms.html>,
<https://www.ogc.org/standards/wfs/>,
<https://www.ogc.org/standards/ogcapi-features/>,
<https://www.ogc.org/standards/sos/>,
<https://www.ogc.org/standards/sensorthings/>.

Planning interpretation: relevant for Swiss GIS interoperability. Prefer a clear
target matrix over blanket "supports WMS/WFS/SOS" language.

**nFADP / DSG**

Swiss companies have had to comply with the revised Federal Act on Data Protection
from 2023-09-01. It introduces privacy by design/default, processing-register
expectations, and breach-notification obligations. Source:
[Swiss SME Portal nFADP page][nfadp-source].

Planning interpretation: high relevance once auth/users/API keys/logs exist. Fold
into the v1 auth/RBAC/audit and deployment-hardening tracks.

## 2. Scope

### In scope

- Create an explicit Swiss privacy/data-protection standard for v1+ operations.
- Create an explicit geospatial interoperability standard covering CRS, output
  formats, OGC target posture, and INTERLIS decision gates.
- Record SVGW W12 as conditional product scope, with a clear non-claim unless a
  drinking-water supply workflow is actually commissioned.
- Add checklist items to downstream auth, logging, deployment, and data-exchange
  plans so agents do not accidentally make unsupported compliance claims.

### Out of scope

- No code implementation.
- No OGC endpoint implementation.
- No INTERLIS parser/exporter implementation.
- No W12 certification or Q-W12-equivalent workflow.
- No public compliance statement before the relevant READY implementation plans land.
- No change to the Plan 106 v1.0 Nepal critical path.

## 3. Phase A — nFADP / DSG posture

**Goal:** make the privacy posture explicit before authenticated Swiss or partner
users exist in production.

Tasks:

- **A1 — Draft `docs/standards/privacy.md`.** Cover controller/processor roles,
  personal-data categories, data minimization, purpose limitation, privacy by
  design/default, access rights, breach-notification owner, and processing-register
  inputs.
- **A2 — Reconcile log and retention docs.** Resolve the current IP-address logging
  ambiguity across `docs/standards/logging.md` and `docs/standards/cicd.md`, then
  define retention for users, API keys, audit events, Caddy access logs, pipeline
  logs, backups, and restore artifacts.
- **A3 — Feed the auth/RBAC/audit plan.** Add privacy gates to the future
  auth/RBAC/audit + tenant-write-isolation plan: scoped access, audit-event
  minimization, admin-account lifecycle, API-key metadata retention, and public
  privacy-notice inputs.

Exit gates:

- The docs distinguish v0 public-data/no-auth posture from v1+ authenticated use.
- No doc claims full nFADP compliance before the auth, logging, retention, backup,
  and hosting plans provide evidence.
- Any production deployment plan has an explicit breach-notification contact and
  personal-data retention table.

## 4. Phase B — OGC / GIS interoperability posture

**Goal:** define what "GIS interoperability" means for SAPPHIRE Flow without
turning the core API into a GIS server by accident.

Tasks:

- **B1 — Draft `docs/standards/geospatial-interoperability.md`.** Define canonical
  internal geometry/CRS rules, partner-facing CRS outputs (`EPSG:4326` and Swiss
  `EPSG:2056` where relevant), geometry validity expectations, metadata fields,
  and stable identifiers for stations, basins, alerts, and forecast products.
- **B2 — Define the OGC target matrix.** Decide which standards are "native",
  "gateway-backed", "export-only", or "not planned": OGC API Features, WFS, WMS,
  WMTS, SOS, SensorThings, WaterML 2.0, OGC API EDR, and STAC. Default posture:
  REST/PostGIS internally; gateway-backed OGC publication externally unless a
  partner contract justifies native service hosting.
- **B3 — Add discovery and export contracts.** Specify minimum partner-facing
  metadata: collection names, CRS list, time axis semantics, station/basin filters,
  forecast issue time, valid time, ensemble member identity, units, provenance,
  and license/source attribution.

Exit gates:

- Architecture docs say which geospatial standards are supported, delegated, or
  intentionally not supported.
- Swiss LV95 (`EPSG:2056`) is addressed for Swiss GIS partners without replacing
  the existing internal WGS84 assumptions blindly.
- Any OGC-serving implementation requires a separate architecture decision before
  a READY build plan.

## 5. Phase C — INTERLIS decision gate

**Goal:** keep INTERLIS partner-ready without making it a speculative dependency.

Tasks:

- **C1 — Inventory candidate exchange objects.** Stations, basin boundaries,
  catchment/AOI geometry, alert thresholds, and forecast product footprints are
  the initial candidates. Forecast time series are not automatically INTERLIS
  objects; they may belong better in WaterML/SensorThings/EDR exports.
- **C2 — Identify model ownership.** For any Swiss partner request, determine
  whether an existing federal/cantonal `.ili` model applies or whether a custom
  model would be required. Do not invent a SAPPHIRE `.ili` model before a partner
  use case exists.
- **C3 — Spike the tooling path.** Evaluate `ili2db` / `ilivalidator` / GDAL or
  FME-based conversion against a small PostGIS-backed fixture before implementing
  native import/export.

Exit gates:

- The docs state that INTERLIS is relevant for official Swiss geodata exchange,
  not for the operational forecast runtime.
- Any INTERLIS implementation plan includes model files, validation tooling,
  round-trip acceptance criteria, and CRS conversion tests.
- No native parser/exporter is built until existing Swiss tooling has been tried.

## 6. Phase D — SVGW W12 applicability gate

**Goal:** avoid unsupported W12 claims while preserving a path if a drinking-water
partner needs it later.

Tasks:

- **D1 — Add a short W12 applicability note.** Place it in the future Swiss
  standards doc or product-scope notes: W12 concerns drinking-water self-control
  and good procedural practice; SAPPHIRE Flow currently forecasts hydrological
  variables and alerts, not drinking-water quality-management workflows.
- **D2 — Define the trigger for W12 work.** W12 enters scope only when a municipal
  water-supply or groundwater-protection partner requests risk workflows, risk
  matrix traceability, control measures, or W12-style self-control evidence.
- **D3 — Predefine the architecture fork.** If triggered, create a separate
  product/architecture plan for risk objects, hazard scenarios, control measures,
  review/approval workflow, evidence exports, and certification/non-certification
  language.

Exit gates:

- Docs do not imply W12 or Q-W12 certification.
- W12 remains conditional and v1+ unless partner scope explicitly changes.
- Any W12 implementation is reviewed as a product-scope architecture change.

## 7. Phase E — Integration with existing plans

**Goal:** connect this posture to the active roadmap without disrupting it.

Tasks:

- **E1 — Keep Plan 106 unchanged unless a requirement becomes contractual.** This
  plan is v1+ market-readiness; it does not reorder the Nepal v1.0 waves.
- **E2 — Update plan index.** Track this plan as low-priority category C until a
  partner turns one of the items into a contractual requirement.
- **E3 — Add references when downstream plans are drafted.** Privacy gates belong
  in the auth/RBAC/audit plan and deployment-hardening plans. OGC/INTERLIS gates
  belong in the Nepal/Swiss data-source, onboarding, and API/export plans.

Exit gates:

- Plan 106 remains the authoritative v1.0 critical-path roadmap.
- Plan 108 is visible in the plan index as low-priority v1+ market-readiness.
- Architecture-impacting requests are split into their own plans before READY.

## 8. Review and readiness

Before this plan can move from `DRAFT` to `READY`:

1. Run the standard plan-review loop with an explicit proportionality lens. This
   plan should remain a low-priority standards posture, not an implementation epic.
2. Run one independent repo-grounded review focused on architecture creep:
   especially native OGC hosting, INTERLIS round-trip support, and W12 product-scope
   expansion.
3. Owner confirms whether this stays category C or moves into category B because a
   Swiss partner requirement became contractual.

## Dependency graph

```json
{
  "tasks": {
    "A1": {
      "title": "Draft privacy standard",
      "depends_on": []
    },
    "A2": {
      "title": "Reconcile log and retention docs",
      "depends_on": ["A1"]
    },
    "A3": {
      "title": "Feed privacy gates into auth/RBAC/audit plan",
      "depends_on": ["A1", "A2"]
    },
    "B1": {
      "title": "Draft geospatial interoperability standard",
      "depends_on": []
    },
    "B2": {
      "title": "Define OGC target matrix",
      "depends_on": ["B1"]
    },
    "B3": {
      "title": "Add discovery and export contracts",
      "depends_on": ["B1", "B2"]
    },
    "C1": {
      "title": "Inventory INTERLIS candidate exchange objects",
      "depends_on": ["B1"]
    },
    "C2": {
      "title": "Identify INTERLIS model ownership",
      "depends_on": ["C1"]
    },
    "C3": {
      "title": "Spike INTERLIS tooling path",
      "depends_on": ["C2"]
    },
    "D1": {
      "title": "Add W12 applicability note",
      "depends_on": []
    },
    "D2": {
      "title": "Define W12 trigger",
      "depends_on": ["D1"]
    },
    "D3": {
      "title": "Predefine W12 architecture fork",
      "depends_on": ["D2"]
    },
    "E1": {
      "title": "Keep Plan 106 unchanged unless contractual",
      "depends_on": []
    },
    "E2": {
      "title": "Update plan index",
      "depends_on": ["E1"]
    },
    "E3": {
      "title": "Add references when downstream plans are drafted",
      "depends_on": ["A3", "B3", "C3", "D3", "E2"]
    }
  }
}
```

[nfadp-source]: https://www.kmu.admin.ch/kmu/en/home/facts-and-trends/digitization/data-protection/new-federal-act-on-data-protection-nfadp.html
