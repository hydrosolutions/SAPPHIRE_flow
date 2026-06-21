# Nepal v1 — Collaborator Integration Requirements

**Scope: Nepal v1 deployment only.** Every decision in these documents assumes the
Nepal topology — HSOL operates the eastern region, DHM operates the western region,
and both run on **one shared local/on-prem production deployment** fed by the
**SAPPHIRE Data Gateway** (NWP + reanalysis extraction) and **Snowmapper** (snow
forcing). A **cloud server** is HSOL's transitional bootstrap for east models and is
**retained as a staging environment** once production is live at DHM, with a
staging→production model-promotion path. **The Swiss deployment does its own data
extraction and does not use the Data Gateway — none of these requirements apply to
it.**

This folder holds four documents:

| Doc | Audience | Status |
|---|---|---|
| [`00-internal-gap-analysis.md`](00-internal-gap-analysis.md) | HSOL (internal) | DRAFT — decision #2 resolved (SAP3 = SoT) |
| [`01-data-gateway-requirements.md`](01-data-gateway-requirements.md) | Data Gateway developer | AGREED 2026-06-18 — interface reduced; most interactions manual for v1.0 |
| [`02-forecast-interface-requirements.md`](02-forecast-interface-requirements.md) | Model implementer | **Index** → live docs in the ForecastInterface repo (`docs/open_design_questions.md` + contract docs) |
| [`03-forecast-interface-adherence.md`](03-forecast-interface-adherence.md) | HSOL (internal) | DRAFT — SAP3-side FI adapter + gap analysis (grill-me 2026-06-17) |

Produced from a structured requirements interview (2026-06-11). The internal
gap-analysis is the source of truth; the two collaborator docs are derived from it.
