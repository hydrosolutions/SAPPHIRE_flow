---
status: READY
owner: orchestrator
created: 2026-11-22
---

# Deliver the FI-declared bounded daily forecast horizon

CMAL static encoder declares four AT_MOST future variables, minimum 1 and
maximum 15 days. The live Gateway archive has 11 complete days. Selected weather
currently requests and admits only the maximum; capacity PR #613 alone cannot
activate this model. Unknown training provenance is already accepted for MVP.

Independent Claude review completed. Orchestrator resolved the final finding by
choosing a defaults-off candidate acceptance predicate in resolve_candidate;
rejecting a group with an interior gap continues the existing walk-back in the
same invocation. Exact callers do not supply the predicate. Full reports local.

## Design

Preserve all persisted v1/v2 weather types, hashes and exact receipt rules.
Negotiate an exact request before OperationalWeatherRefresh.begin, using only
read-only candidate fetching and the same qualified daily support reducer as
normal candidate admission. This is not a change to artifact configuration.

1. Expose typed per-feature minimum/maximum requirements for the selected FI
   time-step branch. EXACT or undeclared semantics has minimum = maximum;
   AT_MOST uses its declared minimum. No model-wide floor collapse or provider
   table. Keep the existing maximum-horizon API for existing callers.
2. Limit initial negotiation to SINGLE-member UTC-daily FI group tracks (source
   expected members exactly {0}), where every assignment and
   every future variable explicitly declares AT_MOST and shares one maximum.
   Station-only or mixed station/group tracks, mixed EXACT/AT_MOST tracks and
   unequal ceilings keep existing strict behavior. Determine group membership
   from the model artifact scope and existing group assignment discovery.
   Per-feature minima remain independent; use their maximum only to determine a
   common delivery length that satisfies all variables. No ragged frames.
3. First prepare the existing maximum requests to freeze the resolver and
   binding identities. Configure the adapter with that resolver. Move the existing
   fetch-task callback above refresh construction. For negotiable tracks call
   the existing resolve_candidate with an otherwise identical track whose
   admission horizons are the largest declared minimum. Same retry budget,
   cadence, earliest cycle, source/member checks; first eligible cycle wins.
   This uses the existing fetch/validate split, not an extra preflight download.
4. From AcceptedCandidate records compute the longest complete prefix with
   recap_complete_daily_records and the same issue boundary. Require every
   requested station to be present; a group is never shrunk. Select one common
   length across all features and all stations, between floor and maximum.
   Reject an interior missing day if later days contain source support; permit
   only a missing/partial trailing tail. No padding. Missing group members,
   below-floor support give no accepted candidate for this group track. An
   interior gap rejects this candidate and continues the same bounded walk-back;
   explicitly inspect later days because the daily reducer stops at the first gap.
   Add an optional candidate_records_check predicate to resolve_candidate, run
   after ordinary completeness and before accepting. It checks complete group
   membership and prefix contiguity. Default None leaves existing callers intact.
   Use discover_group_runs for the same membership source as preparation.
5. Re-run prepare with a typed negotiated-length mapping, validating each override
   against its model branch bounds. Assert frozen resolver and fingerprints are
   unchanged. Revalidate accepted records against the negotiated exact track
   before they can be persisted. Create cache attempts with the final exact scopes.
   Pass the accepted candidate (or explicit unavailable result) into
   resolve_selected_weather_tracks so it skips its fetch/resolve half for that
   track and executes only the existing exact receipt/commit half. Exact-only
   tracks continue through their existing path. No refetch and no renegotiation
   after begin. This avoids caching multiple raw candidate outcomes entirely.
6. Keep a numeric aggregate budget of 65,536 retained forecast records across
   negotiated tracks. On overflow fail closed before beginning cache attempts;
   mark only the affected negotiated track unavailable at maximum requests; do
   not abort unrelated tracks. Count every fetched row, including partial tails
   and rows beyond selected prefix, before conversion and cumulatively after
   conversion. Current SINGLE live archive is at most21*4*116=9744 rows, giving
   >6x headroom. Ensemble51 is explicitly outside this gate and unchanged.
   Do not silently refetch or change candidate. Release accepted records after
   each track commit. Check measured Linux peak with all21 live inputs, including
   overlap with fetch_past, against the existing3GiB capture/validation budget.
7. Same-issue runs obtain a new cache generation (store begin increments prior
   generation); exact scopes can change from11 to12 without rewriting old
   receipts. Test this with real cache store behavior, stale donor selection and
   captured evidence restoration. Persisted v1/v2 types and bytes stay unchanged.
8. Normal proof, assembly, capture and replay use final exact requests. Group
   PER_TRACK adequacy reads each feature minimum from FI, not its ceiling; normal
   selected assembly proves every delivered day. Keep strict behavior elsewhere
   unless tests establish equivalent coverage semantics. Verify model-returned
   metadata horizon11, quantile shapes and API/storage counts. The input carrier's
   maximum forecast_horizon_steps is currently not consumed by FI prediction;
   inspect all output/store consumers before deciding whether it must change.

If negotiation finds no eligible candidate, keep the maximum request and pass
explicit unavailable to the resolver: existing retained exact-scope fallback may
still succeed, but cannot weaken its coverage check. A track with incomplete
membership is conservatively unavailable as a whole, avoiding partial group
execution or hidden group-membership changes. This may reduce availability for
co-assigned tracks; it never weakens a strict assignment. Use a typed three-way handoff: not negotiated, selected candidate, unavailable;
never confuse unavailable with no negotiation. Assert track keys, features,
members, cycle and exact request coverage before accepting the handoff.
For fatal negotiation errors, retain the exception with maximum preparation,
begin the normal cache attempts, call fail_future using _fatal_weather_reason,
then re-raise into the existing finalizer. Do not print raw exception messages.
Test auth, invalid/duplicate support and memory refusal reporting. This preserves
attempt evidence even though fetch preceded begin. No schema or cache-invalidation rewrite. Fatal negotiation aborts the issuance:
all declared future legs receive the fatal category, including exact tracks not
yet attempted; the finalizer marks the past leg NOT_ATTEMPTED/PARENT_FAILURE.

This initial activation covers the existing UTC daily route. Nonzero-grid and
subdaily preparation remain unactivated; their persisted readers remain unchanged.
Do not widen IFS non-00Z interval qualification. The 12Z product is a later
upstream qualification, not a fabricated substitute for current 00Z data.

## Phases and gates

H1: independent Claude review of the design and exact call boundaries. Resolve
findings and mark READY as orchestrator before implementation.
H2: implement the narrow negotiation boundary and FI projection, tests and docs.
H3: independent patch review, targeted tests, changed-module type checks, Ruff,
pre-commit; release target bump once and PR. Full suite must pass before merge.
H4: after owner merge/release identity, deploy compatible images; opt in to
288 MiB selected capture, qualified radiation intervals, and recurring four-
parameter ERA5 refresh, preserving the module entrypoint and other schedules.
Assign the existing artifact once, run the normal forecast, and verify API rows,
actual horizon, all 21 stations, quantiles, source receipts and unknown provenance.
Keep existing climatology/CMAL-small intact. No station promotion.

```json
{"H1":[],"H2":["H1"],"H3":["H2"],"H4":["H3"]}
```

Tests must cover 15 available ->15; 11 ->11; minimum boundary and below-floor;
EXACT short refusal; mixed variables/floors; one short group member; leading and
interior holes; missing/foreign members; unavailable and stale cycles; no extra
fetch; exact cache-attempt/receipt identities; store/capture/restore/API evidence
and v1 byte compatibility. Test real CMAL at 11 days with archived operational
inputs before assignment; no synthetic tail. Preserve output quantile validation.

Host activation requires refreshed resource gates from the capacity plan,
compatible-reader rollback, and measured DB/WAL growth for the first issuance.
If negotiation cannot preserve exact receipt identities without a persisted
schema change, stop implementation and revise this plan rather than weaken proof.
Full review reports stay local. No credentials or production input data go to review.
