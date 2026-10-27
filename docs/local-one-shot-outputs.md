# Local disposable one-shot outputs

`flows.daily_forecast_one_shot_runtime.create_local_one_shot_outputs()` creates a
fresh owned PostGIS container. This is **local development/replay tooling only**.
It is not a manual forecast launcher, API execution endpoint, production-host
provisioner, or authorization to run forecasts. It requires the existing optional
development `testcontainers` dependency and a local Unix Docker socket. Production
imports do not import that dependency. No dependency or worker image changes apply.

The zero-argument factory accepts no DSN, writer, existing container, or isolation
assertion. `LocalOneShotOutputs()` has the same zero-argument ownership boundary.
All connections, accepted committed writes, rejected committed writes, exact-ID
readers and ledger finalization verification use its owned engine. Migrations run
in a child interpreter with that engine's URL; the parent's `DATABASE_URL` is not
changed or used as a migration target. Initialization failure cleans up only the
new resource, including partial startup/migration/composition failures.

## Local lifecycle

- `resource_id` identifies the container created by this instance (not a URL).
- `engine` permits separately authorized staging into this owned local database.
  There is no automatic metadata, input, artifact or live-data copy.
- `ledger`, `accepted_writer`, `accepted_reader`, and `rejected` expose the
  internally composed stores. `rejected` includes committed writes and exact reads.
- Keep the runtime OPEN while using stores or exporting separately authorized
  evidence. Do not hold store/engine references past `close()` or `cleanup()`.
- `close()` releases connections and retains the owned container. State becomes
  RETAINED. Store access through the runtime is then refused. There is no reopen
  or attach-existing-resource API. Local container inspection is operator tooling.
- `cleanup()` releases connections and removes only this instance's container
  and its anonymous volumes. It accepts no cleanup target and is idempotent after
  success. If deletion fails, keep the handle and retry cleanup.
- No automatic deletion occurs on forecast success/failure or `close()`. Call
  `cleanup()` explicitly after the evidence review/export decision. Testcontainers'
  resource reaper can delete containers when this process exits, and daemon/host
  loss can also lose evidence. Retention is therefore **process-lifetime best
  effort**, not durable archival retention. Export approved evidence before exit.

This API does not provide an execution runner, input staging policy, retry,
recovery, scheduled promotion, or a live-output mode. The specific date, group,
inputs/artifacts and evidence retention/export still need separate authorization.
Two-database synthetic integration tests cover natural-key collisions without
model loading, prediction, real artifacts or live data.
