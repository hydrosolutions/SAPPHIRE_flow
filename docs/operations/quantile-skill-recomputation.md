# Quantile skill correction

Computation version 3 preserves quantile probabilities during scoring. Earlier
versions treated quantile knots as equally likely ensemble members. Do not use
those probabilistic scores for candidate acceptance or model weighting.

For quantile output, the median and interval widths use the supplied probability
grid, with interpolation within its support. The distribution score is
`quantile_score_trapz_p05_p95`: twice the trapezoidal integral of pinball loss on
[0.05, 0.95], normalized by 0.9. It has the discharge unit, is lower-is-better,
and is **not full CRPS**. Compare it only on the same quantile grid and observation
pairs. Quantile grids must be finite, ordered and non-crossing; a calculation
cannot mix grids or member and quantile representations.

Quantile forecasts no longer produce pseudo-member CRPS, ensemble range, member
rank histograms or threshold scores/diagrams. Those results are unavailable,
not zero. Per-time threshold probabilities are subsequent work with explicit
interpolation and tail semantics. Member-based calculations remain unchanged.
Configured CRPS gates fail closed for quantile models; they are not automatically
changed to use the new metric.

Before recomputation, configured onboarding gates fail closed on legacy scores
for all representations: the stored rows do not identify whether the earlier
calculation used quantile pseudo-members. A station/lead stratum still at an old
version cannot disappear behind another station's corrected scores. Historical
candidate/donor probabilistic comparisons, and deterministic comparisons using
nonuniform quantile grids, must therefore be replaced before acceptance.

Malformed or inconsistent quantile samples fail the entire scoring scope without
publishing partial scores. This is intentional: silently skipping bad samples
would change the evaluation population. Exact probability grids are required;
no tolerance or automatic repair changes model output on this scoring path.

BMA accepts only finite, nonnegative member CRPS from computation version 3 or
later. Old scores cannot distinguish true members from pseudo-members, so even
member models must be rescored before their old CRPS can supply weights. The
existing no-weight behavior applies until eligible scores exist. The finite-grid
quantile score is not eligible for BMA.

## Production recomputation

1. Finish the ongoing candidate/donor hindcast runs and retain their identities.
2. Review and deploy the corrected release with owner approval. Do not activate
   the candidate or alter operational assignments.
3. Use existing skill computation on persisted hindcasts, scoped to each
   station/model/artifact/run and parameter. Publish a complete **generation**
   per existing generation rules; do not insert isolated replacement scores.
   Generation publication suppresses old metrics and diagrams even when the new
   calculation intentionally omits them. Retain old rows for audit.
4. Preview and verify station counts, leads, evaluation period, observation
   coverage, metric names, computation version and generation identity. No
   retraining or weather retrieval is required for this rescore.
5. Preserve the evaluation warning that candidate/donor common observation pairs
   have not been verified. Equal sample counts alone do not prove pairing. No
   automated superiority or acceptance verdict follows from these reports.

The administrator review work will separate scientific acceptance from
activation. Existing approval commands may promote the artifact; they must not
be used as acceptance-only actions.

## Threshold verification, computation version 4

Recomputation can now score quantile forecasts against canonical station thresholds
using the shared per-time probability converter. Raw Brier score, BSS, POD/FAR/CSI,
reliability and ROC use the same resolved paired subset. Quantile tails without a
point estimate are excluded, never clamped or invented. Member probabilities use
all members at each time, even when ensemble sizes differ.

Each threshold score and diagram carries `threshold_context`: threshold value,
source and update time; probability methods; paired/scored/excluded and event
counts; strict `>` comparison; decision probability 0.5; and temporal/threshold
basis. `sample_size` is the scored count. Partial coverage is explicitly
`conditional_resolved_subset`; do not rank models with different subsets or use
these conditional scores for automatic skill-gate approval. Zero resolved dates
produce null API scores with zero samples and the exclusion context. Undefined
metric denominators remain null, never zero. Exclusions include unresolved tails
and invalid probability input; they are not selected by observed outcome.

BSS uses the event rate in the same scored subset as an in-sample reference. It is
not skill against the separately fitted climatology model, and is undefined when
that subset contains only events or only non-events. Reliability keeps the
existing bin-centre x coordinates and per-bin counts.

Temporal basis remains the existing legacy step aggregation: daily discharge is
daily-mean exceedance, not instantaneous flood-peak risk. Declared-period outputs
remain rejected until matching observation periods are supported. Thresholds are
current snapshots; historical effective dates are unknown. Ambiguous sources for
one danger level are refused before writes. BMA cross-validation omits threshold
metrics/diagrams rather than averaging incompatible fold statistics; its other
scores and single-pass pooled scores remain supported.

Migration 0093 adds nullable context columns. Old results remain readable with null
context; no metadata is invented. Rollback software can still write null, but a
schema downgrade refuses once context-bearing results exist. Existing insert-only
worker grants and generation publication remain unchanged. Context-only changes
(including threshold update time) change generation fingerprints. Do not activate
artifacts or run production evaluation merely because this code is merged.

Diagram context is persisted and available to backend store readers. The existing
stored-skill JSON route serves scalar rows only; this patch adds no new diagram
API or chart renderer. The legacy model page loads diagram rows but does not yet
present this context. Consumer diagram presentation remains a follow-on.

## Hindcast batch input memory

The 365-date candidate and donor attempts on 0.1.1194 reached the dedicated
8 GiB cgroup limit before storing forecasts (two recorded OOM kills). The runner
used a 720-step default while deployed CMAL-small declares 30 steps for discharge,
precipitation and temperature. It retained overlapping per-issue frames until the
whole batch was ready, before FI deduplication. A fully populated 172-station
example estimates 4.71 GiB for historical target/forcing frames alone at 720 steps,
versus 0.20 GiB at 30; these exclude prefetch, model and temporary allocations.

The source fix resolves omitted station/group lookbacks from model requirements;
explicit overrides remain supported. Missing edge history stays missing and model
validation owns length shortfalls. This is not a streaming implementation or a
promise that arbitrary multi-year batches fit memory. The 30-date operational
retry is a workaround; requalify a larger batch after an approved fixed release.


## Interim candidate evaluation (2026-11-25)
Version 4 is deployed on the Mac mini in 0.1.1194. A sequential evaluation uses
candidate artifact `9f084ff1-8294-46ec-ab50-6dc7c8cfde02`, corrected hindcast run
`f7fff7bf-f4b7-4bb3-9b32-9c5c21064e14`, and available per-station forecasts while
that run continues. These are interim stored scores, not complete 2017–2020
verification. Recompute after completion. No candidate activation is involved.

The operator enables score generations for this invocation only. A reviewed
operator subclass splits the deployed store's public insert calls into 500-row
chunks on the same connection, preserving count reconciliation and publication.
This avoids the PostgreSQL bind limit encountered at 3,277 scores in one insert;
the permanent store fix makes the same bounded writes. There are no commits
between chunks. Existing legacy rows remain for audit and are superseded by the
published version-4 generation for the same station/model/artifact scope.

The candidate-selector API addition requires release/deployment before external
reviewers can request these inactive-artifact scores through the scoped route.
