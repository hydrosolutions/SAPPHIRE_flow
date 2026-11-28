# Historical skill against reported water-level thresholds

The optional rated-threshold path in `compute_skills_flow` evaluates discharge
hindcasts against reported water-level thresholds using the rating table valid
for each complete forecast/reference period. It recomputes ordinary discharge
scores in the same generation. It never changes `station_thresholds`, alerts,
model activation or station status.

The statistic is **water-level equivalent of a discharge aggregate**. A converted
daily discharge mean is neither mean water level nor an instantaneous flood peak.
Reanalysis hindcasts are retrospective model evaluation, not archived operational
forecast or historical warning-system performance. Retain each model's training
exposure and declared/legacy period context when comparing results.

## Freeze the input and reference evidence

Use the existing tenant/network/station-code/provider-id binding to obtain public
warning/danger metadata; do not match station names approximately. Missing danger
levels remain missing. The canonical `RatedThresholdManifest` contains:

- Station UUID, tenant UUID, network, station code and positive API station id.
- A credential-free HTTPS source URL, retrieval time and SHA256 of the retained
  raw response. A live station's modification time is not a threshold effective date.
- `thresholds`: named finite levels in metres. Names are lowercase identifiers,
  up to 40 characters; warning must be below danger when both are present.
- `reference_basis`: `verified_common_zero` or
  `owner_assumed_same_zero_unverified`, with operator, evidence and approval time.
  The latter requires an explicit owner decision for the specified stations.
- Complete canonical `CurveSnapshot` values, including id/version, validity,
  interpolation, points and delivery provenance. Use `curve_snapshot` on the
  dated store records; do not substitute a current curve for historical curves.

`store.rated_threshold_codec.parse_rated_threshold_manifest` is the boundary
parser. The manifest is limited to 2 MiB, 16 levels, 128 curves and 4096 points
per curve. Duplicate curve identities and malformed evidence are rejected.
Keep the raw provider response and approved manifest in the private evidence
folder. Future station onboarding can reuse this metadata/parser; reported levels
must not automatically become operational alert settings.

Current reported levels have unknown historical effective dates. Every rated
context therefore states `current_snapshot_historical_effective_dates_unknown`.
The Nepal owner approved the unverified common-zero assumption only for stations
447, 604.5, 647, 670 and 684 for this retrospective MVP evaluation. Do not extend
that decision to another station or change operational datum rules.

## Reader rollout, dry run and publication

1. Deploy schema-2 readers and migration 0098 with no rated writer invocation.
   Existing schema-1 threshold contexts and ordinary skill reads remain supported.
   Confirm API, forecast, scoring and review consumers and the retained rollback
   image can decode schema 2. Do not count an ingest-only process as a skill reader.
2. Record that check as a `RatedThresholdCompatibility` JSON receipt: `operator`,
   aware `verified_at`, `schema_version: 2`, and `consumers`, five `[role,image]`
   pairs with roles `api`, `forecast`, `scoring`, `review`, `rollback`. These are
   operator attestations of performed checks, not an automatic deployment detector.
3. Finish hindcast appends and ordinary recomputation for the intended run. Admit
   only one scoring writer per station/model/artifact. Retain the run id, model
   identity and period/displacement/source catalogue used for ordinary scoring.
4. Invoke the existing `compute_skills_flow` with its usual arguments plus
   `rated_threshold_manifest=<canonical JSON string>` and `rated_dry_run=True`.
   No compatibility receipt is needed for this dry run; skill generations must be
   enabled. It returns computed scores/diagrams and logs each stratum's complete
   coverage context without storing or publishing anything.
5. Review excluded periods, unavailable thresholds, event counts and unknown
   probability tails. To publish, pass the same manifest and
   `rated_compatibility_receipt=<receipt JSON string>`, with `rated_dry_run=False`.
   Omitting the receipt refuses writes. No manifest preserves existing behavior.
6. The task hashes the exact forecast values, source/period metadata and fetched
   observations, then re-reads them before writing. A changed inventory aborts.
   This check complements the operator single-writer rule; it is not a database
   lock against concurrent ingest. Publish only after every cohort and every
   score/diagram write succeeds. A failed attempt leaves the prior generation
   visible. The nullable generation column retains full manifest, receipt,
   hindcast ids/run ids, issue bounds, input digest and coverage diagnostics.

After schema-2 rows exist, rollback requires a compatible image. Disabling the
writer does not make existing rows readable by schema-1-only consumers. Migration
0098 refuses to discard retained evidence; restoring or retiring that evidence
requires an owner-approved recovery procedure. Back up before migration using
the host's normal deployment runbook.

## Interpretation and API

For a unique qualified rating, compute the discharge crossing Q*=f(H_threshold).
Compare Q_obs > Q* and use the original discharge member distribution or quantile
CDF at Q*. This preserves nonlinear-curve probabilities and retains floods whose
numerical inverse water level would be outside the table. The threshold itself
must be in range and unambiguous; no extrapolation is admitted. Conflicting or
partially overlapping curves, validity boundaries and missing curves are counted
as exclusions. Exact physical duplicate deliveries collapse without deleting or
losing their identities.

Scores use distinct names `brier_stage_equivalent_<level>`,
`bss_stage_equivalent_<level>`, `pod_stage_equivalent_<level>`,
`far_stage_equivalent_<level>` and `csi_stage_equivalent_<level>`.
Reliability/ROC use the existing diagram types and threshold identity
`stage_equivalent:<level>`. All use the same resolved sample subset, strict
exceedance and decision probability 0.5. Unknown quantile tails stay unresolved.
Undefined metrics are omitted. BSS uses the scored subset's in-sample event rate,
not an independent climatology hindcast. `low_event_count` means fewer than ten
events or non-events; it is a reporting convention, not a universal validity test.

Existing score and diagram APIs expose a discriminated `threshold_context`:
schema 1 retains its original shape, schema 2 adds the reported level/reference,
manifest digest, paired/qualified/scored counts, event counts, exclusions, curve
use counts and crossing range. Check `schema_version` before interpreting fields.
The ordinary `evaluation_context` still carries declared periods/displacement.

For a generation id returned by the skill API, reviewers can fetch:

```text
GET /api/v1/stations/{station_id}/skill-generations/{generation_id}/threshold-context
```

The station-scoped route returns source/retrieval/assumption evidence, threshold
values, curve ids/versions/validity/physical hashes, input inventory summary and
all stratum diagnostics, including thresholds with no resolved scores. Each
declared diagnostic links by digest to a keyed catalogue of the existing
`SkillPeriodPayload` values, preserving label, aggregation, reference displacement,
availability policy and native source evidence even when no threshold score exists. It does
not repeat rating points. Missing evidence or a generation for another station
returns 404 after the existing reviewer/station authorization checks. Responses
are capped at 4 MiB. Full snapshots remain in the generation ledger.

The Nepal preliminary 2013–2019 inventory qualifies warning levels at 447, 604.5,
647 and 670, and danger levels at 447 and 647, except curve-boundary days. At 684
both reported levels exceed the historical tables; report unavailable rather
than extrapolating. These are eligibility findings, not calculated skill values.
