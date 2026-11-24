# Bounded multi-issue hindcasts through ForecastInterface

status: READY

Owner authorized November 24. No Aquacast source changes, deployment, activation
or replacement long run in this patch. PR #633 is merged on the base. Shared
implementation for capable FI group models across deployments; other models keep
the current per-issue path.

## Evidence

Mac mini: SAP3 0.1.1181, Aquacast 0.1.378, FI 0.1.20. SAP3 calls predict_batch
once per issue, then FI predict. Aquacast delegates this to hindcast with one
issue, rebuilding its datamodule, loader and predictor. Its optional FI
BatchHindcastModel accepts multiple dates. Our shim forwards it, but our adapter
and service do not use it.

## M1 — FI boundary

Add an optional SAP3 group multi-issue capability. Check the inner FI protocol,
including through the shim: an unconditional shim method must not falsely
advertise inner support. Accept bounded, ordered existing GroupModelInputs.
Apply the existing NaN gate independently per station/issue first. Partition
calls by identical eligible station cohort; never remove a station from all dates
because it fails one. Merge only matching group/cadence/static context and
conflict-free overlapping input rows. Reuse canonical-to-FI conversion, preserving
existing time alignment, units, missingness and reanalysis semantics.

Split returned variable frames by their own issue_datetime BEFORE the existing
ensemble converter. Preserve metadata, representation, flags, horizon, valid and
issue times. Reject unknown stations, unrequested issues and malformed output.
Failed/absent station-issue pairs are failed results, never empty success; preserve
successful peers. No silent retry of malformed batches through another path.

## M2 — Shared group runner

Buffer at most seven prepared dates. Dispatch through optional capability, or
retain the existing sequential path for unsupported models. A service argument
within 1..7 permits one-date comparison; no new deployment configuration layer.
Flush final short batches and preserve skipped-issue failure records. Store each
result with original station, issue, artifact and run identity. Fatal DB errors
still abort; model failure affects only the submitted bounded cohort. Full-period
prefetch remains: this bounds extra batching memory, not total run memory.
Station-only model batching is deferred.

## M3 — Verification

Test capability checks, multi-issue quantiles/members, changing station/issue
NaN eligibility, station/model failure, missing/extra dates, conflicting overlaps,
chunk boundaries and unchanged fallback. Check dates, units, quantiles, run identity
and data-use isolation. Run adapter/hindcast/flow/isolation tests, lint/types and
full CI. Independent Claude patch review; update touchpoint/spec documentation.

## M4 — Bounded qualification before replacement runs

Real-model benchmark with frozen Swiss input/artifact: same small station/date
sample at batch sizes 1 and 7. Prefer dev machine; an isolated resource-limited Mac
mini benchmark requires the applicable deployment approval. Record time, peak
memory, coverage, units, dates and quantile validity. Batch shape can change RNG
consumption: require repeatability at fixed batch size/seed, not unsupported
bitwise equivalence across sizes; report numeric differences. Record batch size
in logs/evidence. No speedup claim from fake-model timing, no full reruns before
qualification. Defer full-period streaming and station-model batching.

```json
{"M1": [], "M2": ["M1"], "M3": ["M2"], "M4": ["M3"]}
```

## Independent review disposition

Claude plan review completed; full report remains local at
`/tmp/batched-hindcasts-plan-review.txt`. Orchestrator resolves findings as follows:

- Batch input is a union of per-issue windows. The model must apply its declared
  past/future cutoffs per issue. Before enabling multi-date use, qualify the real
  Aquacast artifact with a leakage test: perturb only later past-target rows and
  confirm the earlier issue is unchanged under the same seed and batch shape.
  Exact cutoffs follow declared grid labels/offsets, not a guessed <= comparison.
- FI's current model_interface.md explicitly specifies varying per-row issues;
  the review cited an older open-question note. Use each row's issue, not the scalar
  output issue. Validate horizon per sliced block. Missing requested blocks are
  explicit failures; unrequested blocks are errors. Never invent values to satisfy
  complete coverage.
- Default remains single-date until real-model qualification passes. Then enable
  seven dates for capable group models. Expose the bounded size as a Prefect flow
  parameter (recorded with its run) and in fine-tuning evaluation receipts linked
  to hindcast run IDs, so it is durable rather than log-only. Sampling/eligibility
  changes may alter peers numerically; reproducibility is fixed inputs, ordering,
  batch size and seed, not batch-size invariance.
- Cohort means identical eligible stations for up to seven dates. Singleton
  cohorts reuse predict_batch. Structural capability is verified through the shim.
- Current group hindcast preparation uses the legacy forcing route. Reject
  unsupported mixed/per-track batch input rather than truncate combined future
  windows through the per-track single-issue head slicing.
- All full-run adoption waits for the benchmark/leakage gate. If the capability
  fails, leave single-date behavior and propose an upstream issue without editing
  Aquacast. Default cap is seven; station-scoped models remain sequential.

M4 local qualification passed on the frozen one-usable-station/seven-date sample:
repeatability, temporal grids, future-target leakage and deterministic prediction
agreement. The default is now seven for capable group models; one retains the
sequential route. Full-group memory/performance qualification remains a bounded
post-deployment check, not a claimed result. Benchmark limits and stochastic
quantile differences are documented in the operations note.

Independent patch review completed; full report stays local at
`/tmp/batched-hindcasts-patch-review.txt`. Findings folded in: model/config/output
errors are contained to their eligible cohort; per-member logs carry issue,
station and cause, and NaN gate reasons reach failed step records. Missing
non-failed variable blocks fail that station/issue instead of silently succeeding
with fewer parameters. Inputs use ensure_utc at the boundary. Regression tests
cover a malformed cohort beside a successful one, UTC representations, missing
issues and receipt/run batch-size agreement. No automatic second review loop.
The receipt uses the exact resolved size passed to each evaluation flow; the
outer attempt uses the same resolver/default (there is no separate override).
Past-known channels other than CMAL-small discharge are not qualified by the
real-model leakage sample; new models/dependency versions require qualification.
