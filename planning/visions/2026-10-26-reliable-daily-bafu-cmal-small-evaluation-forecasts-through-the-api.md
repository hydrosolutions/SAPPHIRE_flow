# Reliable daily BAFU cmal_small evaluation forecasts through the API

## Confirmed outcome

Produce one daily `cmal_small` forecast for each eligible member of the currently
assigned Swiss BAFU group on the Mac mini, and make those forecasts usable through
the backend API. This is an evaluation deployment, not an operational warning
service. Its purpose is to prove the forecasting workflow before retraining the
model on local weather inputs and tightening forecast plausibility screening.

The owner confirmed this scope through discussion on 2026-10-26. This is a
standalone vision, not a Program Effort. It does not authorize implementation,
merging, live-data changes, forecast triggers or deployment by itself.

The `sapphire-flow-map` repository owns the dashboard/map. Our scope ends at the
API, including a clear API contract and handoff for that consumer. Rendering or
changing the dashboard is not part of this work.

## Forecast behavior

- Start with `cmal_small` and its current assignments. Do not expand membership or
  add sub-daily support as part of this delivery. Other models continue running.
- Produce one logical daily forecast, with issue time aligned to the model's daily
  input windows. Execution may occur later when compatible inputs are available;
  actual execution time, issue time, weather-cycle time and valid time must remain
  distinct and truthful. Preserve other models' schedules.
- Use available compatible weather data, including supported older weather cycles,
  rather than waiting for a perfect training-source match. Record weather source,
  cycle and age and preserve relevant quality flags. Retain configured fallback-age
  limits; an arbitrarily old complete cycle is not today's input. Meet declared model input
  requirements for coverage, units, timing and missing-data tolerances. Do not
  fabricate observations or silently bypass FI requirements.
- Local-weather retraining follows proof of the workflow. It is not a prerequisite
  for this delivery, nor a guarantee that every weather discrepancy will disappear.

## Independent group members

A station with unusable QC-filtered inputs must not suppress forecasts for healthy
members of the same group. Keep existing observation QC and its accepted-status
policy. A partly rejected history is not automatically an unusable history; the
remaining inputs are judged against the applicable model requirements.

Account for every assigned station, including input exclusion, model failure,
output-integrity rejection and produced forecast. Preserve expected membership,
identities and reasons instead of making unsuccessful members disappear. Healthy
outputs must not acquire a sibling's bad inputs or misleading quality/provenance.
A total failure remains a total failure when no member can run. Shared storage,
configuration and programming faults must not be disguised as data shortages.

Preserve the ForecastInterface contract, including per-entry partial-success
semantics and the division between pre-predict NaN gates and model-owned shape or
length failures. Reuse current merged repairs; correct a model violation in its
own layer rather than splitting batches or bypassing FI as a workaround.

## Reviewable output, not false quality assurance

Observation QC is unchanged. Forecast-output plausibility screening is initially
permissive for BAFU `cmal_small` evaluation: structurally valid but physically
questionable results remain visible through the evaluation API with clear QC
flags and can remain the preferred evaluation forecast.

Do not erase flags, label a failed check as passed, or hide that this is an
evaluation policy. Preserve hard integrity checks for malformed output,
non-finite values, wrong station identity, invalid timestamps and violated output
contracts. An integrity-rejected forecast is not a usable preferred forecast.
Diagnostic evidence may remain available under its existing authorized review
access without being promoted to a usable forecast.

Scope the permissive evaluation behavior explicitly. It must not silently relax
other models, other deployments, Nepal scientific-use restrictions, alerts or
publication rules. Prove the evaluation exception does not promote flagged results
to usable output through ordinary readers, combinations or caches/exports. Existing
authorized diagnostic visibility is distinct from usable forecast selection.
This delivery grants no new operational-warning or publication
eligibility. Harden plausibility screening only after workflow acceptance, under
a separate owner decision.

## Preferred forecast and API contract

`cmal_small` is the highest-priority evaluation model, and that preference must be
observable in API selection, not only recorded as an assignment number.

When its current structurally usable forecast exists, prefer it, including outputs
with non-blocking plausibility flags under the evaluation policy. If no usable
current `cmal_small` forecast exists, allow an explicitly identified fallback from
the existing models. Keep the latest `cmal_small` result and its age available
separately. A fallback, stale last-good result, absent row or overall green cycle
must never masquerade as a successful current `cmal_small` run.

The consumer must be able to distinguish model identity, issue/valid times,
freshness, fallback selection, QC flags, input quality and station-level outcome.
Preserve existing authentication, authorization, tenant scoping and protected-data
boundaries. No new access to restricted Nepal inputs or outputs is implied.

Specify deterministic current/stale and fallback behavior in the implementation
plan and test it with an injected clock. Document the chosen behavior for the map
team; do not require frontend changes to prove backend acceptance.

## Evidence of completion

Require seven consecutive logical UTC issue days of unattended scheduled operation
on the Mac mini. Freeze the expected roster from the current active assignment and
explicit station/configuration rules before each daily attempt; do not shrink it
after seeing failures. Report one authoritative outcome per expected member for
that logical issue day. Apply declared input requirements to determine which
members have usable inputs, independently of whether prediction succeeded.

A day counts only if every member with usable inputs produces a structurally valid
`cmal_small` forecast, every other expected member has an explicit reason, and at
least one `cmal_small` forecast is produced. No additional percentage threshold is
required; report produced/excluded/failed counts prominently so low coverage cannot
look like fleet-wide success. A model or integrity failure for a usable-input member
is accounted for but does not make that day pass. Configuration or assignment
changes during the acceptance window restart it unless separately pre-authorized
and evaluated. For each daily cycle:

1. Verify persisted `cmal_small` forecasts and values for eligible members, not
   merely Prefect completion or a healthy process endpoint.
2. Verify the authorized API exposes those results, QC flags and evaluation
   preference/fallback behavior correctly.
3. Account for every currently assigned station with an explicit outcome.
   Explained exclusions are acceptable; unexplained omissions and one failed
   member blocking healthy siblings are not.
4. Preserve genuine timestamps, source/age and lineage; do not count a stale row
   or a fallback as today's successful `cmal_small` forecast.

A manually pinned run, isolated replay or manual backfill cannot repair a counted
day after the fact. Restart the consecutive window after a non-counting day.
Demonstrate mixed healthy/unusable members in regression coverage even if the live
week's data happen to be uniformly healthy.

Deterioration notifications are lower priority and may follow core acceptance.
Their intended direction is to warn when expected model/station production drops,
without repeated warnings for the same unresolved incident. Minimal outcome and
freshness visibility needed for acceptance is not deferred with notifications.

## Starting evidence and reuse

As measured on 2026-10-26, the Mac mini runs 0.1.1039. It has only the two
`cmal_small` forecasts issued on 2026-09-25. Its 138-member group encounters an
empty/populated target-frame stacking error before prediction when members 2206
and 2414 have no QC-accepted discharge observations. Their data arrive but are
marked suspect. In the checked 30-day window, 130 members have accepted discharge
on all days; that is not proof of complete model-input readiness.

Current main at `8f1297623d322d580134d5a152f71e2587206dce` already includes
member-local missing-required-target handling, static/future isolation, expected
roster outcomes and the Aquacast area-conversion partial-failure repair. Verify
these in the candidate rather than rebuilding historical Plan 514/519 work.
Existing tests/source are not proof of live deployment acceptance.

Other known gaps include daily issue-time alignment (Plan 326), recent incomplete
NWP fetches, and API preference/readers that do not yet implement the confirmed
permissive evaluation behavior. Address those needed for the daily API outcome;
do not mistake a QC-isolation-only patch for completion of this vision.

## Delivery boundaries

Work in an independent clone. Leave the original shared checkout, its unexplained
`core.bare=true`, and its local changes alone.

Nepal6 owns `/Users/bea/Documents/GitHub/SAPPHIRE_flow-nepal21-test-deploy`, its
private delivery diagnosis, historical replay and deployment adoption. Its
reported base is the same main SHA above, published version v0.1.1076; its delivery
attempt did not activate release images. Do not change its checkout, dependencies,
configuration or running attempt. Coordinate overlapping shared-file edits before
implementation. After owner merge, supply exact merge SHA, review references and
validation evidence; Nepal6 schedules explicit adoption in a subsequent release.

Use the repository's independent review and owner approval requirements. Scope
code changes to demonstrated gaps and keep scientific/data-use isolation intact.
Before any Mac-mini update or acceptance run, require an owner-merged exact SHA,
immutable release/image identity, verified migration state, backup and tested
rollback path across the full upgrade from its older release. A branch/local image
cannot begin the seven-day clock. No implicit deployment permission comes from this
vision. Stop at any missing approval or prerequisite rather than claim success.
