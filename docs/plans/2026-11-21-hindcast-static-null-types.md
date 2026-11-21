---
status: READY
owner: orchestrator
issues: [592]
---

# Preserve null static values when grouping hindcast inputs

The authorized fleet CMAL hindcast stopped in SAP3 services/hindcast.py
_stack_hindcast_inputs: a per-station reservoir_fs column inferred Null could not
be vertically concatenated with Float64 from another station. This is before model
prediction. Previously committed partial hindcasts remain valid retained evidence.
No Aquacast module, FI adapter, model declaration or dependency pin changes.

## Narrow correction

At hindcast static-frame stacking only, find a concrete dtype for each column from
peer static frames. Cast only columns whose dtype is exactly Polars Null to that
peer concrete dtype, preserving every null. Leave all-null columns Null. Keep
strict vertical concat afterward: incompatible concrete types, missing/extra
columns and column-order mismatches retain their existing refusal behavior.
Do not use broad relaxed/diagonal concat, numeric imputation, default zero, column
projection/removal or station omission. Do not alter temporal input partitions,
station ordering/identity, scientific QC, declared-static projection or provenance.
Prefer one small private helper inside existing hindcast module; no framework.

## Verification and delivery

One implementer reproduces the Null/Float64 failure then patches the static boundary.
Test both station orders; values/nulls/IDs unchanged; all-null columns unchanged;
incompatible non-null dtype still fails. Include a public run_group_hindcast
regression with existing fake group model/stores so null statics reach prediction
without losing healthy peer outputs. Use existing test homes and minimal fixtures.
Update affected hindcast documentation/touchpoint map, official release target once
per PR; no hand-edited generated version. Run focused hindcast/static/flow tests,
Ruff/types, one independent patch review, then full suite locally or CI after final
code change before owner merge. Plan receives one independent Claude review before
orchestrator READY. Reports local. Hold at PR; no agent merge or release publication.

After owner-approved deployment, resume the same full-group 2016–2020 hindcast via
existing workflow with explicit new run identity. Duplicates are owner-authorized;
old partial results are preserved. Coordinate long run with operational forecasts;
no unsupervised resource contention, fake current labels or unverified completeness.
This plan authorizes development only; deployment remains separate from code review.

```json
{"M1_reproduce_and_fix": [], "M2_review_and_validate": ["M1_reproduce_and_fix"], "M3_owner_merge_deploy_resume": ["M2_review_and_validate"]}
```

## Independent plan review dispositions

Claude review completed ($0.0292449), local report
/tmp/hindcast-static-plan-review.json. Orchestrator incorporates these clarifications
and marks READY; no runtime scope expansion:

- Resolve all distinct concrete dtypes per static column. Cast exact pl.Null only
  when there is exactly one concrete dtype; zero concrete types stays Null,
  conflicting concrete types retain strict refusal. No first-peer selection.
- Apply before station_id insertion. List(Null)/Struct children and temporal frames
  are out of scope. Preserve static=None station behavior and existing keyed lookup.
- Test null-count totals, row/station/column ordering, names and exact resulting
  dtype in both station orders. Null+Float64+Int64 must still refuse in either order.
- Public regression must first fail with the actual vstack SchemaError, then show
  model-visible missing value and concrete peer value unchanged, with healthy outputs.
- Inspect existing downstream null handling and report whether it converts to NaN
  or anything else; no change to downstream/imputation policy in this patch.
- No new warning log or other-frame fix; retain this as a small static-only patch.
