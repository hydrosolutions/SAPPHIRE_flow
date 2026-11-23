---
status: READY
issue: 538
---

# Configured fine-tuning and admin review

## Outcome and owner decisions
One command starts an explicitly configured fine-tuning experiment for a model and deployment. It creates a new inactive artifact with evaluation evidence; an admin reviews and explicitly approves or rejects it. First qualification: cmal_small on BAFU, adapting the owner's ERA5-Land-pretrained donor to stored Swiss weather. Never change Aquacast, the Nepal server, shared deployment defaults or active artifacts during training.

Use one TOML file containing model/donor, deployment/tenant/group, historical weather selection, fitting and evaluation intervals, timestep, RNG seed and model-owned training parameters. Reuse existing host deployment authorization, source factories, Prefect training, hindcast/skill, artifact and audit mechanisms. No generic experiment platform, hyperparameter search, automatic promotion, new frontend or new authentication system. Admin command/report is the stated MVP assumption; no new screen is included.

## Existing behavior and concrete gaps
- train_models_flow already accepts model/group scope, base_artifact_id and opaque training_params, routes to retrain, records donor lineage, and keeps warm starts inactive. Group scope uses assigned groups; station_ids does not subset a group.
- Production weather selection currently inherits deployment reanalysis_source. The existing hybrid factory reads MeteoSwiss products (RhiresD with RprelimD tail, TabsD and other declared parameters); it must be explicitly selected and recorded for this experiment, without changing the deployment's operational source.
- Automatic hindcasts currently use the fitting interval. New experiments require a separate evaluation interval and label overlap with donor training accurately. Donor global ERA5-Land origin is owner-provided information; exact pretraining dates/stations must be inspected or recorded unknown.
- Current new-model onboarding uses an automatic skill gate and promotion; there is no manual artifact-review screen/command. Existing TRAINING/PENDING_APPROVAL/ACTIVE/REJECTED statuses and promotion/audit service can support the explicit review step without changing ordinary onboarding.
- Training group static concatenation still needs checking against legitimate all-null columns; the hindcast-only fix #597 does not prove training assembly works. No invented static values or relaxed FI requirements.

## M1 — Configured submission and run preflight
1. Boundary-validated TOML -> frozen domain request. Require exactly one model, donor and group; explicit tenant and deployment; UTC bounded nonoverlapping fitting/evaluation intervals; positive step; explicit seed; existing source mode; nonempty operator; model parameter dictionary. Unknown fields fail. No secrets in file or Prefect parameters. Example uses placeholders until live inventory establishes actual dates and compatible parameters.
2. One command, proposed `python -m sapphire_flow.cli.fine_tune start --config PATH`, validates and submits through supported Prefect API. Provide read-only preview. Stable request ID/idempotency key prevents transport retries creating duplicate training runs; a deliberate new experiment uses a new request ID. Preserve config digest and resolved request in run parameters.
3. Revalidate on worker before fitting: host allowlist/principal, target tenant/group and current membership, donor model/hash and access, warm-start capability, compatible static/dynamic requirements, explicit nonempty scope. Refuse rather than silently skip an unauthorized/unknown group. Reuse existing validation and fail-closed source readers; no fallback to fresh training or deployment-default weather.
4. Extend the existing training entrypoint minimally for explicitly configured experiments. Existing ordinary callers retain behavior. Reuse registered train-models deployment; do not create a second training pipeline or modify shared schedules.
5. Freeze and record requested/eligible/excluded membership, source/version evidence and parameters before the expensive fit. Serialize experiments for the same target scope using existing supported concurrency mechanisms; do not assume default pool serialization. Record configuration in an audited run receipt, not just an ephemeral console message.

## M2 — Candidate evaluation and admin decision
1. Evaluate candidate and donor over the same configured evaluation range and source, with distinct explicit hindcast run IDs. Use existing skill computation; preserve gaps and sample counts. If comparison samples differ, report coverage and do not claim a ranking from incomparable metrics. Model-internal validation settings stay model-owned; external evaluation data must not enter fitting/preprocessing.
2. Store artifact as inactive throughout. Evaluation failure leaves a recoverable inactive candidate with truthful failure status; retry evaluation must not retrain. A complete report moves the candidate to PENDING_APPROVAL using existing lifecycle support.
3. Persist an append-only audit receipt keyed to artifact/run containing config digest, artifact hash, donor identity, source selection, requested/fitted/evaluation intervals, roster, evaluation run/generation IDs and completion state. Reuse audit storage; no schema migration unless existing audit permissions/shape demonstrably cannot express the receipt. Never put orchestration metadata into model-owned training_params.
4. `fine_tune review --artifact ID` reads persisted evidence and displays coverage, metrics and limitations. Admin `approve`/`reject` requires trusted host/operator authorization and a reason. Approval rechecks candidate identity, exact reviewed evidence and current active artifact; serialize/lock the scope and atomically promote/supersede plus audit via existing services. Refuse stale reviews or another concurrent promotion. Rejection keeps active artifact/assignments unchanged. No HTTP write API, invented human-auth identity, or changes to station grants.
5. Unknown donor pretraining exposure remains visible. A disjoint fine-tuning/evaluation split alone is not proof of independent global-pretraining holdout. No automatic pass/fail threshold or promotion based on uncertain independence.

## M3 — Swiss qualification
After current hindcasts finish, inspect actual registered deployment, supported CMAL training configuration, donor metadata, historic MeteoSwiss/discharge coverage and resource needs. Choose a bounded compatible trial without silently altering production group membership. Keep full group semantics where required; if smaller experimental groups are unsupported, report that constraint before creating assignments. Use existing source/time/QC conventions; no ERA5 fallback disguised as Swiss training data.

Run one bounded job within a measured resource/time budget, protecting 00/06/12/18Z forecasts and 02Z backup. Persist candidate/report and demonstrate admin review. Do not approve the live candidate automatically; owner/admin decides after seeing results. Successful execution proves the workflow, not improved forecast skill. Do not update Mac release merely to absorb unrelated Nepal work.

## Verification and handoff
Independent Claude plan review before READY/implementation. One independent patch review, focused tests and full suite/CI after final code changes before owner merge. Tests cover malformed/ambiguous config, unknown/foreign scope and donor, source override without global mutation, unsupported warm start, idempotent submission, distinct evaluation windows, inactive candidate on failure, evidence tampering/stale approval, atomic promotion and rejected candidate. Exercise FI adapter only through its existing contract; inspect authoritative protocol/docs before any boundary change. Update operations guide, config example and touchpoint map.

Milestone handoffs remain path-based and small; one implementation owner. No deployment or model activation is implied by a code merge.

```json
{"plan_review":[],"M1":["plan_review"],"M2":["M1"],"patch_review_validation":["M2"],"M3":["patch_review_validation","owner_merge_and_deployment"]}
```

## Review disposition — 23 November
One independent Claude review completed; full report retained locally. Orchestrator accepts the following clarifications. No further reviewer loop is required for these resolutions.

- Admin surface: command/report is the stated MVP assumption after the optional preference question; no UI implementation. Approval authority is the existing trusted host operations principal and writable-tenant allowlist, with explicit operator attribution (not a claim of new human authentication). No read-only bearer credential can approve.
- Add one optional serialized `fine_tuning` request to train_models_flow; ordinary callers retain existing behavior. It contains only orchestration fields and separate opaque model training parameters. Parse it at both CLI and flow boundary. Test both paths and unchanged ordinary training behavior.
- Model-specific parameter validation remains model-owned. Check declared warm-start capability and known configuration/schema capability where exposed; never invent a SAP3 CMAL-only validator or assume an unsupported generic FI validation hook. Unsupported parameters fail through the model's existing validation boundary and leave no active artifact. The pilot must inspect the pinned runtime's documented trainer/finetune schema before launch.
- Record the explicit source mode and selected per-parameter products/version provenance, including any RprelimD tail. Candidate and donor use the same source configuration. Do not label all hybrid weather definitive, or mix ERA5 into a Swiss-only experiment. Require requested variables to be supported by the selected existing factory.
- Evaluation target timestamps cannot overlap fitting targets. Causal lookback preceding evaluation is legitimate context; do not impose an arbitrary purge. Preprocessing fitting must not consume evaluation targets. Record donor exposure known/unknown separately. Report candidate/donor metrics and paired sample coverage; do not provide a ranking if exact common samples cannot be established through existing APIs.
- Stable Prefect submission idempotency key is based on explicit request UUID. On duplicate submission compare the returned stored request digest and refuse conflicting content. Worker refuses an ambiguous/repeated fit with a retained candidate; evaluation retry references that candidate and original config rather than refitting.
- Serialize same tenant/model/group fitting via a dedicated PostgreSQL advisory session lock held on its own connection, released in finally. Promotion uses a transaction-scoped lock for the same scope plus authoritative active-artifact and receipt checks. Avoid a long database transaction around model training. Do not assume default-pool or deployment concurrency alone protects cross-entrypoint conflicts; preserve incumbent checks on approval.
- Receipt authority: append-only audit entries scoped to candidate artifact, run/request and phase; exact digest binds canonical request, artifact hash, donor hash, frozen group membership, selected source evidence and evaluation IDs. A new completed evaluation invalidates an older approval digest. Never accept edited local report JSON as authoritative evidence. Missing/partial/conflicting receipts refuse approval. Atomic candidate/receipt storage must reuse AuditedWriter; evaluate legal transitions against current store/DB constraints before implementation. TRAINING remains on incomplete evaluation, PENDING_APPROVAL only after complete report, ACTIVE only explicit approval, REJECTED only explicit rejection. Repeated identical decisions are idempotent; changed decisions fail.
- Legitimate null static metadata must preserve missingness and FI requirements. If peer column dtypes alone block assembly, reuse the narrow proven stacking behavior, with regression tests; do not fill values, cast conflicting concrete types or change Aquacast.


## Implementation and Swiss inventory — 23 November
M1/M2 are implemented on the isolated issue-538 branch. One independent patch
review identified four correctness/validation findings, all resolved and verified:
expected-incumbent comparison at the shared promotion boundary; atomic candidate,
receipt and required lineage persistence; explicit per-product UTC weather dates;
and configured-flow tests using real PostgreSQL transactions. Full reports remain
local. Final suite/CI and owner merge remain required before deployment.

Read-only Mac inventory: registered train-models deployment exists with empty
default parameters. Pinned Aquacast 0.1.378/FI 0.1.20 declares daily precipitation
and mean temperature, 30-day past inputs, up to 10 future days, and 78 Caravan
statics for CMAL-small; Tmin/Tmax/sunshine are not required. Warm-start strategies
are static_only and last_layer. The current imported donor's database dates span
1985–2020 while the installed YAML declares different train/validation/test splits;
this does not establish the binary's true exposure. Keep holdout claims unverified.
M3 remains a separately bounded, owner-approved deployment and qualification step;
no live fine-tuning or promotion has been run.

### Supplied Aquacast example — qualification follow-up

Owner clarification: use CMAL-small to qualify the fine-tuning workflow now;
CMAL is the eventual operational target. Keep the same configuration envelope,
submission, evidence and admin-review path for both. The pilot does not commit
CMAL-small to continued operation or imply activation of either candidate.

The owner supplied `ch_lora_r8_meteoswiss.yaml` on 23 November. Preserve it as a
research reference and use the existing single-file TOML envelope with the mapping
in `docs/operations/aquacast-fine-tuning.md`. The operational schema at our pinned
Aquacast revision already exposes LoRA parameters; our CMAL-small allowlist does
not yet allow LoRA. The supplied reference has a different donor shape (512 hidden
units, 210/15-day windows and radiation inputs versus 256, 30/10 and precipitation/
temperature). Do not replace the existing donor template or silently change strategy.

Before a LoRA pilot, qualify train/save/load/predict against the existing CMAL-small
donor and review any allowlist change. Faithful reproduction of the supplied
research run additionally requires resolving template-owned early-stopping and
stride settings through the upstream operational contract. Declared checkpoint.load
values alone do not establish the fine-tuner's returned weights; verify its actual
checkpoint path at the pinned revision.
No Aquacast changes, live training or model activation are part of this reference
documentation update. The larger donor would require ordinary model onboarding.

Next steps, in order:
1. Include the reviewed reference and mapping documentation in PR #621; merge and
   deployment remain owner decisions. M1/M2 implementation is unchanged.
2. Qualify LoRA locally with CMAL-small through the pinned FI retrain boundary,
   including artifact round-trip and prediction; review any required wrapper change.
3. After deployment approval, run the bounded Swiss M3 experiment and inspect its
   candidate/donor report. Admin approval remains separate from successful training.
4. Reuse that workflow for CMAL after confirming its actual donor, input coverage
   and onboarding status; coordinate with the model-onboarding owner to avoid overlap.

### Local qualification progress — after PR #621 merge

PR #621 merged at `0580f652`. The pinned Aquacast/FI runtime completed a local
CMAL-small LoRA rank-8/all-target one-epoch check against the hash-verified deployed
donor, using one synthetic station and two CPU threads. Retrain, save/load and
10-day prediction succeeded; the candidate's weights changed and survived the
round-trip, with the donor unchanged. Details and limitations are recorded in
`docs/operations/aquacast-fine-tuning.md`. The follow-up patch adds only `lora` to
the existing CMAL-small strategy allowlist, preserving the donor YAML hash and all
other model allowlists. Review and full CI are required before owner merge.
The Swiss M3 run, actual-weather skill evaluation and activation remain pending.
