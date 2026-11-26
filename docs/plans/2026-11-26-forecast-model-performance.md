---
status: READY
created: 2026-11-26
issue: 677
---

# Forecast-page model performance and event likelihood

## Outcome and ownership

For any deployment, a forecaster can identify the artifact behind a displayed
forecast, inspect its stored performance with honest evaluation provenance, and
read per-time threshold-exceedance probabilities. One shared implementation;
station/model/threshold configuration supplies differences. No Swiss/Nepal feature
fork, new model framework, or full administrator dashboard.

Owner requested this plan and a progress tracker on November 26. Related: #378
(frontend identity/provenance), #454 (model evaluation/acceptance), #538 (fine-tuning
and admin review), #630 (threshold onboarding), #354 (Nepal deployment). This plan
coordinates the forecast-page slice; it does not replace their wider scope.
Keep model acceptance separate from activation. No frontend repository edits,
Aquacast changes, Nepal host changes, model promotions or public alert delivery.
The Nepal owner supplies host qualification and thresholds; Swiss work may proceed
without waiting for Nepal. Keep the stable published release unchanged while the
deployment agent needs it; use only explicitly authorized development deployment.

## Existing behavior to reuse

- `/api/v1/stations/{id}/skill` supports `model_id` and UUID `artifact_id`; response
  states explicit/default selection. Scoped active assignments are required and
  artifact/model identity is checked. Missing stored rows do not trigger evaluation.
- Threshold score rows carry threshold snapshots, probability method, scored and
  excluded counts, event counts, undefined scores as null, periods, training dates,
  forcing, artifact/generation and temporal metadata. Reuse the v4 computation.
- `evaluated_on` distinguishes training, overlap, outside-training and unknown.
  These labels describe recorded intervals, not proof of no pretraining leakage.
- Review-series already exposes strict per-time exceedance, quantile interpolation
  only inside supplied support, tail bounds with null point estimates, and current
  threshold provenance. Reuse `services/threshold_probability.py`, not a second probability engine.
- Reliability and ROC are stored in `skill_diagrams`; the legacy HTML model page
  is not a scoped reviewer JSON contract. Forecast detail has artifact identity;
  network overview currently does not. No scored-artifact catalogue exists.
- `cmal_nepal_static_encoder_20261123` is a separate registered identity. Use exact
  forecast model IDs; never silently alias dated IDs to an undated family.

## Milestones and exit evidence

### M0 — Reconcile availability and temporal qualification

Produce one small matrix per deployment/station/model/artifact: deployed contract,
active assignment/artifact, latest displayed forecast artifact, hindcast run and
coverage, stored score generation, training/evaluation periods and threshold status.
BAFU uses direct host/API evidence; Nepal owner confirms its deployed OpenAPI
`artifact_id` parameter, exact IDs, empty-result reasons and source of thresholds.
Do not assume an ignored query parameter proves only a version problem: also check
proxy forwarding, assignment and artifact matches. No credential expansion.

Record threshold value, parameter, unit, authority/inferred source and snapshot
retrieval/update time. Historical effective dates are unknown unless evidenced;
never use update time as an effective date or convert stage thresholds to discharge
without an approved applicable rating relation. Missing remains unavailable.

Nepal declared daily grid (18:00 UTC, start label, mean) must match observation
aggregation and scoring periods. Verify current shared grid support before runs;
coordinate any remaining implementation with its existing owner, never bypass it.
Exit: matrix distinguishes absent, incomplete, unavailable and deployed; blockers
have owners. Nepal evidence is a regional gate, not a global implementation gate.

### M1 — Replace invalid evaluation and compute usable stored skill

BAFU repair is committed: 49,550 historical daily discharge rows had only the
retired spike/change flags removed; other data/flags preserved. Candidate artifact
`9f084ff1-8294-46ec-ab50-6dc7c8cfde02` stays inactive. Its superseded hindcasts and
scores were removed with rollback backups. Replacement Prefect run
`2c32ff31-7a25-4ef6-a94e-1a41b62b66e3`, hindcast run
`0751e31d-8aad-4409-a991-cb5eeb40796f`, covers 2017-01-02 through 2020-12-31 in
30-issue batches. It is running, not accepted as complete. Group has 173 stations;
2041 has no historical discharge and must remain explicitly incomplete.

After completion, reconcile every requested station/date against outputs and
failure reasons. Compute ordinary and threshold metrics using existing services,
real temporal support, repaired observations and generation publication. Do not
score a partial run as the full requested period. Verify API reads by exact artifact.
Refresh displayed operational donor/climatology evaluations only against admitted,
QC-correct hindcasts; inventory their old-QC provenance before reuse. Candidate
results must never be presented as the current operational donor's results.

Define held-out evaluation from recorded training AND available donor/pretraining
provenance. Respect current closed-boundary overlap labels; do not relabel shared
boundary dates. Keep unknown lineage unknown. Existing climatology trained on its
evaluation period is in-sample: a genuine held-out baseline needs a separately
trained evaluation artifact/split under #454/#538, not a score-only rerun. If that
requires new scientific choices, record the gap and obtain owner decision; never
claim the existing artifact has out-of-sample skill. No automatic retraining or
activation in this milestone. Nepal runs remain with the Nepal owner.

Exit: per-artifact coverage and score generation verified; unavailable/undefined
states remain explicit; no claim that all models are qualified from one run.

### M2 — Match the displayed forecast to its skill

Add nullable `model_artifact_id` to existing overview forecast entries, copied only
from the selected forecast the caller is already permitted to see. Absence, withheld
and redacted entries remain null. Authorized provisional reviewers may see the same
identity as their existing detail read; consumers gain no provisional access. Test
that no artifact identity leaks across publication, data-use or station boundaries. Update shared schemas,
OpenAPI and fixtures. No extra per-station overview queries.

Add a small station-scoped scored-artifact catalogue (proposed
`GET /api/v1/stations/{id}/skill-options`; finalize contract before implementation).
List exact model/artifact IDs, recorded training/evaluation provenance and whether
that artifact is currently active for forecasting at this station. Distinguish that
from the artifact that produced a particular forecast. Reuse artifact/assignment
and latest-generation selection; no scores synthesized from hindcast existence.
Catalogue and scalar/diagram reads are reviewer-only (existing reviewer/admin roles).
An inactive ARTIFACT may have scores while its MODEL retains an active scoped station
assignment; this is already supported by explicit artifact selection. Return only
artifacts readable by that same existing skill policy. An unassigned model remains
out of catalogue scope; no authorization widening. Label candidates separately,
never as performance of an operational forecast from another artifact.
Preserve station/tenant/reviewer scope, generation visibility, standard/provisional
separation and current assignment restrictions. Define unavailable reasons without
revealing out-of-scope artifacts. Catalogue is optional for ordinary forecast reads:
overview supplies their exact artifact so the frontend can request existing skill.

Exit: two artifacts for one model cannot be confused; inactive candidate discovery
and operational status are truthful; direct and group assignments covered; bounded
query count and response. Missing/null forecast artifact does not silently select
another model's scores. No aliasing model IDs and no implicit model ranking.

### M3 — Serve stored reliability and ROC diagrams

Add a reviewer station-scoped JSON read (proposed
`GET /api/v1/stations/{id}/skill-diagrams`) with exact model/artifact and bounded bulk lead/threshold selectors. One
request returns both reliability and ROC for the selected view; support up to 16
explicit leads and eight thresholds, all-season/all-flow-regime initially. Enforce
a 4MiB response ceiling before serving, retain stored bins exactly, and report an
explicit oversized-request error rather than truncate. Qualify this ceiling against
real stored diagrams; do not silently omit leads/thresholds. Reuse the same generation-selection and authorization rules
as scalar skill. Return evaluation/training dates, forcing, threshold context,
sample/excluded counts where recorded and stored diagram bins/points. Never infer
counts missing from old records or return a newer scalar generation with old curves.
Bound the request by one station/artifact and the requested lead/threshold lists; no all-fleet
or all-artifact blob. No computation on GET. Missing stored diagrams are unavailable.
Exit: scalar/diagram generation parity, undefined bins preserved, scope tests, and
representative BAFU fixtures readable through the local/test route. Regional live
route acceptance belongs to M5, not this implementation exit.

### M4 — Operational per-time threshold probabilities

Extend existing forecast detail for every supported displayed model. Reuse full
stored quantiles/members and the existing probability service, not overview median
or IQR. Return strict `P(Q > threshold)` per valid time with point/bounds/method/
reason, exact model/artifact/forecast identity and threshold snapshot/provenance.
Do not invent tails, turn unknown into zero, or round before threshold comparison.
Existing member-based behavior may be reused only with its stated empirical method;
deterministic/unsupported representations return explicit unavailable states.

Declare forecast support/aggregation: an exceedance probability for daily mean
flow is not the chance of an instantaneous peak crossing that threshold. Use the
record's declared period; legacy support stays explicitly legacy/unknown. No
probability of any event over a window from marginal quantiles; no maximum or
independence shortcut. No warning decision or alert delivery is implied.

Initially serve full probabilities in detail, using overview's existing detail URL.
Do not multiply network overview size or introduce per-issue fan-out. A compact
network addition is deferred until payload/latency evidence justifies it.
Exit: ties/strictness, supported quantiles and tails, missing thresholds, members,
publication redaction, scoped station access and declared daily periods tested;
stored BAFU forecast used in a read-only local/test qualification and reusable
Nepal fixtures. Live acceptance belongs to M5.

### M5 — Deployment and frontend acceptance

Ship additive backend schemas plus shared contract/fixtures together, then hand
off for frontend integration; nullable new fields remain ignorable by older clients.
Update shared contract and short handoff with exact routes, version, example IDs,
units/period meaning, limits and absent states. Owner controls PR merge and deployment
approval under existing rules. Record regional deployment evidence separately.
No release publication while the owner holds the stable release for another agent.

Frontend requests data on selection, never on a timer. Reuse/cache by forecast ID,
artifact and generation; bounded selected-model/detail reads, no fleet-by-model or
per-issue fan-out. Stay within 30 requests/minute per page including retries; ordinary
forecast skill uses overview artifact IDs without a mandatory catalogue call. A
selected-model performance view uses at most three reads: scalar skill, bulk
diagrams, and forecast detail with probabilities. Optional artifact discovery adds
one on explicit request. No lead-by-threshold diagram fan-out; keep retries inside
the same 30-request ceiling.
Target <=10s station review and <=5s network list; measure rather than claim.
The full Swiss overview was ~5.8MB uncompressed; agree explicit per-route client
bounds, do not silently inherit a 1/2MiB cap or drop lower-priority models.

Acceptance: a forecaster can select each available model, see its exact artifact's
stored scores with evaluation caveats, load corresponding stored diagrams and see
per-time threshold probabilities. Missing Nepal evidence is not called delivered.
No model activation, hidden ranking or expensive evaluation is triggered by reading.

## Sequencing and review

One implementation owner per milestone; tasks are small enough to hand off with
paths, contract, dependencies and exit evidence. Do not spawn implementers merely
because the graph permits independent work. M2/M4 can proceed while M1 runs; Nepal
qualification proceeds independently with its current owner. Independent Claude
plan review precedes READY; patches receive the required independent review and
full final CI before owner merge. No automatic repeated review loops.

```json
{"milestones":[{"id":"M0","depends_on":[]},{"id":"M1","depends_on":["M0"]},{"id":"M2","depends_on":["M0"]},{"id":"M3","depends_on":["M0"]},{"id":"M4","depends_on":["M0"]},{"id":"M5","depends_on":["M1","M2","M3","M4"]}]}
```

Deferred: full admin UI, acceptance/activation workflow implementation, new model
families, network-wide ranking, new thresholds inference, elaborate temporal
threshold registry, joint window probabilities without trajectories, and generic
performance/monitoring frameworks. Extend existing components first.

## Independent review resolution

Claude reviewed the plan on November 26. Resolved all four findings: clarified
active model assignment versus inactive candidate artifact and catalogue parity;
made overview artifact disclosure follow the selected forecast authorization;
moved live acceptance to M5 with additive contract ordering; and specified bulk
diagram retrieval and a bounded per-view request count. Diagrams can proceed after
M0 without waiting for the catalogue. Orchestrator marks the plan READY; this does
not merge code, publish a release or authorize Nepal deployment.
