---
status: READY
---

# Use declared history for hindcast input preparation

The Mac mini's two 365-issue CMAL-small runs were killed at the worker's 8 GiB
limit before storing results. The runner buffers per-issue windows using a
720-step default although deployed CMAL-small declares 30 for all inputs.
Overlapping buffers are only deduplicated at the FI boundary afterwards.

Change the shared station and group service defaults to `lookback_steps=None`;
resolve omission to `model.data_requirements.lookback_steps` before prefetch and
assembly. Preserve explicit overrides and the existing aligned bucket, horizon,
QC, availability and per-channel handling. The declared maximum already covers
all declared per-variable lookbacks. No Aquacast/FI modification, new setting,
alert change or automatic deployment. This reduces excess retained history;
it does not make arbitrary batch sizes memory-bounded or stream the full period.

1. Review this bounded change independently, then mark READY.
2. Implement defaults and tests capturing delivered input windows for station,
   group single-date and multi-date paths. Test a short and >720-step declaration,
   explicit override, off-boundary alignment and retained forecast values.
3. Run hindcast, batch adapter, flow and data-isolation regressions, lint and types.
   Independent patch review; full CI before owner merge. Document limits and hold
   live adoption until merge/deployment approval. Preserve current live runs.

```json
{"default_resolution":[],"behavior_tests":["default_resolution"],"review_ci":["behavior_tests"],"live_qualification":["owner_merge","owner_deployment_approval"]}
```

## Review disposition

Independent Claude review completed (local report). It identified short history
at the oldest requested bucket and explicit overrides below the declaration.
Keep the existing FI ownership: shape/length shortfalls belong to models, not a
new SAP3 admission gate. Missing edge buckets remain missing; do not pull an older
observation into the required window or fabricate data. A shorter delivered frame
can now reach model validation instead of being rejected incidentally by a cadence
check over older out-of-window rows. Test that behavior explicitly; do not promise
identical missing-data eligibility. Interior gaps keep the existing cadence skip.
Explicit overrides, including smaller ones, retain their current semantics.
Tests also cover declaration 1 and the declared maximum across input channels.

Independent patch review completed with no blockers. Added explicit forcing
prefetch-bound and saved-result-count assertions for its nonblocking suggestions.
Local validation: 76 hindcast unit tests, 100 flow/partition/static/ensemble/FI
prediction tests and 5 PostgreSQL data-use isolation tests passed. Full CI remains
the final merge gate. No live deployment or larger-batch adoption in this patch.
