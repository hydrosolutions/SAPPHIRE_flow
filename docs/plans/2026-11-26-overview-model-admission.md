# Network overview model admission

status: READY

## Problem and decision

The initial network overview limited every station to four assignments and four
outputs per data-use purpose. Swiss stations already have seven active models;
the entire request consequently returns 503. This is a resource guard, not a
scientific preference rule. The owner rejects a four-model display restriction.

Return all admitted models without silently dropping or ranking them. Replace
the per-station model/output caps with an explicit request-wide budget of 3,200
assignments and 3,200 outputs per requested purpose. This admits the current
Swiss roster while retaining bounded queries. Keep the 200-station, 250,000-value,
512-valid-time and daily-ledger limits, existing authorization and publication
rules. No configuration knob, new endpoint, frontend edit, or model activation.
Any future curated display must explicitly use approved preferences; assignment
priority is not a measured skill ranking.

## Review resolution

Independent Claude review completed 2026-11-26. Measured BAFU scope: 183
stations, 1,101 assignments; recent output keys are discharge-only (765 present
keys). At 200 stations and seven single-parameter models, 1,400 outputs fit
with headroom. Both purposes are a fixed enum with at most two per request.
Qualification records actual counts and payload/latency. Existing resource-limit
503 behavior remains for genuinely oversized requests; changing HTTP semantics
is outside this fix. Owner has authorized token repair and the BAFU update;
owner merge remains required.

## Steps

1. Independently review this plan, then mark READY.
2. Replace every related four-model check and header query limit consistently.
   Add regression coverage for seven models and request-wide budget refusal;
   update API resource-limit documentation.
3. Run focused API tests, qualify against a read-only Swiss database, and obtain
   one independent patch review. Full CI must pass before owner merge.
4. Deploy the merged release using the existing receipt procedure, preserving
   host configuration and the active isolated hindcast. Verify authenticated
   Landquart forecast detail and overview. Token scope repair uses the existing
   audited CLI and does not change roles or rotate keys.

```json
{"steps":[{"id":"review","depends_on":[]},{"id":"patch","depends_on":["review"]},{"id":"validate","depends_on":["patch"]},{"id":"owner_merge","depends_on":["validate"]},{"id":"deploy","depends_on":["owner_merge"]}]}
```

## Validation

43 focused integration tests passed. Independent Claude patch review found no
blockers; the requested cross-file cap search found no remaining related limits.
The existing value-count query still bounds values before materialization.
A separate read-only process against the Mac mini returned 187 scoped Swiss
stations, all seven Landquart models available, in 3.73 seconds. The serialized
response was 5,839,346 bytes uncompressed: clients must accommodate this or use
existing station reads. This is not a live deployment or a dashboard verification.
The existing map token grant was repaired through the audited CLI; authenticated
Landquart CMAL detail now returns HTTP 200. Full CI and owner merge remain pending.
