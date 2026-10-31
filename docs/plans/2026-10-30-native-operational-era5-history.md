---
status: READY
owner: nepal1087-weather-ingestion
created: 2026-10-30
---

# Native operational ERA5 history: configuration, admission, Prefect entrypoint

## Scope and evidence

Make PR427 usable through normal CLI/configuration and a registered Prefect run.
No operation-local wrapper/transport, live writes, HRES, model changes, scheduled
activation, schema/grant changes, general onboarding redesign, or new lock framework.

Current source contracts:
- `onboard_nepal history` passes no scope; `chwrr_stations` admits six ONBOARDING
  gauges and `require_history_bindings` expects nepal6_20260923.
- `operational_weather_stations` already checks CHWRR authority, exact group roster,
  allowed status, exact active history sources and station/basin/polygon/HRU joins.
- The injected ERA5 flow cannot be a usable deployment as-is: actual Prefect
  `parameter_schema()` reports five required opaque arguments (connection,
  identity, adapter, window, now), not serializable production inputs.
- `historical_forcing` has a unique natural key: station/source/version/time/
  parameter/spatial_type/coalesced band/member. Native `store_forcing` uses
  `ON CONFLICT DO NOTHING`; same-key repeats do not overwrite values. Latest
  version reads follow existing created_at/id semantics. Keep these unchanged.

## P1 — Named scope in existing merged configuration

Parse `[weather_history.scopes.NAME]` in `config/nepal_weather.py`:
`group_id`, `original_gateway_hru_name`, `runtime_gateway_hru_name`, and
`station_polygons=[{station_id, polygon_name}, ...]`. Validate UUIDs, nonempty names,
unique IDs/features at the boundary; return the existing `OperationalWeatherScope`
plus a small frozen original-HRU admission value. Reuse existing overlay rules:
a later array REPLACES the complete roster, never merges station rows. No new
merge system or hard-coded production UUIDs. Worker resolves `SAPPHIRE_CONFIG`
and existing overlays; deployment parameters cannot select arbitrary config paths.

## P2 — Explicit native binding admission

Add `onboard_nepal weather-bindings --weather-scope NAME --confirm-package-joins`
with existing config/tenant options and rollback `--dry-run`. Use existing
`sapphire_worker` credentials/grants, never owner credentials or new grants.
Service lives in `services/nepal_onboarding.py`, within the CLI's real transaction.

Validate the entire exact roster before writes: CHWRR ownership/group membership,
ONBOARDING or OPERATIONAL, one basin-average polygon, exact basin/name, nonnull
package provenance, and original or already-admitted runtime HRU. Reject unknown
aliases, extra/banded mappings and conflicting source records. Use native stores
to update only runtime HRU and add exact ACTIVE era5_land/REANALYSIS/basin_average
bindings. Preserve package/import time, geometry, stations/statuses, observations,
assignments and all other sources. Exact repeat is no-op; matching partial prior
admission converges atomically. No provider request or implicit ingest.

Use existing tenant advisory coordination, not membership FOR UPDATE (worker has
SELECT/INSERT/DELETE there, no UPDATE). The three existing group-store membership
mutators must take the same existing tenant key in their owning transaction:
otherwise admission's exact-roster check can race a cooperating group edit.
This is three native coordination call sites, no new namespace/framework. Existing
station/polygon UPDATE privileges permit stable-order row locks. Direct privileged
SQL remains an operator writer-exclusion responsibility. Test under actual worker
grants and one concurrent membership-writer case; do not broaden coordination.

Legacy default CODE remains unchanged. Explicit admission of overlapping six
stations intentionally changes their runtime alias: subsequent unscoped six-site
history calls reject that mismatch. Do not promise old runtime compatibility,
create parallel mappings, or reject Nepal21 merely because it overlaps six gauges.

## P3 — Standard registered production flow and CLI submission

In the existing ERA5 flow module add the minimal production entrypoint:
`ingest_operational_era5_history_flow(start: str, end: str, weather_scope: str)`.
Its Prefect name/deployment is `ingest-operational-era5-history`. These are the
ONLY deployment parameters: three required strings. Resolve worker config,
identity and named scope inside it; create native engine/adapter there. Reuse the
existing ingestion body through a shared private helper, retaining the current
injected flow and legacy behavior. Never pass connections/clients/secrets through
Prefect parameters, and never call `.fn` as the operational execution path.

Before HTTP, apply existing operational validator to exact configured roster and
all admitted sources. Use real transactions for existing <=31-day batches, closing
resources on all exits. Error rolls back current batch; earlier committed batches
remain. Empty/partial coverage follows native reporting and is not forecast-ready;
42 nonempty entries do not prove30 complete days. No invented values/tail filling.
No automatic flow retries; retain native SDK request/unit/provenance behavior.

Register through existing registrar: ingest pool, concurrency_limit=1, cron=None.
Keep new name in held Nepal skip defaults until operator admission. Add optional
`history --weather-scope NAME` to existing CLI: submit this REGISTERED deployment
via normal Prefect client, preserving its concurrency/run history, not a local
flow bypass. Reject scoped history `--dry-run` before DB/credential/provider work
or submission; legacy history and binding dry runs remain unchanged.

Standard equivalent: `prefect deployment run ingest-operational-era5-history/ingest-operational-era5-history --param start=... --param end=... --param weather_scope=NAME`.
No new registrar/publisher or automatic worker start. Existing HTTP timeout and
Prefect timeout policy apply; native SDK has no aggregate byte cap, and the
withdrawn custom transport's64MiB cap must not be claimed for this route.

## P4 — Tests/docs and exit

Test typed config/array replacement; legacy six code path; actual-role exact
admission, conflict/no-op/rollback/provenance preservation and one group race;
production resource/transaction lifecycle and no HTTP on inadmissible roster;
partial/error batch semantics and natural-key repeat; CLI deployment submission
and early scoped-dry-run rejection. Generate REAL Prefect deployment/schema from
the production function: exactly three required string parameters; no connection,
adapter, secret or config path. Verify registrar name/pool/manual/concurrency/skip.
Use native adapter with fake provider responses, no live calls.

Update Nepal/Recap runbooks, types/protocol spec, weather/onboarding/Prefect
routing maps, orchestration inventory and optional config example. Document worker
image/mounted overlays, accepted endpoint, existing secret/DB role, and scoped CLI
versus legacy behavior. No separate feature-qualification or rollback subsystem.

Focused unit/PostgreSQL tests, Ruff, type checks, then full suite after final code.
Independent Claude+Codex review; root alone sets READY before implementation.
Repository release-target policy for code commit; hold at PR for owner merge.

```json
{"phases":[{"id":"P1","depends_on":[]},{"id":"P2","depends_on":["P1"]},{"id":"P3","depends_on":["P1","P2"]},{"id":"P4","depends_on":["P2","P3"]}],"execution":"single-owner sequential"}
```

## Root review disposition and execution boundary

Independent Codex review recommends READY. Claude findings were checked against
release source: the runtime HRU alias is not a second weather source; operational
admission tests membership of the exact native history binding, not equality to
a singleton source set. ERA5 version derives from each row's source_run, not the
execution clock. Same-provenance reruns retain native conflict handling; changed
provider versions are not falsely described as no-op repeats.

Reject non-exact existing ERA5 bindings; preserve unrelated sources. Acquire the
existing transaction-scoped tenant advisory lock before membership access or row
locks, on the same owning connection. Parse dates using the shared UTC boundary
contract; reject naive/invalid/reversed windows before submission; end exclusive.

Implementation is authorized for this bounded shared change only. Preserve other
deployments, schedules, legacy defaults and data. No live register-all operation,
custom wrappers, optional hardening or expanded onboarding redesign. Hold at PR
for owner approval; runtime operations remain separate.
