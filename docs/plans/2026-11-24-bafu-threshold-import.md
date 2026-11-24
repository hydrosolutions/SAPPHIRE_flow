---
status: READY
created: 2026-11-24
issue: 630
supersedes: 210
---

# BAFU thresholds, probability wiring, then evaluation

Owner sequence: import authoritative BAFU thresholds, wire per-time threshold
probabilities into existing skill machinery, then score replacement hindcasts.
Keep the same model-independent services for Switzerland and Nepal. This plan
makes the threshold import concrete; later milestones require their own bounded
implementation detail and review before execution.

## Reconciled requirements

Plan 210 is superseded for implementation, not evidence of delivered work.
Carry forward five BAFU levels with four lower bounds (2–5), no invented level-1
threshold, station-specific values/units, explicit provenance, and probabilities
alongside the existing forecast view. Do not use the plot ceiling as a threshold.
Its 40-station colour-inferred reference remains historical evidence; it is not the
canonical import source. No warning delivery or change to publication policy.

Plan 210 contains no additional statistical metric suite. Existing scope and
#454 supply Brier skill, POD/FAR/CSI, reliability and ROC. PR #631 intentionally
omits these for quantile forecasts until correct probabilities are available.
Retain that omission until milestone 2. Probability is per valid time/lead;
maximum probability over times is not a probability of an event during a window.

## M1: authoritative threshold import

Official station pages label all five numeric danger bands directly. Example:
https://www.hydrodaten.admin.ch/de/seen-und-fluesse/stationen-und-daten/2150
publishes discharge bounds 170/280/350/440 m³/s. Use explicit labels, not colours.

- Add a small BAFU source adapter using existing HTTP facilities and stdlib HTML
  parsing. Validate requested station identity, discharge unit, exactly five
  labelled bands, contiguous boundaries and finite strictly increasing values.
  Reject malformed/ambiguous pages; unsupported water-level units are not
  converted into discharge. No threshold is a distinct outcome from fetch/parse
  failure. Construct URLs only from validated numeric station codes on HTTPS
  www.hydrodaten.admin.ch; reject redirects. Bound request timeout and response
  size; no unbounded retries.
- Parse source data into immutable typed values. Keep source URL, retrieval time,
  page digest and the extracted bands in a preview/apply report. Use the existing
  StationThreshold/source=authority representation for canonical storage.
- A shared import service accepts a station and source capability. BAFU adapter
  selection belongs at composition boundaries; no Swiss-specific behavior in
  generic probability/skill code. Preview does not write. Apply compares existing
  rows, reports unchanged/conflict/missing/error/imported, and never silently
  overwrites a conflicting value or source. A station's four bounds are atomic.
  Add an insert-only conflict-checking store operation to the existing store,
  protocol and fake. Hold a short SHARE ROW EXCLUSIVE lock on the threshold table, re-read thresholds, and insert
  the full set in a real caller-owned transaction. The existing unconditional
  upsert is not safe for this import. The table lock also serializes legacy upserts without changing their overwrite
  semantics. Source retrieval happens before the transaction/lock.
- Expose an operator CLI following existing deployment-identity/run-principal
  checks, explicit tenant and station-code selection (or that tenant's BAFU
  roster). Resolve the principal from deployment configuration and an explicit tenant.
  Load every requested station and require station.tenant_id == principal.tenant_id
  before fetching or writing; test a cross-tenant code collision. Keep raw
  source snapshots and the report for review. Do not log secrets.
- Wire the same service into station onboarding before its skill step. Missing
  thresholds or source errors are reported but do not block ordinary onboarding.
  Existing-station backfill must not repeat station onboarding/training. Inject station_store into _make_skill_fn and replace its hardcoded empty
  thresholds with a store read. Existing compute-skills callers already read
  canonical thresholds; their results change only on explicit recomputation.
- Map BAFU numeric levels 2/3/4/5 to Moderate/Considerable/High/Very High
  for the current Swiss deployment; validate those exact configured names and
  ordered levels before importing. Generic import accepts the resolved mapping;
  another deployment supplies its own mapping explicitly. Do not guess by colour.
  Check danger-level keys against the configured deployment definitions, without
  changing any existing trigger probabilities or notification switches. Add no
  default constants for stations without published boundaries.
- Inventory alert and API consumers before live backfill. The reviewer flagged
  the lower-level observation checker as ungated; the caller actually gates it
  with enable_observation_alerts (ingest_observations.py), while the forecast
  caller gates enable_forecast_alerts. Host check found forecast alerts false and observation alerts true. The owner
  explicitly approved observation-alert records on 2026-11-24. Preserve both
  settings and notification behavior; keep the forecast-alert apply guard. The
  onboarding production composition must enforce the same import guard.
  Existing canonical station APIs expose the new authority rows; no overview
  publication policy change belongs here. Apply only after the
  approved code is deployed and the preview reviewed; unchanged notification
  settings must remain unchanged. User already requested the import; no extra
  product-scope permission is needed for preparation.

Tests: labelled page fixtures for river and unsupported lake/no-threshold cases;
wrong station, duplicate/noncontiguous/nonfinite bands and HTTP failure; preview
no writes; idempotence; conflict preservation; tenant rejection; whole-station
transaction; onboarding reuse; station API canonical read. Update relevant
operations/spec/touchpoint docs and require green final full CI before merge.

## M2: probability and metric wiring

Reuse existing representation validation and probability helpers after checking
quantile tails/ties and direction/equality semantics. Expose per-time probability
with model/artifact/threshold provenance; do not derive it from median or IQR.
Wire those probabilities into Brier score/skill, POD/FAR/CSI and reliability/ROC
without treating quantiles as equally weighted members. Undefined scores remain
undefined with counts. No broad new verification engine or automatic promotion.

Before implementation, resolve temporal meaning: current CMAL values are daily
means; BAFU danger bands must not silently be presented as instantaneous-peak
probabilities. Retrospective use of today's thresholds is labelled as such, not
historically effective thresholds. Keep threshold/forecast statistic explicit.

## M3: evaluation

Use new replacement candidate/donor hindcast identities and corrected observations.
Preserve artifacts and older results; no implicit retraining or activation.
Score only after M1/M2 are deployed and hindcast coverage verified. Report
station/lead/period/paired sample and event counts, missing thresholds, and known
availability/training-period limitations. Snapshot thresholds with evaluation
evidence. Insufficient samples/events are unavailable, not zero skill.

## Dependencies and review

```json
{"milestones":[{"id":"M1","depends_on":[]},{"id":"M2","depends_on":["M1"]},{"id":"M3","depends_on":["M2","replacement_hindcasts"]}]}
```

One independent Claude plan review and one independent patch review under the
owner-supplied workflow. Full review reports stay local. Orchestrator sets READY
only after plan review. Owner retains PR merge and production deployment approval.

Review outcome: independent Claude review completed 2026-11-24. Incorporated
transaction/conflict, tenant, key-mapping, onboarding injection and URL findings.
Verified alert caller gates in source rather than accepting the reviewer
assertion that no switches exist. M1 READY; M2/M3 remain sequencing scope, not
permission to implement their unreviewed detailed designs. Report kept locally.

Patch review: source/write paths accepted; added a 5-second lock timeout and
onboarding continuation for DB/filesystem errors. Test additions cover these
failures and import/audit transaction behavior. Observation-alert authorization
was obtained from the owner, not inferred from the review.
