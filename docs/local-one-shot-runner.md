# Local explicit-date group runner

`flows.daily_forecast_one_shot.run_local_one_shot_group` is an internal development
runner, not an operational CLI or an authorization to run real forecasts.

- Supply a genuine open `LocalOneShotOutputs` created by
  `create_local_one_shot_outputs()`. No independent output writers, DSNs or live
  target mode are accepted. Accepted/rejected writes and finalization share the
  owned database.
- Supply `OneShotDailyForecast` with MANUAL or REPLAY and an explicit calendar
  date, one selected configured group, and a fresh execution UUID. One UUID
  admits one group execution once, including failed or uncertain executions.
- Stage approved assignment/artifact metadata in the owned database first.
  Provide already available input stores, weather bindings, baselines and model
  artifacts explicitly. The runner does not copy, fetch or stage live resources.
- `OneShotGroupInputs` separates the actual weather cycle/reference from the
  explicit-date midnight issue time. The injected execution clock records actual
  start/finish time. Neither replaces the explicit date.

All configured assignments are validated before selecting the group, so selection
cannot hide overlapping output identities. The full original roster is frozen
before operational exclusions. Admission precedes input assembly and prediction.
Missing or ambiguous active artifacts produce a recorded failure without prediction.
The common daily executor preserves exact artifact identity, STATELESS_ONLY,
assembly/FI boundary, QC, provenance, committed evidence and summary verification.
There are no scheduled claims, ordinals, pending discovery, retries, catch-up or
acceptance repair. Repeating an admitted UUID cannot invoke the model again.

Known pre-write setup/model failures are recorded. Ownership is removed from the
executor's cleanup list before output persistence: uncertain writes and summary
commits remain ACTIVE and do not authorize another invocation. Cleanup of known
unstarted work runs in `finally`. Ordinary scheduled dispatch is unchanged.

The caller owns resource lifetime. The runner leaves outputs open for exact-ID
inspection. Use `close()` in the caller's `finally` to release connections and
retain evidence; `cleanup()` explicitly deletes the owned resource. Retention is
process-lifetime best effort (Ryuk/process exit or host loss can remove it).
See [local output ownership](local-one-shot-outputs.md).

Approval of real date/group/deployment, input/artifact access and evidence retention
or export remains required. This module supplies no operational invocation command,
export workflow, production provisioner or scheduled-current promotion.
