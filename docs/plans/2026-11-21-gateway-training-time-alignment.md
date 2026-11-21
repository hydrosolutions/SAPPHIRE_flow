---
status: READY
issue: 583
---

# Daily Gateway / ERA5-Land alignment

## Decision and evidence

Owner confirmed on 21 November that CMAL training uses daily ERA5-Land products
and approved preserving UTC-day aggregation, with sub-daily work explicitly deferred.
The prior radiation plan reversed the upstream convention: Gateway accumulations
are already differenced and start-stamped; raw ERA5-Land accumulations are end-stamped.

ECMWF's ERA5-Land documentation assigns the raw accumulation at D+1 00Z to UTC
day D. The Earth Engine DAILY_AGGR documentation likewise takes the following
midnight accumulation for flow bands and averages day D's hourly state samples.
Thus Gateway increments at D00..D23 sum to day D; no +1-hour shift is needed.
Daily radiation is summed interval energy / 86400 seconds. State timestamps stay
unchanged. Native physical bounds and original source_run remain unchanged.

Sources:
- https://github.com/hydrosolutions/recap-dg-client#timestamp-policy
- https://confluence.ecmwf.int/spaces/CKB/pages/140385202/ERA5-Land+data+documentation
- https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR

Independent assumption reviews by two agents confirmed that shifting native
OperationalWeatherSample starts or daily FI labels would break existing support
and model alignment. Aquacast consumes Nepal's precomputed daily products by date.
This decision does not establish the exact training artifact's upstream lineage.

A read-only Nepal probe of 20 November 00Z IFS control for tp/ssr/str returned
68 rows each: 48 three-hour differences followed by 19 six-hour differences,
ending 30 November 18Z. The requested fifteen days yielded eleven days. Preserve
missing-tail refusal; cadence and horizon are distinct, no fabricated support.

## Scope

Keep the existing physical daily reducers and FI boundary. Correct the admission
contract so native start-stamped Gateway radiation can be qualified without
claiming a nonexistent local timestamp correction. No host config, ingestion,
backfill, schedule, stored-data rewrite, model activation, or sub-daily shift.

## Phases

1. Independent Claude plan review; orchestrator marks READY after findings resolved.
2. Add `gateway_native_start_v1` to GatewayIntervalContract. Accept it for radiation
   with a qualification revision `gateway-docs:<40-lowercase-hex-commit>` identifying
   pinned upstream contract evidence; reject arbitrary/legacy revision strings.
   Retain `normalized_start_v1` and its serialized spelling for existing callers;
   both describe start-labelled physical support, neither applies a shift. Keep
   UNQUALIFIED as default. Retain existing config key names for compatibility.
   Existing radiation fingerprints already include contract and revision, so new
   native-contract captures cannot reuse legacy-attested radiation caches. Do not
   change precipitation/temperature fingerprints or old sample codecs because
   their interpretation and numeric values are unchanged.
3. Add regression tests through the adapter parser and daily reducer: last-hour
   precipitation/radiation pulse belongs to day D, next midnight belongs to D+1,
   source_run is retained, temperature stays instantaneous, hourly energy sum
   divided by86400, missing hour remains incomplete. Verify new qualification
   config parsing, missing-revision refusal, legacy qualification compatibility,
   and different radiation fingerprints. Existing IFS transition/final-step tests
   remain required. Include truncated-horizon refusal in focused regression scope.
4. Correct onboarding and Gateway documentation and annotate the prior radiation
   plan's superseded premise. Document the native contract/revision opt-in, UTC-day
   formulas, no extra differencing/shift, and deferred sub-daily timing review.
   Explicitly warn that daily approval does not qualify hourly/end-labelled model
   inputs or SnowMapper sub-daily forecasting. Keep source-run/coverage checks.
5. Independent Claude patch review, focused tests/lint/types, release-target bump,
   and PR. Full suite must pass after final code change in CI before owner merge.

## Acceptance

A qualified native Gateway response produces physically identical UTC daily
values to ERA5-Land daily totals, without changing labels or stored history.
Default unqualified radiation remains refused. Legacy contracts/evidence stay
readable, and native-qualified radiation fingerprints are distinct. No claim
that missing real weather coverage or the eleven-day tail satisfies CMAL's
requested horizon. Revisit endpoint/product durations, timestamp presentation,
query bounds and training support explicitly when adding sub-daily forecasting.

```json
{"plan_review": [], "implementation": ["plan_review"],
 "regression_and_docs": ["implementation"],
 "patch_review_ci": ["regression_and_docs"], "owner_merge": ["patch_review_ci"]}
```

## Review resolution

Independent Claude review completed 21 November; medium findings resolved before
READY. Native revisions identify a pinned Gateway documentation commit (reviewed
`6bcc6d949a33ddda042f844d36bc2710620a9ccb`); this is an operator attestation, not
a runtime proof of provider behavior or training lineage. Acquisition fingerprints
protect operational past/future caches only. Historical forcing retains original
source_run as version and does not store the qualification revision: archive the
operator qualification record before ingestion. Its physical interpretation is
unchanged, so no historical rekey/rewrite is needed or claimed. Add explicit native
flow-admission coverage, missing/malformed revision rejection and legacy compatibility.
