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

This folder holds six documents:

| Doc | Audience | Status |
|---|---|---|
| [`00-internal-gap-analysis.md`](00-internal-gap-analysis.md) | HSOL (internal) | DRAFT — decision #2 resolved (SAP3 = SoT) |
| [`01-data-gateway-requirements.md`](01-data-gateway-requirements.md) | Data Gateway developer | AGREED 2026-06-18 — interface reduced; most interactions manual for v1.0 |
| [`02-forecast-interface-requirements.md`](02-forecast-interface-requirements.md) | Model implementer | **Index** → live docs in the ForecastInterface repo (`docs/open_design_questions.md` + contract docs) |
| [`03-forecast-interface-adherence.md`](03-forecast-interface-adherence.md) | HSOL (internal) | DRAFT — SAP3-side FI adapter + gap analysis (grill-me 2026-06-17) |
| [`04-basin-static-artifact-contract.md`](04-basin-static-artifact-contract.md) | HSOL, DHM, basin/static extraction tool maintainer | DRAFT — file-based package contract for basin geometry + static attributes produced by an **adjacent** tool. SAP3 consumes and validates the package; it does **not** integrate, vendor, or call the extractor's code. |
| [`basin-static-extraction-brief.md`](basin-static-extraction-brief.md) | basin/static extraction implementer | DRAFT — send-ready short form of `04-…`: file shapes, naming rules, and the silent-failure watch-outs |

Produced from a structured requirements interview (2026-06-11), then extended as
new Nepal v1 collaborator interfaces were clarified. The internal gap-analysis is
the source of truth for the original requirements split; later package/interface
contracts document the currently agreed SAP3-side boundaries.
