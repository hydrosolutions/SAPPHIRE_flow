---
status: READY
owner: orchestrator
created: 2026-11-20
base: bafe6826af73df1b848eec917eba5ec6686d52b3
---

# Net-radiation inputs for Nepal CMAL

## Outcome and authority

Make `solar_net_radiation` and `thermal_net_radiation` available as daily
basin-average mean fluxes in W/m² to the registered `cmal_nepal_static_encoder`,
through both historical ERA5 ingestion and selected operational history/IFS
forecast assembly. Qualify the exact supplied checkpoint with real inputs before
assigning it operationally. Preserve current `cmal_small`, climatology, existing
precipitation/temperature values, scientific evidence and publication boundaries.

R1–R4 are ready for implementation after independent review and re-review.
This is not permission to activate. The owner retains PR
merge, release publication and scientific/operational adoption decisions. The
implementer must refresh main, active plans and migration head before starting.
No code, database, deployment or model assignment was changed by this planning pass.

## Settled facts and boundaries

Owner clarification on 20 November is authoritative:

- The gateway exports energy accumulated over each output interval, already
  differenced; its operational product already stitches history and forecasts.
- Gateway accumulation timestamps mark interval **end**; model training uses
  interval **start**. A separate gateway-adapter correction owns that conversion.
  Implement this plan against its corrected, normalized interval contract. Do not
  implement another shift, deaccumulation or stitching algorithm here.
- Unknown training provenance is accepted for the MVP. PR 578 is merged at this
  plan's base. There is no dependency on a future FI provenance release and no
  request for more training-history dates. Import with explicit unknown metadata.

The unit register identifies ECMWF net-radiation energy as J/m². The required
mapping is:

| Canonical parameter | ERA5-Land variable | IFS variable | Stored/model unit |
|---|---|---|---|
| `solar_net_radiation` | `surface_net_solar_radiation` | `ssr` | `W/m²` |
| `thermal_net_radiation` | `surface_net_thermal_radiation` | `str` | `W/m²` |

Do not alias either to `radiation`, `global_radiation`, or downward radiation.
Preserve finite signed net values, including negative thermal values. Do not
apply precipitation's nonnegative constraint to radiation.

Read-only probes found both ERA5 channels for 1 November (24 hourly rows, 21
polygons) and both IFS control channels for 19 November 00Z (16 three-hour rows,
two-day sample, 21 polygons). Responses had source/source_run but no unit/interval
attributes. These are availability samples, not proof of a complete year or all
future steps/members. Private evidence is in
`/tmp/nepal-cmal-onboarding-20261120/`; do not commit provider data or credentials.

The supplied model has 365-day weather lookback, zero tolerated missing weather,
78 required statics, and at-most 15 future days with minimum one at the FI model
boundary. Static completeness and synthetic checkpoint loading/prediction have
already passed. Its exact YAML SHA256 is
`c171b2bc26a3cd11eec64378322fae443a113a86d1fcfb2fe6f5edd2e1e296d9`;
checkpoint SHA256 is
`d10a38983c3fbe93f2df0cf07171ca59a26b18c8bdf04500bbb85bd2c8fc60cb`.
Do not replace the model, checkpoint or YAML, retrain, or change their hashes.

Out of scope: timestamp-bug implementation, hydrological-day cutover, new
gap-filling algorithms, ensemble generation, FI protocol changes, training,
publication decisions, dashboard changes, station promotion and changes to
existing live forecast schedules. The GEE daily ERA5 adapter already has its own
net-radiation `/86400` contract; do not apply that daily conversion to subdaily
gateway rows or modify that independent adapter.

## Current code gaps

| Boundary | Existing behavior and required change |
|---|---|
| Catalog/conversion | `adapters/recap_gateway.py::RECAP_VARIABLES` lacks both channels; its scalar converter cannot express interval-dependent energy-to-flux conversion. `_ifs_variables()` feeds legacy unscoped fetching, so adding catalog rows must not automatically fetch radiation everywhere. |
| History | `flows/ingest_recap_era5_reanalysis.py::_ingest_history` hardcodes `types/nepal_onboarding.py::HISTORY_PARAMETERS` to precipitation/temperature for request, validation and coverage. |
| Operational history | `adapters/recap_operational.py` fetch/reduction loops and binding fingerprints hardcode two variables; `protocols/adapters.py` does not carry a requested channel set on history calls. |
| Typed admission | `types/operational_weather.py`, `types/operational_weather_v2.py` and `flows/operational_weather_prepare.py` allow only precipitation/temperature. |
| Daily projection | `services/operational_weather_projection.py`, `selected_weather_inputs.py` and `retained_operational_weather.py` use precipitation-or-else-temperature selectors. These would silently select temperature for a new parameter unless replaced. |
| Forecast assembly | `services/recap_daily_weather.py` rejects new channels. `operational_inputs.py::_aggregate_nwp_records_to_time_step` uses sample means, which are wrong for radiation at mixed interval lengths. |
| Registration | `parameters` is seeded by migrations and exposed by `store/parameter_store.py`; the initial migration contains only generic radiation. Deployment capability declarations default to precipitation/temperature. |
| Evidence/cache | Existing v1 and v2 retain generic parameter-tagged samples. Their exact bytes, digests, receipt scopes, retry hydration and CAS behavior must survive. |

Relevant routing: `docs/touchpoint-maps.md` sections ForecastInterface/model
execution, selected operational-weather route, persistence/API write path, and
training/hindcast/skill. Read the FI protocol, input requirement, result types and
model/input docs before reviewing the model boundary. FI already supports
`Unit.W_PER_M2`; no FI unit addition is necessary. Preserve returned
`ModelFailure` for anticipated model failures and the existing pre-predict NaN gate.

## Numerical and interval contract

1. At the corrected gateway boundary, radiation input is one finite energy `E`
   in J/m² over an explicit normalized half-open interval `[start, end)` with
   positive duration, original source and original source run. The row label used
   internally is `start`. Validate expected source cadence and horizon; do not
   infer a longer duration from a gap to the next row.
2. Convert exactly once: `mean_flux = E / (end - start).total_seconds()`.
   Persist native-step mean fluxes in `HistoricalForcingRecord` and
   `WeatherForecastRecord`, and retain mean flux plus interval_end in operational
   samples. Re-reading records never repeats the conversion.
3. A daily radiation value is `math.fsum(mean_flux_i * seconds_i) / 86400`, only when
   intervals tile the requested 24-hour period exactly once. Missing, overlapping,
   duplicated or boundary-crossing support is not a complete period. Do not
   divide a partial day's energy by its observed duration and call it a full day.
4. Preserve precipitation's existing interval sum and temperature's existing
   sample-mean behavior. Do not fix temperature weighting as a side effect.
5. For IFS stored forecast rows (which do not carry interval_end), recover duration
   from the same qualified source-cycle/lead schedule used for support validation,
   not adjacent surviving timestamps. Cover the 3h-to-6h transition and final
   horizon edge. ERA5 history is hourly under its normalized endpoint contract.
6. Units and intervals must be validated before a write. Bad normalization,
   unexpected cadence, absent source identity or nonfinite values fail through
   the existing adapter integrity/unavailability categories; never relabel energy
   as flux or substitute zeros.

### Internal normalization admission (before any radiation write)

No new gateway response attribute or service feature is required. Source cadence
and units are endpoint-contract facts, not metadata inferred from these responses.
The separate correction must normalize accumulated precipitation as well as net
radiation; this plan changes neither timestamp shifting nor precipitation values.

Use the existing `RecapGatewayConfig`/merged TOML mechanism for a scoped operator
attestation: add `normalized_interval_contract` parsed to a domain enum
`GatewayIntervalContract` with values `unqualified` (default) and
`normalized_start_v1`, plus `normalization_revision: str | None` (default None).
The qualified value requires a nonempty exact corrected-adapter release/commit
identity. Thread these typed values through all Recap adapter constructors,
including direct history ingestion and operational-window wrappers. Radiation
request admission refuses before fetching/converting/writing when either is
absent. Non-radiation requests retain their existing behavior.

This is an attestation, **not a transformer or proof produced by a flag**. Only set
it in the scoped Nepal configuration after the independently corrected adapter's
code revision and read-only boundary fixtures prove the normalization. Keep it
unqualified on today's uncorrected adapter. Implementation can use normalized
synthetic fixtures now, as the owner instructed; operational writes depend on the
separate fix. Qualification must compare known raw end labels with the corrected
internal starts at midnight, source handover, lead 144 and final lead, and record
that corrected revision. No setting may cause raw end-labelled frames to bypass
the corrected adapter path. Test constructor/composition-root propagation and
refusal on every radiation write path, not just selected-weather refresh.

The typed conversion seam takes canonical parameter, finite energy, normalized
UTC `start`, source, source_run and qualified contract; returns flux and explicit
`end`. Reuse `OperationalWeatherSample` for retained normalized values instead of
introducing another persisted wrapper. One schedule helper is authoritative for
both fetched and stored rows: ERA5 intervals are exactly 1h; IFS normalized start
lead is 3-hourly below 144h and 6-hourly from 144h; 00Z maximum end lead is 360h,
other admitted cycles 72h. Validate alignment and `lead + duration <= horizon`.
Deriving duration from this qualified schedule is allowed; deriving it from the
next surviving row across a gap is forbidden. Future records lack per-row revision,
so their radiation fingerprint includes the contract and normalization revision.

## Retained-format and cache strategy

Use the existing generic `samples` arrays; do **not** add radiation summary fields
to `OperationalWeatherDay` or `OperationalWeatherPeriod`. A shared typed accessor
derives each requested parameter's daily value, using existing stored summary
fields for the two old parameters and interval-weighted reduction for radiation.
This avoids a redundant source of truth and avoids injecting null fields into
historically hashed payloads.

- Extend `WeatherParameter` and matching validators with the two canonical names.
  Keep serialized v1/v2 field layout and canonicalization unchanged. New radiation
  sample entries use the existing `parameter`, `value`, `valid_time`,
  `interval_end`, `source`, `source_run` and identity fields.
- Use the explicit short radiation semantic-contract token `rad-int-v1` for
  the interval/unit convention (normalized start, native J/m² converted once). Put it in the existing sample contract-revision
  field for radiation and in radiation-enabled binding-fingerprint descriptors.
  Do not claim the old precipitation/temperature qualification revision proves
  radiation semantics. Validate the expected identifier by parameter. Preserve
  the old identifier and sample bytes for old channels.
- v1/v2 numbers continue to describe the existing UTC/declared-grid wire layouts;
  this is an additive parameter vocabulary with a new semantic-contract identity,
  not a rewrite of retained data. An old binary is not a valid reader of newly
  retained radiation samples. Document that rollout limitation explicitly.
  If review finds any actual wire-layout change necessary, revise this plan for
  a new envelope version before implementation; do not silently widen a hashed
  structure or discard v1/v2 readers.
- Fingerprints for the exact legacy two-channel requests remain byte-for-byte
  identical. Radiation requests hash a canonical sorted requested-channel set,
  source mappings, units, `rad-int-v1`, `normalized_start_v1` and the attested
  normalization revision, in addition to the
  existing station/polygon/source/member/grid identity. A two-channel snapshot or
  receipt cannot satisfy a four-channel request. Do not delete old entries.
- Collect the final union of each station's assigned requirements before building
  its refresh key. Thread that exact set through adapter batching, refresh specs
  and retained rollover. Batch by HRU plus requested channel set when necessary;
  a station/model not requesting radiation must not gain radiation dependencies.
- Extend matching past/future fingerprint helpers, including v2 wrappers and
  retained-future bindings. Preserve all exact source-cycle/member/request-scope
  checks, digest verification, retry evidence hydration and immutable captures.

### Explicit validation and retained-read obligations

Update `types/operational_weather.py`: `WeatherParameter`, sample parameter
allowlist, per-parameter expected-revision check (old revision unchanged for old
channels; `rad-int-v1` for radiation), interval-end requirement for all accumulated
parameters, and IFS end-horizon validation for radiation as for precipitation.
Keep temperature's no-interval snapshot rule. Extend `PastWeatherRequirement`,
`operational_weather_v2.py::FutureWeatherRequestV2` and `PastWeatherRequirementV2`,
and both admission checks in `flows/operational_weather_prepare.py`.

Day/Period have no stored radiation summary to cross-check. Their validators must
still reject radiation outside the period, duplicated support, overlap or retiming
that breaks source cadence; permit incomplete support only as missing data.
Requested-feature completeness is checked by the shared daily accessor at
projection/rollover/proof. Test dropped, duplicated and retimed radiation samples
both with stale digest (integrity refusal) and recomputed digest (domain validation
or missing-required-input refusal). Map reducer `ValueError` to existing integrity
or unavailable outcomes scoped to consuming models; no whole-cycle crash.

Current readers must round-trip both v1/v2 new samples and retain old bytes. Test
old-reader behavior against a new evidence payload: unknown radiation vocabulary
must surface an undecodable-evidence error, never silent omission. Old cache keys
remain isolated by fingerprints. Add exact old operational, future/member and
retained binding-fingerprint goldens, not just payload hash goldens.

## Implementation phases

### R1 — Parameters, interval conversion and history ingestion

Files: `adapters/recap_gateway.py`, `types/operational_weather.py`,
`types/operational_weather_v2.py`, `services/training_data.py`,
`flows/ingest_recap_era5_reanalysis.py`, relevant Nepal history types/CLI callers,
new next-head Alembic migration, `config/recap_gateway.py`, adapter construction
callers, `config/deployment.py` capability admission, and parameter/deployment docs.

- Add the two catalog mappings with source/canonical units and the owner-confirmed
  interval authority. Add a typed interval-aware conversion seam; scalar
  precipitation/temperature/snow converters remain unchanged. No `None` converter
  fallback may cause unconverted radiation to enter canonical records.
- Route reanalysis and IFS member accumulation through that seam only for radiation.
  Keep legacy unscoped `fetch_forecasts` at its existing two-channel ceiling;
  requirement-scoped `fetch_requirement` selects radiation from track features.
- Extend ERA5 history flow with an explicit requested-parameter option, defaulting
  to its current two channels. Thread it through requests, row validation and
  per-parameter coverage. Preserve the default `HISTORY_PARAMETERS` behavior for
  unrelated callers; add a supported catalog rather than globally changing every
  existing history operation. Reuse 31-day batches and the existing transaction
  boundary and sanitized failures.
- Add distinct `parameters` rows via an additive migration (next head after 0088
  at this base), weather domain, W/m² and mean aggregation. Refuse conflicting
  existing definitions. Do not alter generic radiation or replace existing rows.
  Downgrade must refuse if removing the names would invalidate retained forcing
  or evidence; no cascading cleanup. Concrete predicates: refuse if either name
  exists in `historical_forcing.parameter` or `weather_forecasts.parameter`, in
  retained operational-cache samples/requirements, or in any decoded forecast
  evidence snapshot (including archived future rows and selected weather samples).
  Inspect `forecast_evidence`/`forecast_evidence_blobs` through the bounded existing
  snapshot decoder; searching compressed blob bytes or only manifests is not
  sufficient. An undecodable/unavailable referenced snapshot also refuses downgrade.
  For a small safe migration, conservatively refuse downgrade whenever any retained
  operational cache or forecast evidence row exists rather than introducing a blob
  decoder into Alembic. Document this conservative predicate and test evidence-only
  and cache-only refusal even with empty forcing tables. Update schema guard tests.
- Declare both means in the aggregation register. This is sufficient for complete
  uniform-hourly history; it is not a substitute for interval weighting in IFS.
  Keep global/Swiss NWP capability defaults unchanged. Add the two names only to
  the scoped Nepal CMAL capability configuration/runbook after ingestion support
  is qualified, not merely because a model declares them.

Acceptance: known interval energy yields expected W/m² at 1h, 3h and 6h;
negative thermal survives; direct ERA5 reanalysis fetch/ingest round-trips both
names, source/version, station and hourly values; default request count and values
remain unchanged; migration does not rewrite old parameters or forcing.

### R2 — Requested operational channels and daily history projection

Files: `protocols/adapters.py`, `adapters/recap_operational.py`,
`flows/ingest_recap_era5_reanalysis.py` combined-window wrappers,
`flows/operational_weather_prepare.py`, `flows/operational_weather_refresh.py`,
`preprocessing/recap_operational_weather.py`, `types/operational_weather*.py`,
`services/operational_weather_projection.py`, `services/selected_weather_inputs.py`,
`services/retained_operational_weather.py`, and affected fakes.

- Thread validated parameter tuples from assignment requirements through the
  final per-station refresh specification to operational fetch/reduction. Keep
  default two-channel callers compatible; invalid/duplicate names fail early.
- Replace all four hardcoded fetch/reduce loops in `recap_operational.py`
  (currently around lines 414, 444, 496, 515), not only the parser.
- Fetch the radiation endpoint pairs only for requesting station scopes. Parse
  normalized intervals and retain the original source/source_run. Reuse bounded
  transport, provenance checks and station/polygon containment.
- Require interval_end for radiation just as for precipitation, validate the
  original source schedule/horizon, and allow finite signed radiation values.
- Add the shared daily accessor and replace every precipitation/else-temperature
  selection in projection, input assembly, rollover and retained-support checks.
  Reducer completeness must use the requested set, not an unconditional new
  four-channel ceiling. Legacy default completeness stays two-channel.
- Keep the existing gap-fill allowlist unchanged. Neither net-radiation channel
  may borrow `global_radiation` fills, temperature interpolation, precipitation
  bounds or donor semantics. Missing radiation remains missing and the model's
  existing zero-NaN requirement blocks that model; other compatible models continue.
- Apply the fingerprint/semantic-contract strategy above. Test requirements union
  before key construction, including two models sharing one station and HRU.

Acceptance: four-channel history produces the exact daily model columns; missing
radiation blocks only consuming models; mixed source/interval support conserves
energy; rollover and stale-cache reuse recheck all requested channels; old two-channel
serialized bytes/digests and provider call sets remain exact.

### R3 — Future radiation, selected evidence and FI execution

Files: `services/recap_daily_weather.py`, `services/operational_inputs.py`,
`services/selected_weather_inputs.py`, `services/operational_weather_proof.py`,
`services/selected_weather_evidence.py`, `store/operational_weather_codec.py`,
`store/weather_period_wire.py`, selected evidence/capture/store/API readers and
their schemas only where the vocabulary is enumerated. Include
`services/track_resolution.py::_station_complete`, `track_assembly.py` and
`track_projection.py` in the consumer audit: preserve candidate walk-back and
full-horizon completeness with the new features. Verify the FI feature-name
mapping passes both exact names through. Keep the ICON-only branch in
`operational_inputs.py` and `_FILLABLE_PARAMETERS` unchanged and regression-test
both. `adapters/era5_land_reanalysis.py` and `services/era5_land_backfill.py` retain
their independent existing contracts, including their deferred-radiation scope.

- Admit radiation into complete-period support validation with existing
  source/cycle/member/cadence rules. Preserve native converted rows in stores and
  archive receipts; aggregation must not change what the receipt attests.
- Under `FutureWeatherPolicy.RECAP_COMPLETE_DAILY`, reduce radiation records per
  parameter/member/period using qualified interval durations. Feed daily values
  into the normal model-frame path without averaging them a second time. Preserve
  the old aggregator for precipitation/temperature and unrelated weather policies.
  Reject radiation on an unqualified duration path rather than guessing weights.
- Continue to bind the model frame to the same raw-record receipt; prove future
  radiation values from its complete raw support. Preserve exact member identity,
  cycle selection, station boundaries, archive conflicts, CAS and retry behavior.
- Read old and new samples with current code, preserve all old golden hashes,
  and add new radiation round-trip/tamper tests for both UTC v1 and declared-grid
  v2 evidence. Update any API enum/schema snapshot and map-contract version when
  its public surface changes; do not add radiation output to discharge forecasts.
- Use the existing FI W/m² mapping and unchanged model declaration. Assert exact
  dynamic feature names, station-code mapping, daily timestamps and no NaNs at
  `predict`; no broad FI adapter workaround or anticipated-failure exception path.

Horizon boundary: the model declares AT_MOST 15/minimum 1, but the selected-route
assembly currently demands its full requested future window. Do not silently
change receipt/request horizon policy in a radiation patch. Qualify with a complete
15-day issue-day 00Z source-cycle window: `first_period_start` is exactly that
UTC midnight and equals the retained past window's `end`. Issue/evaluation time
may be later that day, but must admit this source run under its cutoff. Normalized
starts span leads 0 through 354; lead 144 is a 6h interval, and day 14 uses
336/342/348/354, ending at 360h. A previous-day cycle, or a first-period start one
day after the cycle, yields only 14 supported days and legitimately refuses the
current full-window request. Add a 15-period `FutureWeatherRequestV2` plus receipt
and proof test with the exact past/future seam, leads 0, 144 and 354; reject a raw
end-stamped final row at 360. Record any
shorter-window refusal accurately. General selected-route AT_MOST relaxation is
a separate plan if the owner later needs it; no one-day sample proves 15-day
operational readiness. Preserve the model's own minimum-horizon tests.

### R4 — Regression, bounded capacity and qualification runbook

Write the operator steps in `docs/operations/model-onboarding.md` and update the
gateway requirements/unit-register and relevant touchpoint/spec documents.
Supersede older “basin averaging only / start-stamped gateway” text with the
owner clarification and explicit separately owned normalization dependency;
do not overwrite the separate fix's implementation or claim it deployed.

Capacity gate **before production implementation**: retain all native hourly
radiation support in past evidence, alongside native precipitation/temperature;
no lossy daily replacement is permitted. Run a bounded synthetic wire-size probe
using the existing real codecs for a valid two-channel template and canonical
serialization of the proposed additional samples with `rad-int-v1`. Label this
as projected new-vocabulary size, not a successful new-validator round-trip.
Measure a 365-day four-channel station plus 15-day rollover, longest admitted
identity strings, complete 15-day future receipts, and the complete single-model
capture envelope. After R3 repeat with actual new typed objects/codecs, including
v1/v2 and the actual group capture shape; projection is not final evidence.

Decision rule: proposed scoped Nepal `max_cache_bytes` is **16 MiB** per past
snapshot, only if the projected 380-day snapshot is <=12 MiB (25% headroom).
Keep the global 2 MiB default unchanged. The full uncompressed selected-evidence
capture, including all station sources and future rows, must be <=24 MiB under
the unchanged 32 MiB ceiling; compression does not excuse excess decoded size.
Apply the gate per actual capture (a group capture may contain several stations),
not by dividing its total by the station count. If either threshold is exceeded,
stop and escalate before implementation/activation respectively; do not raise
limits, drop support, split an immutable group capture ad hoc or change scope
silently. This plan does not promise that a multi-station group capture fits.

Record bytes and peak memory for sequential preparation; do not introduce more
concurrent station preparation. Enforce existing 64 MiB response bound and all
cache/evidence decoder limits. Actual response or memory-budget exhaustion is a
qualification refusal requiring an explicit resource decision. The runbook must
record host memory headroom before selecting concurrency. No global limits are
raised by this plan.

Run targeted tests below, changed-module type checks, Ruff/pre-commit, and the full
offline suite after the final patch (locally or CI). Arrange the required
independent patch review; keep reports local. Open a focused PR with the current
single `RELEASE_TARGET` patch bump. The owner merges and publishes release identity.

### R5 — Authorized release and real-input activation evidence

This phase is an operator checklist for the orchestrator after the implementation
is merged and a matching release is qualified; it is not executed by a planner or
by an implementer treating this DRAFT as authority.

1. Refresh live images, station/binding state, schedules, model assignments and
   cache policy. The last refresh showed healthy 0.1.1159; 0.1.1161 predates CMAL
   registration. Use a release containing registration, unknown provenance and
   radiation support, plus independently corrected timestamp normalization.
2. Before enabling the scoped normalization attestation or any radiation write,
   prove normalization is actually deployed using known raw/end and internal/start bounds
   at midnight, source handover and cadence transition. Treat absent correction as
   a specific rollout dependency; do not shift radiation locally a second time.
3. Through scoped existing ingestion, load only missing radiation history and
   verify 365 consecutive daily values for all four weather channels per target
   station. Include the recent historical tail and stitched operational period.
   Count usable support per parameter/day, not just earliest/latest timestamps.
4. Fetch/retain the qualified 00Z future window and verify source cycle, requested
   control/member set, all required daily steps, flux units and finite values.
   Verify bytes/memory budget and no new request or data dependency for old models.
5. Import the exact checkpoint through the normal restricted import route with
   `training_provenance="unknown"`, preserving hashes/operator/import time. Run
   isolated real-input qualification first. Check group/station-code coverage,
   declared missing-discharge behavior, deterministic seeded repeatability,
   supported quantile output, finite values and FI result/failure contract.
   Do not claim independent skill while training exposure is unknown.
6. Propose the bounded assignment/schedule change using normal services and the
   owner's operational authorization. Preserve all current cmal_small and five
   climatology artifacts/assignments; station 450 is not automatically promoted.
   Trigger one forecast, verify stored lineage and authenticated API list/detail
   (model_id, data_use, issued_at, status, tier, quantiles and weather evidence).
   API availability is not publication approval; no map-colouring/alert override.

Rollback: disable only the new model assignment/schedule via supported services,
retain artifacts, radiation records and evidence, and preserve the current models.
Do not roll back to a reader unable to decode retained radiation evidence or
unknown provenance. Restore a compatible release/config or forward-fix; never
delete historical evidence to make a downgrade possible.

## Required verification matrix

| Concern | Acceptance tests / existing test homes |
|---|---|
| Exact conversion | `tests/unit/adapters/test_recap_gateway_variables.py`, `test_recap_gateway.py`, `test_recap_operational.py`: 360000 J/m² over 1h, 1080000 over 3h, 2160000 over 6h each become 100 W/m²; invalid/zero duration refused; negative thermal retained; no second conversion after store/read. |
| Daily weighting | `test_recap_daily_weather.py`, preprocessing and selected-input tests: two 3h intervals at 100 plus three 6h intervals at 200 produce 175 W/m², not the 160 sample mean; complete hourly day agrees with energy/86400; a missing interval never widens its neighbor; boundary overlaps/refusals. |
| Existing behavior | Precipitation totals, temperature values, old default request count, forecast member behavior, snow paths, GEE daily-radiation conversion and cmal_small synthetic execution unchanged. |
| Scoping and failure isolation | `test_selected_weather_scope.py`, `test_operational_weather_refresh.py`, adapter/projection tests: mixed two/four-channel assignments, shared HRUs, missing polygon/variable/source metadata, duplicate rows, candidate fatal vs unavailable, radiation absent for nonconsumers. |
| Historical persistence | `test_ingest_recap_era5_reanalysis.py`, PostgreSQL history/store tests: requested channels only, canonical names/units, valid range/source/station checks, transaction failure and rerun idempotency. Migration rows and downgrade refusal; update pinned schema heads. |
| Cached evidence | `test_operational_weather_codec.py`, cache/proof/retained/evidence tests: prechange v1/v2 golden bytes and hashes unchanged; four-channel fingerprint differs; two-channel cache cannot pass; radiation value/interval/identity tampering fails; retry/restore uses exact retained values. |
| Native execution/API | `test_selected_weather_fi_cycle.py`, `test_selected_weather_native_rating.py`, integration selected-weather/store/API/role tests: all four columns reach FI and discharge quantiles remain correctly labelled; standard/provisional isolation, authorization and no-event alert classification preserved. |
| Capacity and live readiness | Real-codec 365-day plus rollover size fixture; scoped limits remain enforced; runtime worksheet lists each station/channel/day and 15 future days, original cycle/member identities, exact artifact/config hashes and observed API results. |

Tests must assert outcomes and contracts, not duplicate implementation loops. Use
synthetic interval data for unit tests; keep real provider/model data private.
Full-suite success is required before merge, not inferred from focused checks.

## Dependencies and handoff

```json
{
  "R1": [],
  "R2": ["R1"],
  "R3": ["R1", "R2"],
  "R4": ["R1", "R2", "R3"],
  "R5": ["R4", "owner_merged_release", "timestamp_normalization_deployed"]
}
```

One implementer owns R1–R4; do not parallelize edits to the shared weather types
and codecs. No further owner answer is needed about accumulation, timestamps or
unknown training provenance. Remaining evidence to obtain during execution:
the separate normalization change's revision/deployed identity, measured cache
capacity gate results, actual 365-day/all-channel completeness and a complete future cycle.
Those are verification tasks, not invitations to invent missing facts or to
change the model contract. Independent review must settle this DRAFT's additive
wire-vocabulary strategy before the orchestrator marks it READY.

## Independent review disposition (revision 2)

- B1: internal typed/configured admission now precedes every radiation write;
  qualified cadence supplies bounds. No external marker/service delivery is added.
  Schedule derivation is permitted; gap-based duration inference is not. Timestamp
  implementation remains separately owned, assumed fixed for development fixtures.
- B2: explicit sample/requirement/period validators, retained-read behavior and
  stale/recomputed-digest tamper tests specified; legacy revisions/bytes unchanged.
- B3: issue-day 00Z, exact past-end/future-start seam, leads 0/144/354 and legitimate
  older-cycle refusal specified with a full request/receipt test.
- B4: missing track consumers, all adapter loops, capability and FI name admission
  added; ICON, fills and independent GEE/backfill contracts explicitly preserved.
- B5: native interval support retained; short token fixed; projected pre-code and
  actual codec capacity gates have scoped 16 MiB/32 MiB ceilings and stop rules.
  The projected station gate passed before implementation; see measured results below.
- Other concerns: one cadence authority for fetched/stored rows, `math.fsum`,
  fingerprint goldens, scoped failures and conservative evidence-aware downgrade
  refusal incorporated above. Independent re-review completed; see readiness disposition below.

## Readiness disposition

Independent Claude re-review returned READY, with no remaining actionable
blockers (local reports under `/tmp/cmal-radiation-review`). The orchestrator
accepts the revised design and marks R1–R4 READY. Capacity checks remain mandatory
and R5 remains a separate qualified rollout. The review's statement that the owner
personally selected the attestation design is imprecise: the owner settled the
timestamp semantics and scope; the planner proposed this implementation detail.

## Implementation measurements

Integrated main through `46f5f8bf` (tenant-aware onboarding). Actual typed v1/v2
380-day native-hourly station payloads: 11,708,471 / 11,730,261 bytes; sequential
Python allocation peak 47,329,297 bytes. Both pass the station gate. Six-station
history alone is 70,250,826 bytes, above the unchanged capture ceiling. R5 group
activation is therefore not qualified; a separate capacity decision is required.
No native support was dropped, no global limit changed, no live setting enabled.
Local measurement evidence: `/tmp/cmal-radiation-review/capacity-typed.json`.

Independent patch review prompted fixes to UTC candidate aggregation policy,
adapter integrity-error mapping and missing-value handling, per-assignment
unqualified-input refusal, and constructor propagation. Added regressions cover
those paths, shared-station requirement unions, exact FI names/signed inputs,
history parameter scoping, all four legacy fingerprints, and v1/v2 digest/domain
tampering. The complete 15-period request/receipt/proof has an exact past/future
seam. Actual pre-change v1 and v2 readers were exercised against new payloads and
both rejected the new vocabulary without silently omitting it (local evidence:
`/tmp/cmal-radiation-review/legacy-reader-check.txt`). No old reader is permitted
for rollout after new evidence exists. Full Linux CI remains the merge gate;
macOS temporary-checkout tooling tests refused untrusted path ancestors.

## Authorized capacity follow-up

The owner authorized raising the group evidence ceiling on 20 November.
`2026-11-20-cmal-evidence-capacity.md` supersedes this plan's 24/32 MiB group gate
with 96/128 MiB qualification/absolute limits. The 12/16 MiB station-cache gate,
timestamp qualification and real-input activation checks remain unchanged.
