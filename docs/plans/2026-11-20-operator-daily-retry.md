---
status: READY
owner: orchestrator
created: 2026-11-20
issues: [590]
---

# Audited operator retry after daily automatic attempts are exhausted

## Problem and scope

On Nov20 both scheduled CMAL attempts finished before release1164. The corrected
runtime cannot be exercised through the operational workflow until tomorrow:
manual Prefect invocations intentionally cannot impersonate scheduled work.
Owner approved an explicit audited operator retry, while keeping the automatic
budget at two. This is shared daily execution behavior, with no Swiss/Nepal switch,
no Aquacast edits, no new model support, no backfill or changed publication policy.
Baseline origin/main325386b9; live release1164 remains untouched during development.

## Smallest useful behavior

An authorized operator on the existing trusted Prefect/host operations surface can
preview and request one retry of an exact exhausted daily attempt. Each request has
an immutable UUID, actor, bounded nonempty reason, exact parent, expected artifact,
and expected current configuration digest. Duplicate delivery of the same request
never invokes inference twice. There is no automatic retry of operator requests.
The first version supports today's logical UTC day only, exhausted automatic
attempt2 with a terminal incomplete/failed/abandoned result, and no active owner.
Completed days, historical replays and artifact/roster changes stay out of scope.
Another operator request after a terminal operator failure must explicitly name
that latest parent; the two scheduled records remain unchanged.

The new attempt freezes the same roster and artifact, but may accept the current
configuration digest explicitly obtained by preview. This supports deployment/input
repair without silently treating changed settings as the original experiment.
Validate all other existing scientific input, age, artifact and output contracts.
Record software version/source identity using existing runtime identity facilities;
original logical issue time, weather reference time and execution time remain distinct.
A day rollover before admission refuses; once admitted, the frozen day stays fixed.

## M1 — Typed ledger admission and schema

Reuse the daily attempt table and transactional per-day lock. Add a distinct
operator_retry trigger and typed request/claim; never extend scheduled ordinal1/2
or reuse disposable manual/replay identities. Operator rows have no scheduled tick
or scheduled ordinal; retain explicit parent and request metadata. Scope parent
lookup by tenant/deployment/group/model/data-use/day. Validate expected latest parent,
exhausted automatic history, terminal state, roster/artifact/config expectations
inside admission. Recheck configuration at execution as existing code does.

Extend the active-owner uniqueness constraint to scheduled AND operator operational
attempts; one-shot disposable identities remain separate. Automatic admission must
see any active operator owner under the same lock, while its own two-attempt budget
is unchanged. Request UUID is unique and payload-bound: identical repeated requests
return their existing identity/status; different payload with same UUID refuses.
No mutation/deletion of finalized attempts and no age-based ownership reset.
Extend existing exact-owner termination recovery to operator attempts, preserving
proof that Prefect is terminal and every writer stopped. No automatic crash replay.

Exit: migration plus real PostgreSQL concurrency/idempotency tests; default
scheduled behavior and disposable one-shot tests unchanged.

## M2 — Existing workflow entry and operator command

Provide a small preview/submit command using existing operations conventions. It
resolves current settings and exact eligible parent, presents the immutable target
and configuration change, and submits to the existing forecast deployment with an
explicit operator request parameter. No second scheduler, deployment-wide settings
mutation, spoofed scheduled metadata, direct model invocation or standalone loop.
The command is for authorized host/Prefect operators, not a public reviewer API;
actor text is audit context, not an authentication mechanism. Reuse existing trusted
control-plane access and restrict scope from configured deployment identity.

At flow entry parse the request into a domain type; verify real top-level Prefect
owner/deployment identity, parameter exclusivity, and day before expensive shared
work. Admission failures do not fetch weather or observations. Reuse the current
weather/observation assembly, group executor, QC, evidence, persistence and finalizer.
For MVP the existing shared acquisition cost may remain; report that cost honestly.
Do not optimize the slow timestamp lookup in this patch. No automatic task retry may
repeat inference after uncertain persistence. Ensure every early-exit path accounts
for an admitted operator attempt, as scheduled execution already does.

Exit: supported command + actual flow integration tests; no-op duplicate request,
refused stale preview, active owner, midnight rollover and prerequisite failure.

## M3 — Authoritative reads, docs and verification

Operational authority readers include operator attempts, using explicit parent-chain
order rather than scheduled ordinal. Never classify an operator result as automatic
or allow an isolated replay to become current. Preserve existing policy for active,
failed, incomplete and completed attempts, selected exact forecast IDs and withholding.
Do not implement issue581's alternate previous-success policy here. A failed operator
attempt remains honestly failed. Existing natural-key supersession and committed
output verification remain required; test that earlier evidence stays immutable and
no reader selects invalidated references during execution or after failure.

Update summary/API provenance where needed so operators/reviewers can identify
operator retry, actor/reason where appropriate to existing authorization, actual run
and execution time. Do not leak operations-only actor/reason in public responses.
Expose existing public output provenance consistently and update contract if changed.
Document preview, submission, expected latency, status inspection, crash recovery,
rollout/migration compatibility and the next-day alternative. Include example from
both configurable deployment identities in tests; no country-specific code.

Run focused types/store/flow/API tests, lint/types, full suite after final code
change and one independent patch review. Owner retains PR approval/merge and live
operational adoption. This plan does not change the running database or trigger a run.

## Review and dependencies

One independent Claude plan review before orchestrator READY, per owner's supplied
workflow. Review reports remain local. One implementer owns these sequential steps.
Coordinate shared cycle/ledger/API surfaces with concurrent model-input work before
implementation; do not touch Aquacast, FI pins, radiation inputs or weather-fill policy.
Use repository release-target policy, not historical version bumps.

```json
{"M1": [], "M2": ["M1"], "M3": ["M2"]}
```

## Independent review dispositions — implementation invariants

Independent Claude review completed ($0.0356089); report kept at
/tmp/operator-retry-review.json. Orchestrator incorporated the concrete concerns
below and sets READY. This is sequencing authority, not owner PR/merge approval.

- Ship M1–M3 together in one PR/release. No operational submission or activation
  until all three pass review/tests and owner accepts rollout. No intermediate
  migration-only or command-only deployment. An extra feature flag is unnecessary.
- Active uniqueness: one partial unique index over tenant/deployment/group/model/
  data_use/issued_at WHERE state='active' AND trigger_kind IN
  ('scheduled','operator_retry'). Both admissions use the same advisory/day lock.
  Freeze admissions/drain writers for migration using the existing deployment
  procedure. Migration preserves every existing row; check for conflicting active
  rows before replacing the old scheduled-only index. Old code works with the
  migrated schema only before operator rows exist. After activation, rolling back
  to old readers/writers is unsupported without a forward-compatible release;
  no destructive downgrade deleting evidence. Document this M1 compatibility gate.
- Canonical request binding includes full scope/day, parent UUID, artifact UUID,
  current configuration digest, exact roster identity, normalized actor and reason.
  Actor max128chars, reason max500chars, nonempty after trimming. Persist parsed
  fields (or existing canonical serialization/hash conventions), not an informal
  string. Same UUID/same payload returns active OR terminal status with
  admitted_new=false; only the transaction creating an active claim returns a
  right to execute. Domain representation uses an enum, not a boolean state.
  Different payload refuses. Two UUIDs racing for the same parent yield exactly
  one new claim; loser refuses latest-parent/active-owner mismatch. No concurrent
  branch of the parent chain. Add real PostgreSQL race tests.
- The admission result is an execution capability for the creating invocation,
  never inferred from a matching owner UUID. Prefect redelivery/flow retry returns
  existing status and does not execute. Inference/persistence tasks have no retries;
  do not re-enter after an uncertain write. No extra admitted/running state needed.
- Successful terminal operator attempt becomes authoritative for its day. Existing
  selection policy applies to the latest operator attempt: while active and after
  a failed/incomplete outcome, never silently return older successful outputs.
  Serve only exact references allowed by that outcome and existing withholding
  rules; no older-success fallback and no union of attempt results. Tests must
  prove reader behavior during active, completed, failed and partial outcomes,
  including superseded ordinary rows. This is not issue581's policy redesign.
- Execution compares live configuration to the newly frozen request digest, NOT
  to the parent's old digest. Retain parent digest, new digest and software identity.
  Reuse existing configuration hashing; explicitly compare artifact UUID/hash and
  complete roster independently so omitted fields cannot evade checks. A mismatch
  after admission finalizes known no-output configuration failure before inference.
- Trust boundary: authenticated callers with trigger rights on the deployment's
  trusted Prefect control plane can request operator execution directly as well
  as through the CLI. CLI text is not stronger authorization. No anonymous/public
  API support or bypass of tenant/configured deployment scope. One child per latest
  parent, with no automatic retries, enforced in the store. Restrict normal trusted
  control-plane access operationally; do not invent a second identity platform.
- Derive logical issue date from the scoped parent row. Compare it to the injected
  authoritative UTC clock at atomic admission; preserve that frozen issue after
  midnight and use existing explicit weather-reference rules. A later queued flow
  that has not been admitted refuses an old-day request. Existing deployment/pool
  concurrency protects cross-day resource overlap; day-scoped locks alone do not.
- Known stopped failures after admission terminalize through existing fail_unstarted
  handling. Crashes or uncertain commits MUST stay ACTIVE until explicit verified
  termination/recovery; do NOT implement reviewer suggestion to finalize every
  exception indiscriminately. Next day's ledger key is distinct, while pool/worker
  concurrency still prevents simultaneous owned execution. Test both cases.
- Include public-response leak regression for operations-only actor/reason.
