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
