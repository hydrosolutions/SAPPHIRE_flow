# Operator retry of an exhausted daily forecast

The automatic daily budget remains two attempts. An operator retry is a separate
record, executed through the existing forecast deployment. It is available only
for the current UTC logical day after both automatic attempts have finished
unsuccessfully. It cannot replace a completed day, change its station roster or
model artifact, or bypass forecast input/QC/publication requirements.

Use the deployed worker environment, configuration and existing trusted Prefect
credentials. These commands do not install software or update deployment settings:

```sh
uv run python -m sapphire_flow.cli.operator_daily_retry preview \
  --group GROUP_UUID --actor OPERATOR --reason 'Repair deployed; verify inputs' \
  > retry-request.json
uv run python -m sapphire_flow.cli.operator_daily_retry submit \
  --request-file retry-request.json
```

`SAPPHIRE_CONFIG` and its normal overlays select the deployment. `--config PATH`
may be supplied before the subcommand. Preview emits the exact request JSON and
logs parent/current configuration digests to stderr. Inspect the scope, parent,
artifact, roster digest and expected configuration before submission. Retain this
request with the incident record; it contains operations-only actor/reason text.
Submission returns the Prefect run UUID. Follow that run in the existing Prefect
UI; inspect its `daily_forecast.operator_admission` event and daily summary.

The command is for operators with trigger access to the configured Prefect
control plane. A direct authenticated Prefect caller has the same authority; the
actor string is audit context, not authentication. No reviewer/dashboard token
can use this command through the public API. It is not a second scheduler.

Admission binds the request UUID to the exact payload and parent. Repeating the
same request returns its existing run/status; it never repeats inference. A fresh
execution delivery of an existing unfinished or unsuccessful request fails with
its attempt identity and state; it does not report a healthy forecast cycle.
A completed request may return a healthy no-work result. A new
request requires a new explicit preview of the latest unsuccessful parent. If
another run is active, configuration changed since preview, the roster/artifact
changed, or the queued request reaches admission after UTC midnight, it refuses
before shared weather/observation work. The expected configuration may differ
from the parent's configuration, but that difference is explicit and immutable.

Once admitted, the run retains its parent's midnight logical issue time even if
execution crosses midnight. Weather reference and actual execution time remain
separate. The existing forecast cycle also runs its ordinary models; this MVP
reuses acquisition, ordinary forecast publication and alert checks, and may incur
its existing observation-lookup latency. It does
not promise a faster run or repair missing source data automatically.

The latest operator attempt is authoritative for that day. While it is active or
failed, older successful values are not silently substituted. A completed or
incomplete summary exposes only its verified references under existing QC and
publication rules. Days with scheduled attempts only retain the existing ordinal
selection and last finalized summary, including legacy rows without matching parent links.
Evaluation API contract 3.1 adds `latest_attempt_trigger` and
`authoritative_attempt_trigger` (`scheduled` or `operator_retry`, or null); actor
and reason are not returned by the public API. No historical one-shot/replay is
promoted to operational authority.

A crash or uncertain write keeps ownership ACTIVE. First verify the exact Prefect
run is terminal and all writers have stopped, then use the existing confirmed
termination recovery transition. Never delete ledger rows, forge scheduled ticks,
reset automatic ordinals or release ownership based on age. A recovered operator
attempt may be the explicit parent of a new request. Normal next-day scheduling
has a distinct daily key; existing deployment concurrency still serializes runs.

## Deployment and rollback

Ship the schema, execution path and readers together. Pause admissions and drain
writers under the normal deployment procedure before migration 0090. It adds
nullable operator metadata, retains scheduled ordinals 1/2 and extends active-owner
uniqueness across both operational triggers. No existing evidence is rewritten.
The migration's table lock and replacement unique index fail atomically if an
unexpected conflict exists.

Old code can run against this schema only before any operator request is admitted.
After operator records exist, old readers ignore their authority and old writers
do not understand it: do not roll back to those binaries. Use a forward-compatible
fix or the established whole-deployment backup restore procedure; there is no
schema downgrade that deletes retry evidence. Deployment and operational adoption
remain owner decisions.
