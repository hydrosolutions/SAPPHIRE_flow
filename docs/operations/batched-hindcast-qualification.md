# Batched hindcast qualification — 2026-11-24

This benchmark predates the merge of ForecastInterface 0.2.10 and the newer
Aquacast pin. It does not qualify that newer dependency combination; repeat the
bounded real-model check before replacement runs. Batched inference enforces the
same artifact and per-issue input time-grid checks as single-date inference.

The local dev-machine check used Aquacast 0.1.378, ForecastInterface 0.1.20,
PyTorch CPU with one thread, and the inactive fine-tuned CMAL-small artifact
`9f084ff1-8294-46ec-ab50-6dc7c8cfde02` (SHA256
`703c2df4ed538684c79aa3576ade6f7d64f6e9be53ca0b87a15d4f510c2bec1b`).

Inputs were exported in a read-only Mac mini transaction after the historical
false-spike repair, using the existing trusted observation reader, hybrid stored
Swiss weather reader and hindcast assembler. The local harness applied the normal
Caravan attribute projection. Requested sample: 2009 and 2305, June 1–7, 2019;
2305 retained a genuine input gap and was skipped. Thus this is **one usable
station, seven dates**, not a network-wide performance result. No production
forecasts, artifacts, scores or activation were written.

| Check | Result |
|---|---|
| Seven separate calls | 0.523 seconds |
| One seven-issue call | 0.161 seconds (3.25× first-pass speedup) |
| Repeated seven-issue call | 0.053 seconds |
| Process peak RSS | 537,690,112 bytes (whole benchmark, not incremental memory) |
| Same input/seed/batch repeatability | Exact quantile equality |
| Future-target leakage | Earliest issue unchanged when all later target rows were multiplied by 100 and increased by 1000 |
| Issue/valid-time and quantile grids | Same between single-date and batched calls |
| Deterministic output comparison | 70 rows; max absolute difference 0.0001221 m³/s, relative difference 3.25e-7 |
| Quantile comparison across batch sizes | Max absolute difference 94.265 m³/s; not bitwise equivalent |

Aquacast computes quantiles from sampled distribution draws. Batch shape changes
RNG allocation; its deterministic predictions agree within float32 precision,
while stochastic quantiles differ. Do not present changed quantiles as a model
skill improvement. This is a correctness/smoke and small inference benchmark,
not an estimate of full 172-station runtime or steady-state performance: timings
include different warm-up/cache effects. Database reads, storage and scoring are
not timed. The leakage check covers CMAL-small's declared past discharge channel;
weather is intentionally reanalysis forcing, including future-known weather.

The batch route reuses the established FI-0.1.20 UTC localization rule for both
issue and valid times. Aquacast 0.1.378 emits timezone-less UTC frame columns,
although its scalar issue time is UTC-aware. No upstream source was changed.
Future dependency upgrades must rerun this qualification against their contracts.

After owner merge/deployment, begin with a bounded production-sized station/date
sample under the existing memory limits before scheduling full replacement runs.
Keep original artifact/run identities. Fine-tuning acceptance and activation
remain separate; prior old-QC results remain audit evidence.

The leakage sample does not qualify other models' past-known channels. Capability
support is an FI contract, not a substitute for deployment/model qualification.
The adapter contains malformed cohort outputs and records per-member failures;
this does not silently retry or convert a failed output into a successful one.


## Testing controls after dependency update

`run_hindcast_flow(issue_batch_size=...)` accepts any positive integer: for
example 366 daily issues, or 1827 to cover a five-year daily period with room for
leap days. The default remains seven; it was a conservative buffer choice, not a
measured optimum or an Aquacast limit. A smaller final batch is flushed normally.
Larger requests keep more overlapping prepared windows and outputs in memory;
no full-network multi-year memory/runtime qualification has been performed.

Aquacast 0.1.396 splits observed-input batches into per-issue calls before masking.
A real seven-date run confirmed advancing issue cutoffs and admitted observations.
The earlier shared-cutoff suspicion was incorrect; no upstream defect is claimed.
Do not assume large outer batches imply vectorized CMAL inference or a speedup.

For testing, unknown availability is explicitly null at the FI boundary. Supplied
per-variable availability is preserved; no period-closure assumption is generated.
Aquacast currently masks observations with unknown availability and may return
DATA_AVAILABILITY warnings. Such outputs do not qualify full-input model skill.
No production deployment or replacement hindcast run is part of this change.
