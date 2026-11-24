# Combined-purpose network overview admission

status: READY

The deployed 0.1.1182 overview returns 200 for each forecast purpose separately
and 503 for both. Five DHM stations have three active model assignments. The
reader creates an entry for each assignment and purpose, including absence
entries, then rejects more than four entries per station. Thus six valid entries
fail the combined request before observations can be returned.

Keep four active model assignments per station. Admit at most four forecast
outputs per station **and purpose**, with at most eight for the two supported
purposes. Apply the same key to header admission, projected headers plus absence
entries, and final response admission. Bound the header query to
200 * 4 * number-of-requested-purposes plus one sentinel row; reject overflow.
Do not change the 200-station, 250000-value, 512-native-time, query timeout,
publication, evidence, tenant, reviewer, or observation visibility safeguards.
No output truncation and no omission of absence entries.

Change all four reader sites together: projected counts, final response counts,
header per-purpose counts and the header SQL sentinel/total check. Both query
limit and total check use the same 200 * 4 * P bound. Count absence entries too.
The unchanged value cap can still reject larger combined networks: this is
intentional, not a guarantee that the Cartesian maximum fits. Compact response
serialization and ETag work may double at the new eight-entry ceiling. Update
the specification without extending the old performance receipt to this shape.

Add public endpoint regressions for three and four assigned models requested
under both purposes, single-purpose behavior and order independence. Cover
five outputs within one purpose remaining refused even if the combined total
fits eight. Retain unauthorized combined-request rejection and existing query,
value and publication tests. Update the overview contract and touchpoint docs.

One independent Claude plan review precedes implementation. One independent
patch review follows focused tests. Full suite must pass locally or in final CI.
Version bump and PR; owner approves/merges. Deploy only the merged release and
verify all three public request variants before reporting the incident resolved.

Independent Claude review completed. Its two findings (explicit intersecting
capacity limits and coordinated sentinel/count changes) are resolved above.
Orchestrator marked READY; the full review remains local.

```json
{"phases":[{"id":"review_plan","depends_on":[]},{"id":"implement","depends_on":["review_plan"]},{"id":"test_review_pr","depends_on":["implement"]},{"id":"deploy_verify","depends_on":["test_review_pr"],"gate":"owner merge and release"}]}
```
