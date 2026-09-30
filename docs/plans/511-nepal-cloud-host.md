---
status: READY
reviews:
  - "claude + codex 2026-09-29/30 rounds 1-4 — FINDINGS each round, all folded; round 4 residue was two sentences"
  - "claude 2026-09-30 — CLEAN on exact state 2fcef37e (diff of the last two edits + plan consistency)"
  - "READY: the owner, who is also the orchestrator, directed it on 2026-09-30; the author session made the edit"
---

# Plan 511 — Nepal instance on a cloud server, readable by the flow-map

**Date**: 2026-09-29 (revised after round-1 reviews, same day)
**Priority**: first hosting plan (owner, 2026-09-29) — v1 Nepal comes before the Swiss/BAFU dashboard (Plan 049 covers the Cloudflare side of both dashboards)
**Related**: Plan 049 (Cloudflare edge for the dashboards: access, reachability, data paths), Plan 513 (declared tenants), Plan 515 (grid prune), Plan 401 (reviewer key, tenant binding), Plan 268 (DHM
delivery import; owns the `chwrr` tenant identity), Plan 109 (Prefect network fix; cross-linked both ways), Plan 208 and Plan 340
(backups, follow-on), `docs/standards/security.md`, `docs/standards/cicd.md`
**Scope**: stand up SAPPHIRE Flow for **Nepal** on a rented cloud server with a public HTTPS address, and
connect the flow-map's Cloudflare Worker to it. Provisioning, deployment and access wiring — **not**
Nepal onboarding, models or data feeds, which are separate tracks.

---

## Context

- **Development stage, keep it simple (owner, 2026-09-29).** The restricted DHM delivery (Plan 268) is
  already on the Mac mini. This host starts with **no restricted DHM data**; moving data over is a later step,
  not part of this plan. **Update 2026-09-30:** the owner, on an **assumption** (DHM has not replied) and taking
  full responsibility, has approved hosting the DHM data on this password-protected host — see
  `docs/decisions/2026-09-30-dhm-data-hosting-assumption.md`. It is not DHM's consent. **Loading the data onto this host triggers the backup follow-on (D9): plan
  it first.** The host is
  **staging**, never production; anything beyond staging (production deployment, merging) stays with the
  owner (CLAUDE.md § Orchestration authority).
- **Different host, different data terms from the Swiss instance** (Swiss data path: Plan 049 T4): the Swiss BAFU
  material never goes on this host.
- **The map side is ready for an `https://` origin.** The Worker takes `SAPPHIRE_API_BASE_URL`, the
  region's reviewer key (`SAPPHIRE_API_TOKEN`) and an optional Cloudflare Access service token; it refuses
  plain `http`. The reviewer key uses Plan 401's tenant binding.
- **What the repo gives us, and what it does not** (verified 2026-09-29):
  - `Caddyfile` supports a domain via `{$SAPPHIRE_DOMAIN::80}`, **but** the `caddy` service in
    `docker-compose.yml` is not passed `SAPPHIRE_DOMAIN`, the Caddyfile has **no HSTS directive**
    (its comment says it is automatic; it is not), and it forwards **every** path to `api:8000`.
  - BAFU collectors are **not** compose services: `cli/register_deployments.py` registers them
    unconditionally on the shared workers, and the base `config.toml` points at Swiss sources.
  - No Nepal *deployment* overlay exists (`config/overlays/` has `chwrr-import`, `local`, `mac-mini`,
    `staging-5-stations`, and `nepal-history`, which only configures recap-gateway history import and
    selects no forecast adapter or schedule). The overlay must be applied on all four of `api`,
    `prefect-worker`, `prefect-worker-ingest`, `init` (cicd.md), or the missing one silently uses the
    base config.
  - Tenants are now **declared in config**: a `[tenants.<code>]` table is created by
    `python -m sapphire_flow.cli.provision_tenants`, which `init` runs between `alembic upgrade head`
    and `bootstrap-roles.sh` (Plan 513; `docker-compose.yml`; security.md). `config/overlays/mac-mini.toml`
    declares `[tenants.chwrr]` (name "CHWRR Nepal") this way. The older `bootstrap_tenant` command in
    `cli/import_dhm_delivery.py` is gone.
  - Application images are built locally (cicd.md: no registry, no publish workflow); only third-party
    images are digest-pinned. The `sapphire-flow-aquacast` image installs torch and needs build secrets.
  - A fresh database always gets the seeded tenant `sapphire` ("SAPPHIRE (Swiss v0)", migration 0041).
  - `access_tokens create-admin` mints an unscoped admin access token, not a human org admin
    (security.md § v1.0 headless implementation).
  - The compose stack needs `db_password`, `sapphire_api_db_password`, `sapphire_worker_db_password`,
    `sapphire_backup_db_password`, `access_token_pepper` (+ build secrets); security.md's list omits three.
  - Raw NWP grids are pruned after `nwp_grid_retention_days` (default 3); the permanent record is the
    extracted values in the database. **But** the Mac mini holds 421 GB of grids back to 2026-07-03, so
    pruning is not working there (diagnosed in Plan 515: the prune never matches the symlink + versioned-directory layout). Size the disk from the database and confirm
    pruning on this host; do not trust the 3-day figure until seen.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **One VM, Docker Compose, the base compose plus a Nepal overlay.** No Kubernetes, no managed database. | Matches what is tested; smallest surface. |
| D2 | **Caddy terminates TLS on the host**, ports 80 (certificate issuance/redirect), 443 and SSH only. Prefect UI is not published. | Production TLS rule in security.md; no tunnel needed. |
| D3 | **DECIDED (owner, 2026-09-29): Infomaniak Public Cloud (Switzerland); AWS is not required.** Target ~8 vCPU / 16 GB, disk sized from the database (T0). The ~CHF 32/month price is an **extrapolation** from Infomaniak's own 4 vCPU / 8 GB CHF 16.10 example, unverified until the T1 trial. Fallback: Hetzner CPX-class (~EUR 70/month; the cheaper CX line was "not available" on 2026-09-29). | Cheapest credible option found; Swiss-hosted; the Worker only needs an HTTPS address. Hydromet will not take this host over. |
| D4 | **Nepal-only host**: no Swiss data or Swiss collectors; the seeded empty `sapphire` tenant is allowed but has no write authority (`writable_tenants` names only the Nepal tenant). | Separate licences and delivery terms. The migration cannot be prevented from seeding it. |
| D5 | **Map credential = a reviewer key bound to the Nepal tenant** (Plan 401), held only as a Worker secret. Optional second layer: a Cloudflare Access service token, only if DNS is on Cloudflare; otherwise the reviewer key is the sole gate, so it must be long, unique and rotatable. | Reuses the merged access model; revocable. |
| D6 | **DECIDED (owner, 2026-09-29): this host is the v1 staging host**; the restricted DHM delivery stays on the mini during development. | One Nepal staging host; no restricted data move yet. |
| D7 | **Only `/api/v1/` reaches the API from the internet**; Caddy allows that prefix and answers 404 to every other path. | The map reads `/api/v1` only. The base Caddyfile forwards everything, so this needs a change (T2). |
| D8 | **Size the disk from the database, not the grid archive** (see Context). Evidence tables are permanent (migration 0057), so budget their growth. | The disk is the recurring cost and the backup size. |
| D9 | **Backups are a follow-on, not a v1 staging gate** (owner, 2026-09-29). Target Cloudflare R2 via an updated Plan 208. Plan 208 replicates an operational dump; it is **not** Plan 340's protected-preservation bundle (separate device, manifest and image archives, restored evidence, attestations) — that is assigned separately when the host is treated as more than staging or restricted data is moved here. | Keeps v1 moving without pretending R2 replication meets Plan 340. |

## Tasks

### T0 — Owner decisions and re-measurement
**Outcome**: the hostname and cloud-account owner are recorded, and the disk is sized from measurements.
- Record: hostname; who owns the cloud account and billing.
- Re-measure on the Mac mini: database size by table, and why grid pruning is not keeping the volume
  small; use the result to size this host's disk.
**In / Out**: this file only. Out: any provisioning.
**Verification**: inspection — answers dated under "Decisions recorded", measurements quoted with the
command that produced them.
**Pre-change**: N/A (documentation).

### T1 — Provision the host
**Outcome**: a hardened VM reachable over SSH, 80 and 443 only, with a correct clock.
- **T1 price record (Infomaniak calculator, 2026-09-30, owner's screenshot):** instance `a8_ram16_disk0` (8 vCPU / 16 GB, 730 h) CHF 23.50/month; 200 GB `Perf1` block storage CHF 17.52/month; one reserved IPv4 CHF 3.34/month; **total CHF 44.35/month**. Not in the estimate: snapshots/backups and outgoing traffic (check the first invoice). Project `nepal-staging` in Public Cloud `sapphire`, region `dc4-a`, quota 20 vCPU / 64 GB RAM / 1000 GB.
- **Trial first (CHF 300 credit, 3 months):** confirm the real price of the size, a public IPv4,
  block-storage and snapshot prices; record them. If any unknown breaks the budget, take the Hetzner fallback.
- Ubuntu LTS, Docker, non-root deploy user, key-only SSH (allow-list by source address if practical —
  security.md marks SSH restriction Critical; record any accepted deviation), host firewall 22/80/443
  (port 80 is for certificate issuance), automatic security updates, NTP on and system timezone UTC,
  DNS A record for the hostname.
**In / Out**: the VM and DNS. Out: SAPPHIRE code.
**Verification**: external port scan shows only 22/80/443; password SSH refused; `timedatectl` reports
synchronised.
**Pre-change**: N/A (infrastructure).

### T2 — Deploy the stack
**Outcome**: `GET https://<host>/api/v1/health` returns OK with a valid certificate, HSTS present, and
nothing but `/api/v1/` reachable from outside.
- **Caddy**: a Nepal Caddy configuration (or overlay) that (a) receives `SAPPHIRE_DOMAIN` via the
  caddy service's `environment`, (b) sets HSTS, (c) proxies only `/api/v1/` and answers 404 otherwise.
  Check the compose health check (`http://localhost:80/api/v1/health`) still passes with a domain set;
  adjust it if not.
- **Nepal config overlay** `config/overlays/nepal-cloud.toml`, applied on all four services
  (`api`, `prefect-worker`, `prefect-worker-ingest`, `init`): `[tenants.chwrr] name = "CHWRR Nepal"`
  (Plan 268 D11's identity, so this host and the mini agree), `writable_tenants = ["chwrr"]`, no BAFU
  adapters (`adapters.bafu_forecast`, `adapters.bafu_observation` absent), no Swiss
  `onboarding.data_source`/`basin_ids`, a Nepal `default_display_timezone`, weather adapters and
  schedules per the Nepal feeds actually live (decided from T0's measurements, not invented here).
- **Deployment registration**: BAFU deployments must not be registered. Smallest mechanism, chosen in
  this task (e.g. a config list of deployments to skip; `register_deployments` reads no TOML today,
  but `init` now has `SAPPHIRE_CONFIG`, so this task also states how the list reaches it); the test
  asserts the **registered deployment set**, not service names.
- **Prefect**: `prefect-server` off the `frontend` network (Plan 109's one-line change; land it here if
  109 has not landed, and record it in Plan 109 when it does, so the two plans do not both land it).
- **Images**: build `sapphire-flow` and `sapphire-flow-aquacast` on the host at the release version per
  cicd.md's upgrade procedure, with the `recap_dg_client_token` and `aquacast_token` build secrets. Keep
  the existing third-party digest pins.
- **Secrets**: create every file the compose secrets block lists (the four database passwords,
  `access_token_pepper`); update security.md's stale list.
- **First boot**: `init` runs migrations, role bootstrap and deployment registration together; a failed
  `init` blocks everything, so check its exit code and logs before proceeding.
- **Bootstrap**: mint the admin access token with the documented CLI command. The Nepal tenant comes
  from the overlay's `[tenants.chwrr]` declaration, created by `init` on first boot; verify the row exists
  with **zero stations**. No station rows are created here and no global-admin overlay is needed.
**In / Out**: the Caddy configuration, `docker-compose.nepal-cloud.yml` (the overlay wiring and mount on
all four services, and the caddy `environment`), **the base `docker-compose.yml`** (the `prefect-server`
network change, `[backend]` only — a Nepal-only override would not satisfy Plan 109), the Nepal overlay,
`register_deployments` change, tests, security.md list. Out: onboarding stations, models, feeds.
**Verification**: a compose/config test asserting: overlay env present on all four services; caddy gets
`SAPPHIRE_DOMAIN` and its health check is compatible with a domain being set; `prefect-server` not on
`frontend`; no published Prefect port. A **merged-configuration test** (load the base plus
`nepal-cloud.toml` as the services do) asserting the concrete keys above: `writable_tenants ==
["chwrr"]`; `[tenants]` is exactly `{chwrr: "CHWRR Nepal"}`; the BAFU adapter tables are absent;
`onboarding.data_source` and `basin_ids` are not the Swiss set; `default_display_timezone` is not
`Europe/Zurich` — so an empty overlay fails. A registration test asserting the
complete registered deployment set for this host (no BAFU deployment; each remaining deployment named).
On the host, after the first scheduled runs: a named query, written in this task against the actual
schema, returns zero observation or weather rows from Swiss sources. On the
host: `docker compose ps` healthy; health URL over HTTPS with a valid
certificate and an `Strict-Transport-Security` header; a non-`/api/v1/` path (legacy HTML, a `.json`
export, `/docs`) returns 404 from outside.
**Pre-change**: RED — the config test fails today (`caddy` has no domain env, `prefect-server` is on
`frontend`); the merged-configuration test fails while an empty overlay inherits Swiss settings; the
registration test fails while BAFU deployments register unconditionally.

### T3 — Map access
**Outcome**: the map's Worker reaches the host with its own key; the key cannot read another tenant.
- Issue the Nepal-tenant reviewer key (D5). Tenant mode only when every station in that tenant belongs to
  the dashboard's client (Plan 401); otherwise use stations mode. In the map project (not edited here):
  set `SAPPHIRE_API_BASE_URL`, `SAPPHIRE_API_TOKEN` (and the Access token if used).
- Acceptance is for an **empty host**: `/stations` answers 200 with an empty list; no key → 401. Tenant
  isolation is proved with a **temporary synthetic station in the seeded `sapphire` tenant** (a
  nonexistent ID would return 404 even with broken isolation). Nothing in the application can create or
  delete it under the identity left in force (no `sapphire` write authority; the worker role has no
  station DELETE; the stores expose no deletion), so this is an **exceptional database-owner
  procedure**: insert one standalone `stations` row with the `sapphire` tenant via owner-credential SQL
  in the `postgres` container (the owner role is `DB_USER` with the `db_password` secret, via
  `docker compose exec postgres psql`; the insert supplies `id`, `code`, `name`, a POINT/4326 `location`,
  `station_kind`, `timezone`, `measured_parameters`, `network`, `tenant_id` — unique on `(network, code)`;
  the other columns have defaults), check an admin can read it and the Nepal key cannot, then delete it by
  its exact ID with the same credential (a bare station row has no dependents to block deletion).
  Record both statements and the owner identity used, and confirm afterwards that the `sapphire` tenant
  has zero stations. The
  populated-map check waits until Nepal stations are on this host; restricted data stays on the mini.
**In / Out**: key issuance and a check record. Out: map-repo changes, restricted data.
**Verification**: the calls above, recorded with the commands used, including the synthetic station's
insert and delete statements and the final zero-station count.
**Pre-change**: N/A (integration check).

### T4 — Runbook and standards
**Outcome**: `docs/deployment/nepal-cloud-host-runbook.md` (update, restart, rotate keys, take offline)
and the `docs/standards/security.md`/`cicd.md` notes for this host, including the port-80 and SSH
decisions.
**In / Out**: docs only.
**Verification**: inspection against T1, T2, T3; `uv run pre-commit run --all-files` clean.
**Pre-change**: N/A (documentation).

## Exit gates

1. `https://<host>/api/v1/health` is healthy with a valid certificate and HSTS; only 22/80/443 are open;
   any non-`/api/v1/` path returns 404 from outside.
2. The Worker reads through the public address with its own key; no key → 401; the key cannot read
   another tenant's station.
3. No Swiss data and no BAFU deployment on the host; Prefect is not reachable from the public network.
4. The runbook is committed and the owner has recorded the hostname and account owner.

## Not in this plan

Backups (D9, follow-on); moving restricted DHM data here; Nepal station onboarding and statics; DHM
observation and NWP feeds; models and forecast cycles; thresholds and alerting; individual hydromet
logins (Plan 341); the map's own changes; production.

## Risks

| Risk | Mitigation |
|---|---|
| The host is live but the map is empty | Stated up front; T3 accepts an empty host; the populated check waits for Nepal data. |
| No off-box backup during early staging | Accepted by the owner (2026-09-29); the host carries no restricted data yet; D9 follow-on before that changes. |
| Public 443 exposes the API | D7 (only `/api/v1/`), reviewer-key gate, tenant-bound key, firewall; a single long rotatable key is the sole gate if Access is not used. |
| A Nepal overlay missing on one service silently falls back to Swiss config | The T2 test asserts it on all four services. |
| Grid volume grows unbounded as on the mini | D8: size from the database; confirm pruning on this host before trusting it. |
| Cloud account or price surprise | T1 trial first; Hetzner fallback. |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-2", "tasks": ["T1"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T2"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T3"], "depends_on": ["phase-3"] },
    { "id": "phase-5", "tasks": ["T4"], "depends_on": ["phase-4"] }
  ]
}
```
