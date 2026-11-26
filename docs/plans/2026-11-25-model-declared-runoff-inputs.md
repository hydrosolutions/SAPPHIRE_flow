# Live runoff inputs driven by model declarations

status: READY

Owner: Nepal/model-onboarding session. Branch: `fix/model-declared-runoff-inputs`.
Base: `1b6e2236` (refreshed main, including #665/#667). Supersedes the live-input prerequisite of the separate
`feat/declared-grid-hindcast` draft; no hindcast/scoring implementation in this PR.

## Outcome

Supply available rating-derived runoff to models declaring a daily grid instead
of the current unconditional missing-discharge path. Reuse `ModelTimeGrid`,
`TimeGrid`, `PeriodLabel`, `ForecastPeriodDefinition` and model-declared target
aggregation. No model ID, station, country or 18:00 special case. At onboarding,
the operator confirms the model day against the model/artifact declarations;
runtime preparation consumes those declarations without repeat questions or a
second deployment-specific day definition. Unknown or contradictory declarations
must not be repaired by silently changing timestamps or training metadata.

The owner accepts a 15-minute DHM/model-day approximation. Source daily means and
native rating-derived points are different inputs: native points can be bucketed
on the actual model grid; the 15-minute approximation must not be used to shift
UTC-midnight aggregates by 18 hours. Ordinary daily-mean import alignment and
hindcast verification retain their separate scope and explicit source intervals.

## Concrete changes

1. **Onboarding contract.** Validate model/artifact grid compatibility before import
   storage, using the existing FI training-grid contract. Expose the declared day
   (step, origin, label, target aggregation) in the import validation output and
   onboarding guide. The operator supplies/confirms this once in the model package configuration and
   artifact import manifest; do not add a second runtime override. The FI adapter
   validates the deserialized artifact in `deserialize_artifact`, which the import
   service already calls before any registry/artifact/provenance write.
   Keep existing legacy import behavior unless this assertion/new declared-grid
   path is requested; no invented declaration for old artifacts. No FI/Aquacast
   dependency update or upstream contract workaround.
2. **Period reducer.** Extend existing rating aggregation with an explicit period
   definition, keeping omitted-argument legacy behavior. Derive physical completed
   buckets through `TimeGrid.floor/lookback_bounds`, aggregate using the declared
   supported method, and label only afterwards (START/END). Preserve actual source
   instants, measured precedence, rejected/omitted-source evidence, partial-coverage
   warnings and provisional purpose. Support daily MEAN and MAX of native point discharge, with explicit partial
   support warnings. Refuse SUM: sparse point discharge has neither complete
   interval support nor volume units. Never silently substitute MEAN. A sample
   exactly at the right boundary belongs to the next bucket under either label.
3. **Availability.** Add a database-assigned `recorded_at` to each exact retained
   rating-derived fingerprint. The existing caller-supplied `captured_at` is NOT
   a knowledge timestamp. Existing rows receive the migration timestamp as a
   conservative upper bound, never reconstructed past availability. A database
   insert trigger prevents backdating; immutable-row protection remains in force.
   Read `(fingerprint, recorded_at)` in the same current-evidence transaction,
   tenant/station scoped, and attach it outside the source fingerprint content.
   A day uses only source versions recorded by issue time; unknown/later versions
   are excluded, and an empty day stays NaN. Its availability is the maximum of
   included fingerprints' recorded times and physical closure. Pass nullable
   Int64 UTC microseconds in `available_at_us:discharge` through joins/casts into
   FI; missing slots remain null. Aquacast masks null or post-issue availability,
   so test the actual FI payload as well as the assembler's missing-data gate.
   Both native-capture and approved-legacy rows use the same conservative basis.
   An old issue cannot consume evidence inserted after it; insertion and commit
   are distinct (see implementation review disposition below). No availability
   is invented from observation creation, source timestamp or validation `at`.
4. **Live assembly.** Replace the declared-grid `rating_inputs` rejection with the
   supported period-aware path. Request complete physical periods starting at or after
   `max(model_start, issue_time - reconciliation_days)` (ceil onto the grid);
   stop at the last complete physical boundary. Never read or fill a partial
   leading period outside that authority. Aggregate after all six-hour read chunks
   have been combined. Pad the remaining model slots with missing values,
   never extend reconciliation authority automatically. Respect model-declared
   per-variable lookbacks and missing-data tolerance. Preserve selected weather's
   physical support and evidence. The reducer supports START/END labels, but
   this live weather path retains its existing daily START/group/matching-V2
   weather gates; END and subdaily operational weather remain explicit refusals.
   Remove only the rating-input rejection. Preserve GroupInputGate behavior and
   use the latest admitted native instant for freshness. Unsupported source/grid/aggregation combinations refuse.
   Resolve aggregation per variable; never impose a universal mean. Keep standard
   and provisional forecasts separated. No station promotion or model opt-in is
   performed by code; operational activation will use existing scoped bindings.
5. **Retained evidence/readers.** Introduce separate `RatingPeriodSupport` / `RatingPeriodInputLineage` types
   under a v2 policy carrying physical period starts, period definition and per-day
   availability/basis. Keep
   old canonical lineage bytes/fingerprints round-trippable. Extend rating-history
   reads to interpret the retained period rather than enforcing UTC midnight;
   preserve original native timestamps and disclose partial support. Extend the SQL security-definer projection with an additive migration,
   preserving v1 validation/results and existing role ACLs, resource bounds and
   protected-source checks. Update Python boundary/history models and aggregation
   accordingly. Old evidence serialization stays byte-identical; parser dispatches
   by policy. New periods retain the native source timestamps.

## Verification and exit gates

- Independent literal tests for midnight and two other phases, START/END labels,
  mean/max and refused sum, on-boundary samples, incomplete days, missing old history,
  unavailable/unknown constituents, conflicting duplicates and measured precedence.
- Stored-fingerprint availability test: changed derived evidence cannot inherit the
  availability time of an older fingerprint; reads stay tenant/station scoped.
- Actual FI-input tests prove values AND availability reach the model, that values
  available by issue survive masking and later values do not. Both group and station
  assembly regressions where supported; no assertion that stochastic quantiles must
  match across differently seeded or batched calls.
- Golden old-lineage round trip, new-lineage replay and rating-history API tests;
  ordinary/provisional isolation, model-import failures before writes, existing
  selected-weather and model-grid gates.
- Ruff, changed-module type checks, independent Claude patch review and full final
  CI before owner merge. Update specs, touchpoint map and onboarding/runoff runbooks.
- After approved release: one station/model canary using the same artifact/weather,
  inspect admitted runoff counts at the model boundary and compare with a no-runoff
  diagnostic. Do not call a closer value proof of improved skill. Deployment and
  source binding changes follow owner approval; no live patching of installed code.

## Dependency graph

```json
{"phases":[{"id":"contracts-and-review","depends_on":[]},{"id":"reducer-and-evidence","depends_on":["contracts-and-review"]},{"id":"assembly-and-onboarding","depends_on":["reducer-and-evidence"]},{"id":"tests-review-pr","depends_on":["assembly-and-onboarding"]}]}
```

## Coordination and exclusions

No edits to `services/hindcast.py` or historical preparation performance work.
The period reducer is intended for reuse by that subsequent correctness slice.
No retraining, new rating curves, source-data edits, availability-at-closure
assumption, mandatory full-year history, automatic publication or bias correction.
The current SE forecast discrepancy is motivation, not proof of its cause.

## Independent review disposition

One independent Claude review completed (local report, not committed). Its seven
blocking findings are addressed above: SQL projection migration/ACL coverage;
explicit FI availability propagation and masking; truthful database recording
time rather than validation time; separate v2 evidence types with v1 golden
compatibility; physical bucketing and aggregation refusals; import-time FI
validation before writes; and bounded complete-period reconciliation with
remaining live gates stated. The orchestrator sets READY for this scope.

No host changes are part of implementation. Migration conservatively makes old
rows available only from deployment onward; a future issue is needed to validate
operational consumption. Deployment and station/model opt-in use the existing
authorized rollout process after owner merge/release approval.


Implementation audit: artifact deserialization already validates the declared FI
grid, so that mechanism is reused without a duplicate check. Migration 0095 must
also extend `native_rating_lineage_guard`, whose v1-only insert check otherwise
rejects v2 before the history reader runs. Group target projection must retain
availability columns alongside each declared target and fill unknown members
with nullable Int64. These are covered by storage and group-boundary tests.


Independent patch review found no blocking/high issues. Medium findings resolved
with a stable migration-time default after explicit table lock (no heap rewrite),
SQL negative tests, a mixed-member availability test, and precise INSERT-vs-commit
semantics in operations/security docs. Existing v1 golden tests pass unchanged.
The current-version reader is not an as-of reconstruction of superseded sources.
This implementation does not enable historical declared-grid runs. Full CI remains
an owner-merge gate; local focused tests do not replace it.

CI follow-up: retain the existing qualification oracle against schema 0095.
Rating-store and worker round trips now distinguish pre-storage derived content
from database-assigned recording time, assert unchanged source evidence, and
check that reconciliation retries preserve the stored timestamp. Refreshed onto
main `44564ed5`, retaining release target 0.1.1198 under the development-phase rule.
