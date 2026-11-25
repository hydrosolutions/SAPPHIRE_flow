---
status: READY
---
# Candidate stored skill access

## Need
The corrected CMAL-small candidate remains inactive. Its version-4 threshold
scores must be readable without activating it. The existing scoped station skill
route already serializes periods, samples, null scores and threshold context,
but selects only the operational artifact.

## MVP change
Add optional UUID `artifact_id` to GET `/api/v1/stations/{id}/skill`.
Reuse existing station authorization and active model assignment restrictions.
For each allowed model, accept the requested artifact only when its metadata
record belongs to that model. Return only that artifact's latest-generation
scores for the authorized station. Missing/mismatched artifacts return no rows,
without exposing artifact metadata. No artifact bytes are loaded.
Without the selector, preserve existing operational artifact selection exactly.
Set response `selection` to `explicit_artifact` when the selector is supplied;
retain `latest_generation_on_forecast_artifact` otherwise. Existing per-row
artifact, evaluation/training periods and threshold metadata remain authoritative.
This is stored skill, never skill for a frontend-selected event period.

## Boundaries
No model activation, public roster expansion, hindcast assembly changes, diagram
route, or model ranking. Current partial hindcast evaluation is explicitly
interim; its actual score periods/sample counts are served, not a claim of
complete 2017–2020 coverage. The route does not compute scores.

## Steps
1. Independent plan review; resolve findings before implementation.
2. Add typed optional artifact selection to the service and HTTP boundary/schema.
3. Test default behavior, inactive candidate selection, model mismatch,
   out-of-scope station, malformed UUID, and threshold null/context serialization.
4. Update API contract/docs; independent patch review; checks, release-target
   bump, PR. Owner merges; deployed endpoint verification follows deployment.

```json
{"steps":[{"id":"review","depends_on":[]},{"id":"implement","depends_on":["review"]},{"id":"validate","depends_on":["implement"]},{"id":"pr","depends_on":["validate"]}]}
```

## Evaluation write bound
Real evaluation reached 3,277 score rows in one station, exceeding PostgreSQL's
65,535 bind-parameter limit with the current one-statement insert. Chunk score
and diagram inserts into 500-row statements on the SAME connection/transaction;
sum RETURNING row counts. Preserve ON CONFLICT behavior and generation count
reconciliation/publication after all chunks. No intermediate commits.
Test >65,535 parameters against PostgreSQL, duplicate replay counts and rollback.
For the already deployed release, an operator-only PgSkillStore subclass may
split calls to the unchanged public insert methods into the same 500-row chunks;
use the existing compute task and generation publication, no schema/code/image
replacement. Continue sequentially, record actual evaluation coverage, and
recompute after the hindcast finishes. Do not claim complete multi-year scores.

## Review resolution
Independent Claude plan review completed 2026-11-25. Candidate skill visibility
is intentional for existing reviewer/admin station grants, consistent with
artifact-selectable hindcast review; this is not public publication. Anonymous
and observer roles remain denied. The owner explicitly requested calculation
and API availability; the already-authorized evaluation is candidate-only and
interim, with no activation. The bounded insert wrapper is reviewed as part of
this plan and uses the same writes and publication checks as the permanent fix.
No separate deployment or persistent configuration mutation is involved.

Explicit selection checks `record.model_id == mid`, still requires an active
model assignment and still drops future evaluation end dates. Null-artifact
combined scores cannot match. Verify CMAL-small assignment before evaluation.
Add tests for cross-model selection, future dates and role/scope denial.
