---
status: DRAFT
owner: deployment orchestrator
parent: 2026-11-26-source-inventory-admission.md
---

# S2 — inventory retained inputs on model-declared periods

## Outcome and boundary

Extend the existing private inventory to describe rating-derived inputs that the
newer forecast route can read. Keep deliberately omitted stored discharge visible
as an omission. A completed inventory establishes its stated read coverage; it does
not establish usable model inputs, historical consumption or readiness to restore.

This is the S2 design amendment to the [source-admission plan](2026-11-26-source-inventory-admission.md).
S1 is locally qualified. S2 implementation, S3 matching-image qualification and S4
source execution remain separately gated. No application change, migration, public
command, provider acquisition, source write, forecast run or reconstruction is
proposed. Deployment profiles, helper code, full reviews and execution evidence
remain private.

The repository trace uses release 0.1.1199's source commit `3f8a8b2f`; the relevant
assembly, flow, rating reader and migration files are unchanged through this plan's
base `f508fc66`. This is code evidence, not a new observation of the installed source.
S3 must independently verify and qualify the exact admitted image. A different
release or implementation refuses until separately admitted.

## Existing capability and proposed change

| Boundary | Existing | Proposed |
| --- | --- | --- |
| Declared-grid route | The private planner deliberately omits stored discharge. Rating-present routes are not qualified. | Keep that omission; separately plan the native rating read selected by the observed job, binding and installed flow. |
| Selection | A bounded, lock-free adaptation of the production reader preserves custody, current capture checks and cumulative accounting. | Attach the matching persisted row's database recording time to each retained candidate. |
| Report | Per-member target disposition, partial coverage, bounded output and explicit unchecked categories. | Add native-read disposition, recording-time candidate counts and aggregate member dispositions. |
| Reconstruction | The temporary comparison permits limited renewal of bookkeeping. | No extension of that permission: recording times affect scientific eligibility and need a separate preservation decision. |

## Trace and implementation locations

Use the installed `src/sapphire_flow/flows/run_forecast_cycle.py` for job mode and
scoped rating opt-in; `services/run_group_forecast.py` and
`services/operational_inputs.py` for the selected assembly; `services/rating_inputs.py`
for physical periods and availability; and `store/rating_derived_store.py` for
persisted current evidence. Paths after the first share `src/sapphire_flow/`.
Also pin `types/rating_derived.py`, `types/rating_input_lineage.py`, `db/metadata.py`
and `alembic/versions/0095_rating_period_inputs.py`. Consult the
[model-declared runoff touchpoints](../touchpoint-maps.md#model-declared-native-runoff-input-days).
No model or ForecastInterface contract change is needed.

The private implementation manifest names every loaded helper by exact path and
final hash, including reused modules. Changed copies retain their old hashes only
as history. Verify the composed helper digest and installed application pins before
calling installed code, including period arithmetic. Preserve the prior qualified
helpers and evidence; refreshing hashes alone never qualifies the new route.

## Sequential milestones

One implementer owns A–C. Each slice uses the same frozen manifest and typed member
ordinals; none expands the admitted station/model cohort.

| Slice | Concrete private paths | Acceptance |
| --- | --- | --- |
| S2-A — route and roots | `planning.py`, `roots.py`, `report_builder.py`; `metadata.py` only for a demonstrated membership mismatch | One native-read interval supplies root selection, empty/scope checks and the runner. Stored-target omissions stay separate. Prove the installed DB metadata and guarded full-row read include and byte-account `recorded_at`. |
| S2-B — retained candidates | `census_batched.py`, input manifest | Matching persisted records carry their actual UTC recording-time bound. Missing or wrong-scope evidence refuses. Retained late-bound records still count toward the complete dependency inventory. |
| S2-C — report and integration | `runner.py`, `report_builder.py`, `report_v2.py`, `protocol_v2.py`, focused tests | Strictly versioned report distinguishes queried, empty-authority, omitted and pending members; cumulative coverage and output limits hold. Final helper checks and one independent patch review precede S3. |

### A — route and root contract

Admit the new native route only for a group model with a discharge-only daily START
declaration, matching selected Weather V2 grid, MEAN or MAX discharge aggregation,
and one matching binding in the observed provisional-rating job configuration.
Standard mode never gains rating inputs because a binding exists. An otherwise
included declared-grid member in provisional mode with zero or multiple bindings
refuses. Excluded members retain their reason; nongrid routes retain existing behavior.

Keep stored `targets` and `omissions` disjoint and complete. A separate native read
can overlap an omission, but cannot turn it into a measured-discharge read. Use the
model's per-target discharge lookback, distinct from its weather lookback. Reuse
the installed grid's `lookback_bounds` and `NativeRatingInputs.period_start`:
the lower bound is aligned upward from the later of model start and permitted
reconciliation start; the upper bound is the declared target end. Do not include
a partial leading period or extend into the ordinary raw-target freshness window.
An empty authority interval is unqueried, not a successful query returning zero.

Replace target-only assumptions in `roots.windows`, the rating-subset/empty checks
in `collect_roots`, and the runner's `plan.targets` lookup. The same native-read
value must reach all these sites and the census selection. Construct one prebounded
union of ordinary roots and measured water-level roots in the native intervals,
including rejected candidates needed by the reader. Never query stored discharge
for the newly admitted declared route. Include native root IDs in the existing
fixed-point capture/batch/sighting closure; shared rows count once across members.

### B — recording-time contract

After the existing guarded full-row read, match the persisted fingerprint and verify
tenant, station and observation identity. Parse its `recorded_at` as an aware UTC
instant and attach it to the recomputed candidate. Missing, null, malformed or
wrong-scope recording evidence refuses. There is no fallback to measurement time,
validation time or a migration number. Unreconciled and stale evidence retain the
inventory's conservative HOLD policy; this does not imply that the operational
forecast, which can omit unreconciled inputs, necessarily fails.

First prove that the installed DB metadata exposes the column, the guarded read
returns it, and the cumulative encoded-byte calculation includes it. This permits
reuse of the full-row read without a new SQL shape. If the proof fails, hold and
review the precise missing boundary. Do not fabricate a table mirror, broaden the
SQL allowlist or call the production reader's locking queries on the read-only
connection. `sql_guard.py` and `guarded_census.py` stay unchanged unless a concrete
missing boundary is demonstrated and reviewed.

Validation time drives current-evidence revalidation. Forecast issue time determines
whether the database recording-time bound is at or before that issue; equality is
included. Keep candidates with later bounds in the inventory and count them
separately. The report must say that migration 0095 gives pre-existing records a
migration-time upper bound: a bound after an issue does not prove late original
arrival. Do not infer or export the migration instant. These are current retained
candidates, not a reconstruction of a historical forecast's consumed inputs.

Amend the 35-table ledger with this column's meaning and the migration's insert
trigger, lineage guard and restricted history projection/ACL changes. Distinguish
the bounded input-column read from release-code tracing and isolated tests of the
related objects. Live function bodies, history contents and restoration remain
unchecked; a matching schema revision is not proof of them. No backdating, trigger
bypass or new bookkeeping exception is authorized.

### C — report and limits

Retain stored-target disposition and add a closed native-read disposition: outside
the new route, pending, empty authority or queried. Preserve existing legacy rating
diagnostics. For queried native reads, report candidate counts whose database
recording-time bounds are at/before or after the issue; counts partition the returned
persisted records. Keep held/stale counts explicit on HOLD. Export no timestamps,
fingerprints or private member identifiers. Version the strict private report
format; do not reinterpret older evidence or add a public compatibility layer.

PASS requires complete planned dispositions, observed route identity, successful
bounds/closure and final identity rechecks. Pending members forbid PASS. At least
one applicable read must actually be queried; an all-omitted/unqueried inventory
cannot PASS. Aggregate member dispositions must expose empty or omitted members
in mixed cohorts. PASS means this partial inventory completed, not full coverage
or usable inputs. Preserve partial coverage on failure and never turn missing
evidence into zero.

Weather, tensors, protected/history readers, final input availability, reducer-only
failures and restoration remain unchecked. Byte totals remain lower bounds. No
scientific reduction is added to the inventory runner.

Keep all existing limits: 128 admitted members, 64 KiB SQL, existing cumulative
table limits, 256 MiB selected-row bytes, 16 closure rounds, 8-second statement and
1-second lock timeouts, 55-second child/65-second controller deadlines, 2 GiB address
space, 30/35-second CPU limits and 16 KiB output. Preserve cancellation, rollback,
controller provenance and cleanup. No truncation, scope splitting or larger limits.
The member ceiling is not a guarantee that every maximum-size report fits. Prove
the actual whole-cohort report fits before S3; otherwise return the existing closed
output-limit HOLD.

## Verification and exit gates

- Route/window tests cover mode/binding mismatch, unsupported periods and drift,
  non-midnight grids, distinct target/weather lookbacks, exact authority boundaries,
  empty authority and shared roots. An omitted-plus-native-only cohort reaches root
  counting; no stored-discharge read occurs. Selection outside the root union refuses.
- Selection tests cover recording bounds before/equal/after issue, absent or invalid
  evidence, wrong scope, changed validation time and unreconciled/stale records.
  Preserve native instants and ordinary/legacy results. Late-bound candidates and
  their dependencies remain in accounting and closure.
- Guard/report tests cover cumulative limits before materialization, joined evidence,
  rejected locking/writing/unaccounted queries, impossible count/disposition states,
  partial HOLD, all-native completion and all-unqueried refusal. Test report size for
  the admitted cohort and worst-case 128-member counts; overflow must stop explicitly.
- An isolated synthetic Postgres fixture compares the adapted selection with the
  pinned production current reader. The diagnostic uses the worker role in one
  read-only repeatable-read transaction; the reference reader uses its normal
  permitted transaction against the same stable fixture. Exercise multiple six-hour
  chunks, exact boundaries, the 1000-row chunk cap and an unreconciled source. Keep
  raw parity separate from the inventory's stricter HOLD policy. Use normal inserts
  and injected issue times around actual recording instants, never disabled triggers
  or backdated writes. Test the real reducer's period/recording exclusion on both
  selected record sets without adding reduction to the diagnostic.
- Final helper tests, Ruff and changed-module types pass; one independent patch
  review completes before runtime qualification. S3 verifies the exact image and
  reuses unaffected evidence. No source execution or reconstruction follows from S2.

The [comparison guide](../deployment/operational-comparison.md) explains the
administrator consequence and links maintained configuration/model references.
Implementation history stays here; private execution details remain outside Git.

## Independent review disposition

One independent Claude design review requested clearer root/runner integration,
complete code pins, exact binding classification, migration-bound wording,
column/byte-accounting proof and report coverage/size tests. Those requirements
are incorporated. The reviewer requested no repeat design review; the subsequent
patch review must verify the implementation. Full review reports remain private.

The suggested compact format to guarantee reports for every 128-member case is
deferred. The unchanged encoder already returns an output-limit HOLD for such a
synthetic case. Retain that boundary and require the actual admitted cohort to fit;
do not add a new capacity promise to this increment. This disposition is presented
for owner review. S2 remains DRAFT until the orchestrator records its admission.

```json
{"phases":[{"id":"S2-design","status":"DRAFT","depends_on":["independent_plan_review_complete","owner_review_disposition"]},{"id":"S2-A","status":"DRAFT","depends_on":["S2-design_READY"]},{"id":"S2-B","status":"DRAFT","depends_on":["S2-A"]},{"id":"S2-C","status":"DRAFT","depends_on":["S2-B"]},{"id":"S3","status":"DRAFT","depends_on":["S2-C","independent_patch_review","verified_current_image"]}],"source_execution":"NOT_ADMITTED","reconstruction":"NOT_ADMITTED"}
```
