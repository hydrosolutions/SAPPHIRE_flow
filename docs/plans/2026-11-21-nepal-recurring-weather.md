# Nepal recurring weather refresh and forecast polling

status: READY (Phase A only; Phase B remains DRAFT)
owner: Nepal orchestrator

## Outcome and scope

Owner requested recurring CHWRR ERA5 refresh and forecast polling starting at
first expected arrival, using docs/requirements/05-snowmapper-gateway-contract.md.
Live 0.1.1168 has an unscheduled fixed-window ERA5 deployment. Stored precipitation
and temperature end 2026-10-26; Gateway readiness succeeds. Existing observation,
climatology, forecast deployments, assignments and bindings must be preserved.

Ship ERA5 refresh first, separately from exact-cycle forecast orchestration. The
latter cannot be a cron-only change: selected daily weather requires midnight issue
times, while the upstream initialization can be 12Z; fallback is currently allowed.
Do not change daily aggregation, model horizons or FI contracts for scheduling.

## Phase A: bounded rolling ERA5 refresh

Add a separate refresh flow in flows/ingest_recap_era5_reanalysis.py, keeping the
manual fixed-window flow unchanged. Inputs: required weather_scope; window_days=30
(integer 1..31); lag_days=8 (integer 1..31); parameters defaults precipitation and
temperature. Compute once from UTC execution time through a pure injected-now helper:
end = midnight(today) - (lag_days - 1) days, exclusive; start=end-window_days.
Thus 21 Nov requests [15 Oct,14 Nov), through 13 Nov. A missed day is recovered by
the next overlap; outages beyond 30 eligible days require explicit manual backfill.
Reuse _run_operational_history, tenant admission, 31-day batch transactions, key loading,
idempotent historical store and readback. No watermark, full-history scan or new API.
All fetch errors retain the existing sanitized public exception (no URL/key leak).
Add refresh-only strict coverage mode at the existing store readback: compare the
exact finite hourly timestamp set for every admitted station and requested parameter,
filtering to basin-average, no band/member. Missing coverage raises a dedicated
sanitized exception before batch commit, so the whole refresh rolls back. Empty
station coverage fails. The manual flow keeps report-only behavior. The refresh
wrapper emits a distinct static coverage error; other exceptions remain generic.
Do not fabricate values or infer success from HTTP status. A later run retries the
whole bounded overlap. The 1..31-day window fits one transaction.

Register an opt-in new deployment refresh-operational-era5-history on ingest,
concurrency1, UTC cron, only when both SCHEDULE_REFRESH_OPERATIONAL_ERA5 and
SAPPHIRE_ERA5_WEATHER_SCOPE are supplied. Neither means no registration; exactly
one is a configuration error. Give the deployment explicit scope parameters;
leave all existing definitions and schedules unchanged. Extend DeploymentSpec
with immutable default parameters only if necessary; use the same registration
mechanism. Registration repeats preserve refresh parameters and operator-selected
parameter lists, but must reject scope conflicts rather than silently broadening.
Use tuple-pair defaults; existing scope must match, then retain operator parameters.
Validate cron before registration and set UTC explicitly for the new deployment.
Reject bool/coerced integer limits at the boundary. No compose default or host mutation in this PR. Document selective registration,
required environment, rolling bounds, recovery limitations and readback verification.

Tests: UTC/timezone equivalent instants, day/year/leap boundaries, invalid limits;
flow calls exact bounded window, preserves parameter list, sanitizes failures,
rejects missing coverage; deployment absent by default, paired env validation,
correct scope/pool/concurrency/UTC schedule and unrelated specs unchanged.
Use existing production flow tests for tenant and transaction semantics.

## Phase B: exact-cycle forecast polling (separate implementation plan)

Policy: 12Z baseline polling starts22UTC; optional00Z starts10UTC, off by default.
Every15min through init+14h, hourly thereafter until superseded by the next cycle
of that type. Explicit UTC initialization; restart-safe processing identity;
no substitution for exact-cycle requests; late and recovery events once each.
Model issue day remains independently represented. Inspect candidate resolution,
retained evidence, forecast persistence and Prefect idempotency before design.
A completed orchestration run is not proof forecasts exist. A 12Z source currently
has a shorter admitted horizon than 00Z; preserve insufficient-input refusal.
Snow availability only gates models requiring snow; no invented manifest support.
Independent review required before implementing this phase. Existing live forecast
and climatology schedules remain until replacement has proven equivalent coverage.

## Phase C: adoption

Owner merges each PR; use authorized release/deployment track. Refresh live state,
register only the new refresh deployment with existing forecast config/key mounts.
Proposed ERA5 cron 35 0 * * * UTC is a local refresh choice, not a claimed Gateway
publication deadline. Forecast polling separately follows first-arrival policy.
Obtain explicit one-off backfill approval before running Oct27..Nov13 manually.
The rolling refresh itself overlaps those days, so disclose that before activation;
the first rolling run is itself the backfill and needs that approval. If it
succeeds no separate manual run is needed. After 3 December the rolling window
no longer covers the beginning of the gap. Manual and rolling deployments have
separate concurrency limits; overlapping writes are idempotent but operators
should avoid simultaneous runs to limit provider load. Verify one successful ingest by DB coverage and repeat
station225 readiness; report weather_wait_facts and preserve other failing checks.

```json
{"phases":[{"id":"A","depends_on":[]},{"id":"B","depends_on":[]},{"id":"C","depends_on":["A","B"]}]}
```

Review: independent Claude review completed 21 Nov; five findings resolved above.
Orchestrator sets Phase A READY; Phase B requires a separate design/review.
