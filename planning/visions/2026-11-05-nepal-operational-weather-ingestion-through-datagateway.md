# Nepal operational weather ingestion through DataGateway

## Outcome

Daily Nepal forecasts use DataGateway's combined operational weather feed to bridge
the roughly 6–7-day ERA5-Land publication lag with older IFS forecasts. The native
weather-ingestion path must deliver usable precipitation and temperature for the
current forecast's required dates without manual weather stitching. Explicit
configuration selects this route. Swiss weather products and routes stay unchanged.

This is a standalone vision, not an implementation plan or authorization to deploy.

## Confirmed design

- Deployment configuration explicitly selects the operational weather route. Station
  bindings select the source, product, role and extraction. Do not infer routing from
  country names or introduce a globally hardcoded DataGateway route.
- Provider-specific processing remains responsible for its actual format, units,
  timestamps, cadence, accumulation/aggregation and provenance. Model-requirement
  assembly follows that processing; it must not guess provider semantics or change
  the scientific meaning of precipitation totals or temperature means.
- For current daily forecasts, ingest the combined operational feed through the
  existing native orchestration boundary. ERA5-Land remains authoritative where it
  is available; IFS bridges its recent unavailable tail. Preserve the distinction
  between reanalysis and forecast-derived weather, including the actual forecast
  issue/source cycle and valid time. Do not relabel old IFS as observed or fresh data.
- An unusable refresh, whether a transport failure or an incomplete payload, may reuse
  complete, valid stored weather covering every required date. Mark affected forecasts
  **STALE** and retain the failure and the weather's real age/provenance. Never replace
  complete cached coverage with an incomplete payload. Cached rows do not turn a failed
  refresh into a fresh successful delivery. Retained failure detail must exclude
  credentials and credential-bearing prepared request URLs.
- Missing required precipitation or temperature blocks the affected forecasts.
  Partial days, missing intervals, invalid values and inadequate date coverage must
  not pass as complete weather. No invented filling, interpolation to make a check
  pass, or relaxation of model weather requirements. Unaffected forecasts retain
  their normal behavior.
- Preserve ERA5-only training history. Keep historical runs and hindcasts used for
  skill evaluation on their existing historical-data paths, with no future-data
  leakage. Do not extend the combined operational endpoint to those runs in this
  change. Operational forecast-derived tail data must not contaminate those reads.

## Current system and the gap

Deployed 0.1.1104 and main at `32adadfd2c40152e199e8192d568b3967fcaaf87`
route weather through separate paths:

- `flows/ingest_recap_era5_reanalysis.py`:
  `ingest_operational_era5_history_flow(start, end, weather_scope)` builds
  `RecapGatewayReanalysisAdapter`. Its `fetch_reanalysis` calls the SDK's
  `ecmwf.era5_land_reanalysis`, not the combined operational endpoint.
- `adapters/recap_gateway.py` deliberately removes forecast-fill rows from history
  ingestion before storage. That training-leakage protection must remain intact.
- `flows/run_forecast_cycle.py` builds `RecapGatewayForecastAdapter` for the configured
  forecast source. Forecast Phase A and candidate-aware fetching call
  `ecmwf.ifs_forecast` separately.
- `services/operational_inputs.py` reads stored reanalysis and uses
  `fill_past_forcing_tail` to bridge from stored IFS in memory. The approved
  `CONTROL_3H` path validates complete control-member days, preserves ERA5 precedence
  and limits eligible leads. This is local assembly, not ingestion from the combined
  Gateway operational feed.
- The pinned SDK already exposes `ecmwf.operational`, calling
  `exports/ecmwf/operational`. Its inputs include HRU, start date, ERA5 and IFS variable
  names, subdaily resolution and provenance selection. The repository caller found
  is `scripts/recap_probe_loop.py`, a diagnostic probe—not the native ingestion flow.

A method's presence in the SDK does not prove deployed ingestion uses it. Trace the
actual deployed Prefect entry point, configuration, bindings and adapter call when
qualifying the new route. Reuse established source and workflow boundaries rather
than introducing a parallel ingestion framework.

## Qualification and evidence of success

The combined endpoint's real response cadence, units, temporal/accumulation meaning,
coverage, member selection and provenance are **not yet qualified**. Establish them
from actual bounded responses and the provider contract before choosing conversions
or aggregation. Do not assume that its resolution options or output have the same
semantics as the existing three-hour IFS bridge. Investigate any conflict with
existing source-validity safeguards rather than silently removing them.

Success must be visible in native daily operation:

1. The explicitly configured route demonstrably calls the combined endpoint for the
   intended station bindings, stores or retains source-truthful weather through the
   native ingestion path, and, together with the appropriate future-forcing path,
   supplies the current model's complete required past and future daily precipitation/
   temperature windows. Future coverage from the combined endpoint is unqualified;
   do not assume it replaces the existing future-forcing path. Do not claim a longer
   horizon than the data actually cover. The currently active `cmal_small` model's 30-day past
   window is a concrete qualification case, not a global requirement for every model.
2. Provenance shows ERA5 precedence and identifies each IFS-derived part of the tail.
   Daily values follow the qualified source semantics, without double counting,
   partial-bucket acceptance or invented observations.
3. A failed refresh with complete valid cached coverage can still supply a forecast,
   visibly marked STALE. An incomplete payload has the same fallback rule. If required
   weather is still missing after checking valid stored coverage, block the affected
   forecast rather than masking the gap.
4. Tests and readback show unchanged Swiss routing/products, unchanged ERA5-only
   training and historical/skill-evaluation inputs, and no forecast-derived leakage.
   Weather readiness is reported separately from runoff readiness and forecast
   acceptance; complete weather alone does not establish a valid hydrological forecast.

## Boundaries

Daily forecasts only. Subdaily support is deferred. No new models, retraining,
runoff-gap tolerance changes, Aquacast changes or model-contract bypasses. At the time
of discovery, only `cmal_small` has an ACTIVE artifact and active assignment in the
Nepal runtime; installed entry points are not evidence of runnable alternatives.
Aquacast PR 144 is a separate declaration/flag change awaiting modeller review and
must not be absorbed into this weather work.

Keep tenant/station scope, permissions, source lineage and normal deployment
safeguards. This vision does not authorize live operations, release publication,
production deployment or merging. The owner retains those decisions.
