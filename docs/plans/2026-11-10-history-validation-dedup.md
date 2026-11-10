---
status: READY
---
# Deduplicate frozen rating-history validation

## Goal and evidence
The deployed 0083 history projection returns HTTP 503 for a real 944-sample,
seven-day forecast. Its derived JSON contributes 472,000 nodes and repeated
reference-curve parsing another 407,808, exceeding the 500,000-node budget.
Do not raise limits or weaken integrity checks. Validate identical frozen curve
content once per request; preserve all sample-specific checks and output.

## Scope
New forward migration replacing the projection (0083 stays immutable), focused
integration regressions, affected history specification, patch version bump.
No frontend, model, input-data, provider, authentication, or live deployment change.
Existing ACLs, SECURITY DEFINER settings, timeouts, byte/sample/node/curve limits,
lexical digests, duplicate-key rejection, and safe 503 behavior remain contracts.
Only actual duplicate work stops counting against the node-work budget.

## Reviewed decisions
Owner accepted the independent Claude review on 2026-11-10. Cache identity includes
both exact curve texts (and curve ID); successful validation only. Per-sample
reference equality, shape, validity, endpoint, digest and interpolation checks stay.
The traversal helper skips only the top-level curve child on an exact cached-byte
match, after checking its parent key for duplicates. First validation occurs at
the same depth. Changed bytes, duplicate keys, and altered references cannot hit.
Use CREATE OR REPLACE and restore original function definitions/ACLs on downgrade.
Include capture-node work in the red fixture measurement. No further review loop.

## Progress
- T1: reproduced 0083 node-walk refusal with 944-sample rich-curve v1/v2 fixtures.
- T2: implemented; 147 focused history/ACL/migration/API tests passed. Independent
  Claude patch review found no blocking issues; minor cache-accounting limits
  are documented in the API specification.
- T3: incomplete. Full suite: 13,155 passed, 17 failed. Six failures were stale
  migration-head assertions, now corrected and passing targeted checks. Other
  failures include local PostgreSQL startup and package-index access. The two
  logging failures reproduce on unchanged origin/main with the same test order;
  all 39 logging tests pass alone on both baseline and this branch. No unrelated
  logging fix is included. A clean full-suite run remains required before merge.

## Tasks
### T1 — Reproduce real-density repeated curve failure
Add a deterministic rich-curve fixture using existing dense v1/v2 fixture builders.
Verify old projection refuses a bounded 944-sample history for node budget, not
invalid evidence; retain a small equivalent positive fixture.
Pre-change: regression fails with 0083's rating history bounds.
Verification: focused PostgreSQL integration tests.

### T2 — Validate repeated curve content once
Inspect the narrowest implementation. Cache successful curve validation by exact
frozen bytes within the invocation, not curve ID or semantic JSON equality alone.
Skip repeated reference-curve traversal and invariant curve validation. If full
sample-envelope traversal still charges repeated curve subtrees enough to fail,
use a narrowly scoped owner-only traversal helper that skips ONLY exact-byte
previously validated curve subtrees, while checking every surrounding object and
every sample-specific field. Bound the cache by the existing 64-curve limit.
Do not cache failed validation, skip per-sample digests/capture/QC/identity/validity/
interpolation checks, or read current curves instead of frozen evidence.
Use a forward migration with reversible function definitions and explicit ACLs;
no table or retained-evidence changes. Review whether eliminating reference-copy
work alone meets the real-density v1/v2 fixtures before adding helper complexity.
Pre-change: T1 red.
Verification: T1 green; existing malformed/duplicate/digest/ACL tests remain green;
new later-sample altered-curve/duplicate-key tests still reject, even after a cache
hit. A second request does not inherit cache state.

### T3 — Document and verify
Update the existing history spec to explain per-request duplicate-work accounting
and unchanged safety limits. Run focused tests and lint/type checks, then one full
suite after final changes. Obtain independent patch review; stop for owner findings
and PR/merge approval. No deployment in this change.
Pre-change: N/A documentation and release metadata.
Verification: test reports and concise review outcomes outside commits.

```json
{"phases":[{"id":"regression","tasks":["T1"]},{"id":"fix","tasks":["T2"],"depends_on":["regression"]},{"id":"verify","tasks":["T3"],"depends_on":["fix"]}]}
```
