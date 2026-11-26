---
status: READY
---
# Ingest-pause command: a CLI for set_station_ingest_paused

HIGH RISK under `AGENTS.md` (writes station state with an audit trail on a live host). MVP, no
production: smallest change only; no dashboard, no endpoint. Base the branch on current `main`
(it must contain #676: `services/station_ingest_pause.py`, `update_station_ingest_paused`,
migration 0096).

## Problem

#676 added `set_station_ingest_paused` and its audit row, but nothing outside tests can call
it. To stop polling station 447 (destroyed, not in BIPAD) on the Nepal host, an operator would
need raw SQL, which skips the audit row and the tenant check. The admin dashboard control is a
later hand-off to #455.

## Decision (owner, 2026-11-26)

Build a minimal operator command now, mirroring `cli/promote_station.py` (Plan 540 M7a).

## Change

New `src/sapphire_flow/cli/ingest_pause.py`:
`python -m sapphire_flow.cli.ingest_pause --tenant T --network N --code C --operator O
--reason "..." (--pause | --resume) [--apply]`. `--pause`/`--resume` are an argparse
`add_mutually_exclusive_group(required=True)` (argparse exits 2 itself).

- Wiring: as `promote_station.main` (`promote_station.py:~192`): ONE
  `create_engine_from_env().begin()` connection; build `PgStationStore(conn)`,
  `PgAuditLogStore(conn)`, `PgTenantStore(conn)` on that same connection, so the flag update
  and the audit append commit or roll back together. Resolve the station with
  `fetch_station_by_code(code, network)` (the service takes a `station_id`).
- Structure (needed so tests can inject a failing audit store): a function
  `run_ingest_pause(args, *, identity, tenants, stations, audit, clock)` holds the logic, as
  `promote_station.run_promote` does; `main` only builds the Pg stores on the single
  connection and calls it.
- Input checks BEFORE the dry-run/apply branch (so dry run and apply agree): `--reason` must be
  non-blank after strip and `--operator` non-blank after strip, else exit 2.
  `resolve_run_principal` keeps `""` as a principal id, so the CLI does this check itself.
- Identity and tenant gate: `load_deployment_identity_config`, `resolve_run_principal(tenants,
  identity, tenant_code=..., operator=...)`, `TENANT_REFUSAL_FOR_CLI`. The write gate is the
  host's `[deployment] writable_tenants` (or `global_admin`), enforced by
  `resolve_run_principal`. Do NOT reuse `promotion_allowed`: it gates promotion only, defaults
  false and the Nepal overlay does not enable it; pausing is reversible and has its own audit.
- Dry run (default, no `--apply`): the CLI must NOT call the service (it writes, including the
  tenant-rejection audit row). It resolves the principal, reads the station, compares
  `principal.tenant_id` with `station.tenant_id` read-only (mismatch: print
  `TENANT_REFUSAL_FOR_CLI`, exit 3, no write, so a dry run cannot leak another tenant's
  state), and prints the station, current value, the change that would be made and the audit
  text, or "NO CHANGE" when already in the requested state.
- `--apply`: call `set_station_ingest_paused` (the service re-checks tenant, blank reason and
  blank operator, no-ops with no audit row when unchanged). Print the result and "NO CHANGE"
  when it returned False.
- Exit codes, mirroring `promote_station` exactly (`promote_station.py:129-135`):
  0 ok (including NO CHANGE); 2 usage or `ConfigurationError` (blank reason, blank operator,
  unknown station, invalid config, DB error); 3 `TenantIsolationError`.
- Structured logging per `docs/standards/logging.md`: log `ingest_pause_applied` with station
  id, `to` and operator; keep the free-text reason out of the log (it is in the audit row).

Nepal host prerequisites (state in the docs): image at or after the #676 release with
migration 0096 applied (otherwise the station queries fail); `SAPPHIRE_CONFIG` (or `--config`)
and `DATABASE_URL` set; the `[deployment]` section grants the tenant (`chwrr` in
`config/overlays/nepal-cloud.toml`) in `writable_tenants` or `global_admin`. Implementer
confirms `audit_log.event_type` is free text (the enum member is Python-only; no DB check
constraint).

## Tests

Unit tests with the fakes, in the style of `tests/unit/cli/test_promote_station.py` (a `_run`
helper, BASE argv, `FakeTenantStore`): dry run writes nothing (no flag change, no audit row);
`--apply --pause` changes False to True and writes exactly one audit row whose `from` is the old
value and `to` the new one, with reason and operator; `--resume`; apply on a station already in
that state is exit 0 with zero audit rows ("NO CHANGE"); unknown station exits 2; blank and
whitespace `--reason` exit 2; blank `--operator` exits 2; `--pause` with `--resume` and neither
exit 2; an ALLOWED CLI tenant targeting ANOTHER tenant's station exits 3, leaves the flag
unchanged and records exactly one rejection audit row (`outcome=rejected_tenant_mismatch`, no
from/to change row); a dry run on a wrong-tenant station also exits 3 without writing.
Integration test on the PostGIS testcontainer (style of
`tests/integration/cli/test_promote_station_pg.py`): `--apply` updates `stations.ingest_paused`
and appends the audit row in one transaction; a failing audit store (the `_FailingAudit`
pattern, `test_promote_station_pg.py:~135`) after the flag update rolls the flag back,
asserted on the SAME connection after the savepoint rolls back, exactly as
`test_promote_station_pg.py:~136-148` (the `db_connection` fixture's seed is uncommitted, so a
fresh connection cannot see it; wrap the call in `begin_nested()` as the `_Engine` class at
`:~155-163` does); a tenant-mismatch refusal leaves `ingest_paused`
unchanged and persists exactly the one rejection audit row. Mutations, each must fail a test:
write the flag without the audit row (killed by the failing-audit test); skip the tenant
check (killed by the allowed-tenant-other-station test); swap pause and resume polarity.

## Docs

`docs/operations/dhm-ingest-gap-recovery.md`: a short section "Pausing ingest for a station
that is down or out of service": the command, run the dry run first, how to resume, what it
changes (polling only; forecasts, QC and station status untouched) and the host
prerequisites above.

## Out of scope

Admin dashboard endpoint/UI (#455), bulk pause, scheduling, any change to the service, the
store or the ingest flow.

## Review and merge

Independent Claude and Codex plan reviews before READY (round 1 done 2026-11-26; this
revision folds both); independent patch reviews; ruff, changed-module types, the tests above;
full CI. Owner merges; deployment is the host operator's.
