# Latest observation timestamp lookup — issue 549
status: READY

## Purpose and evidence
Remove the repeated global authority-view scan in PgObservationStore.fetch_latest_timestamp without changing which observations are eligible. Deployed 1172 EXPLAIN estimated 8.75 million authority rows. A read-only candidate retaining correlated EXISTS with OFFSET 0 used existing indexes: single-sample execution 3.795 ms for 2150 and 0.165 ms for 2009. Original 2150 execution exceeded a two-second diagnostic timeout. These are not whole-cycle benchmarks.

## Scope
Only latest-timestamp query planning changes. Preserve MAX semantics, station/parameter selection, TRUSTED and QC_CONTEXT authority rules, fail-closed missing evidence, UTC/None results. No schema migration, index, cache, concurrency change, Aquacast change, or live patch.

## Steps
1. Independent Claude plan review; orchestrator sets READY after resolving findings.
2. Refactor shared authority subquery construction minimally so latest-timestamp can retain correlation with OFFSET 0. Other predicate consumers retain existing SQL. Prefer a named helper over a boolean planning flag; reuse eligibility construction.
3. Real PostgreSQL regression tests: newer held/ineligible row falls back to older eligible row; canonical versus missing/noncanonical evidence; TRUSTED versus QC_CONTEXT; empty/all-ineligible history; station and parameter isolation. Preserve existing tests. Reproduce planner behavior on the deployed PostgreSQL major version with the actual migrated security-barrier view and worker-role permissions, using representative local data without timing assertions. Include a long ineligible tail as well as an eligible newest row; record the deployed version before choosing the test image.
4. Update relevant store documentation; focused tests and independent patch review. Full suite locally or CI after final code change before owner merge. Follow current release conventions when preparing commit.
5. After approved deployment, measure timestamp-task and whole-cycle duration. Roll back code if semantics or planner regress; do not bypass authority rules.

```json
{"steps":[{"id":"review","depends_on":[]},{"id":"patch","depends_on":["review"]},{"id":"verify","depends_on":["patch"]},{"id":"release_validation","depends_on":["verify"]}]}
```

## Acceptance
Identical eligible maximum timestamps; no global authority scan on representative common-path lookup; unchanged other read consumers. Pathological long ineligible tails remain measurable rather than promising constant-time performance. No deployment is implied by plan approval.

## Review disposition
Independent Claude review completed 2026-11-22; orchestrator accepted correctness safeguards. The retained-correlation helper shares purpose validation and rejects SOURCE_AUDIT and untyped purpose. Verify literal OFFSET 0 compilation and unchanged ordinary predicate SQL. Use ANALYZE statistics and EXPLAIN JSON on representative data with original-query negative control; assert structural behavior rather than timing. Explicitly test held canonical latest rows under both read purposes. Full report remains local.
