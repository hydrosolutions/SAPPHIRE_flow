---
status: READY
owner: Nepal deployment orchestrator
branch: feat/hindcast-water-level-thresholds-20261127
---

# Threshold skills from discharge hindcasts

## Outcome and scope

Compute warning/danger skill from stored discharge hindcasts using dated rating
tables and published water-level thresholds. Keep the existing discharge scores.
The first execution covers 2013–2019 for CMAL SE, CMAL Small and persistence,
and refreshes the already calculated climatology comparison where eligible.
This is retrospective, reanalysis-forced model evaluation, not reconstruction of
historical warnings or proof of performance with archived operational weather.

The ordinary hindcasts and scores are already being extended through supported
deployed services and do not depend on this code change. Persistence passed normal
onboarding at five operational sites; twelve lack the required discharge target.
Fresh persistence forecasts using provisional discharge are a separate gap: that
forecast-cycle branch currently accepts group models only. Do not change forecast
dispatch, model algorithms, FI, alert thresholds, or station status in this patch.

## Evidence and decisions

- Base: refreshed remote main `ec4c44a9`; deployed forecast application 0.1.1203.
- `services/rating_conversion.py::RatingConverter.inverse` already supports
  bounded Q-to-H conversion, including ambiguous plateaus. Reuse it.
- `services/skill/thresholds.py` currently compares ensembles and observations
  with a constant threshold in their own units. Existing Brier/BSS, POD/FAR/CSI,
  reliability and ROC implementations are reusable.
- `ThresholdSkillContext` and its boundary codec currently accept schema 1 only,
  and call every temporal basis `legacy_step_aggregate`. Declared-period cohorts
  must retain their actual evaluation period and displacement in the new path.
- Preserve the existing `skill_generations.evaluation_contexts` list unchanged.
  Add one nullable JSONB `threshold_evaluation_manifest` column to that table;
  old consumers select their known columns and ignore it. A new writer freezes
  one validated manifest per generation, not one copy per metric.
- Public BIPAD `api/v1/river-stations/` exposes `warningLevel`, `dangerLevel`,
  station identity, and `dataSource=hydrology.gov.np`. On 2026-11-27 all seventeen
  operational bindings matched. Four have no danger value; do not invent one.
  `modifiedOn` changes with station readings and is not a threshold effective date.
- The owner explicitly directed reading levels from the API and wants this
  metadata included when stations are onboarded in future. Retain a source URL,
  acquisition time, raw-response digest and exact station identity now. The
  reusable onboarding integration is a follow-on using the same parsed metadata;
  it must not silently turn reported levels into operational alert settings.
- Existing references: five operational stations with archived daily discharge.
  No fabricated scores for the other twelve. Keep missing-reference reasons.
- CMAL SE uses declared START 18:00 UTC daily periods, with the accepted
  +900-second DHM reference displacement and retrospective input availability.
  CMAL Small/persistence/climatology retain their legacy period contracts; no
  claim that all models now describe identical daily periods.
- Current thresholds used retrospectively are explicitly labelled as a current
  snapshot with unknown historical effective dates. Gauge-zero linkage requires
  evidence or the owner's explicit common-zero assumption, recorded per station.
  The owner approved the labelled historical common-zero assumption for the five
  named reference stations on 2026-11-27 UTC. Do not extend it to other stations.

## Design

### 1. Parse and freeze one evaluation manifest

Add a boundary payload and frozen internal types for a per-station manifest:
station/tenant/network identity; warning/danger values and unit; provider identity,
URL, retrieved-at and digest; optional effective bounds; reference basis and
attributed assumption/evidence; selected rating snapshots with id, version,
validity, interpolation, points and canonical digest. Reuse existing rating and
reference types/canonical serialization. Reject nonfinite/invalid values, duplicate
identities, reversed levels, conflicting mappings, missing provenance and malformed
validity. A missing danger value is valid. Do not guess station mappings by name.

The orchestrator owns acquisition and freezing. Resolve identity by the existing
(tenant, network, station code, API station id) binding, not by a name search.
The frozen manifest is at most 2 MiB, with at most 128 curves per station and
4096 points per curve; reject oversized inputs before a write. Curve points
are parsed into finite typed pairs and canonical JSON using the existing
canonical serializer. Hash the parsed scientific content separately from the
raw response digest, whose unrelated live readings can change.

The scoring flow accepts this manifest explicitly. Default behavior remains
unchanged. An operator dry run reports eligible stations/periods, exclusions and
references before scores are written. This input is evaluation metadata only;
never write `station_thresholds` or enable alerts as a side effect.

### 2. Evaluate matched physical periods

Reuse existing observation preparation, period qualification and stratification.
Retain each pair's physical support (and displaced reference support) until
threshold evaluation. For each pair select exactly one rating snapshot whose
validity covers the complete forecast and reference support. Do not use the newest
curve for all years. Exact duplicate deliveries may be collapsed only when
station, validity, interpolation and every point agree; retain every delivery identity and use one identical physical converter. Live inspection found 50 rows
representing 25 physical snapshots at the five reference sites. No matching curve, conflicting overlapping curves, or a
curve change inside the support makes that pair unavailable with a counted reason.
Legacy cohorts use their existing evaluation support and are labelled legacy.

For each period, require an in-range H threshold on a strictly monotone segment
of its qualified rating. Calculate its discharge crossing Q*=f(H_threshold) with
`RatingConverter.convert`, refusing its out-of-range/clamped result. Use Q_obs>Q*
for the event, member counts above Q* for member distributions, and the original
Q-space quantile CDF at Q*. This compares the water-level equivalent against H
without inventing numerical stages beyond the table. Crucially, do not remove
large observations or high-spread members because their inverse H is out of
range: doing that would preferentially discard the flood events being scored.
The same transform applies to observations, members and quantiles. At flat table
segments refuse ambiguous thresholds; test exact plateau-edge/tie behavior.

Use `RatingConverter.inverse` for optional in-range level diagnostics only, not
as an event-selection gate. Probabilities are exact relative to the existing
Q-space quantile-CDF interpretation under this monotone threshold mapping;
linearly interpolating transformed H quantiles would change that interpretation
for nonlinear ratings. Unknown quantile tails remain unresolved, with coverage
reported just as for ordinary threshold scores.

Apply strict exceedance and decision probability 0.5 consistently. Compute all
threshold scores and diagrams on the same resolved subset. Include paired,
scored, excluded, event/non-event counts and exclusion reasons so curve-domain and
tail exclusions cannot masquerade as whole-period skill. Set a reporting flag
when fewer than ten scored events or non-events are present (a transparent MVP
reporting convention, not a universal scientific cutoff); retain counts and avoid
model ranking claims. BSS retains the existing scored-subset in-sample event-rate
reference, not an independent climatology benchmark. Undefined scores remain
undefined; do not synthesize zeros or fabricate ROC/reliability bins.

These daily values mean **water-level equivalent of a daily discharge aggregate**.
They are not observed daily mean water level or instantaneous flood maxima. Carry
that statistic in context and documentation. Using converted observed discharge
does not independently validate the rating curve.

### 3. Persist and expose provenance without losing current scores

Introduce a discriminated schema-2 rated-threshold context alongside unchanged
schema-1 decoding. Schema 2 records: danger level; H value and units `m`; provider
and source URL; retrieved-at (NOT `threshold_updated_at`); effective bounds or
unknown; canonical manifest digest; comparison `strict_gt`; decision probability;
statistic `water_level_equivalent_of_discharge_aggregate`; actual temporal basis
(`declared_period` or `legacy_step_aggregate`); reference basis; curve-use counts
and Q* range by curve digest; paired/resolved/excluded/event/non-event counts;
exclusion reasons, probability methods, coverage and low-event reporting flag.
Do not add a BIPAD `ThresholdSource` DB enum: this schema records reported-source
provenance separately and never creates an operational StationThreshold.

Distinct metric identities (`brier_stage_equivalent_<level>`,
`bss_stage_equivalent_<level>`, `pod_stage_equivalent_<level>`,
`far_stage_equivalent_<level>`, `csi_stage_equivalent_<level>`) prevent collisions
with existing discharge-threshold metrics. Reliability/ROC keep their existing diagram types and use distinct
`stage_equivalent:<level>` threshold identities, with the same context. Ordinary discharge metrics stay unchanged.

Store the full frozen manifest in the new nullable generation column; contexts
refer to its digest. Keep the existing period-context list and API meaning intact.
API score/diagram schemas accept the v1/v2 union; expose bounded manifest metadata
and curve references through the generation context, not full rating tables per
score. Preserve the existing 4-MiB response ceiling. Unknown schema versions fail
explicitly rather than silently dropping evidence.

Roll out readers/codecs and the nullable column first with new writers disabled
by default. This may be one PR/release, but it is two operational stages: verify
all API, forecast, scoring and review consumers plus retained rollback images can
read schema 2, then explicitly enable the scoring invocation. Ingest-only workers
need not be upgraded unless they actually consume skill records. A preflight
must refuse schema-2 writes without the recorded consumer-compatibility receipt.
Once schema-2 data exists, rollback is only to a schema-2-compatible image; any
older rollback requires an owner-approved restore/retirement procedure. Do not
promise that disabling the writer makes existing new rows safe for old readers.

Recompute the full cohort (ordinary scores plus threshold scores/diagrams) in one
new generation. Do not publish a threshold-only generation that hides ordinary
scores. Preserve the previous complete generation on any failure. Preserve model,
artifact, hindcast run, lead, season, flow regime, forcing, period and provenance
identity. Store an input inventory digest (hindcast IDs/run IDs, issue bounds,
counts and observation/evaluation content) with the manifest. Live execution waits
for the current hindcast extensions and ordinary scoring jobs to finish, freezes
their inventory, and refuses publication if that inventory changed. No overlapping
manual scoring writer is admitted for that station/model/artifact; document this
operator single-writer requirement rather than imply a new scheduler lock.
Update API schemas/examples and map contract only where context changes
are exposed. No new peak/window-event probability claim from marginal quantiles.

### 4. Verify, deploy, execute

After independent patch review and full CI, hold the PR for owner merge. Deploy
through the approved Nepal update track only after release authority applies.
Run the dry-run inventory against the frozen public metadata and dated curves.
A dry run may report unavailable reference evidence, but a scoring manifest
cannot be finalized for a station without the explicit datum decision/evidence.
Full rating-period coverage is required for an unqualified full-period claim;
partial coverage may be scored only with its excluded periods/counts disclosed.
Climatology eligibility is the same five reference sites and period qualification,
using their already completed 2013–2019 runs. Execute eligible cohorts for all four models, preserving ordinary metrics and
reporting missing thresholds/references/curves. Verify the API returns new current
generations, context and diagrams for representative stations and that unavailable
sites remain explicit. Confirm alert configuration did not change.

## Tests and exit gates

Behavior tests: date changes/gaps/overlaps; full-period boundary crossing; SE
displacement and legacy support; LINEAR and LOG_LINEAR conversions; equality,
plateaus and out-of-range inputs; exact transformed-CDF probabilities; unknown
tails; no-event/all-event and no-resolved-pair cases; source identity/datum mismatch;
missing danger threshold; frozen-manifest digest mismatch; bounded payloads.
Integration: schema-1 and schema-2 round trips, existing period contexts preserved,
complete-generation publication/rollback, ordinary scores remain visible, reviewer
authorization unchanged, and threshold output has correct counts/provenance.
Run affected suites, lint/changed-module type checks and the full suite in CI.

## Dependency graph

```json
{"phases":[
  {"id":"review","depends_on":[]},
  {"id":"manifest","depends_on":["review"]},
  {"id":"owner_datum_decision","depends_on":[]},
  {"id":"pair_evaluation","depends_on":["manifest"]},
  {"id":"persistence_api_docs","depends_on":["pair_evaluation"]},
  {"id":"patch_review_ci","depends_on":["persistence_api_docs"]},
  {"id":"owner_merge_deploy","depends_on":["patch_review_ci"]},
  {"id":"compat_gate","depends_on":["owner_merge_deploy"]},
  {"id":"dry_run","depends_on":["compat_gate","owner_datum_decision"]},
  {"id":"hindcasts_complete","depends_on":[]},
  {"id":"live_scores","depends_on":["dry_run","hindcasts_complete"]}
]}
```

One implementer owns this pass. One independent Claude plan review and one
independent relevant patch review, following the owner's current review policy.
Full reports remain local. No implementation starts while this plan is DRAFT.

## Independent review outcome

One Claude review completed (local report, US$0.1411). Its three blockers were
accepted and addressed: event selection now uses the forward threshold crossing
without discarding out-of-range floods; datum evidence is an execution gate; and
new contexts require a staged consumer-compatibility gate with explicit rollback
limits. The revision also names metric identities, exact context fields, size
bounds, source acquisition ownership, rare-event labels and publication ordering.
The owner approved the attributed common-zero assumption for the five reference
stations. A read-only dry-run found usable warning crossings for 447, 604.5, 647
and 670, and danger crossings for 447 and 647. At 684 both levels lie above the
2013–2019 tables; retain unavailable status, never extrapolate. No live threshold write has
occurred. Implementation readiness is evaluated separately from live-data admission.

The orchestrator marked this plan READY after resolving the independent review findings. This permits implementation, not unmerged-code deployment.
