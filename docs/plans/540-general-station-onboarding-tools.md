---
status: READY
created: 2026-11-17
revised: 2026-11-17
plan: 540
title: General station onboarding tools - replace the by-hand steps with commands
scope: Turn the by-hand steps in docs/operations/new-station-by-hand.md into commands an administrator can run for ANY new station (not only the six DHM gauges), in small milestones (M0 to M8, with M3 split 3a-3d and M5b after M5). NOT production hardening (MVP), NOT the hydrologist pathway design (program #450 job B), NOT model training, NOT new QC rules.
depends_on: ["branch docs/452-guide-gaps-walkthrough (unmerged guide rewrite; merge it first - see Cross-milestone notes)"]
blocks: []
related: [450, 452, 517, 541]
open_decisions: []
source: 2026-11-16 sandbox rehearsal of Trishuli 00001 (program #450 / #452) and the merged runbook PR #532. Number chosen by the orchestrator session 2026-11-17 at the owner's request (300 and 400 were taken).
---

# Plan 540 - general station onboarding tools

## Status

**READY** (set by the orchestrator 2026-11-17). Reviews: one reviewer per milestone, then an administrator and a developer review, a fresh final review, and a narrow review of the fix pass, which approved flipping once its small text fixes (S1 to S4 and the minor items) were applied. Those fixes were applied exactly as listed; no owner decision was open. Caveats for implementers: M2 and M3b for bridge sensors follow the recorded design; the Nepal-host binding-row check (M5a) and the M8 items 2 to 4 read pass are preconditions inside their own milestones, not reasons to hold the others.

## Implementation status

Status notes only (2026-11-20); the plan text below is otherwise unchanged. PR numbers are from `git log origin/main`.

**Merged:** M0 (#541), M1 (#542), M2 (#551, migration 0087), M3a (#552), M3b (#557), M3c (#559), M3d-1 (#554, the synthetic discharge generator and fully synthetic onboarding scenario in tests), M4a (#563), M4b (#587), M5a (#574), M5-assign (#561), M5b (#580), M6(a) (#545), M7a (#566), M8 item 1 (#546).

**Deferred:** M3d-2 (a synthetic-series command; the guide keeps a sandbox snippet), M6(b) (verify-only overlay documentation), M8 items 2 to 4, and M7b (the dashboard promotion action, a separate plan).

**Owner decision 2026-11-19:** for the MVP the only promotion approval is the host switch `[deployment] promotion_allowed` (off by default; the owner turns it on for one run and back off). No per-person admin approval; it belongs to the M7b plan.

**Skipped for the MVP:** the follow-up to make the `global_admin` flag parse strictly instead of through `bool()` (it fails open on a malformed value). Not scheduled.

**As shipped in M4:** the optional training-window check (check 11, `training_samples`) is a separate optional section next to `--check-gateway`; neither is counted in the "N checks failed, M passed" line, and thin training history does not change the operational checks. `weather_wait_facts` (10c) treats the newest days of the lookback as not expected (cutoff today minus 8 days, so 7 days of a lookback that ends yesterday; the operational product fills them at forecast time), and it does not show the last forecast cycle's outcome (the cache has no read-by-station method).

## Why

The rehearsal onboarded a synthetic station end to end, but steps 3, 5 (group, forecast and operational bindings), 6, 7 and 8 were done with heredocs, and `onboard_nepal qualify` only knows six gauges. The runbook (`docs/operations/new-station-by-hand.md`) is the specification of what each command must replace. Findings: `docs/operations/new-station-by-hand.md` error table and the rehearsal notes in the #452 thread.

## Owner decisions already taken (2026-11-16)

- One Gateway runtime name per station, used identically in all tools (e.g. `trishuli_00001`).
- The sandbox uses its own sandbox group, never `chwrr`; the group script gets required `--tenant` and `--network` options as a separate piece of work (M1).
- Onboarding asks which GAUGE TYPE the station has first (decided 2026-11-17), then asks for the datum in that type's terms: (1) staff/pressure gauge: enter the elevation of the gauge zero in m a.s.l.; the reading is the gauge height above it; stored = datum + reading. (2) bridge-mounted radar/ultrasonic sensor: enter BOTH the gauge-zero elevation (stored as the station datum) and the sensor's elevation in m a.s.l.; the reading is the distance down to the water; stored = sensor elevation - distance. Stored water level is always m a.s.l.; live QC subtracts the datum (gauge zero) exactly as today, so it sees the gauge height. The tool echoes one converted example reading before saving (and in dry run). The gauge type is stored as a typed enum (not a bool) beside the datum; the loader uses it. Range checks run on the converted level (OD4, decided); a hydrologist confirms the QC limits once for such stations.
- The static-name library already exists in code (`CARAVAN_ALIAS`, `services/caravan_statics.py`); onboarding must use it.
- A new station may join an already existing group.
- Step 8 (promotion to operational): CLI command now (M7a); the dashboard button is a later, separate plan. ONE safeguard only: host setting `promotion_allowed`, off by default (no `--confirm-shared-host` flag).
- `onboard_nepal` generalisation away from CHWRR is a separate milestone M5b, after M5; M5b keeps the strict CHWRR-only host rule for tenant chwrr, other tenants get the normal tenant check (OD5, decided).
- Every command that writes is dry-run by default and needs `--apply` (consistent across all milestones).
- No sandbox helper script (Plan 541 S7 dropped); the standing rule for the IFS backfill and the sandbox review-reference placeholder are accepted (Plan 541).

## Milestones

Each milestone is one PR, independently useful, except M3 (3a to 3d), M4 (4a/4b) and M5 (5a, assign, 5b), which are split as described in their own sections. M0, M1, M6 and M8 do not need the unmerged guide branch; the runbook-editing parts of the others do. Recommended order is in Cross-milestone notes.

- **M0 - Gateway key from a file.** New `RECAP_API_KEY_FILE` setting.
- **M1 - Group script: tenant, network, join.** `scripts/create_station_group.py` gets required `--tenant` and `--network`; joining an existing group is documented and tested.
- **M2 - Station creation command** with gauge-type and datum prompt; also lists existing tenants and networks.
- **M3 - General observation load.** 3a parse/convert/store with datum and gauge type; 3b QC on load; 3c rating curve and derived discharge; 3d synthetic generator and a committed discharge fixture (sandbox only). Loads get new audit event types.
- **M4 - Readiness report** replacing `qualify` for any station (facts only).
- **M5 - Forecast and operational weather bindings; `assign_group_model` command** (two PRs: M5a bindings, M5-assign assignment).
- **M5b - Generalise `onboard_nepal` away from CHWRR** (after M5).
- **M6 - Registrar code-path option; overlay doc verification.**
- **M7a - Promotion command** (`promote_station`). The dashboard button (M7b) is a later separate plan, not in this plan.
- **M8 - Gateway `prepare` usability** (named reasons, short output); needs a read pass first.


**Implementation status (2026-11-18, superseded by the "Implementation status" section above):** merged: M0 (#541), M1 (#542), M6(a) (#545), M8 item 1 (#546), M2 (#551). M2's migration is **0087**, not 0086, because `main` already had `0086_weather_period_evidence`; the revision-0086 wording below is superseded. Tests that stand at an older revision and use `PgStationStore` need `tests/integration/legacy_stations.py::legacy_station_columns`.

## M0 - Gateway key from a file (added 2026-11-17 from the fresh-admin walkthrough)

**Problem.** The key convention is a file (`./secrets/sapphire_dg_api_key`, mounted at
`/run/secrets/sapphire_dg_api_key`; developers keep it under `~/.config/sapphire-flow/secrets/`).
`prepare_gateway` takes `--secret-file`, but the history job, the model-import and ingest workers
and the forecast cycle (`config/recap_gateway.py::load_recap_api_key`) read only that Docker path
or the `RECAP_API_KEY` variable. Outside Docker a person must export the key by hand, and an
assistant cannot. There is no key manager (the security standard rejects SOPS/age); none is added.

**Change.** In `load_recap_api_key`: after the Docker path and before `RECAP_API_KEY`, read the
file named by `RECAP_API_KEY_FILE` if set (non-empty content required; an unreadable or empty file
named by the variable is a `ConfigurationError`, not a silent fall-through). Existing sources and
order for deployed hosts are unchanged. Never log the path's content. Precedent: `scripts/recap_probe_loop.py:91-98` already uses `RECAP_API_KEY_FILE` (reuse the name and the 0600 convention). Callers that all benefit: `cli/onboard_nepal.py:119`, `cli/prepare_gateway.py:252`, `flows/ingest_recap_era5_reanalysis.py:197`, `flows/ingest_recap_reanalysis.py:340`, `flows/run_forecast_cycle.py:1049, 3029`. The error for an empty/unreadable file names the variable (`RECAP_API_KEY_FILE`).

**Tests.** `tests/unit/config/test_recap_gateway.py`: file variable read; empty file raises; unset
keeps old behaviour; Docker path wins over the variable (testable through the `secret_path` argument of `load_recap_api_key`, no monkeypatching of the constant). **Docs.** security standard (secrets
section), `recap-gateway-runbook.md`, step list in `new-station-by-hand.md` (replace the
`$(cat ...)` export line). Patch version bump (code). Size: small (about 10 lines plus tests).

## M1 - Group script: `--tenant` and joining an existing group

### Goal
`scripts/create_station_group.py` can create or extend a group in any tenant the host is allowed to write (sandbox, `chwrr`), not only the Swiss default tenant. Replaces runbook step 5a. **Owner decision 2026-11-17: `--tenant` and `--network` are REQUIRED, with no Swiss default.** Existing Swiss callers must now pass both (M1 greps every use and updates docs and scripts); omitting either fails with a clear message.

### What the code already does (verified by reading)
- **Join an existing group already works.** `plan_station_group` looks the group up by `(tenant_id, name)` (`PgStationGroupStore.fetch_group_by_name`), reuses its id, and plans only the missing members; `apply_station_group` skips `store_group` when `group_exists` and only calls `add_station_to_group`. Re-run is a no-op. Unit tests exist (`TestApplying::test_an_existing_group_is_reused_not_recreated`, `TestPlanning::test_an_existing_member_is_not_planned_again`). So "join" needs no logic change, only a documented, tested path with a non-default tenant, and a clear output line.
- **Other-tenant station is already reported, not written.** `plan_station_group` puts a station whose `tenant_id != tenant_id` into `wrong_tenant_codes`; `blocking_codes` makes `main` print `OTHER TENANT:` and exit 1 before any write; `apply_station_group` raises `ValueError` if called anyway (`TestTenantSafety`).
- **The only hard-coded part:** `main` passes `DEFAULT_TENANT_ID` to both `enforce_tenant_isolation` (pre-check) and `plan_station_group`. This is the one real gap.
- `stations` is unique on `(network, code)` globally (`uq_stations_network_code`, `db/metadata.py`), so a code lookup is unambiguous across tenants and the wrong-tenant check is meaningful.
- Tenant resolution convention: `resolve_run_principal(tenant_store, identity_config, tenant_code=..., operator=...)` in `services/write_principal.py` validates an explicit code against `[deployment] writable_tenants` (bypassed for `global_admin`) and resolves it via `services/tenant_boundary.resolve_tenant_code` (unknown code -> `ConfigurationError "unknown tenant code"`). `provision_tenants` only creates tenants from config (no `--tenant` flag). The Nepal CLI (`cli/onboard_nepal.py`) uses `--tenant` with `choices=["chwrr"]` and a hard CHWRR-only identity check; that choice list is Nepal-specific and must NOT be copied.

### Files / functions to change
1. `scripts/create_station_group.py` only (plus tests/docs below).
   - `_build_parser`: add `--tenant CODE` (required, no `choices`; help: "tenant code, e.g. a sandbox or chwrr"); `--network` becomes required (today default `bafu`).
   - `main`: always call `resolve_run_principal(..., tenant_code=args.tenant, operator=args.operator)` and take `target_tenant_id = principal.tenant_id`; use it in the existing `enforce_tenant_isolation` pre-check and in `plan_station_group(tenant_id=...)`. There is no default tenant and no `DEFAULT_TENANT_ID` fallback (owner decision).
   - The `global_admin` + `--tenant` case: `resolve_run_principal` with an explicit code returns that tenant's principal, so the target is the requested tenant. Good, no special case.
   - `apply_station_group` and `plan_station_group` signatures unchanged (they already take `tenant_id`/`plan.tenant_id`).
   - `_render`: no change needed (it already prints `tenant:` and `exists: yes - reusing`). Optionally add the tenant code to the `tenant:` line; deferred (see open questions).
2. No change to `services/write_principal.py`, stores, or schema.
3. Dockerfile curated list unchanged (script already shipped; `tests/unit/deploy/test_dockerfile_operator_scripts.py` unaffected).

### CLI surface
```
create_station_group.py --name NAME --station-code CODE [--station-code CODE ...]
    --network NETWORK          # REQUIRED (was default "bafu"); e.g. research for Nepal/sandbox
    --tenant CODE              # NEW, REQUIRED
    [--description T] [--operator O] [--apply]
```
Output unchanged in shape: `DRY RUN (nothing written)` / `APPLIED`, then group, tenant id, `exists: yes - reusing | no - would create`, `add:`, `already:`, `UNKNOWN:`, `OTHER TENANT:`. Join example: `--tenant chwrr --network research --name sandbox --station-code 00002 --apply` against an existing group `sandbox` prints `exists: yes - reusing`, `add: 00002`.

### Error cases
| Case | Behaviour |
|---|---|
| `--tenant` unknown in `tenants` table | exit 1, `ERROR: unknown tenant code '...'`, no write (from `resolve_tenant_code`) |
| `--tenant` not in `writable_tenants` and not `global_admin` | exit 1, `ConfigurationError ... not in this host's writable_tenants`, no write |
| Station code resolves but belongs to another tenant | exit 1, `OTHER TENANT: code`, nothing written (existing) |
| Station code not found (including wrong `--network`) | exit 1, `UNKNOWN: code` (existing) |
| Group exists in tenant | reused, missing members added, never removed (existing) |
| Same group name exists only in ANOTHER tenant | treated as "new" in the target tenant (name unique per tenant); a new group is created. Intended. |
| `--apply` rejection by tenant mismatch | durable `rejected_tenant_mismatch` audit row (existing path; now reachable for non-default targets) |

### Tests to add
- **Update the seven existing call sites that omit the now-required options:** `tests/unit/scripts/test_create_station_group.py` `main()` calls at lines 425, 446, 511, 533, 569, 588, and the integration helper at `tests/integration/scripts/test_create_station_group_script.py:342` (line 210 passes `--network` only; add `--tenant`). Verify the line numbers when coding.
- `tests/unit/scripts/test_create_station_group.py`, class `TestCliTenantOption` (reuse `cli_stores`, `deployment_config`, `_FakeTenantStore`; extend `_FakeStationLookup` fixture so a station can carry the foreign tenant id):
  - `test_tenant_option_targets_that_tenant`: config `writable_tenants=["foreign"]`, `--tenant foreign --apply` -> exit 0, group stored with `tenant_id == _FOREIGN_TENANT_ID`, audit `detail["tenant_id"]` is the foreign id.
  - `test_tenant_option_outside_writable_tenants_is_refused`: config `["sapphire"]`, `--tenant foreign` -> exit 1, "writable_tenants" in stderr, nothing stored.
  - `test_unknown_tenant_code_is_refused`: `--tenant nope` -> exit 1, "unknown tenant code", nothing stored.
  - `test_global_admin_can_target_a_named_tenant`.
  - `test_station_of_another_tenant_is_reported_with_tenant_option`: `--tenant foreign` but station is Swiss -> exit 1, `OTHER TENANT` in stdout, nothing written.
  - `test_joining_an_existing_group_in_the_named_tenant`: pre-seeded group in foreign tenant, new station code -> only `add_station_to_group` called, no `store_group`, audit `created: False`.
  - `test_omitting_tenant_or_network_fails_with_a_clear_message` (two cases; argparse exit 2, option named in stderr). This replaces the old "default tenant unchanged" regression guard, which no longer applies; `test_configured_authority_controls_real_apply` is kept but must pass `--tenant`/`--network` explicitly.
- `tests/integration/scripts/test_create_station_group_script.py` (Postgres testcontainer; `committed_cli_seed` already provides a foreign tenant and station): one test `TestCliTenantOptionAgainstPostgres::test_group_is_created_then_joined_in_a_non_default_tenant` - create group with station A in the foreign tenant, re-run with station B, assert group members {A,B}, two audit rows, default-tenant groups untouched. (Verified by the final review: `committed_cli_seed` returns only ONE foreign station code, so extending the fixture with a second foreign station is part of M1.)
- Acceptance item: grep every use of `create_station_group` repo-wide (code, cron, compose, docs). Verified by the developer review: `Dockerfile:177`, `ci.yml:996`, `tools/private_verification_gates.sh:161`, `docs/standards/cicd.md:711` only list the file name and are unaffected; the docs listed below are the callers to change.

### Docs to update
- `docs/operations/new-station-by-hand.md` step 5a (and the line ~145 mention of the script): replace the "no tool for a non-Swiss tenant" heredoc with the command above; keep the heredoc out. Keep the "scope must list all members" warning (belongs to 5b, not this script).
- `docs/deployment/mac-mini-staging.md` (~line 849, "Its fixed pilot target remains the default tenant"): rewrite - `--tenant` and `--network` are required; the tenant must be in `writable_tenants` or `global_admin`.
- `docs/standards/security.md` (~line 227) and `docs/spec/types-and-protocols.md` (~line 1677): check wording; "CLI pre-check" now authorizes the requested tenant. Likely a one-clause edit only.
- Script module docstring: usage example with `--tenant`.
- Plan 540 status line / milestone checkbox when merged.

### Version / release target
One code PR: `uv run bump-my-version bump patch`, stage the version files with the code, per AGENTS.md. Operator script only: no migration, no API or flow change. It reaches a host only with the next image deploy (the script ships in the image); the sandbox can run it from the checkout straight away.

### Risks
- Authority widening: `--tenant` makes the CLI usable against any tenant the config allows. Mitigation is already in place (writable_tenants check + durable rejection audit); the new tests must cover refusal, since the existing ones only cover the default tenant.
- A wrong `--network` gives `UNKNOWN`, which will confuse new users. The error text hints at `--network` and, once M2's listing helper exists (distinct networks query), lists the known network codes; until then only the hint. The listing needs a new small query, so M1 is a bit larger than 20 lines.
- **Plan 517 overlap:** Plan 517 (DRAFT) adds `--remove` to the same script and has its own tests. Whichever lands second rebases onto the other; both must use the required `--tenant`/`--network`.
- Do not mix up this script's `--tenant` (free code, config-gated) with the Nepal CLI's `choices=["chwrr"]`.
- Atomicity comment in `main` (group store must join the write transaction) must stay intact; do not refactor that block.

### Out of scope
Removing members; renaming groups; assigning models to groups (T3b); weather scopes (5b, M5); making station creation tooling (M2); any change to `station_status`.

### Owner questions (1 and 2 decided 2026-11-17)
1. DECIDED: no Swiss default; `--tenant` is required.
2. DECIDED: `--network` is required too; print a hint listing known network codes when one is unknown.
3. **Show the tenant code (not only its id) in the output?** Recommendation: yes if cheap (the code is already known from `--tenant`); otherwise skip - small nicety.

### Size
Small (about 20-40 changed lines in the script, 7-8 unit tests plus updating 7 existing call sites, 1 integration test, 4-5 doc edits).

### Cross-milestone note
M2 (station creation) and M5 (weather scope) need the group id and station ids printed here; the output already prints the group id. If M2 wants to call the group step itself, it should import `plan_station_group`/`apply_station_group` (both already take `tenant_id`) rather than re-implement. M1 already follows the write convention below (dry run by default, `--apply`, `resolve_run_principal`, audit row).

## M2 - Station creation command with gauge-type and datum prompt

### Goal
Replace step 3 of `docs/operations/new-station-by-hand.md` (heredoc around `PgStationStore.store_station`) with one command that creates the `ONBOARDING` station row for any tenant, asks for the gauge type and the water-level datum, stores both, and tells the admin what to give the basin handover and the Gateway. Dry-run by default, `--apply` writes. A small `list` subcommand prints the existing tenants and networks (so the person knows what to pass to `--tenant` / `--network` here and in M1).

### Verified facts (read in the code)
- Datum already exists end to end (the gauge TYPE does not; see Gauge type below): `StationConfig.water_level_datum_masl: float | None` (`types/station.py:53`), column `stations.water_level_datum_masl` (`db/metadata.py:304`), written by `PgStationStore.store_station` and `update_station` (`store/station_store.py:121,156`), read by ingest QC, historical ingest, forecast cycle (`services/qc_datum.py`, `obs_skipped_rules` skips range rules when it is None). No migration, no new field. Stored water level is m a.s.l. and QC subtracts the datum (`services/qc_datum.py`). The meaning of the datum depends on the gauge type (owner decision 2026-11-17, see below).
- `stations` uniqueness is `(network, code)` globally, NOT per tenant (`uq_stations_network_code`, `metadata.py:368`). So "refuse if (code, network) exists" is also a cross-tenant refusal; `fetch_station_by_code(code, network)` (`station_store.py:79`) finds it.
- `tenant_id` is required on `StationConfig`; resolve a code with `services/tenant_boundary.resolve_tenant_code(PgTenantStore(conn), code)` (hard error on unknown code).
- Write authority: `config/deployment_identity.load_deployment_identity_config` (`writable_tenants` / `global_admin`). `cli/onboard_nepal.py` hard-codes `choices=["chwrr"]` and requires CHWRR-only authority; M2 must NOT copy that (general tool).
- The basin importer matches stations by `(code, network)` via `fetch_station_by_code` (`cli/import_basin_package.py:85`); a missing station holds the basin (`onboarding_held`). So the link to the handover package is only the exact `(code, network)` pair; there is no foreign key to create.
- The Gateway runtime name is NOT stored on the station. It lives only in the `[weather_history.scopes.<name>]` TOML (`config/nepal_weather.py`, `runtime_gateway_hru_name`) and as `gateway_hru_name` in `recap_gateway_polygon_bindings`. Nothing in the station table to write it to (verified by grep).
- `timezone` is a free `str` on the station; I found no IANA validation in `types/station.py` (not fully verified elsewhere); the allowed `measured_parameters` vocabulary is also unverified. Both must be checked before coding (developer review finding).
- Existing tenants can be listed with `PgTenantStore.fetch_all_tenants()` (`store/tenant_store.py:60`). No distinct-networks query was found; add a small one (`SELECT DISTINCT network FROM stations`) for the `list` subcommand and for M1's error hint.
- Write authority and audit: follow the M1 pattern. `resolve_run_principal(tenant_store, identity_config, tenant_code=..., operator=...)` (`services/write_principal.py`) gives the principal; `--apply` writes an audit row (`AuditEventType.STATION_ONBOARDED` exists; operator in `detail`). Dry run writes neither station nor audit. `--config` / `SAPPHIRE_CONFIG` convention: use the same mechanism as `scripts/create_station_group.py` (developer review found `--config` here versus the env var there inconsistent; **decided:** env var `SAPPHIRE_CONFIG` as M1 does, with an optional `--config PATH` override; this applies to all new commands M2, M3, M4, M5-assign, M7a; the examples show `--config` for clarity).
- Existing CLI pattern to copy: `cli/provision_tenants.py` (argparse, `create_engine_from_env`, `configure_cli_logging`, `engine.begin()`, structlog, `main(argv) -> int`), tests in `tests/unit/cli/test_provision_tenants.py` with `FakeTenantStore` from `tests/fakes/fake_stores.py`. `scripts/onboard.py` is CAMELS-CH only (not reusable); `flows/onboard.py` is the Prefect CAMELS flow (not touched).

### Gauge type and datum (owner decision 2026-11-17; OD4 closed)
The tool asks the gauge type FIRST, as a typed enum `WaterLevelGaugeType` (`STAFF_OR_PRESSURE`, `BRIDGE_SENSOR`; no bool):
- `STAFF_OR_PRESSURE`: "Elevation of the gauge zero in m a.s.l." (the datum); reading = gauge height above it; stored = datum + reading.
- `BRIDGE_SENSOR` (radar/ultrasonic): asks for BOTH "Elevation of the gauge zero in m a.s.l." (stored as the station datum, which live QC subtracts exactly as today) and "Elevation of the sensor in m a.s.l." (stored as `water_level_sensor_masl`); reading = distance down to the water; stored = sensor elevation - distance. `create_station` REFUSES a bridge sensor unless both elevations are given, and refuses a sensor elevation on a staff-gauge station.
The command echoes one converted example (staff: `reading 2.0 m -> stored 1482.0 m a.s.l.`; sensor: `reading 3.0 m, sensor 1490.0 -> stored 1487.0 m a.s.l., 7.0 m over gauge zero 1480.0`) in the prompt confirmation, the dry run and the apply output.
Why this shape (final review F1, CONFIRMED): live QC subtracts the datum as a bare float (`services/qc_datum.py::shift_observations_for_water_level_datum` does `value - datum`) and the datum is passed without gauge type by `flows/ingest_observations.py:1511,1866`, `flows/historical_ingest.py:355`, `flows/run_forecast_cycle.py:3391,4005,4146,4336,4416`, `api/network_overview.py:175,293`, `api/routes/api_review.py:176,233`, `adapters/dhm.py:65`. Because the loader stores values already converted to m a.s.l. and the datum is the gauge zero for BOTH types, all of these paths work unchanged and no live code needs the gauge type. QC range checks therefore run on the converted level (decided). The citations were taken from the review; the implementer re-greps them.
**Out of scope for this plan:** live ingest of RAW distance readings for a bridge-sensor station. Its feed must deliver converted values (m a.s.l.) or be handled by a later plan; a promoted bridge-sensor station whose live feed delivers raw distances would be QC'd wrongly. A hydrologist confirms the QC limits once for such stations.
**Storage and migration:** new nullable columns `stations.water_level_gauge_type` (text with a CHECK allowing BOTH enum values `staff_or_pressure`, `bridge_sensor`, or NULL) and `stations.water_level_sensor_masl` (double; CHECK: set only when the gauge type is `bridge_sensor`). New Alembic revision 0086 chained on the current head 0085 (`alembic/versions/0085_station_rating_history.py`) with a downgrade; keep `db/metadata.py` in sync; bump the head pin `_RELEASE_B_HEAD = "0085"` at `tests/unit/db/test_alembic_head_release_b.py:108` (and any other test that references 0085; grep); extend `_row_to_station`, `store_station` and `update_station` in `store/station_store.py`; add an up/down testcontainer test. `StationConfig` gains `water_level_gauge_type: WaterLevelGaugeType | None = None` and `water_level_sensor_masl: float | None = None` (defaults keep the ~32 existing constructors valid; `__post_init__` enforces the pairing rules). Hosts get the columns only on the next image deploy, when the owner runs the migration. Record the types in `docs/spec/types-and-protocols.md`.
- M2 migration checklist additions (narrow review S3): besides the head-pin test at `tests/unit/db/test_alembic_head_release_b.py:108`, two more tests assert revision 0085 and must be bumped: `tests/integration/db/test_operator_role.py:1773` and `tests/integration/services/nepal_readiness_isolation_fixture.py:5370` (re-grep for `"0085"` before the PR). New contradiction rule: a bridge-sensor station's sensor elevation must be greater than its gauge-zero datum (refuse otherwise), in addition to the pairing rule.
Validation note (final review): nothing validates the `measured_parameters` vocabulary or the timezone today (free text columns), so the `{water_level, discharge}` and `ZoneInfo` checks below are NEW rules introduced by M2.

### Files / functions
New:
- `src/sapphire_flow/cli/create_station.py` with `main(argv=None) -> int` run as `python -m sapphire_flow.cli.create_station`.
  - `parse_station_request(args) -> StationRequest` (frozen dataclass: code, network, name, `GeoCoord`, tenant code, `ZoneInfo` timezone string, `frozenset[str]` parameters, `gauge_type: WaterLevelGaugeType | None`, `datum_masl: float | None`, `sensor_masl: float | None`, `gateway_name: str`). Parse, don't validate at the edge.
  - `plan_station(store, tenants, request, *, new_id, clock) -> StationConfig` (pure apart from the two reads; raises `StationExists` / `ConfigurationError`).
  - `main` opens `engine.begin()`, builds the plan, prints it, writes only with `--apply`.
Edit: none in `src/` other than the new module (reuse `PgStationStore`, `PgTenantStore`, `resolve_tenant_code`). Optional small helper only if M3 needs it: none planned.

### CLI surface
`uv run python -m sapphire_flow.cli.create_station --config <toml> --tenant <code> --code 00001 --network research --name Trishuli --lon 85.34 --lat 28.16 --timezone Asia/Kathmandu --parameters water_level,discharge --gauge-type staff|bridge-sensor --datum-masl 1480 [--sensor-masl 1490] --gateway-name trishuli_00001 [--apply]`
`... create_station list --config <toml>` prints existing tenant codes and network codes (read only).
- Default is dry-run: prints the row that would be written, the QC consequence of the datum, and the exact next steps (give `code`+`network` to the basin handover; register `gateway-name` in the Gateway; paste a `[weather_history.scopes.<...>]` snippet skeleton using it). `--apply` writes. (Dry-run is the default, so no `--dry-run` flag; this deliberately differs from `onboard_nepal` where dry-run is opt-in.)
- Gauge type and datum: `--gauge-type`, `--datum-masl <float>` (gauge zero) and, for a bridge sensor, `--sensor-masl <float>` are the scripted path. If absent and stdin is a TTY (dry run or apply), first ask "Gauge type: 1) staff or pressure gauge, 2) bridge-mounted radar/ultrasonic sensor", then the elevation prompt for that type (texts above; empty = unknown, water-level range checks stay off). If absent and not a TTY, require explicit `--datum-unknown`; otherwise refuse (a forgotten datum must not pass silently). A datum without a gauge type is refused; a bridge sensor without both elevations is refused. Only asked when `water_level` is in `--parameters`; for discharge-only stations both are rejected as not applicable. `water_level_unit` is set to `"m"` when the datum is set.
- `--config` is used only for `load_deployment_identity_config` (write authority: the tenant must be in `writable_tenants` or `global_admin`).
- Fixed values for the first cut: `station_kind=RIVER`, `station_status=ONBOARDING`, `ownership=OWN`, `gauging_status=GAUGED`, `basin_id=None`, `forecast_targets=None`, `wigos_id=None`, `regulation_type=None` (same as the by-hand heredoc).

### Error cases (each: exit 1, one clear structlog event, no write)
- `(code, network)` already exists (any tenant) - states that it exists and its status; names the other tenant only when the caller is `global_admin` (administrator review: do not reveal another organization to a scoped admin).
- Unknown tenant code; tenant not writable by this deployment.
- Lon/lat out of range (`GeoCoord.__post_init__`); non-IANA timezone (`zoneinfo.ZoneInfo`); empty code/name/network.
- Parameters outside `{water_level, discharge}` (assumed v1 vocabulary - verify the allowed set before coding); empty set.
- Datum non-finite, or outside a sanity band (e.g. -500 to 9000 m); datum or gauge type given for a station without `water_level`; datum without gauge type.
- Gateway name not matching `^[a-z0-9_]+$` (lower-case slug like `trishuli_00001`).
- `--apply` with insert race: DB unique constraint violation is caught and reported as "exists" (no bare except).

### Tests
- `tests/unit/cli/test_create_station.py`: dry-run writes nothing (fake store); `--apply` stores one `StationConfig` with `ONBOARDING`, given tenant id, gauge type, datum and unit "m" plus one audit row, dry run neither; the converted example is echoed for both gauge types with the right sign; tenant not writable refused; existing `(code, network)` refused, including when owned by another tenant; unknown tenant; non-writable tenant; bad coordinates / timezone / parameters / gateway name; datum rejected without `water_level`; missing datum + non-TTY refused unless `--datum-unknown`; prompt path with monkeypatched stdin gives the stored datum; dry-run output contains the `(code, network)` and gateway name next steps. Uses fakes (`FakeStationStore`, `FakeTenantStore`) and injected clock/id factory.
- `tests/integration/cli/test_create_station_cli.py` (testcontainer PostGIS, like `test_rating_only.py`): real insert, round-trip through `fetch_station_by_code`, second run refused, and `import_basin_package` resolve path finds the station (`fetch_station_by_code` returns it) - this is the "link to the handover" check; plus QC consequence: `obs_skipped_rules("water_level", station.water_level_datum_masl)` empty when datum was given.

### Docs to update
- `docs/operations/new-station-by-hand.md`: replace Step 3 body with the one command; keep the datum explanation in "Values you need first"; leave a pointer for the Gateway name.
- `docs/operations/onboarding-start-here.md`: reference the command at the station-creation step (not read in this pass - check).
- `docs/touchpoint-maps.md` (onboarding / stations map) and `docs/conventions.md` only if they list CLI entry points (unverified).
- Plan 540 status line for M2 when merged.

### Version bump
Code change: `uv run bump-my-version bump patch` before commit, stage `pyproject.toml` and `src/sapphire_flow/__init__.py`; no tag by hand.

### Risks
- Global `(network, code)` uniqueness means a code clash with another tenant's station blocks creation; the message must say so plainly.
- Datum entered in the wrong frame silently breaks range QC. Mitigation is the gauge-type-first question, the worked example echoed before saving, and the plausibility band (gross errors only).
- Gauge type/datum can be changed later only via `update_station`; M2 does not provide an edit command (see out of scope), so a typo needs a heredoc; changing either does not re-QC old rows (see M3a risk).
- Gateway name is not persisted, so "one name in all tools" is by convention only until M5 writes the scope/bindings; do not claim it is enforced.
- `onboard_nepal` stays CHWRR-only; two CLIs with different tenant rules must be explained in docs.

### Out of scope
Group creation/joining (M1), basin import, observation load, rating curve (M3), readiness (M4), weather bindings (M5), promotion to operational (M7), editing existing stations, bulk CSV creation, non-river station kinds, thresholds, writing the Gateway name anywhere, Gateway registration itself.

### Owner questions (plain language; recommendations apply unless said otherwise)
0. DECIDED 2026-11-17: ask the gauge type first (see above).
1. Where should the Gateway name be remembered? Today it only exists in the weather configuration file, not on the station. Recommendation: do not add a station field in this step; the command only checks the spelling and prints it into the next-step instructions, and the weather-binding step (M5) stores it.
2. If the datum is unknown, may the station still be created? Recommendation: yes, but only with an explicit "datum unknown" choice and a printed warning that water-level range checks stay off; never silently.
3. Should codes be unique across all organizations, as the database already forces? Recommendation: keep it as is (changing it is a migration and out of scope) and just report clashes clearly.
4. Should a sanity range apply to the datum (e.g. -500 to 9000 m)? Recommendation: yes, wide, to catch gauge-height-instead-of-elevation mistakes only.

### Size
Small to medium: one module (~200-250 lines incl. `list`), enum + two fields + two columns + Alembic migration 0086 + head-pin test bump, two test files, three doc edits. One PR.

### Cross-milestone dependencies
- M1 and M2 are independent (M1's network hint reuses M2's networks query if both exist); M5 consumes the gateway name printed by M2 and the station id; M3/M4 assume the station exists with the datum set here. M2 should land before M3.

## M3 - General observation load (3a, 3b, 3c, 3d: four PRs)

### Goal
Replace the step-6 heredocs in `docs/operations/new-station-by-hand.md` with one command family that, for ANY existing station: (3a) loads a CSV of observations, converts the reading to metres a.s.l. using the station's gauge type and datum, and stores it; (3b) runs the real Stage 1 QC; (3c) stores a rating curve and derives discharge; (3d) can generate clearly labelled synthetic data for sandboxes. The gauge type and datum are NOT set here (M2); M3 reads them from the station and refuses to load water level without them. Four PRs (developer review: 600-800 lines is not one reviewable PR; administrator review: 3d is COULD, only sandboxes need it).

### Verified facts (read in this checkout)
- Stored water level (m a.s.l.) = datum + gauge height (staff gauge) or sensor elevation - distance (bridge sensor, owner decision 2026-11-17); QC subtracts the datum (gauge zero) unchanged: `services/qc_datum.py::shift_observations_for_water_level_datum` needs no change. With `datum is None`, `obs_skipped_rules` returns `{range_check, gross_outlier}` and `obs_qc_rule_version` returns `1.2-datum-skip`; everything still reads `qc_passed` (fail-open).
- **The real QC entrypoint is `flows/ingest_observations.py::_evaluate_qc_window` (~line 917).** Its inputs are more than a rule list: `baseline_store`, `station_networks`, `overrides` and `target_ids`; it uses the module constant `_QC_PICKUP_STATUSES` (~711) and the PRIVATE `_aggregate_qc_status(flags, rules_ran=...)` (~310). The public `aggregate_qc_status` (`types/domain.py:104`) has no `rules_ran` argument and is not a substitute. Flow: fetch via `PgObservationStore.qc_context_reader().fetch_observations(...)`, shift for the datum, `Stage1QualityChecker.check_with_coverage(...)` (`.flags`, `.judged`), `add_observation_datum_details`, mark unjudged/zero-rule readings `qc_unchecked` via `resolve_selection` (Plans 272/317/323), then `update_qc_batch_if_current(updates, qc_rule_version=...)`.
- **Correction of an earlier claim:** `services/onboarding.py` (~l.799) does NOT use `check_with_coverage`; it calls `checker.check(...)`, which delegates to `check_with_coverage` and drops `judged` (`services/qc.py` ~399). The `qc_unchecked` marking lives only in the ingest flow. M3b follows the ingest flow, not onboarding.py and not the old heredoc (which used `.check`, fail-open).
- Rules come from `config/qc_rules.py::resolve_qc_rules()` (builtin Swiss default when `SAPPHIRE_CONFIG` is unset, ~l.401; selection depends on inferred cadence, so a sparse or irregular series can resolve zero rules). A sandbox tenant needs `SAPPHIRE_CONFIG` set to use its own rule set; the CLI documents this and prints which rule set resolved.
- Store: `PgObservationStore.store_raw_observations(list[RawObservation])` upserts on the natural key (station, timestamp, parameter, source), resets QC to RAW on value change, batches of 5000. `RawObservation` carries `rating_curve_id`.
- Rating: `PgRatingCurveStore.store_rating_curve(RatingCurve)` (plain insert, no supersede; `supersede_curve(id, valid_to)` exists; `fetch_active_curve` = `valid_to IS NULL`). `RatingConverter.from_curve(curve)` validates; `.convert(level)` clamps out-of-range and reports `RatingRange` (never extrapolates). The interpolation enum value is `log_linear` (`types/enums.py` ~488), so the CLI choice is `linear|log_linear`.
- `RatingCurve.points` are `{"water_level": m a.s.l., "discharge": m3/s}`; `rating_type_label` is a free string.
- Closest existing loader: `cli/import_dhm_delivery.py` is CHWRR/delivery-specific and tenant-locked, but is the pattern for rating-derived observations (`rating_curve_id=selected.id`).
- CLIs run as `uv run python -m sapphire_flow.cli.<name>`; `[project.scripts]` only has `check` (`pyproject.toml:60`), so no entry point is added.
- "Usable" daily values for the forecast cycle are `passed` or `unchecked` (`services/input_quality.py:38-40`); `suspect` counts as missing.
- `measured_parameters` is not read by the observation store, ingest or `input_quality` (final review), so M3 does not check it.
- **Audit types (decided):** add `AuditEventType.OBSERVATIONS_LOADED` and `RATING_CURVE_STORED` (the `audit_log.event_type` column is plain text, so no migration; update the enum docstring). Reused existing types: M1 `STATION_GROUP_CREATED`, M5-assign `MODEL_ASSIGNED`, M2 `STATION_ONBOARDED`, M7a `STATION_STATUS_CHANGE`.
- Baseline store: `PgClimBaselineStore.fetch_baselines` returns `[]` when a station has none (`store/clim_baseline_store.py:54-68`), and `services/qc.py` ~370 then runs no baseline-dependent rule (`if baseline is None`).

### Rules common to every M3 command (decided)
- **Write authority and audit (same pattern as M1/M7a):** `resolve_run_principal(tenant_store, identity_config, tenant_code=..., operator=...)`, then `enforce_tenant_isolation` against `station.tenant_id` (rejection audit row on mismatch). A successful `--apply` writes one audit row (`OBSERVATIONS_LOADED` or `RATING_CURVE_STORED`; station code, parameter, row count, first/last UTC instant, curve id where relevant, operator).
- **Dry run by default; `--apply` writes** (every writing subcommand: `water-level`, `discharge`, `rating`, `derive-discharge`). Dry run parses, converts, runs QC in memory (3b) with fresh uuids, prints everything, writes nothing.
- **Every loader prints the usable-daily-values count in the lookback** (default 30 days ending at the last loaded day, or `--as-of`; `--lookback-days` overrides; M4's `--model` supplies the real lookback): "N of 30 daily values usable (passed or unchecked)". In 3a, before QC exists, the line reads "N of 30 days have a value (QC not run yet)". This is the number that stalled the walkthrough cycles.
- Exit codes: 0 ok, 2 usage/validation, 3 refused. On exit 3 print the reason and the next step in plain words (for example zero-rule group: "too few readings per day for any quality rule to run; check the file's time step and time zone").
- Tenant/config flags follow the decided convention (`SAPPHIRE_CONFIG` env, optional `--config`).

### M3a - parse, convert, store (PR 1)
New pure service `src/sapphire_flow/services/station_observation_load.py`:
- `ObservationValueKind` enum: `GAUGE_READING` (interpreted by the station's `WaterLevelGaugeType`: staff -> `datum + value`, bridge sensor -> `sensor_masl - value`), `LEVEL_MASL` (already a.s.l.; stored as is), `DISCHARGE_M3S`. No bool flags.
- `parse_observation_csv(text, *, time_column, value_column, utc_offset: timedelta | None, timezone: ZoneInfo | None) -> list[tuple[UtcDatetime, float]]` (plain csv). Rejects missing columns, unparseable/non-finite values, duplicate timestamps, naive timestamps without an explicit zone, empty file, future timestamps. Zone default = the station's `timezone`; `--utc-offset-minutes` overrides. Per the modeller convention (daily = local/basin day, sub-daily = UTC) the tool does not guess; it prints the first/last UTC instant.
- `to_raw_observations(rows, station, parameter, kind, source=MEASURED) -> list[RawObservation]`; raises `DatumRequiredError` (subclass of `ConfigurationError`) when the kind needs datum or gauge type and either is None. No skip escape hatch.
- Output echoes one converted example (`reading 2.0 m -> stored 1482.0 m a.s.l. (staff gauge, datum 1480.0)`) in dry run and apply, plus row counts, datum, gauge type and zone used.
- CLI `src/sapphire_flow/cli/load_station_observations.py`: `water-level --csv F --time-column C --value-column C [--value-kind gauge-reading|level-masl] [--utc-offset-minutes N | --zone NAME] [--apply]` and `discharge --csv F ...` (kind DISCHARGE_M3S, source MEASURED). Common: `--station-code`, `--network`, `--tenant`.
- Warning when values look already absolute (all values > datum - 1 and datum > 100): "did you mean level-masl?"; error only with `--strict` (heuristic, judgement call).
- Tests: `tests/unit/services/test_station_observation_load.py` (parse happy/offset/zone and every parse error; staff `stored == datum + h`, sensor `stored == sensor_masl - d`; refuses without datum or type; LEVEL_MASL passthrough; full precision kept); `tests/unit/cli/test_load_station_observations.py` (arg parsing, exit codes, dry run writes nothing, `--apply` writes rows and exactly one audit row, non-writable tenant refused, lookback count line).
- Docs: runbook Step 6 (water-level part), touchpoint map, plan status.

### M3b - QC on load (PR 2; the riskiest part)
- **Decision (MVP): copy, do not refactor the live Swiss ingest path.** Add `run_stage1_qc(...)` in `services/station_observation_qc.py` mirroring `_evaluate_qc_window` (the original is about 190 lines, ~917 to ~1100, with counters, zero-rule bookkeeping, unjudged and stale handling; the copy is about 150 lines): same `check_with_coverage` call, same `resolve_selection`, same `qc_unchecked` marking, the same `rules_ran`-aware aggregation (copy the logic of the private `_aggregate_qc_status`; neither import a private function across modules nor swap in the public one), same `update_qc_batch_if_current` and `qc_rule_version`. A **parity test** pins identical statuses and version against `ingest_observations._evaluate_qc_window` on the same data; it must also pin the counts and the zero-rule output, and it is the only guard against drift.
- What it needs and where it comes from: rules from `resolve_qc_rules()` (needs `SAPPHIRE_CONFIG` for a non-Swiss rule set); the station's network (a `station_networks` mapping with the single station); baselines from the same baseline store the ingest flow uses (returns `[]` for a new station, so the baseline-dependent rule, `gross_outlier`'s baseline check, does not run; the report says so in plain words: "history-based check not run: no baseline yet"; readings the unrun rule would have judged are counted exactly as live ingest counts them, and the report never presents an unrun rule as passed); `overrides` read from the same host config (empty if none); `target_ids` = the loaded observation ids.
- Report: counts by status, rule version, zero-rule groups, usable-daily-values count in the lookback (passed + unchecked). Exit 3 when the rule version is `1.2-datum-skip` or any zero-rule group exists, unless `--allow-unchecked`; stale QC (`update_qc_batch_if_current` returns False) -> report and exit 3.
- Wired into `water-level`/`discharge`: QC runs after the store on `--apply`, in memory on a dry run.
- Tests: `tests/unit/services/test_station_observation_qc.py` (with the datum a gauge-height series passes range_check; the same series stored without shifting fails it, pinning the units trap; datum None refused before QC; a spike is `qc_failed`; sparse series -> `qc_unchecked`; rule version `1.2-datum`; the parity test); integration `tests/integration/cli/test_load_station_observations_pg.py` (real PostGIS: station with datum, load, assert `1.2-datum` and never `1.2-datum-skip`; second load changes 0 rows).
- Docs: `docs/touchpoint-maps.md` observation ingest/QC row lists the new runner as a QC caller that must stay in lockstep with `_evaluate_qc_window`.
- Risk R3: upsert resets QC only on changed values; after a datum or gauge-type change, reload the same file (values change, so QC resets).

### M3c - rating curve and derived discharge (PR 3)
- `parse_rating_csv(text, level_column, discharge_column, level_kind, datum, gauge_type)` converts to m a.s.l. exactly like the observations (curve in stored units) and validates through `RatingConverter.from_curve` (strict stage ordering, non-decreasing Q, Q>0 for log-linear, at least 2 points).
- `rating --csv F --level-column C --discharge-column C [--level-kind gauge-reading|level-masl] [--valid-from ISO] [--label TEXT] [--interpolation linear|log_linear] [--supersede] [--apply]`: an existing active curve is refused unless `--supersede` (then `supersede_curve` old at `valid_from`, new version = old + 1). Authority/audit per the common rules.
- `derive-discharge [--start --end] [--curve-id] [--apply]`: for stored `water_level` MEASURED observations in the window, `RatingConverter.convert`, write `RawObservation(parameter="discharge", source=RATING_CURVE_DERIVED, rating_curve_id=curve.id)`. Out-of-range levels are skipped and counted, not stored clamped (Q3). Then the M3b QC (no datum, `obs_qc_rule_version("discharge", None)` = `1.2`) and the usable-daily-values count for discharge.
- Prints the curve stage range against the observed level range and warns on no overlap (R6).
- Tests: rating CSV conversion for both gauge types; derived values vs `RatingConverter` (in range; clamped endpoints skipped and counted); supersede flow; integration asserts `source='rating_curve_derived'` and `rating_curve_id` set.
- Docs: runbook Step 6 (rating and derive parts).

### M3d - synthetic generator and a committed discharge fixture (PR 4; sandbox only; deferrable)
**Amended 2026-11-18 (owner decision: discharge support for the synthetic case; most models use discharge).** The generator writes a daily **`discharge.csv`** (columns `time,value`; `time` is a date `YYYY-MM-DD` meaning the station-local day, per the modeller convention that daily series are local-day; `value` in m3/s, full precision, at least 4 decimals in the file) directly, so a discharge-based forecast needs only M3a and M3b, not M3c. `water_level.csv` and `rating.csv` stay as the optional second route (they need M3c for derived discharge). The same PR commits one small, fixed, agency-neutral discharge fixture, `tests/fixtures/synthetic_station/discharge_daily.csv` (about 90 daily values) with a README (synthetic, seed and parameters, the station time zone to load it with, units, no real data), generated once by the pure function below with a recorded seed. A test loads it with the fixture station's own `timezone` (no zone flag) through the real Stage 1 QC (the M3b runner, so M3d lands after M3b) under the **Swiss built-in default rule set** (`SAPPHIRE_CONFIG` unset; it has the daily discharge rules) and requires no `suspect` or failed flags and a usable-days count of all days in the 30-day window ending at the last loaded day (`passed` or `unchecked`; with no baseline the history-based outlier check does not run, so the exact `passed`/`unchecked` split follows M3b's parity-pinned output, and the test asserts that output, not a guess); the deployment hello-world plan consumes this file through M3a/M3b and does not duplicate the generator. The fixture is regenerated only by a deliberate seed or parameter change recorded in its README.
`synthetic --out-dir D --start --end --step daily|hourly --datum-from-station --seed N --sandbox [--discharge-only]` writes `discharge.csv` (always), and `water_level.csv` and `rating.csv` unless `--discharge-only` (never touches the DB); the user loads them through the normal commands. `--sandbox` is a cheap habit guard against synthetic data entering a shared host (not hardening; it does not stop loading the CSV into a shared host, and raw synthetic water level is unmarked in the DB, only the curve carries `SYNTHETIC`, R2).
- Pure `synthetic_series(...)`: seeded injected `random.Random`, no `now()`; no flat floor (never clip the recession to a constant baseflow: the frozen-sensor rule fires on 5 consecutive daily values within 0.001) and no coarse rounding. **Must be clean under the real Stage 1 QC (owner request 2026-11-17; already recorded spec):** no noise and no bare sine (frozen-sensor rule); baseflow plus gamma-shaped storm pulses on an exponential recession (seeded storm days) so no step approaches the spike or rate-of-change limits of the rule set in use (read the limits from the resolved rule set, do not hard-code the Swiss ones); optional: drive a one-bucket linear reservoir with stored ERA5 precipitation if the station has it.
- Acceptance: run the series through the same `check_with_coverage` QC the load path uses and require zero non-`good` flags over the whole span; print the usable-daily-values count for the 30 days ending at the last generated day (or `--as-of`, as the loaders do). Synthetic rating `Q = a*(h + c)**b`, defaults `a=60, b=1.8, c=0.2`, always `rating_type_label="SYNTHETIC"`.
- Tests: determinism with a seed, no `datetime.now`, label, QC-clean acceptance for the discharge series AND the committed fixture, `--sandbox` guard, fixture file equals the generator output for its recorded seed. Plan 541 S3's interim generator covers the guide until this lands.
- Docs: `docs/touchpoint-maps.md` (fixture row), the guide's Step 6 pointer to `discharge_daily.csv` and its README.

### Error cases (each a test)
station not found; datum or gauge type missing for water level; datum present but `water_level_unit` not in `SUPPORTED_WATER_LEVEL_UNITS` (`m`, `m a.s.l.`); missing column; empty file; non-numeric/NaN/inf; duplicate timestamps; naive timestamps with no resolvable zone; future timestamps; rating: non-increasing stage, decreasing Q, <2 points, Q<=0 under log-linear, existing active curve without `--supersede`; derive: no active curve, no water-level observations in window, level outside curve (counted + skipped); QC: zero-rule groups or `datum-skip` -> exit 3; stale QC -> exit 3.

### Risks
- R1 QC drift: the copied body diverges from the ingest flow. Mitigation: parity test and the touchpoint-map lockstep note.
- R2 Synthetic observations are not marked in the DB (a new `ObservationSource` member touches enums, constraints, migrations; out of scope). Mitigation: curve label, log line, `--sandbox` guard, runbook warning.
- R3 see 3b. R4 timezone mistakes silently shift the series; mitigation: explicit zone, printed first/last UTC instant. R5 large files in one transaction: acceptable for MVP. R6 a curve built on a wrong datum looks plausible; mitigation: the stage-range overlap warning.

### Out of scope
Setting/changing the datum or gauge type (M2); station creation; readiness (M4); weather and bindings (M5); promotion (M7a); adapters/live ingest; QC rule changes; native-capture/provisional rating machinery; a `SYNTHETIC` source enum; Excel formats; multi-station batch files; production hardening.

### Owner questions (plain language; recommendations apply unless said otherwise)
Q1 No datum: refuse. Q2 The file value is the gauge reading by default (the gauge type sets the sign), with an explicit switch for absolute levels and a warning when the numbers look wrong. Q3 Discharge outside the rating table: skip and report the count. Q4 Keep full precision (no 2-decimal rounding). Q5 Synthetic only with `--sandbox`, never on a host that serves real stations.

### Size
Four PRs: 3a small-medium, 3b medium (riskiest), 3c small-medium, 3d small; deferrable unless the deployment hello-world consumes its fixture (then it follows 3b).

### Cross-milestone dependencies
- Needs M2 for the gauge type, datum and sensor elevation; 3a's integration tests may seed the station directly, so M2 and 3a can overlap.
- 3b needs 3a; 3c needs 3a and 3b; 3d needs 3b (acceptance QC).
- Feeds M4: the readiness report reads the same facts (qc_rule_version `1.2-datum`, rating present, observation range, usable days).

## M4 - Readiness report replacing `onboard_nepal qualify`

### Goal

One read-only command, `station_readiness`, that prints a checklist of facts for ANY station (and optionally against ONE model), so an administrator can hand the hydrologist the evidence instead of the Step 7 SQL in `docs/operations/new-station-by-hand.md`. It states facts and a per-line PASS / FAIL / NOT-APPLICABLE with a reason. It does not write anything and does not issue one magic "ready" (facts only; promotion blocking conditions are defined separately in M7a from the same underlying facts, so M7a does not depend on a verdict from M4). The report ends with a plain last line "N checks failed, M passed" so nobody reads only the exit status (administrator review).

### What exists today (verified by reading code)

- `qualify_discharge_targets` (`services/nepal_onboarding.py:464`) and the CLI branch (`cli/onboard_nepal.py:158-250`). Hard-wired to: the six CHWRR stations (`chwrr_stations`, "missing DHM station ..."), the delivery id (`DELIVERY_ID`, `DeliveryObservationReader`), discharge-only models, `MANUAL_IMPORT` source, one reanalysis source (`era5_land` mapped to the Gateway), Gateway polygon names from `GAUGE_POLYGONS`.
- IMPORTANT: `qualify` is NOT read-only. It calls `store.update_station(... forecast_targets = {"discharge"} or None)` per station (inside a `begin_nested`, rolled back only by `--dry-run`). The new report must not inherit that write; the sandbox Step 8 already sets `forecast_targets` explicitly.
- Reusable pieces:
  - `resolve_available_static_keys_for_stations(model, station_ids, station_store=, basin_store=)` (`services/caravan_statics.py:350`): already applies `declared_static_naming` (NATIVE vs CARAVAN) and `CARAVAN_ALIAS`/collision rules via `_resolve_declared_value`. Report = `model.data_requirements.static_features` minus the returned set, per station. Do NOT re-implement alias lookup.
  - `PgStationStore.fetch_station_by_code`, `fetch_weather_sources(station_id)` (returns `StationWeatherSource` with `role` REANALYSIS/FORECAST/OPERATIONAL and `status`), `PgStationGroupStore.fetch_groups_for_station`, `fetch_group_model_assignments(group_id)`, `PgRatingCurveStore.fetch_active_curve`, `PgObservationStore.fetch_latest_timestamp`, `PgHistoricalForcingStore.fetch_available_sources`.
  - `complete_training_rows` / `count_training_samples` / `assemble_station_training_data` for an optional "complete training windows" line when a model and window are given (the only part of `qualify` worth keeping).
- NOT reusable as is: `history_binding_issue` (requires Gateway HRU `GATEWAY_HRU` and `GAUGE_POLYGONS` names, CHWRR-only), `chwrr_stations`, `DeliveryObservationReader`, `ReadinessStatus.READY/HELD` (a single verdict; value `ready_for_model_onboarding`).
- Gaps (no store method exists; verified by grep of store method names): aggregate observation stats (count, min/max timestamp, QC share per parameter) and aggregate historical-forcing stats (count, min/max `valid_time` per parameter). Only `fetch_latest_timestamp`, `fetch_covered_days`, `fetch_latest_valid_time` exist. Needs two small aggregate read queries (see Files).

### Decision on `qualify` for the six gauges (recommend: keep untouched in M4; retire later)

`qualify` is the Plan 268/143 gate with a delivery-tenant lock, extensive integration tests (`tests/integration/services/test_nepal_onboarding_readiness.py`, `test_nepal_readiness_input_isolation.py`, `nepal_readiness_isolation_fixture.py`) and a runbook step (`docs/operations/nepal-station-onboarding-runbook.md` ~line 379). Rewiring it onto a new report risks that gate for no gain, and the October six-gauge path is held. So M4 adds the new command beside it, does not change `qualify`, and adds one line to the runbook and `new-station-by-hand.md` pointing admins to the new report for any non-CHWRR station. Retire/delegate `qualify` only after the owner decides the six-gauge path is done (open question Q3). The two outputs will differ in naming on purpose (`qualify` keeps its `ready_for_model_onboarding` word; the new report uses PASS/FAIL per fact).

### Files / functions

- New `src/sapphire_flow/types/station_readiness.py`: frozen dataclasses. `CheckStatus` enum (`PASS`, `FAIL`, `NOT_APPLICABLE`), `ReadinessCheck(name: Literal[...], status, detail: str)`, `StationReadinessReport(station_id, code, network, checks: tuple[ReadinessCheck, ...])`. No bool verdict field; a derived `failed()` helper only.
- New `src/sapphire_flow/services/station_readiness.py`: `build_station_readiness(conn, station_code, network, *, model: ForecastModel | None, window: HistoryWindow | None, now: UtcDatetime) -> StationReadinessReport`. Pure read; takes `now` injected (recency). One private function per check, each returning a `ReadinessCheck`:
  1. `station`: row exists, `station_status`, `measured_parameters`, `forecast_targets`.
  2. `datum`: PASS if `water_level_datum_masl` set; NOT_APPLICABLE if `water_level` not in `measured_parameters`.
  3. `basin`: `basin_id` set and basin row exists.
  4. `statics` (needs model): missing names via `resolve_available_static_keys_for_stations`; NOT_APPLICABLE without `--model`. Detail lists missing names (first N).
  5. `weather_bindings`: one line per role (REANALYSIS, FORECAST, OPERATIONAL) from `fetch_weather_sources`: bound/active source name, or "none".
  6. `weather_history`: per `historical_forcing` parameter count, min/max `valid_time`, and overlap with the observation range.
  7. `observations`: per parameter count, min, max, age of newest vs `now`, share per `QcStatus`, distinct `qc_rule_version`.
  8. `rating_curve`: `fetch_active_curve` present; NOT_APPLICABLE unless discharge is a target or measured from level.
  9. `discharge_series`: discharge observations exist (stored or derived; check the `source` column values in the aggregate).
  10. `group_and_model`: groups of the station, and `GroupModelAssignment` rows (with `--model`: is that model assigned to a group containing the station; status).
  10a. `discharge_lookback` (administrator review; the number that stalled the walkthrough cycles): count of local days with a `passed` or `unchecked` discharge value (`services/input_quality.py:38-40`; `suspect` counts as missing) in the lookback before `now`, printed "N of L days usable" and the missing dates. L = the model's lookback with `--model`, else 30 days (stated in the line). Shares its counting function with M3's loader line.
  10b. `naming`: cross-check of the names that must agree: station code, station name, the weather scope's `runtime_gateway_hru_name` and the `recap_gateway_polygon_bindings.gateway_hru_name` (compared; a mismatch is FAIL with both spellings), and the polygon binding present (BASIN_AVERAGE). A typo here otherwise surfaces hours later as a cycle failure.
  10c. `weather_wait_facts` (what the Plan 541 guide describes): from stored data only, the days in the lookback without stored reanalysis (`historical_forcing`) and the operational/forecast binding state per role (bound/missing/inactive); the latest stored outcome of the station's last forecast cycle if one exists (the `missing_support` reason, as stored; READ PASS: confirm where the cycle stores the outcome, the walkthrough saw a cache row `past_outcome=unavailable/missing_support`). This tells the person whether to wait for the Gateway or fix something. UNVERIFIED storage location; if it cannot be read cheaply, this line is dropped and the guide keeps its prose.
  10d. optional `--check-gateway` (read only, off by default): asks the Gateway for the station's precipitation and temperature over the lookback and lists days without data ("OK: all N days present" or the missing days; "try again after the backfill"). The key is read through M0's `RECAP_API_KEY_FILE` / `load_recap_api_key`, so the person can run it in their own terminal without handling the key by hand; the key is never printed or logged. Reuse the Gateway adapter/client the operational cycle uses (READ PASS: which call; Plan 541 S1 on the guide branch carries the current snippet to port). Not a unit-testable path against the live Gateway; tests use a fake client, and the live call is verified once by hand.
  11. optional `training_samples` (needs `--model --start --end --time-step-hours`): complete-window count via `assemble_station_training_data` + `count_training_samples`; no minimum threshold unless `--minimum-samples` given.
- Aggregate reads: add `PgObservationStore.fetch_station_summary(station_id) -> tuple[ObservationSummaryRow, ...]` and `PgHistoricalForcingStore.fetch_station_summary(station_id) -> tuple[ForcingSummaryRow, ...]` (GROUP BY parameter [, source, qc_rule_version, qc_status]). Keep them on the concrete Pg stores; add to Protocols only if the service is typed on Protocols (check `protocols/stores.py`; this is a docs/spec update, see below). Use the TRUSTED read purpose predicate (`unit_read_predicate`) so report counts match what training sees (unverified: confirm the predicate against `fetch_latest_timestamp`, which applies it).
- `list` for existing tenants and networks lives in M2 (`create_station list`); M4 does not duplicate it.
- New `src/sapphire_flow/cli/station_readiness.py`: argparse, `main() -> int`. Resolves the model through `discover_models()` as `onboard_nepal` does. Opens a connection with `create_engine_from_env()`, no transaction writes (use `conn.begin()` with rollback or a read-only connection; do not commit).

### CLI surface and output

```
uv run python -m sapphire_flow.cli.station_readiness \
  --code 00001 --network research [--config PATH] [--weather-scope S] [--model cmal_small] \
  [--start ISO --end ISO --time-step-hours 24] [--json]
```
Default output is a plain text table, one line per check: `PASS  datum  water_level_datum_masl=1250.0`, `FAIL  statics  3 of 78 missing: slope, glacier_fraction, ...`, `NOT_APPLICABLE  rating_curve  discharge not a target`. `--json` prints a STABLE shape (documented in `docs/spec/types-and-protocols.md`: `{station, checks: [{name, status, detail}], failed, passed}`; names and status values are a contract, adding fields is allowed, renaming is not). Exit code 0 always when the report ran (it states facts, it does not gate); 2 only for usage errors / unknown station. The table is the command's product output: write it with `sys.stdout.write` (not `print`) and use structlog for diagnostics; check `cli/hydrologists.py` for the convention before coding.
`SAPPHIRE_CONFIG` (optional `--config`) is read for the deployment identity and the weather scopes; `--weather-scope S` names the scope that supplies `runtime_gateway_hru_name` for the `naming` check (10b). Without `--weather-scope`, 10b compares only station code, name and the stored polygon binding and says the scope comparison was skipped.

### Tests

- **Fakes versus Pg:** `build_station_readiness(conn, ...)` takes a connection and the new aggregate reads are deliberately not on the Protocols. Decision: add `fetch_station_summary` to the Protocols and to `tests/fakes/fake_stores.py` so the service can be unit-tested with fakes; the testcontainer test remains the check of the real queries.
- `tests/unit/services/test_station_readiness.py` (fakes over the store Protocols, per AGENTS.md; add `TestDischargeLookback`, `TestNaming`, `TestWeatherWaitFacts`; `weather_bindings` status: PASS when all three roles are bound ACTIVE, FAIL naming each missing/inactive role, never NOT_APPLICABLE): `TestDatum` (set / missing / not applicable for discharge-only station), `TestStatics` (alias name resolves via `CARAVAN_ALIAS`; missing static reported by declared name; NATIVE model uses raw keys), `TestWeatherBindings` (all three roles reported, inactive binding shown as FAIL, none bound), `TestObservations` (counts, QC shares, recency using injected `now`, empty station), `TestRatingAndDischarge`, `TestGroupAndModel` (station in two groups; model not assigned), `TestNoSingleVerdict` (report contains no aggregate "ready" field).
- `tests/unit/cli/test_station_readiness.py`: argument errors (`--start` without `--end`), unknown station exit 2, `--json` round-trips, table lines for a canned report.
- `tests/integration/services/test_station_readiness.py` (testcontainer Postgres, as the other integration tests): onboard a synthetic non-CHWRR station (research network), assert the aggregates against real rows including a station with zero observations; assert the command makes NO writes (row counts and `stations.updated_at` unchanged) - this is the explicit contrast with `qualify`.
- Existing `qualify` tests must stay untouched and green (regression guard for the "keep" decision).

### Docs to update (same PR)

`docs/operations/new-station-by-hand.md` Step 7 (replace the SQL with the command; keep the SQL as a fallback only if the owner wants), `docs/operations/nepal-station-onboarding-runbook.md` (one pointer line near `qualify`), `docs/touchpoint-maps.md` (onboarding/readiness map: new files, must-not-change: `qualify` and its write behaviour), `docs/spec/types-and-protocols.md` (new readiness types, new store methods), plan 540 status line.

### Version bump

One patch bump per the AGENTS.md sequence (`uv run bump-my-version bump patch`, stage `pyproject.toml` and `src/sapphire_flow/__init__.py`). Note `tests/unit/versioning/test_runtime_version.py` references `onboard_nepal`; check it does not enumerate CLI modules (unverified).

### Risks

- "Statics resolve" hides a trap: NATIVE vs CARAVAN naming. Using `resolve_available_static_keys_for_stations` avoids it; a hand-rolled alias lookup would reintroduce D15's silent bare-name fallback.
- Counting rows through a different read purpose than training would make the report disagree with training. Use the trusted predicate.
- Large observation tables: the GROUP BY per station is index-friendly (station_id, parameter) in the normal case (unverified on the mini's data volume; MVP, accept).
- Temptation to add thresholds ("90% QC pass = ok") sneaks a policy decision in; keep facts only until Q1 is answered.

### Out of scope

Any write (no `forecast_targets`, no status change); promotion (M7); thresholds and an overall verdict (Q1); training/skill evaluation; delivery-cohort checks; changing or deleting `qualify`; dashboard UI; hardening (MVP deferred).

### Which facts need a model (`--model` dependency)
Need a model: `statics`, `training_samples`, the exact lookback length of `discharge_lookback` (30-day default without one) and `group_and_model` assignment match. Everything else works without. M7a does NOT take `--model` from the operator: it derives the model from the station's ACTIVE group assignment and computes its own blocking conditions (see M7a); the readiness command remains the human-facing report.
- M4's `datum` fact also reports the gauge type and, for a bridge sensor, the sensor elevation, and flags a sensor elevation not above the gauge zero.

### Owner questions (plain language)

1. **What does "ready" mean?** Today the report lists facts and says pass or fail for each, with no single "ready" light. Who decides what counts as enough (for example how many months of data, how much of it must pass quality checks)? This belongs to the hydrologist pathway design (program #450 job B). Recommendation: ship facts only now; add a verdict later once the hydrologist names the rules.
2. **Should the report also say whether the station is safe to switch on for forecasts?** Recommendation: no; keep that to M7 (promotion), which can show this report next to its button.
3. **What happens to the old six-gauge check?** Recommendation: leave it exactly as is for now, and retire it once the six DHM gauges are finished; the new report covers any station.
4. **Should the report fail loudly (non-zero exit) when something is missing?** Recommendation: no, exit 0 and let the admin read the list; a script can use `--json` if it wants to gate.

### Rough size

Medium-large after the additions: about 800-1000 lines including tests. FIRM split into two PRs: 4a (checks 1-10b: station, datum, basin, statics, bindings, history, observations, rating, discharge, group/model, lookback, naming) and 4b (10c `weather_wait_facts` and 10d `--check-gateway`, which have an unverified storage location and need the live Gateway). One independent reviewer.

### Cross-milestone dependencies

- M2 (datum) and M3 (observation load, rating) create the data this reports; M4 can be built against synthetic rows in parallel but its integration test is best written after M2/M3 merge to reuse their helpers.
- M5 (forecast/operational bindings): the `weather_bindings` check shows all three roles from M4 on; no code dependency, but M5 should confirm role names/source names it writes match what the report prints.
- M1 (group join): the `group_and_model` check reads what M1 writes.
- M7a reuses M4's fact-reading functions (not its report statuses; M7a defines its own blocking conditions); keep the service functions free of CLI concerns.

## M5 - Forecast and operational weather bindings at onboarding

**Two PRs (developer review: service/type changes, a CLI semantics change, two Nepal suites, a new command and a doc rewrite are too much for one): M5a = bindings + `weather-bindings` default flip; M5-assign = the `assign_group_model` command. M5a is chwrr-tenant only until M5b lands (the existing `admit_weather_bindings`, `operational_weather_stations` and `chwrr_stations` require `writable_tenants == {"chwrr"}`, `services/nepal_onboarding.py:76,130,200`, `cli/onboard_nepal.py:151`); the M1 example using `--tenant chwrr` is the workable path in between. State this in the docs.**

### Goal
After `onboard_nepal weather-bindings` the station carries all three weather bindings the cycle needs (reanalysis `era5_land`, FORECAST `ifs_ecmwf`, OPERATIONAL `recap_ecmwf_operational`), so runbook step 5d (heredoc) disappears. Add a small separate command for the group model assignment (runbook "Model and first forecast" item 2). Report the single Gateway runtime name once, in the dry-run output.

### What the code does today (verified by reading)
- `services/nepal_onboarding.py::admit_weather_bindings` locks tenant, checks the scope group matches exactly (`group.station_ids == ids`), renames the package polygon to the runtime Gateway name, stores only `history_binding()` (era5_land / REANALYSIS / BASIN_AVERAGE / ACTIVE). Final call is `operational_weather_stations(...)`, which validates only the reanalysis binding + polygon. That function is ALSO called by the live reanalysis flow `flows/ingest_recap_era5_reanalysis.py:83,194`.
- `store/station_store.py::fetch_forecast_binding` raises "N FORECAST weather-source binding(s), expected exactly 1" (line ~329); `fetch_operational_binding` raises "N OPERATIONAL bindings, expected exactly 1".
- `store_weather_source` is an upsert on (station_id, nwp_source) and REFUSES to change the role of an existing (station, source) pair (ConfigurationError). So re-running is safe; a wrong-role row is refused, not overwritten.
- Source names live in: `RecapGatewayForecastAdapter.NWP_SOURCE = "ifs_ecmwf"` (adapters/recap_gateway.py:855, filtered with role FORECAST), `OperationalWeatherConfig.source: Literal["recap_ecmwf_operational"]` (config/operational_weather.py:21; recap_operational.py:271 requires role OPERATIONAL, ACTIVE, BASIN_AVERAGE). Not hand-typed anywhere else except the runbook and `scripts/nepal_forcing_seed.sql` (a demo seed).
- All three adapters resolve the Gateway polygon through the station's single `recap_gateway_polygon_bindings` BASIN_AVERAGE row (`StoreBackedGatewayPolygonResolver`). So the "one Gateway runtime name" is already shared by construction: `weather-bindings` writes `runtime_gateway_hru_name` into that row and forecast/operational reuse it. No new hand-off code is needed (verified; the cycle itself against a live Gateway is unverified).
- `create_group_assignment` (services/model_onboarding.py:1023) has production callers (flows/onboard_model.py:584, model_onboarding.py:1818) but no CLI. It upserts a `GroupModelAssignment` ACTIVE; an existing INACTIVE assignment for the same model is returned untouched with a warning; a re-run of an ACTIVE one rewrites `created_at`.
- `chwrr_stations()` (used by `qualify`/`basins`) refuses stations that already have an ACTIVE assignment. Unrelated to `weather-bindings`, but means: assign the model AFTER `qualify`, never before, for the six gauges.

### Design
1. One place for the names. Add to `types/nepal_onboarding.py` (next to `GATEWAY_HISTORY_SOURCE`): `FORECAST_SOURCE = "ifs_ecmwf"`, `OPERATIONAL_SOURCE = "recap_ecmwf_operational"`. Do not import adapters into the service. A unit test asserts they equal `RecapGatewayForecastAdapter.NWP_SOURCE` and `OperationalWeatherConfig().source` (drift guard; this is the "read from one place" guarantee).
2. In `services/nepal_onboarding.py`: add `forecast_binding(station_id)` and `operational_binding(station_id)` beside `history_binding`; add `weather_bindings(station_id) -> tuple[StationWeatherSource, ...]` returning the three. In `admit_weather_bindings` replace the single `sources`/`era5_land` logic with: for each of the three expected bindings, find existing rows with that `nwp_source`; if present and not equal to expected -> `ConfigurationError("conflicting <role> binding")`; if absent -> `store_weather_source`. Also refuse a station that already has a FORECAST or OPERATIONAL binding under a different `nwp_source` (e.g. `icon_ch2_eps`), since the cycle needs exactly 1 (message names the source, never silently adds a second).
3. Put the three-binding assertion (all three bindings present and that `fetch_forecast_binding` / `fetch_operational_binding` succeed) in `admit_weather_bindings` itself (or a new function it calls) and LEAVE `operational_weather_stations` unchanged: extending it would make the live ERA5 reanalysis flow refuse any station lacking forecast/operational rows (the six DHM stations' state is unverified). This makes dry-run and apply report the exact condition the cycle would later fail on.
4. Scope-membership rule: unchanged code path (`group.station_ids != ids` check stays first, before any write). Do not loosen it.
5. Old scopes already admitted (the six DHM gauges) just converge on re-run: the two new rows are added, reanalysis untouched. Existing integration test asserts `sources == [history_binding]` and must be updated to the three-binding list.

### CLI surface
`uv run python -m sapphire_flow.cli.onboard_nepal weather-bindings --config C --tenant chwrr --confirm-t8 --weather-scope S --confirm-package-joins [--dry-run | --apply]`
- Make dry-run the default for this subcommand and require `--apply` to write (owner decision: every writing command). **Before the default flips, check the existing binding rows on the Nepal host** (`station_weather_sources` for the six DHM stations: `ifs_ecmwf` / `recap_ecmwf_operational` rows, their role, extraction and status) read-only, because the new admission refuses a row whose extraction or status differs from the expected one (development review: P540 itself flagged this UNVERIFIED). The Nepal-host row check is a read the owner/orchestrator must do (agents cannot reach that host): the code may merge, but the runbook text flip waits for it. Anyone running the old command by habit after the flip gets a dry run and must add `--apply`; the runbook (PR #532) text is updated in the same PR. Keep `--dry-run` accepted as a no-op alias for one release so the merged runbook (PR #532) text still works. Other subcommands (`basins`, `history`, `qualify`) keep today's opt-in `--dry-run`; M4 owns `qualify`. Implementation: in `cli/onboard_nepal.py` add `--apply` to `weather-bindings` only; compute `dry = command == "weather-bindings" and not args.apply or args.dry_run` and use it where `transaction.rollback()` is decided. Log (structlog, no free-text exception messages, as today) per station: `nepal_onboarding.weather_bindings` with `station`, `would_add=[...]`, `gateway_hru_name` (the runtime name, once), `applied`.
- No new flags for source names: they are constants, not operator input.
- Model assignment (M5-assign): new **`src/sapphire_flow/cli/assign_group_model.py`, NOT `scripts/`** (run as `python -m sapphire_flow.cli.assign_group_model`). Reason: the curated operator-script list is pinned in five places that would all have to change: `Dockerfile:177`, `.github/workflows/ci.yml:996`, `tools/private_verification_gates.sh:161`, `docs/standards/cicd.md:711`, and the exact-set test `tests/unit/deploy/test_dockerfile_operator_scripts.py:32`. Every other new command in this plan is a `cli/` module. Same shape as `create_station_group.py` (dry run by default, `--apply`, plan/apply split, required `--tenant`, `resolve_run_principal` + `enforce_tenant_isolation`, audit row on apply, exit 0 on no-op). Args: `--group-name`, `--model`, `--time-step-hours`, `--priority` (default 1). Behavior: resolve the group by (tenant, name); refuse unknown model (not in `discover_models()` and no `models` row, because the FK needs the artifact import first); if an identical ACTIVE assignment exists -> no-op without calling `create_group_assignment` (avoids rewriting `created_at`); if an INACTIVE one exists -> report and refuse rather than the silent skip; else call `create_group_assignment` with the real principal (`resolve_run_principal`) and audit store, in one transaction. Prints the exact change it would make.
- Where it belongs: separate small script, not `onboard_nepal`. Reasons: it is group+model scoped and not weather; it must run after the model artifact import; it should work for Swiss tenants too; folding it into `weather-bindings` would make a weather command fail on a missing model. Order in the runbook: group (M1) -> basin -> weather-bindings (M5) -> history -> observations (M3) -> readiness (M4) -> artifact import -> assign model (M5 script) -> promote (M7).

### Error cases
- Scope group missing / other tenant / membership differs from scope -> existing "weather scope must match exact CHWRR group membership" (unchanged, before any write).
- Existing `ifs_ecmwf` or `recap_ecmwf_operational` row with a different role/extraction/status -> "conflicting forecast/operational binding", whole batch rolled back (all stations are processed in the existing `pending` pass before any write).
- Another FORECAST or OPERATIONAL source already on the station -> refuse, name it.
- Station not ONBOARDING/OPERATIONAL, polygon missing or conflicting -> existing errors.
- Model assignment: unknown group, unknown model, group in another tenant (`enforce_tenant_isolation`), INACTIVE assignment present, fallback-model priority invariant (`ConfigurationError` from `_assert_assignment_priority_invariant`).

### Tests
- `tests/unit/types/test_nepal_onboarding_sources.py` (new, small): `FORECAST_SOURCE == RecapGatewayForecastAdapter.NWP_SOURCE`; `OPERATIONAL_SOURCE == OperationalWeatherConfig().source`; `weather_bindings()` roles are REANALYSIS/FORECAST/OPERATIONAL, all BASIN_AVERAGE ACTIVE.
- `tests/integration/services/test_nepal_onboarding_readiness.py::TestWeatherAdmission` (update + add): success/rollback/partial modes now expect three bindings; add `test_second_forecast_source_is_refused` (pre-seeded `icon_ch2_eps` FORECAST -> ConfigurationError, nothing written); `test_conflicting_operational_role_is_refused` (`recap_ecmwf_operational` stored as FORECAST -> refused); `test_after_admission_cycle_lookups_succeed` (`fetch_forecast_binding` and `fetch_operational_binding` return the expected rows); `test_scope_missing_a_group_member_is_refused_before_writes` (keeps the exact-membership rule) ; repeat run changes nothing.
- `tests/unit/cli/test_onboard_nepal.py`: weather-bindings without `--apply` rolls back (fake connection records rollback); with `--apply` commits; `--dry-run` still accepted.
- `tests/unit/cli/test_assign_group_model.py` (new, fakes for group store/audit): plan reports the change; dry run writes nothing; `--apply` writes assignment + audit row; identical ACTIVE re-run is a no-op and leaves `created_at`; INACTIVE refused; unknown group/model refused; wrong tenant refused.
- Existing mutation guard: grep that the new constants appear in tests (rule: new identifier with 0 test hits = untested).

### Docs to update
- `docs/operations/new-station-by-hand.md`: delete the 5d heredoc, replace with the `weather-bindings --apply` call and one sentence that it creates all three bindings; change the 5b dry-run example to the new default; replace Model-and-first-forecast item 2 with the script command; drop the "Step 5d not done" row's fix (point to `weather-bindings`); keep the error-table row for the symptom.
- `docs/touchpoint-maps.md` (onboarding / weather map) and `docs/plans/540-...md` status line; `docs/standards/logging.md` only if a new event name is added (`nepal_onboarding.weather_bindings`).
- `scripts/nepal_forcing_seed.sql` untouched (demo seed).

### Version bump
One patch bump (`uv run bump-my-version bump patch`) in the PR, version files staged with the code. M5 is two PRs (M5a and M5-assign), each with its own patch bump.

### Other findings folded (developer/administrator reviews)
- The three-binding admission changes `TestWeatherAdmission` (`tests/integration/services/test_nepal_onboarding_readiness.py` ~546, 621, 650); run both Nepal suites and the CI shards, not only touched tests.
- Model assignment does not hard-code a forecast target: promotion's `--target` stays in M7a. The default priority 1 is unverified against any staging group already holding several models; `--priority` exists.
- The Gateway name is checked again in M4's `naming` line (weather scope vs registered name), not only printed here.
- Gateway-side registration/subscribe/reconcile stay hand commands outside this plan (Plan 541 and M8 cover the prose and messages); what evidence to write for `reconcile` when only HTTP success exists is a Plan 541 item, not owned here.

### Risks
- The existing admission test and any deployment that relied on "weather-bindings creates only reanalysis" change behavior; run the two Nepal suites plus unit CLI shards, not only touched tests.
- Making dry-run default changes operator habit on a command that today writes by default. Mitigated by keeping `--dry-run` accepted and by the doc update; the only existing caller is the runbook.
- Staging DB consistency: names and roles match the runbook's list and the seed SQL (ifs_ecmwf/forecast/basin_average/active). Whether the six staging DHM stations already carry these rows is UNVERIFIED (no DB access); re-run is idempotent either way, but a staging row with a different extraction/status would now be refused instead of left alone.
- `create_group_assignment` upsert/INACTIVE behavior is why the script pre-checks instead of calling blindly.
- Priority `1` default for the assignment follows the runbook example; unverified against any staging group that already has several models.

### Out of scope
Generalizing the CHWRR-only identity check or `--tenant choices=["chwrr"]` in `onboard_nepal` (that is M5b); creating the group (M1); promotion (M7); artifact import; weather polygons for non-package stations; Gateway-side HRU registration; any hardening beyond the refusals above.

### Owner questions (1 and 2 DECIDED 2026-11-17)
1. DECIDED: `--apply` is required to write for `weather-bindings` and the assignment command; the other three subcommands are left alone.
2. DECIDED: the model assignment is its own command (`cli/assign_group_model.py`), not part of the weather command.
3. If a station already has a different forecast weather source (for example the Swiss `icon_ch2_eps`), should onboarding refuse or replace it? Recommendation: refuse and tell the administrator; replacing could silently change a live station.
4. Default assignment priority 1 and one model per group at onboarding: acceptable? Recommendation: yes, with `--priority` available.

### Rough size
Two PRs: M5a (about 80 lines service/type changes, 20 in the CLI, tests, doc rewrite) and M5-assign (about 120 lines, tests). Each bumps the patch version.

## M5b - Generalise `onboard_nepal weather-bindings` away from CHWRR (after M5a)

Owner decision 2026-11-17: a separate milestone after M5. Without it, general (non-CHWRR) onboarding cannot use the weather-binding step; M2-M4 and M5-assign are tenant-general, M5a is not (administrator and developer reviews, MUST).

### What is CHWRR-locked today (verified in `sapphire-plan-540`, 98ae8af86)
- `services/nepal_onboarding.py`: three identical locks, each `if identity.global_admin or identity.writable_tenants != frozenset({"chwrr"})` followed by `resolve_run_principal(tenants, identity, tenant_code="chwrr")`: in `chwrr_stations` (def line 73; lock 76/79), `operational_weather_stations` (def 123; lock 130/135) and `admit_weather_bindings` (def 193; lock 200/205).
- `cli/onboard_nepal.py:55` `--tenant` with `choices=["chwrr"]` on all four subcommands, and `cli/onboard_nepal.py:151` the CHWRR-only identity check before any subcommand runs ("Nepal onboarding requires CHWRR-only write authority").
- Six-gauge-specific code that stays six-gauge: `GAUGE_POLYGONS` / `GATEWAY_HRU` (`types/nepal_onboarding.py`), `history_binding_issue`, `import_nepal_package` / `basins`, `require_history_bindings`, `qualify_discharge_targets` (calls `chwrr_stations`, lines 348, 393, 477). M5b does NOT generalise these.

### Scope (small)
1. In `admit_weather_bindings` and `operational_weather_stations` replace the hard-coded `"chwrr"` with a `tenant_code` parameter (typed; passed by the CLI). **`operational_weather_stations` is also called by the live flow `flows/ingest_recap_era5_reanalysis.py` at lines 83 and 194; M5b updates both call sites (they pass `chwrr`, the Nepal host's tenant) and tests them, so the signature change cannot break the reanalysis ingest.** and drop their private CHWRR-only identity check in favour of `resolve_run_principal(..., tenant_code=tenant_code)` + the existing tenant isolation behaviour (the resolver already validates against `writable_tenants` and bypasses it for `global_admin`). Their station set already comes from the weather scope group (`group.station_ids == ids`), not from the six-gauge list; Verified by the final review: neither function reaches `chwrr_stations` / `GAUGE_POLYGONS` (only `chwrr_stations` uses them); their error strings still say CHWRR and are updated.
2. `cli/onboard_nepal.py`: `--tenant` for `weather-bindings` becomes a free tenant code (no `choices`); the other three subcommands keep `choices=["chwrr"]`. The global CHWRR-only identity check at line 151 stays for those three; for `weather-bindings` it is replaced by the resolver above.
3. Keep the CHWRR strictness for the CHWRR tenant: when `--tenant chwrr`, the old rule (`writable_tenants == {"chwrr"}`, not `global_admin`) still applies, so the October six-gauge contract does not loosen. Owner decision (OD5): yes.
4. **`--confirm-t8`** (Plan 268 staging acceptance gate) and **`--confirm-package-joins`** stay required for `weather-bindings`: `--confirm-t8` is a chwrr/Nepal-host acceptance flag, so for a non-chwrr tenant it is accepted but not meaningful; M5b keeps it required for chwrr and makes it optional for other tenants (document this in the runbook). A general station also needs its basin package imported first: `admit_weather_bindings` requires the imported package polygon row (`package_id`, `imported_at`, `name == polygon_name`), so the order is group, basin import, then weather-bindings.
5. Remove the "chwrr-only until M5b" note from the runbook and docs; Nepal runbook text keeps `--tenant chwrr`.

### Tests and Nepal-suite impact
- `tests/integration/services/test_nepal_onboarding_readiness.py` (`TestWeatherAdmission` and the isolation fixtures in `nepal_readiness_isolation_fixture.py`) must stay green unchanged for chwrr; add: a non-chwrr tenant scope is admitted (all three bindings), a host whose `writable_tenants` does not include the tenant is refused, a `global_admin` host naming the tenant is allowed for non-chwrr but refused for chwrr (if OD5 confirmed), and a scope that does not match the group is refused before writes.
- `tests/unit/cli/test_onboard_nepal.py`: `--tenant sandbox` accepted for `weather-bindings` only; `basins`/`history`/`qualify` still reject it.
- Run the two Nepal suites and the CI shards (local venv lacks the aquacast extra).

### Out of scope
`basins`, `history`, `qualify` generalisation (M4 covers readiness for any station); any change to the six-gauge constants.

### Size / order
Small-medium, one PR, patch bump; lands after M5a (same functions) and before the runbook claims a general weather step.

## M6 - Registrar code-path option and the overlay-replacement trap

### Key finding (changes the milestone)
Part (b) rests on a wrong premise. `src/sapphire_flow/config/_overlay.py::_deep_merge` (verified) already merges nested tables key by key; only scalars, lists and arrays of tables are replaced (pinned by `tests/unit/config/test_overlay.py::test_nested_dict_deep_merge_preserves_base_keys`, `test_overlay_list_replaces_base_list`, `test_overlay_array_of_tables_replaces_wholesale`). An overlay's `[adapters.recap_gateway]` does NOT erase a base table. The real cause of the rehearsal error: the Swiss base `config.toml` has no `[adapters.recap_gateway]` at all (verified: only `config/overlays/nepal-history.toml` carries the full six-key table; `nepal-daily-weather.toml` carries just `max_cycle_age_hours`). Layering `nepal-daily-weather` without `nepal-history` leaves a one-key table, so `load_recap_gateway_config` (`config/recap_gateway.py`) raises "missing required field(s)". The runbook sentence "an overlay replaces this table whole: repeat all keys" (`docs/operations/new-station-by-hand.md` ~line 352, and the table row ~390) is therefore inaccurate. (`nepal-history.toml` needs env `SAPPHIRE_RECAP_BASE_URL`; the sandbox may not have it set, which is likely why the rehearsal repeated all keys inline. Unverified.)

### (b) Decision: documentation only, no merge change; VERIFY-ONLY once the guide branch merges
The guide branch `docs/452-guide-gaps-walkthrough` already corrects the overlay wording (developer review: runbook text at :587 and :625 on that branch; Plan 541 S6 says so). So (b) becomes: after the guide branch merges, check the overlay text in `new-station-by-hand.md` and the troubleshooting row match the verified facts below; edit only if they do not. Facts:
- Deep merge fix: nothing to fix. Changing `_deep_merge` would alter every host's effective config (Swiss mac-mini, Nepal host) for no gain. Not recommended.
- Load-time warning/error: not needed; the existing error already names the missing keys. Optional tiny improvement: append "(the base config has no Gateway table; layer config/overlays/nepal-history.toml or supply all keys)" to the `ConfigurationError` in `load_recap_gateway_config`. Message-only, no behaviour change on live hosts; skip unless owner wants it (tests would need a matching `match=` check in the recap_gateway config tests; not located).
- Docs: correct the runbook: the overlay is merged key by key; the trap is "base has no Gateway table, so some overlay must supply all six keys". Show the sandbox overlay either adding `nepal-history.toml` to the overlay list (with `SAPPHIRE_RECAP_BASE_URL` set) or repeating the keys. Fix the troubleshooting row. State the true replace-wholesale cases (lists, arrays of tables, e.g. `deployment.writable_tenants`) once.

### (a) Registrar option
File: `src/sapphire_flow/cli/register_deployments.py` (verified: `FLOW_SOURCE_ROOT = "/app"` used only in `_register_one`; `main()` calls `register_all()`; `_build_specs()` already filters by env `SAPPHIRE_SKIP_DEPLOYMENTS` and rejects unknown names).

Minimal design (keeps deployed behaviour byte-identical when nothing is set):
1. Env `SAPPHIRE_FLOW_SOURCE_ROOT` (default `/app`), read via a small `_flow_source_root()` called in `_register_one` (replaces direct use of the constant; keep the module constant as the default so existing tests/monkeypatching still work). Env rather than flag matches the file's existing style (skip list, schedules).
2. Env `SAPPHIRE_ONLY_DEPLOYMENTS` (comma-separated, symmetric to `_SKIP_ENV_VAR`): `_build_specs()` keeps only those names; unknown name raises `ValueError` like the skip list; if set together with the skip list, REJECT the combination with a clear error (decided; the earlier "skip wins" wording in Risks is dropped).
3. Alternative for operators: CLI flags `--source-root` / `--only` parsed in `main()` that set the same values. Recommend env only (MVP; compose `init` uses env already).
4. `register_all()` already creates only the pools of the selected specs, so the subset needs no further change.

Tests (`tests/unit/cli/test_register_deployments.py`, extend `TestSkippedDeployments`-style class `TestOnlyDeployments` and `TestRegisterOne`):
- unset `SAPPHIRE_ONLY_DEPLOYMENTS` returns every spec (regression for deployed behaviour).
- set to two names returns exactly those two.
- unknown name raises `ValueError` matching the env var name.
- both ONLY and SKIP set raises.
- `_register_one` passes `source=` default `/app` when env unset, and the env value when set (existing test patches `afrom_source`; assert its kwargs).
- `register_all` creates work pools only for selected specs (follow `test_creates_work_pool_and_registers_all`).

### Docs
- `docs/operations/new-station-by-hand.md`: replace the Part 0 heredoc with `SAPPHIRE_FLOW_SOURCE_ROOT=$PWD SAPPHIRE_ONLY_DEPLOYMENTS=ingest-operational-era5-history,import-model-artifact uv run python -m sapphire_flow.cli.register_deployments`; fix the overlay text and troubleshooting row as above.
- `docs/touchpoint-maps.md` (registrar entry, grep `register_deployments`) and `docs/standards/orchestration.md`/`cicd.md` registrar section: document the two env vars (not located exactly; check).
- Check `docs/plans/540...` M5/M3 text that cites the heredoc.

### Version bump
Code PR: `uv run bump-my-version bump patch`, stage `pyproject.toml` and `src/sapphire_flow/__init__.py`. A docs-only PR for (b) does not touch code, so no bump if it ships alone.

### Risks
- (a): low. Defaults unchanged; the only live-host exposure is `init` in compose, which sets neither var. Risk is an operator leaving `ONLY` set in a long-lived env and silently registering a subset on a real host: mitigate by logging `deployments.subset` with the names at info level and keeping the unknown-name error.
- (b): docs-only has no runtime risk. Any merge change WOULD affect deployed Swiss and Nepal hosts (all overlay-loading code paths: deployment config, onboarding, forecast QC, recap gateway); that is why it is not recommended.
- Do not touch `forecast_job_variables`/overlay env handling.

### Out of scope
Changing `_deep_merge` semantics; list merging; registering deployments on a real host from a checkout; making `/app` source root auto-detected; a general sandbox bootstrap script.

### PR split
Two PRs are cleaner but (b) is small: PR 1 = (a) code + tests + runbook heredoc replacement; (b) docs correction can ride in the same PR since it touches the same runbook (owner may prefer separate; either is fine). No code in (b).

### Open owner questions (plain language)
1. The runbook says an overlay wipes the whole Gateway settings block. In fact it does not; the base config just has no such block, so one overlay must supply all six values. Is a docs correction enough? Recommendation: yes; do not change how configs merge, since that would affect the live Swiss and Nepal hosts.
2. Should the "register only these deployments" and "use this code folder" settings be environment variables (matches existing style) or command-line options? Recommendation: environment variables only.
3. Want the missing-fields error to also hint at the fix? Recommendation: skip for now (MVP).

### Size
Small: about 40 lines of code, about 6 tests, runbook edits. Under half a day.

### Cross-milestone dependency
Runbook edits overlap with M5 (forecast/weather bindings, overlay section of `new-station-by-hand.md`) and any milestone that rewrites Part 0 of the runbook; sequence docs edits or have M5 reference the corrected overlay wording. No code dependency.

## M7a - Step 8 promotion command (the dashboard button, M7b, is a later separate plan)

### Goal

Replace the Step 8 heredoc in `docs/operations/new-station-by-hand.md` with a command that
switches a station to `operational` only when a defined list of blocking conditions holds (below), and writes an
audit row. Dry run by default, `--apply` writes. The admin-dashboard form is a later, separate plan (owner decision).

### What exists (verified by reading code on origin/main)

- Step 8 = `PgStationStore.update_station(replace(st, forecast_targets=frozenset({"discharge"}), updated_at=...))`
  then `update_station_status(station_id, StationStatus.OPERATIONAL)`
  (`src/sapphire_flow/store/station_store.py:156` and `:355`). `update_station_status` is a bare
  UPDATE: no readiness check, no audit, no tenant check.
- `AuditEventType.STATION_STATUS_CHANGE` already exists (`types/enums.py:420`), unused by promotion.
  `AuditEntry.system(...)` + `PgAuditLogStore(conn).append_entry(...)` is the audit pattern; operator
  handle goes in `detail` (see `cli/hydrologists.py:25-40`). `audit_log.event_type` is plain text, so
  no migration.
- Tenant write authority for CLIs: `[deployment]` `writable_tenants` / `global_admin`
  (`config/deployment_identity.py`), `services/write_principal.py::resolve_run_principal` and
  `enforce_tenant_isolation` (writes a rejection audit row, raises `TenantIsolationError`).
  `StationConfig.tenant_id` exists (`station_store.py:407`).
- CHWRR guard precedent: `cli/import_dhm_delivery.py::_assert_chwrr_scoped`,
  `cli/onboard_nepal.py:151`, `services/nepal_onboarding.py:76` refuse unless the host is
  CHWRR-scoped (they require writable_tenants == {chwrr}, not global_admin).
- Collection and forecast cycle select on `station_status == operational`
  (`flows/ingest_observations.py:1117`, `:1997`).

### Dashboard: what is really there (this drives the scope call)

- The browser dashboard is the legacy Jinja app (`src/sapphire_flow/api/templates/stations/detail.html`,
  routes `api/routes/stations.py`), mounted with `Depends(require_admin)` (`api/__init__.py:104-109`).
  `require_admin` needs an `Authorization: Bearer` access token (`api/security.py`). A browser page
  cannot hold that without shipping an admin token to the browser.
- `docs/standards/security.md` (lines ~82-92, 154): "Access tokens are strictly GET-only - no bearer
  key of any role may POST/PATCH/DELETE"; admin role = read + CLI management; human sessions, MFA and
  dashboard user management are "v1.x target, not built". Also: a dashboard token is held server-side
  by that dashboard, never in a browser.
- The only state-changing browser-style route is the reviewer publish/withdraw
  (`api/routes/forecast_publication.py:189,219`) using `require_human_principal`
  (`api/human_auth.py`, OIDC + MFA). Its `HumanPermission` has only `REVIEW` and `PUBLISH`
  (`types/human_auth.py:7`), grants are per-station, no admin/promote permission.
- Per the security doc, writes are authorised by a config `WritePrincipal` "never a read-only access
  token" (`types/write_principal.py`). An admin POST route would therefore need: (a) a new
  authorization concept (a new HumanPermission such as PROMOTE, or relaxing the GET-only rule), (b) a
  tenant check against the station's tenant using the human grant / host `writable_tenants`,
  (c) CSRF/Origin handling (CORS today is GET-only except the publication origin; see
  `api/__init__.py:52`), (d) a UI. That is a security-model change, not a button.

### Sub-steps

**M7a - CLI `promote-station` (this plan, one PR).**

1. New service `src/sapphire_flow/services/station_promotion.py`:
   `promote_station(conn, *, station_code, network/tenant_code, operator, identity, clock, facts) -> PromotionResult`.
   Steps in one transaction:
   - resolve the station (`fetch_station_by_code(code, network)`); not found -> error.
   - tenant gate: `resolve_run_principal(..., tenant_code=...)` then `enforce_tenant_isolation` against
     `station.tenant_id` (reuse, no new mechanism).
   - idempotency: already `OPERATIONAL` with targets set -> return `already_operational`, no write, no
     audit duplicate. `SUSPENDED`/`DECOMMISSIONED` -> refuse (promotion only from `ONBOARDING`; revival
     is a separate decision, out of scope).
   - blocking conditions: computed here from facts (list below), not from a verdict in M4. Any blocking condition -> refuse with every failing condition listed (all, not just the first); no override flag in v0.
   - bridge-sensor stations (narrow review S4): promotion prints a warning, not a block: "live raw-distance feed for a bridge-sensor station is not supported by this plan; confirm the feed delivers converted values (m a.s.l.)".
   - write: `update_station(replace(st, forecast_targets=frozenset({"discharge"}), updated_at=clock()))`
     then `update_station_status(...OPERATIONAL)`, then `AuditEntry.system(STATION_STATUS_CHANGE,
     target_type="station", target_id=str(id), detail={"code", "from": "onboarding", "to": "operational",
     "operator", "conditions": <the seven blocking conditions, each pass/fail>}, ip_address=None)`.
   - `forecast_targets` is the `--target` value (default `discharge`, the value the by-hand step sets), not hard-coded.
   - write authority: `resolve_run_principal` + `enforce_tenant_isolation` as above; the audit row is also the apply record.
2. New CLI `src/sapphire_flow/cli/promote_station.py` run as `python -m sapphire_flow.cli.promote_station`. Typed args: `--code`, `--network`, `--tenant` (required), `--operator` (required), `--target` (default `discharge`), `--weather-scope S` (needed by condition 5 to compare the polygon name with the scope's Gateway name), `--apply`; config via `SAPPHIRE_CONFIG` (optional `--config PATH`), which also supplies `promotion_allowed` and the deployment identity. **No `--dry-run` and no `--confirm-shared-host` flag**: without `--apply` it prints the conditions and what would change and writes nothing. **No `pyproject.toml` entry point:** the repo convention is `python -m sapphire_flow.cli.<name>`; `[project.scripts]` only has `check` (`pyproject.toml:60`) and M2, M3, M4 all use `python -m`.
3. **ONE safeguard (owner decision): host setting `promotion_allowed`, off by default.** New field on `DeploymentIdentityConfig` (`config/deployment_identity.py`, class ~l.22, loader and its tests in the same PR): `[deployment] promotion_allowed = false` by default; `--apply` on a host where it is not true refuses with "promotion on a shared host is the owner's decision" (a dry run is always allowed, so the person can see what would happen). The owner turns it on for one run by editing the host config; the sandbox overlay turns it on. No agent flag bypasses it; the CLI never decides this for the owner.
   **Blocking conditions (explicit list, defined here; all must hold; computed by small fact readers shared with M4's service, but not read from M4's report):**
   1. station status is `ONBOARDING` (already `OPERATIONAL` is a no-op; `SUSPENDED`/`DECOMMISSIONED` refuse).
   2. if `water_level` is measured: datum and gauge type are set (M2).
   3. `basin_id` is set and the basin row exists.
   4. the station is in a group with an ACTIVE model assignment. **The model is derived from that assignment** (`fetch_groups_for_station` + `fetch_group_model_assignments`); the operator never passes `--model`; none found -> blocked.
   5. all three weather bindings (reanalysis, forecast, operational) are bound and ACTIVE, and the polygon binding exists with a name equal to the weather scope's Gateway name (M4 `naming`).
   6. the model's static features all resolve for the station (`resolve_available_static_keys_for_stations`).
   7. the discharge series has every day of the model's lookback usable (`passed` or `unchecked`, `suspect` counts as missing) before the latest day; an active rating curve exists when discharge is derived from level. The strict "every day" rule follows the cycle's `missing_support` condition (window shorter than lookback); the hydrologist may relax it later (M4 Q1).
   `--check-gateway` style live checks are NOT blocking (network dependency).
4. Docs: Step 8 in `docs/operations/new-station-by-hand.md` -> the command; keep the shared-host warning.

**Owner decision 2026-11-19:** for the MVP, promotion approval is the host switch `promotion_allowed` only (owner turns it on for one run and back off); no per-person admin approval. A logged-in admin approval (new permission, CSRF, audit row naming the person) is deferred until after the MVP and belongs in the M7b plan.

**M7b - Dashboard action: separate plan (recommended, see Q1).**
Only outline here: `POST /admin/stations/{id}/promote` (or on the review API), calls the same
`promote_station` service (so CLI and UI cannot diverge), new permission/role decision, CSRF and
Origin check, `Cache-Control: no-store`, idempotency key or state-based idempotency (second call
returns the existing state), audit row with the human actor (`AuditEntry.user`/`api_key` with
`actor_id`, not `system`), confirmation dialog stating "starts live collection and enters the forecast
cycle; demoting later can lose observations", button hidden/disabled until readiness passes, and
refused on shared hosts unless the owner's identity is the actor. Frontend location: the Jinja
`stations/detail.html` (admin-gated, bearer-token only today) or the external review dashboards
(separate repos/deployments - UNVERIFIED where they live). Neither can host a POST today without
the security-model change above.

### Files (M7a)

- new `src/sapphire_flow/services/station_promotion.py`
- new `src/sapphire_flow/cli/promote_station.py`
- edit `src/sapphire_flow/config/deployment_identity.py` (`promotion_allowed` field + loader) and its tests (required, not optional)
- reads (not changes) the fact readers of M4's `services/station_readiness.py`; no `pyproject.toml` change
- no change to `PgStationStore`, no migration, no new enum member.

### Tests

- `tests/unit/services/test_station_promotion.py` (fake stores + fake audit store, injected fact readers):
  - promotes an ONBOARDING station when all conditions hold; asserts status, `forecast_targets`, one audit row
    with from/to/operator.
  - one parametrised refusal test per blocking condition (1 to 7); asserts no status change, no audit row, every failing condition named.
  - second call is a no-op (`already_operational`), still exactly one audit row.
  - refuses SUSPENDED / DECOMMISSIONED.
  - cross-tenant: principal for tenant A, station in tenant B -> `TenantIsolationError`, rejection audit
    row, station unchanged.
  - unknown station code -> clear error.
- `tests/unit/cli/test_promote_station.py`: `--operator` required; no `--apply` writes nothing; `--apply` refused without `promotion_allowed`, allowed with it; exit codes. `tests/unit/config/test_deployment_identity.py`: `promotion_allowed` defaults false and parses.
- `tests/integration/...` (testcontainer Postgres, project rule: fakes are more permissive): one
  end-to-end test that promoting writes both the status and the audit row in ONE transaction (audit insert
  failure rolls the status back) and that the station then appears in the operational-station query.

### Docs to update

`docs/operations/new-station-by-hand.md` (Step 8), `docs/touchpoint-maps.md` (onboarding map: new
promotion path), `docs/spec/types-and-protocols.md` only if a new type/Protocol is added (none planned),
`docs/standards/security.md` only for M7b. Plan 540 status/milestone list.

### Version bump

One patch bump (`uv run bump-my-version bump patch`) in the M7a PR; the dashboard plan bumps on its own.

### Risks

- The refusal is only as good as the condition list above; it does not depend on an M4 verdict, but reuses M4's fact readers, so M4 should land first.
- Setting OPERATIONAL starts live collection immediately (`ingest_observations.py:1117`); no dry staging
  period. Mitigation: dry run by default and the `promotion_allowed` host setting.
- Audit and state in separate transactions would leave an unaudited promotion: use one transaction
  (connection `.begin()`), as the onboarding service does for STATION_ONBOARDED.
- Demotion not provided; do not add it (can lose observations; owner decision).
- Dashboard-first would require shipping a write-capable token to browsers, contradicting
  `security.md`.

### Out of scope

Demotion/suspend/decommission, bulk promotion, readiness override flag, any dashboard POST route, new
roles/permissions, session/MFA work, changing `PgStationStore.update_station_status` semantics, promoting
the six CHWRR gauges on the shared host (owner action).

### Open owner questions (plain language)

1. **Is the dashboard button part of this plan?** It cannot be a small add-on: the dashboard cannot
   change anything today, by a written security rule, and a button needs a new permission and a safe
   login for people. Recommendation: no. Finish the command in this plan; open a separate plan for the
   button when you decide how administrators will sign in to the dashboard.
2. DECIDED: a host setting `promotion_allowed`, off by default; the only safeguard.
3. **Should the command refuse to promote when a blocking condition fails, with no override?**
   Recommendation: yes, no override for now; you can still promote by hand if you truly must.
4. **Who may promote on the shared host?** Recommendation: only you, by editing the host setting from Q2
   for that one run.

### Rough size

M7a: about 200-250 lines of service, CLI and config, about 300 lines of tests, small doc edits; one PR, roughly
half a day plus review. M7b (separate plan): several days, mostly the authorization and CSRF design.

## M8 - Gateway `prepare` usability (added 2026-11-17; kept small, needs a read pass)

Found by the fresh-admin walkthrough. Administrator review: the first and most opaque block ("Stuck 1"), so ship it early and independently. The Gateway-preparation code is on main (#469, `75c708a66`), but confirm no unmerged Gateway-preparation work (worktree `effort-452-gateway-preparation`) touches the same files before editing.

**Verified in code:**
- The only message is `cli/prepare_gateway.py` ~l.285-290: the `except (ValueError, OSError, ConfigurationError, ValidationError)` block writes "Gateway preparation held or failed. Inspect local state and unresolved scope; no automatic retry." and nothing else. Deliberate: no raw third-party text is printed.
- The evidence-folder check is `services/gateway_preparation.py:263-264`: `directory.mkdir(mode=0o700, ...)` then refuse when `directory.stat().st_mode & 0o077`. A pre-existing folder with mode 755 is refused through that path, then lands in the generic message. (The mkdir mode only applies to a NEW folder.)
- `_show` (`cli/prepare_gateway.py:148-168`) prints the whole report with `json.dumps(report, indent=2)`; exit 2 when any station is `held`.
- `failure_reason` exists, but per subscription change inside the report rows (`services/gateway_preparation.py:1363`, with `failure_history` below), not at top level.

**Changes (small):**
1. Name the reason. Introduce a small typed reason (enum, e.g. `PrepareFailureReason`: `EVIDENCE_DIR_NOT_PRIVATE`, `SCOPE_INCOMPLETE`, `OTHER`) raised from the two known places (the 0o077 check; scope validation), and print one fixed sentence per reason on stderr (for the folder: "evidence folder must be private: run `chmod 700 <folder>`"). No raw exception text, key redaction unchanged. The existing exit status stays.
2. Short default status: print a summary by default (registration status, revision, per-station outcome `held`/ready, hold reasons, the next command to run); the current full JSON stays behind a flag such as `--full`. The "3,400 lines" and "model_discovered log flood" claims from the walkthrough are NOT located in code (the `model_discovered` log is `services/model_registry.py:201`); NEEDS A READ PASS to reproduce the size before choosing what the summary omits. The log flood is out of scope for this milestone unless the read pass shows it comes from `prepare_gateway`.
3. `failure_reason`: align the runbook with the output (a search of `docs/` on main finds no "failure_reason" mention; the walkthrough claim is on the guide branch or the Gateway runbook, NEEDS A READ PASS), by editing the doc to say where the field appears (inside each subscription-change row).
4. Example scope file for a new station: only if the guide branch's Trishuli scope example is lifted into a tested fixture used by a test that loads it through the scope validator (`tests/unit/...`); otherwise leave it to the guide. NEEDS A READ PASS of the scope schema.

**Tests:** one unit test per reason (mode-755 existing folder, incomplete scope) asserting the stderr sentence and exit status and that no key or URL text appears; one test for the short summary shape and that `--full` equals today's JSON.
**Not done here:** Gateway-data coverage check (that is M4 `--check-gateway`); registration/subscribe/reconcile commands; what evidence to write for `reconcile` (Plan 541).
Size: small; its own PR; independent of the other milestones.

## Cross-milestone notes

- **Recommended implementation order** (administrator review order, checked against the dependencies; the developer review's start-first set M0, M1, M6(a) is consistent with it):
  1. Merge the guide branch `docs/452-guide-gaps-walkthrough`, then Plan 541 (docs only, no code). Every runbook edit below is made against the guide branch's text, which differs from `main` (the walkthrough rewrite: `main` has 391 lines; the branch worktree `sapphire-guide-gaps` holds a much larger UNCOMMITTED rewrite, about 910 lines by the final review, so the edits exist only in that working tree until someone commits them; steps 3, 5a, 5d, 6, 7, 8 and the overlay text). If this plan's doc edits land first they conflict; whichever lands second rebases, and each PR edits only its own steps.
  2. M8 and M0 (small, independent; M0 is needed by M4's `--check-gateway`), M1, M6(a) (independent, small).
  3. M2, with M3a alongside (3a can seed the station directly in tests); then 3b, 3c.
  4. M5a, then M5b, then M5-assign. M5a and M5b are only needed by non-chwrr tenants after M5b; before it, general onboarding works for chwrr-tenant stations only.
  5. M4 (reuses M3's lookback count and M5a's binding names), then M7a (reuses M4's fact readers; depends on M2 for the gauge type and datum).
  6. M3d (after M3b; before the hello-world uses its fixture), M6(b) (verify-only) any time after their prerequisites; M7b (dashboard button) is NOT in this plan.
  Why: items 1-3 replace every heredoc except weather bindings and promotion and are the usable core for a first new organization; M4 before M7a; M5b right after M5a.
- **Shared conventions (decided):** every command that writes is dry-run by default and needs `--apply` (M1, M2, M3 all subcommands, M5a `weather-bindings`, M5-assign, M7a), the same word everywhere; read-only commands (M4, `create_station list`) have no `--apply`. Write authority: `resolve_run_principal(..., tenant_code=..., operator=...)` plus `enforce_tenant_isolation` against the station's tenant, and an audit row on `--apply` (M1, M2, M3, M5-assign, M7a). One config convention (decided): env var `SAPPHIRE_CONFIG` as in M1, optional `--config PATH` override, for every new command. No default tenant/network anywhere.
- **Command names:** all new commands are `python -m sapphire_flow.cli.<name>` modules, no `pyproject.toml` entry points and no new `scripts/` files (the curated Dockerfile script list is pinned in five places, see M5). The step-order table of the commands has ONE owner: Plan 541 (listed there as an open gap, closed in its fix PR); this plan does not add it.
- **`onboard_nepal` is CHWRR-locked** for `basins`, `history`, `qualify` and, until M5b, `weather-bindings`. M5b generalises only `weather-bindings`.
- **Gateway runtime name:** printed by M2, persisted by the existing weather-bindings row in M5a, cross-checked by M4's `naming` line; no new station field.
- **Datum/gauge type:** M2 stores them; M3 converts with them; QC uses them; all three use the one `WaterLevelGaugeType` enum.

## Owner questions (collected, plain language)

OD1 (DECIDED 2026-11-17, against the recommendation): `--tenant` and `--network` are REQUIRED on the group script, no Swiss default. Consequences are in M1.
OD2 (DECIDED 2026-11-17): a separate milestone M5b after M5 generalises `onboard_nepal` away from CHWRR (now written above).
OD3 (DECIDED 2026-11-17): promotion command now; dashboard button as its own later plan; ONE safeguard, host setting `promotion_allowed`, off by default; no `--confirm-shared-host`.
Also decided 2026-11-17: no sandbox helper script; standing rule for the IFS backfill and the sandbox review-reference placeholder accepted (Plan 541); datum asks the gauge type first (staff/pressure vs bridge-mounted sensor) and echoes a converted example; gauge type stored as an enum; every writing command is dry-run by default.
OD4 (DECIDED 2026-11-17): QC range checks run on the CONVERTED water level, not the raw distance. Design: the tool stores both the gauge-zero elevation (the station datum) and the sensor elevation; the loader converts distance to m a.s.l.; live QC works unchanged. Live raw-distance ingest for a bridge-sensor station is out of scope for this plan. A hydrologist confirms the QC limits once for such stations (a confirmation step, not an open decision).
OD5 (DECIDED 2026-11-17): M5b keeps the strict CHWRR-only host rule for tenant chwrr; other tenants get the normal tenant check.
Other per-milestone questions and recommendations are listed in each section; the recommendations apply unless you say otherwise: datum may be unknown only with an explicit flag (M2); a bridge sensor needs both elevations (M2); refuse water level without a datum, skip out-of-range discharge instead of clamping (M3); facts only, no single "ready" verdict yet (M4); refuse a second forecast source on a station, assignment as its own command (M5); reject ONLY+SKIP and use environment variables for the registrar (M6).

## Review record

Milestone sections drafted 2026-11-17 by one read-only reviewer per milestone against `origin/main` (98ae8af86); no tests were run by them. Findings that changed the plan: M1 - join and other-tenant handling already exist, only `--tenant` is missing; M6 - the overlay "replaces the table" claim in the runbook was wrong (nested tables merge; the base config simply has no `[adapters.recap_gateway]`).

**2026-11-17, administrator review and developer review of Plans 540/541 (both read-only; revision pass by the plan author; no code run).** Facts cited were re-checked against the checkout (`98ae8af86`): the three CHWRR locks and CLI lines, the five pinned operator-script locations, `[project.scripts]`, `_evaluate_qc_window` and its inputs, `onboarding.py` using `.check`, the prepare-gateway message and folder check.
- Folded: M5b written and ordered; M1 stale lines fixed (required options, seven test call sites, Plan 517 overlap, hint query size); M2/M3 write authority and audit row; M3 split into 3a-3d with QC copy-versus-extract decided (copy plus parity test) and its inputs listed; usable-days line in every loader; every writing command dry-run by default; M4 extended (lookback coverage, naming cross-check, wait/failure facts, `--check-gateway`, stable `--json`, `--model` dependency, fakes decision, tenant/network listing in M2, final failed-count line); M5 command moved to `cli/`, split in two PRs, Nepal-host row check before the default flips; M7a one safeguard, no entry point, explicit blocking conditions independent of M4's report; M8 fleshed out where verified and flagged where not; datum wording replaced by the gauge-type-first decision (adds a column and a migration); guide-branch dependency and runbook conflict risk; recommended order recorded.
- Deliberately not folded: a single wrapper command (`sapphire-onboard ...`) - a step-order table instead (MVP, no new entry points); making `qualify` read-only or retiring it - left untouched (six-gauge gate, M4 Q3); a numeric "ready" verdict in M4 - facts only (M4 Q1); not naming the other tenant to non-global admins is folded, but a general permission model is not; a `SYNTHETIC` observation source and extra shared-host guards for synthetic CSVs - out of scope for MVP (R2); refactoring `_evaluate_qc_window` into a shared callable - rejected for the first cut because it edits the live Swiss ingest path; a `cycle_status` command and Gateway registration/subscribe/reconcile commands - outside this plan (Plan 541 prose and M8 messages; M4 covers the data-wait facts); Plan 541 findings are not edited here (separate plan).
- Not verified in code and flagged in the text: M4 wait/outcome storage location and the Gateway coverage call; M8's "3,400 lines of JSON", the log flood and the runbook `failure_reason` claim; allowed `measured_parameters` vocabulary and timezone validation (new rules in M2); whether the M5b functions still reach six-gauge constants.
**2026-11-17, fix pass after the final independent review (F1-F8; cited facts re-checked: `_RELEASE_B_HEAD = "0085"`, head file 0085, `operational_weather_stations` call sites at `flows/ingest_recap_era5_reanalysis.py:83,194`, `SAPPHIRE_CONFIG` in M1's script).** Folded: bridge-sensor design reworked so live QC stays unchanged (both elevations stored, loader converts; OD4 and OD5 closed, `open_decisions` empty); full migration consequences for M2; new audit types for loads; M3b size corrected and baseline-store behaviour stated; the three-binding check moved into `admit_weather_bindings`, M5b updates the two live call sites and states `--confirm-t8`; M4/M7a given config and `--weather-scope`, M4 split firm; stale text fixed (5c, one-PR line, decided questions, config convention decided as env var plus optional `--config`, guide branch size, single owner for the step-order table, M1 fixture extension). Not verified by me: the line numbers of the live datum call sites in `qc_datum` consumers (taken from the final review; the implementer re-greps), and the second-level QC limits for bridge sensors (hydrologist). Independent review of the whole revised plan is still required before the orchestrator can set READY.

**2026-11-18, owner amendment to M3d (discharge support for the synthetic case).** The generator now writes `discharge.csv` directly and a small fixed fixture `tests/fixtures/synthetic_station/discharge_daily.csv` is committed with M3d (after M3b), so the deployment hello-world can use it through M3a/M3b without M3c or a second generator. Raised by the deployment-pipeline agent's fixture question (no existing repository fixture was suitable: `tests/fixtures/trishuli_synthetic` is hourly water level with an arbitrary datum and was never run through QC; `tests/fixtures/dhm/synthetic_daily_flow.txt` is six lines in a DHM-specific format). Reviewed by one independent read-only reviewer (see below).

**2026-11-18, owner decision: synthetic onboarding scenario, built ahead of M3b (M3d split).** M3d is split. **M3d-1** (now, independent PR, no dependency on M3a/M3b code): the pure seeded generator `src/sapphire_flow/services/synthetic_series.py` (limits taken from a passed-in resolved rule set, never hard-coded; no flat floor; full precision), and the committed fully synthetic, agency-neutral scenario `tests/fixtures/synthetic_station/`: `README.md`, `scenario.json` (station fields in `create_station` argument form: code, network, name, lon/lat, timezone UTC, parameters `discharge`), `discharge_hourly.csv` (`time,value`, ISO UTC, m3/s, 72 contiguous hourly values ending 2025-01-02T23:00:00Z, for the deployment smoke test) and `discharge_daily.csv` (`time,value`, `YYYY-MM-DD`, 90 days ending 2025-01-02, station zone UTC). Acceptance: both files pass the real `Stage1QualityChecker.check_with_coverage` under the Swiss built-in default rule set (hourly group for hourly, daily group for daily) with no `suspect`/failed flags; files equal the generator output for the recorded seed; `scenario.json` is accepted by `create_station`'s parser (dry run). A later follow-up adds the create-then-load integration test once M3a is merged. **M3d-2** (the `synthetic` command writing arbitrary spans, water level and rating) stays deferrable and follows M3b/M3c.
