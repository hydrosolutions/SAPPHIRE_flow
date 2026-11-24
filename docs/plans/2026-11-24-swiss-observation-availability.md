# Configurable hindcast batches and unknown availability

status: READY

Owner direction: allow multi-year batch requests, preserve unknown availability as unknown, testing only. This supersedes the earlier proposal to invent labelled period-closure availability. No schema migration, new availability policy, live deployment, model activation, or Aquacast changes.

## Evidence

Seven dates was a SAP3 conservative memory bound, not an FI restriction or measured optimum. The service and adapter both enforce it. Aquacast 0.1.396 correctly splits observed-input multi-date requests into per-issue calls before availability masking. Instrumented real-model evidence (/tmp/batch-cutoff-recheck.json) confirms advancing daily cutoffs; there is no demonstrated upstream cutoff defect. Multi-year outer requests will not imply vectorized multi-year CMAL inference.

## Implementation

1. Accept any positive integer `issue_batch_size`; reject bool, floats, zero and negatives. Keep seven as the default chunk size to avoid silently changing running jobs. Allow any nonempty ordered adapter batch, preserving existing grid/context checks and unsupported-model sequential behavior. Existing flow parameter is the sole configuration, no new layer. Users can select 365/366 or a multi-year issue count.
2. Deliver per-variable optional `available_at_us:<parameter>` columns from SAP3 past-known frames as FI nullable integer `available_at_us`. Unknown/absent availability becomes explicit Int64 null. Preserve supplied timestamps/nulls and sort with values. Never use created_at or measurement time as publication time. Future-known variables do not inherit past metadata. No assumptions or metadata generation in observation storage/assembly.
3. Test acceptance above seven, rejection of invalid sizes, chunk completeness, variable isolation, unknown/null handling and invalid metadata types. Update contracts and qualification notes. Local targeted tests, lint/types and independent Claude patch review; full CI before owner merge.

## Limits

Current Aquacast masks past discharge when availability is unknown; this is the observed model behavior, not fixed by representing unknown explicitly. Test results must retain its DATA_AVAILABILITY warnings. No claim of full-input scientific qualification or new skill improvement. Large requested batches retain overlapping prepared windows and outputs in memory; document this without imposing an unmeasured hard ceiling. No full multi-year run or Mac mini change in this patch.

```json
{"metadata": [], "batch_size": [], "validation": ["metadata", "batch_size"]}
```

## Review disposition

Independent Claude plan review completed. Normalize absent/all-null per-variable availability to Int64 null before batch concatenation and FI projection; reject non-integer values rather than coerce dates/floats. Overlapping rows with conflicting availability (including known versus unknown) remain explicitly rejected: do not silently invent which evidence is authoritative. Test mixed absent/null schemas, and conflict rejection. These refinements address the two findings without relaxing existing overlap checks. Orchestrator marks this revised narrow scope READY.

Independent Claude patch review completed with no correctness findings. Full report remains local; diagnostic-detail and extra overlap-test suggestions are non-blocking.
