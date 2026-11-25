---
status: READY
created: 2026-11-25
issues: [454, 630]
---

# Threshold skill with scored-subset provenance (M2/B)

Depends on merged #658. Reuse its representation-aware strict exceedance converter;
owner chose probability bounds without invented tails. Do not deploy or run live
skill jobs in this patch. No model activation, retraining or alert-policy changes.

## Smallest durable addition

Add nullable JSONB `threshold_context` to both skill_scores and skill_diagrams,
with object-or-null check constraints and no backfill. Old rows and rollback writers
remain null. Keep diagram data unchanged so its strict NaN/null codec still works.
An immutable typed `ThresholdSkillContext` holds schema version 1, threshold level,
value, source, updated_at, canonical units, probability methods, total paired count,
scored count, excluded count, total observed event count, scored event/non-event
counts, decision probability 0.5, comparison `strict_gt`, score coverage
`all_paired`/`conditional_resolved_subset`/`no_resolved_probabilities`, reference
`same_scored_subset_event_rate`, temporal basis `legacy_step_aggregate`, and threshold
basis `current_snapshot_historical_effective_dates_unknown`.
Validate finite values, count identities, range bounds, source enums and fixed
literals. Use Pydantic only for JSONB/API parsing and serialization, returning the
frozen domain type internally. Snapshot means current canonical threshold value
and source/update time; it does not claim a historical effective date. Existing
station/model/artifact/run-generation/lead/period columns retain their roles.

Use existing store write/read methods, generation publication, insert-only grants
and transactions. Fingerprints include canonical threshold context, otherwise a
retry with changed thresholds could silently match old results. Preserve old scores
and version 3, increment new score computation to 4. Export threshold_context in
stored skill API rows, with null for historical rows. Update the contract version
and example. No new evaluation endpoint or frontend arithmetic.

## Probability metric inputs

Extract probability-input cores from existing BSS/contingency/reliability/ROC
functions. Existing member-signature wrappers delegate unchanged. Add raw Brier
score. Validate paired finite probabilities in [0,1] and observed event labels;
use the same strict `observation > threshold` event semantics as current skill.
No tolerance, no observation-alert >= change. Retain the 0.5 decision threshold
explicitly in context, not the deployment's alert trigger probability.
BSS compares against the event rate of the SAME scored subset; zero reference
variance is undefined, not zero. Reliability keeps existing bin-centre forecast_freq semantics and sample_counts;
empty observed-frequency bins stay undefined. Mean predicted probability and
count-weighted fold merging are deferred together.

Reuse #658 conversion for every quantile sample. Retain a point estimate only when
provided; tail bounds without a point are excluded from probability metrics.
Do not exclude based on the observed outcome. Member probabilities use every
member in that sample, not truncation to a common member count. For each threshold
construct one paired probability/event subset reused by scores and diagrams.
Persist raw Brier, BSS, POD/FAR/CSI and reliability/ROC with the same context.
Rank histograms remain member-only; deterministic and quantile scores unchanged.

For zero resolved probabilities, persist undefined scalar scores (existing NaN
storage/API-null convention), sample_size=0, and context with all exclusions;
no diagram is required. This distinguishes no support from missing thresholds.
Otherwise sample_size is the scored subset count, never total paired count.
Undefined denominator scores remain NaN internally/null in API. Report event and
non-event counts even when Brier is defined but BSS or ROC is not.
No scores when the existing temporal/observational admission rejects input.

## Correctness boundaries

- Validate threshold station and parameter. Score only matching parameter bounds;
  require canonical forecast units and existing homogeneous cadence/phase checks.
  Do not infer temporal semantics from CMAL model name. The current skill service
  explicitly assumes legacy step aggregation and rejects declared-period outputs;
  keep that boundary and expose the assumption. Daily discharge therefore means
  daily-mean exceedance, not instantaneous peak risk.
- Multiple sources for one level cannot share current scalar metric natural keys.
  Until a dedicated multi-source evaluation selection exists, refuse ambiguous
  same-level threshold sets before storing a generation; do not silently pick a
  source or overwrite results. No new configuration knob is needed for the current
  single-source BAFU roster.
- New context-bearing threshold results must not pass through combined-model fold
  averaging that carries one fold's context. Inspect combined_skill.py and filter context-bearing
  rows and diagrams before BMA cross-validation fold merging, with an explicit
  log event. Keep non-threshold BMA and single-pass pooled skill unchanged. Recomputing combined predictions
  on a common paired subset is follow-on, not part of this Swiss pilot.
- Do not rank models whose scored subsets differ; context-labelled conditional
  skill is for diagnosis. There is no new automatic promotion criterion.
- Existing ordinary/provisional data-use isolation, publication and artifact
  selection remain unchanged. API disclosure follows existing stored-skill scope.

## Steps and evidence

1. Context type, nullable migration, codec/store roundtrip and fingerprint tests.
2. Probability metric cores and representation-correct service wiring; hand-worked
   Brier/BSS/contingency tests, all/no-event cases, zero resolved dates, partial tails,
   ties, unequal quantile grids, variable member counts, count/provenance parity.
3. Stored-skill API readback/contract, old-row null compatibility, scope and combined
   rejection tests. Update types/spec/touchpoint/operations docs.

Migration roundtrip and worker-role writes use isolated test DB, never Mac mini.
Run existing skill isolation and publication regressions, changed-module types and
lint. Full final CI must pass before owner merge. One independent Claude plan and
patch review per owner-supplied workflow. Reports local; orchestrator sets READY.

```json
{"steps":[{"id":"B1","depends_on":[]},{"id":"B2","depends_on":["B1"]},{"id":"B3","depends_on":["B2"]},{"id":"evaluation","depends_on":["B3_merged","owner_deployment_approval","replacement_hindcasts_verified"]}]}
```

## Independent review resolution

Claude review completed 25 November; report is local at
`/tmp/threshold-skill-plan-review.txt`. A hard combined-fold failure was replaced
with a context-bearing output skip, so ordinary BMA results remain publishable.
This intentionally removes invalid fold-averaged threshold scores. Single-pass
pooled scores retain valid context. Diagram format remains unchanged. Both score
and diagram fingerprints append sorted-key canonical context JSON only when
non-null, preserving old fingerprints. Threshold update-time changes intentionally
create new identities, even if numeric scores match. Keep context on diagrams as
well as scores: separately read diagrams need their own threshold/sample evidence.

Metric names are brier_danger_{level}, bss_danger_{level}, pod_danger_{level},
far_danger_{level}, csi_danger_{level}; metric is unrestricted DB/API text. Version
4 applies to every newly computed score, including stations without thresholds.
The same-subset climatology reference is in-sample; this is not an independent
climatology-model comparison. Exclusions count all unavailable point estimates
(tail bounds and invalid probability inputs), explicitly documented. Ambiguous
threshold sets raise before writes and retain existing cohort failure/publication
semantics; do not invent new partial-generation behavior. Table-level existing
grants cover added columns; no privilege expansion. B READY.

Gate inspection: the validated deployment metric allowlist excludes threshold-
specific metric names. They cannot become automatic promotion criteria through
this patch. A proposed extra gate filter was unnecessary and was removed.

CI follow-up: advance the existing single-head, downgrade-retention and Nepal
qualification fixture pins to reviewed migration 0093. Persistence expectations
include the additional Brier row (14 scores); retain replay, publication failure
and protected-observation isolation checks unchanged.

Merge reconciliation: preserve main’s model-label migration 0092 and chain the
threshold-context migration as 0093. Retain both API schema additions and both
touchpoint entries; verify the combined contract and migration chain.
