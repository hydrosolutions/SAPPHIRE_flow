# Nepal start-stamped CMAL replacement

status: READY

## Reviewed first slice

**Dependency qualification and metadata preservation: READY for isolated branch
implementation only.** The independent Claude review is complete. Its five
activation findings remain tracked below; this slice does not activate the model,
publish a release, or claim the remaining operational plan is READY.

Owner direction on 24 November: hold Aquacast 0.1.396 at the exact revision below;
do not require another Aquacast iteration. The owner implemented the FI #22 slice.
FI #23 is now merged: pin 0.2.10 at
`c1faa2e70b3832502efc5c22c99274635c027bb4` in both the root source and dependency
override, with the exact-version gate updated. The shared contract reserves
nullable integer `available_at_us`; pass it through unchanged, excluding it from
value renaming/scaling/NaN counting. No conversion to a different metadata name
and no new Aquacast version are necessary. An independent agent checked the PR
against the fixed Aquacast consumer, including closure, late, null and absent
availability behavior. The shim regression covers all four FI entry points.
The exact supported FI version gate follows the pin; do not accept an arbitrary
version range or silently downgrade. The application owns the public FI boundary.
No availability is invented or exposed as a physical measurement. Live dependency
adoption still requires truthful end-to-end assembly and old-model compatibility.

The first slice pins the dependency pair in this isolated branch, resolves the
lock, and preserves FI anchors and ModelInputs grid declarations through shim
unit conversion and station filtering. Add public shim behavior tests proving
metadata survives prediction and requirement projection, including exclusion of
an invalid-area station. Existing unit/name conversions remain unchanged. This
resolves the review's exact-version-policy finding and prevents known metadata
loss without guessing the unfinished operational/storage design. No new model
config, model activation or global nonzero-grid guard removal belongs to this slice.

The review's missing-discharge claim is corrected: this supplied config inherits
`min_past_discharge_fraction=0.0`, hence permits 365 missing discharge values.
That does not authorize silently masking previously consumed inputs in CMAL-small.
Preserving those inputs remains a release gate, with availability represented by
the shared contract rather than fabricated closure timestamps. Independent review
report stays local at `/tmp/nepal-cmal-start-grid-plan-review.txt`.

## Target and evidence

Replace the old static encoder with the owner's 23 November CMAL, not Engression.
Source checkpoint SHA256:
`d04c3942b8bbe152ddd049e7b7497716a70656bb2704429e2d00aa1155625a3e`.
Original config SHA256:
`9f2ef091d0d627afc03594df0795979b809aab80726856a98942fd1084a07c60`.
Its declared daily grid is START, 18:00 UTC (UTC+06 reporting dates), with 365
history days and up to 15 future days. Discharge's physical reporting boundary is
15 minutes later, explicitly acknowledged in the training contract. Do not turn
that acknowledgement into exact-match scoring evidence. Donor provenance stays
unknown under the owner's MVP decision; the declared temporal contract is known.

Upstream Aquacast main 0.1.396 at
`fc772226703940f26d29d8e62a5c39175851b2f8` includes reporting-window support;
its latest visible tag 0.1.390 does not. Merged FI 0.2.10 at
`c1faa2e70b3832502efc5c22c99274635c027bb4` supplies native TimeAnchor,
TimeGrid, PeriodLabel and output temporal metadata. Pin exact tested revisions.
Use FI's native declaration, never Aquacast's private delivery-contract keyword.

Base main is c6bf8480 (release target 0.1.1185), including the merged dependency
integration, quantile-skill and observation spike-QC fixes.
M2a selected-weather preparation
is merged. Model/observation/FI/output consumers remain on the old contract:
the shim rebuilds ModelInputs without grid metadata, the FI adapter does not
project anchors, and DeploymentConfig refuses nonzero daily origins. This work
implements the bounded operational standard-CMAL portions of M2b/M2c/M2d/M2e
from the hydrological-time milestones. It does not silently mark their broader
training, hindcast or skill work complete.

## Implementation sequence

### Operational design decisions resolving review findings

- Keep the existing model entries/configs and deployed legacy worker intact.
  Introduce `cmal_nepal_static_encoder_20261123` / `CmalNepalStaticEncoder20261123`
  with a separate vendored inference config. The new registered job selects only
  this ID in a dedicated worker pool; the legacy runtime cannot discover it.
  Retire the old static assignment/deployment only during qualified activation.
  Existing CMAL-small and climatology remain on their current runtime/grid.
  Host memory checked 24 November: about 13 GiB available. Record all actual image
  identities and job config checks; do not claim a uniform-version deployment.
  Pin the legacy forecast worker to its current image digest in its Compose
  service definition, not merely its running container. The replacement worker
  gets its own service, pool and explicit new-model-only job. CI-reuse delivery
  may load the new archive but must not recreate the legacy forecast service.
  Before activation, test rendered Compose identities and registered job routing;
  abort if the legacy digest, pool, model selection or configuration changes.
  A later uniform worker upgrade is blocked until truthful availability reaches
  CMAL-small. This release does not claim old-model inference on Aquacast 0.1.396.
- Project the model's FI anchor into an optional immutable SAP model-grid
  declaration in `ModelDataRequirements`; validate all selected assignments and
  group members against one declared grid before fetching. The explicit
  model-scoped job, not a station-wide/global config switch, chooses this path.
  Preserve the current global nonzero-grid guard. Unknown legacy declarations
  continue through the unchanged legacy path; declared mismatches refuse.
- Carry the delivered grid in station/group input objects and validate it against
  model and artifact declarations at the FI boundary. Use `artifact_training_grid`
  (the public FI declaration) at artifact load and prediction. For this model,
  UNKNOWN is a refusal. Bind source checkpoint/config hashes separately from the
  vendored operational config hash; import `expected_config_hash` is the latter.
  Operational config changes are limited to CPU and bounded loader/batch settings.
- Native prediction was tested with issue 24 November 00:00 UTC: its first START
  is 23 November 18:00 UTC, followed by 14 further periods. Therefore past bounds
  end at `grid.floor(issue)` and future bounds start there, not at the next full
  day. Keep the real issue and source-cycle cutoff unchanged. Full future-period
  support is still mandatory; an insufficient primary cycle must walk back to a
  complete older cycle or refuse. Never manufacture the pre-issue part of that
  period. Use V2 period request/proof/cache primitives already merged in M2a;
  thread this context through horizon negotiation rather than the legacy cached
  candidate shortcut, which explicitly refuses V2 context.
- Standard operational CMAL currently has no compatible recent measured daily Q
  at these stations. Build its declared 365-day missing-Q calendar on the grid;
  retain coverage diagnostics and the model-owned tolerance. Do not derive Q from
  stage in this standard job. Do not shift archived 18:15 DHM labels or invent
  availability. A later finite-Q producer must provide compatible physical support
  and truthful per-row availability before consumption; this slice does not claim
  historical fitting support. Shared FI metadata passthrough is already tested.
- Add an optional immutable `ForecastPeriodDefinition` on `ForecastEnsemble`
  containing step, origin, START/END label and aggregation. For the new model
  validate returned FI metadata and every row against the bound definition before
  QC/store. Persist it in a nullable JSONB `forecasts.period_definition` via one
  additive migration and expose it in detail/summary/overview serializers. Null
  means old/unknown, never infer START for existing rows. Include it in existing
  immutable header protection, safe-column grants and read validation. Leave the
  forecast-values hash algorithm unchanged: it hashes value rows, not headers.
  The network overview is a current dashboard consumer of these rows, so its
  compact response must preserve period meaning too. Derive period
  bounds from valid_time, label and step; never rewrite valid_time or issued_at.
- Preserve existing signed floored `lead_time_hours` storage semantics: the
  first tested period has start lead -6h and ends +18h. The explicit period
  definition distinguishes this from an instantaneous forecast in the past.
  Do not reinterpret this value as an end lead. New-grid forecasts are excluded
  from legacy skill/event-review paths until those paths explicitly support this
  period contract; they remain visible in forecast listing/detail/overview.
  Combining outputs requires identical period definitions; never silently merge
  known START with unknown or a different grid. No historical score rewrite.

The dependency/metadata slice received independent patch approval and merged as
PR #634 after all required CI checks passed. No host modification was made.

The revised independent review resolved model-grid, observation exclusion,
dependency-version and persisted-period concerns. Its remaining isolation
findings are addressed by the explicit immutable legacy-image and routing checks
above; missing availability is not repaired by claiming compatibility on the new
runtime. Its scope concern narrows integrity work to existing header protection
and grants, without changing value hashing or adding a new evidence mechanism.
The review report remains local. The orchestrator sets the operational plan READY
after that independent review and the above resolutions, with PR #634's full CI
passing. Readiness authorizes implementation, not release or activation. The
legacy-image isolation proof remains a mandatory deployment gate.

The next boundary slice adds typed model/input grid declarations and validates
artifact grids at load, predict and warm-start retrain. Its independent patch
review requested the retrain check; that finding is resolved with regression
coverage. Fully unknown legacy anchors remain unchanged. Operational producers,
new model registration and output-period storage/API are still outstanding, so
this slice alone cannot activate the replacement model.

1. Upgrade Aquacast and FI together, regenerate the lock using uv, preserve the
   optional-extra boundary and frozen deployment dependencies. Inspect upstream
   protocol, requirements, result and model/input docs. Adapt only actual API
   incompatibilities. Preserve old CMAL-small and climatology requirements and
   timing. Test base and Aquacast-extra discovery and existing artifact loading.
2. Bind the new model's inference configuration and temporal contract to its
   exact native checkpoint. Preserve architecture, static feature order,
   normalization and declared NaN tolerance. Only operational execution settings
   (CPU, zero loader workers, bounded batch) may differ, with an explicit source
   versus inference config audit. Do not infer training completion or donor
   training periods from the filesystem or fine-tuning split.
3. Carry model-declared step, origin and labels through the FI adapter and shim.
   Preserve grid metadata on every ModelInputs reconstruction, including station
   filtering and hindcast entry points. Validate agreement with actual UTC-aware
   series before prediction. Keep source stores/canonical native stamps intact.
   Translate labels only where physical interval bounds prove equivalence.
   Unknown legacy grids retain legacy behavior; a new declared grid must never
   fall back to UTC midnight. Reject incompatible groups before fetching/writing.
4. Complete the standard daily group operational path on the declared grid:
   bounds, selected weather history/future negotiation, ordinary discharge
   calendar/NaN padding, input quality windows and output metadata. Use existing
   explicit-grid weather helpers and complete-bucket/evidence validation. No
   partial past bucket or future observation leakage; keep actual issue instant
   separate from forecast period start. Missing discharge stays missing and
   max_nan is enforced. Do not shift daily measured DHM values 15 minutes to claim
   an identical interval. Until an interval-compatible observation path exists,
   exclude such values explicitly with coverage diagnostics rather than silently
   relabelling them. Preserve the approved strict weather NaN rules.
5. Store/serve the returned start-stamped forecast with truthful interval meaning
   and unchanged actual issue identity. Audit target metadata, valid-time
   conversion, integer lead handling, ensemble QC, selected-weather lineage,
   API summary/detail and dashboard interpretation. Reuse existing metadata if it
   unambiguously binds grid/label to each artifact/forecast; otherwise introduce
   the smallest explicit persisted contract. Old forecasts retain old meaning.
   Keep new-grid hindcasts, skill publication and default-off daily evaluation
   refused until their consumers meet the same contract; no misleading scores.
6. Scope activation to the explicit standard static-CMAL deployment and qualified
   artifact, preserving CMAL-small/climatology/ingest jobs and their old grid.
   Do not simply remove the global nonzero-grid guard. Require model, artifact,
   input and configured-grid agreement at import/activation/load/predict; unknown
   scientific provenance is allowed, unknown temporal compatibility is not.

## Verification and deployment

Use one boundary-valued native-weather fixture across aggregation, FI input and
output tests: exact 18:00 boundaries, accumulated flux support, temperature
support, leap/year rollover, complete versus incomplete days, history/future
split, first/last output interval and shortened source horizon. Verify the same
physical intervals survive relabelling; never validate only stamp arithmetic.
Test old models unchanged, wrong phase/label/artifact rejected, missing-Q tolerance,
station exclusion metadata, UTC awareness, serialization and API period meaning.
Run a CPU prediction with the actual new checkpoint before host import. Verify
finite ordered seven-quantile outputs and longest available complete horizon up
to 15 days. Record runtime/memory and exact versions/hashes.

Independent Claude plan review before implementation; one independent patch
review, affected docs and full CI after final edits. Owner approves/merges every
PR. No direct host code patch. After approved release, use CI-reuse delivery and
verified targeted activation. Import/activate the new artifact once, run a manual
standard forecast, verify stored records and authenticated API listing/detail,
then enable recurrence only after successful operational qualification. Preserve
17 operational/four onboarding stations and every publication/evidence gate.

```json
{"phases":[{"id":"review_plan","depends_on":[]},{"id":"dependencies_contract","depends_on":["review_plan"]},{"id":"operational_grid","depends_on":["dependencies_contract"]},{"id":"qualification_review_pr","depends_on":["operational_grid"]},{"id":"release_activation","depends_on":["qualification_review_pr"],"gate":"owner merge and approved release"}]}
```
