# Plan 047 — Nepal v1 data sources (ECMWF IFS, DHM, ERA5-Land, elevation bands)

**Status**: DRAFT (stub)
**Phase**: v1
**Depends on**: v0 complete

---

## Why this exists

Plan 046 references "Plan 047+" as the home for Nepal v1 data-source adapters
(see `docs/plans/046-mac-mini-staging-deployment.md:622`). Without a stub the
reference dangles. This file exists so the commitment to Nepal data-source work
is captured in the registry ahead of v0 wrap-up; scope is intentionally minimal
and is filled in when the plan is promoted from stub to DRAFT.

## Scope (to be filled in when promoted to DRAFT)

- **ECMWF IFS NWP adapter** — operational forcing source for Nepal production
  forecasts (replaces ICON-CH2-EPS used in v0).
- **DHM station adapter** — real-time observation ingest for Nepal's
  Department of Hydrology and Meteorology network (replaces BAFU LINDAS).
- **ERA5-Land reanalysis adapter** — historical training-forcing source via
  the `WeatherReanalysisSource` Protocol (replaces CAMELS-CH used in v0).
- **Elevation-band NWP extraction** — hypsometric aggregation of gridded NWP
  into basin elevation bands, layered on top of the existing `GridExtractor`
  basin-average path.

## Owned elsewhere — do not re-open here

- **Basin geometry + static catchment attributes** are owned by **Plan 117**
  (`docs/plans/117-basin-static-artifact-architecture.md`, READY). SAP3 consumes a
  validated **basin/static** artifact package from an *adjacent* extraction tool and
  does not integrate that tool's code. Do not add basin delineation or
  static-attribute extraction to this plan's scope.
- **This stub is stale.** Before 047 is promoted to DRAFT/READY it needs its own
  **re-scope per Plan 106** — strip elevation-band extraction, the standalone
  ERA5-Land CDS adapter, and the DHM observation adapter, and align the exit gates to
  Plan 106 §0. Plan 117 unblocks only the basin/static piece; it does not make 047
  ready.

## Not in scope

- Anything v2 (multi-country generalisation, additional reanalysis products,
  non-ECMWF ensemble providers, satellite-derived precipitation).
- Changes to the v0 Swiss data-source adapters (LINDAS, STAC, CAMELS-CH) other
  than sharing Protocol surfaces.
- Rating-curve correction (separate open design item — see memory).

## Open questions (to resolve before promoting to READY)

- ECMWF IFS access route: MARS, Open Data, or institutional licence?
- DHM data delivery channel: direct feed, FTP, or intermediary?
- ERA5-Land extraction: CDS API on demand, or pre-staged archive?
- Elevation-band definition: fixed bands (e.g., 500 m), catchment-specific
  hypsometric tiles, or model-declared via `ForecastInterface`?

## Exit gates (sketch)

1. ECMWF IFS adapter implemented against `WeatherForecastSource` Protocol with
   live-fetch and replay paths.
2. DHM station adapter implemented against the observation-ingest Protocol and
   validated against a representative subset of DHM stations.
3. ERA5-Land adapter implemented against `WeatherReanalysisSource` and used to
   train at least one Nepal-basin model end-to-end.
4. Elevation-band extraction path produces banded forcing series consumed by a
   `GroupForecastModel` during a full Flow 1 dry run.
5. Nepal staging deployment on the Mac mini runs the full pipeline end-to-end
   against all four new data sources.
