# Swiss CMAL forecast coverage and input quality

## Confirmed outcome

Improve Swiss `cmal_small` forecast coverage and provide trustworthy per-station
freshness, attempt status and proportionate input-quality information to the
separate forecast dashboard project. The owner confirmed this scope on 2026-11-07.
This is a standalone vision, not a Program Effort or an implementation plan.
Publication does not authorize implementation, deployment, historical reruns or
operational adoption.

Start with a bounded diagnosis, then fix confirmed causes and explain remaining
limitations. Do not turn this first stage into an exhaustive scientific investigation.
Inspect obvious risks in apparently usable forecasts as well as missing outputs;
missing forecasts remain visible and restoring justified coverage remains a priority.
Record deeper questions as a separate second-stage follow-up, with evidence and
consequences rather than silently assuming they are harmless.

## Scope and ownership

- Prioritize the Swiss daily `cmal_small` path. Preserve other models and regions;
  shared changes need regression evidence, not a wider feature programme.
- Publish usable results for unaffected stations even when other stations fail or
  are excluded. Do not relax model requirements, QC, authorization, or publication
  gates to reach a target station count. Respect genuine group-model dependencies.
- This repository owns the backend facts and API contract. The dashboard project
  owns colours, rings, layout and other visualization. A red ring was an example,
  not a prescribed UI design. No new notification system is included.
- No historical-event browser, hindcast campaign, new gap-filling algorithm,
  retrospective forecast rerun, model retraining, blanket performance optimization,
  backup repair, or BAFU danger-level/third-party ingestion work is included.
- Preserve stored provenance. Do not silently rewrite historical flags or forecast
  values. Existing/legacy evidence must remain identifiable; missing evidence is
  unknown, not healthy. Targeted future-run corrections may change future results
  when justified by the diagnosed cause and normal review.

## Independent status dimensions

For every authorized station in the applicable roster, expose enough information
for the dashboard to distinguish:

1. **Forecast availability/freshness:** current usable forecast, older forecast only
   (stale), or no usable forecast. A withheld/rejected result is not a usable forecast;
   preserve its existing safe publication status and explanation.
2. **Latest relevant attempt:** waiting, running, failed, completed with a usable
   result, completed without a usable result, or unknown. These are outcome meanings,
   not prescribed enum names or a new workflow engine. Reuse authoritative records.

Include the expected issue time, available forecast issue time when known, relevant
attempt timestamps and a concise reason. Associate attempt evidence with its issue
and station outcome; do not let an unrelated older attempt explain today's missing
forecast. Distinguish an expected but not started update from an attempt whose state
cannot be established. Absence of a forecast alone does not prove a model failure.
A scheduler's RUNNING label alone does not prove active work or a successful output.
A completed group attempt does not prove every station produced a usable result.

The two dimensions must coexist: an older forecast with today's run in progress is
**stale + running**, not current. A failed retry must not invalidate an otherwise
eligible saved forecast for the current issue. No status may reveal unauthorized
stations, rejected numeric values, raw secrets, or internal exception details.

### Freshness boundary: issue time, not a grace period

The owner explicitly chose: yesterday's forecast becomes stale when today's
scheduled issue time arrives, **not** when the run completes or its deadline expires.
The current daily issue contract is UTC midnight (`ScheduledDailyForecast.issue_time`).
Use the deployment's authoritative issue semantics rather than the browser's date.
At the next expected issue boundary, an older issue is stale even if its forecast
horizon still covers the future. A retry or refresh does not renew its issue time.

Expose freshness separately from input quality and publication eligibility. A current
forecast can have input limitations; a stale forecast can have sound original inputs.
Providing stale status or the older issue timestamp does not authorize rendering
otherwise withheld, expired or ineligible numeric curves. Preserve current selection
and numerical-retention safeguards; no silent fallback that presents an old issue as new.

## Proportionate input-quality reporting

Distinguish expected initialization, minor documented repairs, material input
limitations and unknown evidence. Report what is known, not an invented accuracy loss.

- Cold start alone must not imply degradation when authoritative model requirements
  and the selected configuration establish that it is normal. Missing required state,
  failed warm starts, fallback initialization or unknown policy must not be hidden.
- A small permitted repair, such as bridging one weather-data point seven days before
  issue time, must not automatically produce a severe degradation label. Record the
  repair and explain it proportionately. This example is not a universal seven-day
  exemption or proof that every single-point gap is harmless.
- Assess relevance using the model's actual consumed/required input window, gap age
  and extent, variable, and repair method where known. Preserve the distinction between
  a missing timestamp, a nonfinite value and a value repaired by an existing method.
- Minor repairs or expected initialization must never erase an independent problem,
  such as materially stale required inputs, insufficient required history, unchecked
  observation QC, unsuitable forcing, or failed output QC. Do not globally downgrade
  existing warnings or claim unchecked observations are QC-passed.
- Every remaining limitation needs a concise reason and relevant factual context
  (affected input, timing, extent or threshold where established). Unknown impact stays
  unknown. Do not invent model capabilities, numerical impact estimates, or per-model
  rules merely to remove warnings.

Diagnosis must establish which existing mechanisms already satisfy this intent.
Reuse them. Do not introduce new repair behavior under the guise of relabeling.
Models and the SAP3 adapter must obey ForecastInterface. Check the pinned upstream
protocol, input requirements, result types and docs before changing that boundary;
if required semantics cannot be represented, propose an upstream issue and co-design,
not a model-name allowlist or SAP3-only workaround. Anticipated model failures return
`ModelFailure`; `max_nan` remains a pre-predict gate, not a substitute for model checks.

## Starting evidence and source landmarks

These are a dated diagnostic baseline, not permanent acceptance counts or proof of
root cause. Read-only checks on 2026-11-07 against Swiss release 0.1.1115 found:

- 134 authorized dashboard stations; 131 current CMAL forecasts and three with
  `no_current_candidate`. That reason alone does not establish why output is absent.
- Saved standard, QC-passed CMAL forecasts: 132 stations on November 4 and 5, 131 on
  November 6 and 7. Daily attempts completed around 01:40 UTC. November 2 had incomplete
  attempts with no saved standard forecasts; November 3 had outputs but incomplete
  attempts. Reconcile station outcomes, saved references and attempts, not just totals.
- All 131 available forecasts carried cold-start degradation; 53 also carried
  unchecked-observation flags, and one also reported observation inputs 363.5 hours
  stale. These are emitted flags, not independently proven forecast-accuracy impacts.

Source inspection at `20444391946d6baae963ab3fc1afaf33218558eb` establishes:

- `src/sapphire_flow/services/input_quality.py`: `assess_input_quality` marks cold
  start degraded; `assess_past_forcing_gaps` already distinguishes recent gaps from
  older gaps (older gaps are PARTIAL). `past_forcing_flags` considers declared
  per-series lookbacks. These gap checks cover missing timestamps, not values already
  repaired upstream. Whether repair provenance is captured and propagated is a bounded
  diagnosis question; do not assume either that the example is already handled or that
  it requires a new mechanism.
- `src/sapphire_flow/types/daily_forecast.py`: UTC daily issue identity and attempt
  states. `types/daily_forecast_outcome.py` carries per-member outcomes and reasons;
  COMPLETE can include input/configuration exclusions alongside produced outputs.
- `services/daily_forecast_completion.py`, `services/daily_forecast_candidates.py`,
  `store/daily_forecast_store.py` and the daily forecast flow path are starting points
  for matching claims, attempts, roster membership and committed output evidence.
- `services/swiss_overview.py`, `types/swiss_overview.py`,
  `api/swiss_overview_schemas.py` and `api/routes/api_swiss_overview.py` define the
  current projection. Available forecasts expose input-quality flags; unavailable
  headlines do not currently provide the requested independent freshness/attempt view.

The earlier local input-degradation draft supplied background, but is not required
for this handoff and is not execution authority. Refresh evidence before implementation;
coordinate with concurrent weather/AquaCast work rather than duplicating or adopting
unreviewed fixes. No live mutation is authorized by the diagnostic examples above.

## Evidence of success

- A short diagnostic account explains the observed missing outputs and obvious
  input-quality issues as far as evidence permits. It separates targeted corrections,
  legitimate exclusions and unresolved second-stage questions. Do not force all
  stations to produce forecasts or hide unresolved stations from the roster.
- Targeted fixes restore coverage where justified. Per-station status accounts for
  every authorized station, including those still absent or excluded. Unaffected
  stations remain publishable under existing gates.
- Clock-controlled tests cover the instant before/at the next UTC issue boundary,
  stale + running, stale + failed, no prior result, current result + failed retry,
  partial group outcomes, and unavailable/legacy evidence. Completion deadlines and
  continuing horizons must not keep yesterday's issue current.
- Model-aware tests cover expected cold start, missing required state, unknown
  initialization policy, minor permitted old-gap repair, material recent input faults,
  and combinations where a minor condition must not suppress a serious independent one.
- API/consumer contract evidence demonstrates the separate status dimensions,
  timestamps and safe reasons without requiring dashboard UI implementation. Preserve
  existing clients or agree an explicit versioned contract with that project; update
  affected canonical schema/docs/examples as required by the implementation.
- Maintain authorization, no-store/retention, output QC, scientific provenance and
  existing overview cost safeguards. Do not add unbounded per-station scans simply to
  supply status. Verify shared-path compatibility with other models and regions.
- Follow repository review, test and owner merge/deployment requirements. Apply all
  AGENTS.md high-risk categories and its owner-commissioned relevant independent
  review requirement, in addition to independent Claude and Codex reviews. Missing
  reviews are not approvals. This vision does not prescribe schema migrations, new
  dependencies or a new status store.
