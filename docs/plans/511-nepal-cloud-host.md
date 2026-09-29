---
status: DRAFT
---

# Plan 511 — Nepal instance on a cloud server, readable by the flow-map

**Date**: 2026-09-29
**Priority**: first hosting plan (owner, 2026-09-29) — v1 Nepal comes before the Swiss demo (Plan 049, deferred)
**Related**: Plan 049 (Swiss half of this split — mini + tunnel), Plan 401 (reviewer key, tenant
binding), Plan 208 (off-box backup), Plan 273 (Nepal flow-map demo hand-off), Plan 268 (DHM delivery
import), Plan 300 (DHM observation adapter), `docs/standards/security.md` (VM procedure, firewall),
`docs/standards/cicd.md`
**Scope**: stand up SAPPHIRE Flow for **Nepal (DHM tenant)** on a rented cloud server with a public
HTTPS address, and connect the flow-map's Cloudflare Worker to it. This is provisioning, deployment
and access wiring — **not** Nepal onboarding, models or data feeds, which are separate tracks this
plan waits on.

---

## Why a separate plan from 049

Different host, different data terms, different timeline. The Swiss instance stays on the mini and is
buildable now (Plan 049). The Nepal instance needs a provider, a domain, backups, and — before it shows
anything — Nepal stations with data. They share one design idea (the map's Worker reads `/api/v1` with a
reviewer key) and nothing else: no tunnel is needed here, and DHM's data must never share a host or
database with the Swiss BAFU material.

## Context

- **The stack already supports a public host.** With `SAPPHIRE_DOMAIN` set, the Caddy service in
  `docker-compose.yml` provisions TLS automatically and applies HSTS (`Caddyfile`);
  `docs/standards/security.md` §Prerequisites describes a VM deployment (Ubuntu, Docker, Caddy, secrets,
  `docker compose up`) and §Firewall requires only 443 and SSH. No new proxy design is needed.
- **The map side is ready for an `https://` origin.** The Worker takes `SAPPHIRE_API_BASE_URL`,
  the region's reviewer key (`SAPPHIRE_API_TOKEN`) and an optional Cloudflare Access service token; it
  refuses plain `http`. A Nepal-scoped key uses Plan 401's tenant binding (DHM tenant only) — no new
  engineering.
- **What Nepal has today** (from project notes, to be re-verified at T0): onboarding tooling merged
  but statics resolved for 0 of 78 stations and nothing deployed; DHM's delivery is six real gauges
  with runoff and no level data; a Nepal forcing feed (Plan 192) exists as a Postgres-only stack on the
  mini. A cloud host with a working address can therefore still serve an **empty** Nepal map until those
  tracks land. This plan does not fix that; it removes the address/host blocker.
- **Backups matter more here than on the mini.** DHM's data is restricted and hard to re-obtain; a cloud
  disk is not a backup. Plan 208 (off-box replication) and Plan 340's protected-backup proofs apply.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **One VM, Docker Compose, the same images as the mini**, base `docker-compose.yml` plus a Nepal overlay. No Kubernetes, no managed database. | Matches what is tested and what DHM's IT team would run; smallest surface. |
| D2 | **Caddy terminates TLS on the host** (`SAPPHIRE_DOMAIN`), ports 80/443 and SSH only; Prefect UI stays internal. | Already the supported shape; security.md's production rule (TLS on the application host) is met. No tunnel. |
| D3 | **DECIDED (owner, 2026-09-29): Infomaniak Public Cloud (Switzerland), working choice; AWS is not required.** Target ~8 vCPU / 16 GB / 200-300 GB block storage (the Public Cloud price for this size is an extrapolation, ~CHF 32/month, from Infomaniak's own 4 vCPU / 8 GB CHF 16.10 example; unlisted prices are checked in T1's trial). Fallback: Hetzner CPX-class (~EUR 70/month; the cheaper CX line was "not available" on 2026-09-29). Backups go OFF-provider (Cloudflare R2, which Plan 162 D4 allows). Plan 208's earlier AWS/S3 sink decision is superseded for this host. | Cheapest credible option measured; Swiss-hosted; the Worker only needs an HTTPS address. Hydromet will not take this host over, so provider familiarity for a hydromet IT team is not a criterion. |
| D4 | **Nepal-only host**: no Swiss data, no BAFU collectors, no Swiss tenant. | Separate licences and delivery terms; independent release pace. |
| D5 | **Map credential = a Nepal reviewer key bound to the DHM tenant** (Plan 401), held only as a Worker secret. Optional second layer: Cloudflare Access service token in front of Caddy (only if DNS is on Cloudflare; otherwise the reviewer key alone). | Reuses the merged access model; revocable; a stolen key reads DHM data only. |
| D6 | **DECIDED (owner, 2026-09-29): this host IS the v1 staging host** for the six DHM gauges — one Nepal host, promoted through the normal staging gates, not two. | The v1 goal is daily Nepal forecasts on the staging host; a second Nepal box doubles backups and drift. |
| D8 | **Size the disk for the hot window, not an unbounded grid archive.** Serving the map needs only the database; raw NWP grids are needed by the forecast cycle for recent cycles, and older grids move to cold storage by design (`docs/architecture-context.md` Flow 1.2: hot for `weather_hot_days`, default 180). T0 confirms the tiered-retention job actually runs before the disk is sized; the Swiss mini's 448 GB grid volume is what an unpruned archive looks like. Evidence tables are permanent (migration 0057), so plan disk growth for them. | The cloud disk is the recurring cost and the backup size. Keep the archive off the hot path. |
| D7 | **The API reads through `/api/v1` only from the internet**; legacy HTML/JSON routes are blocked at Caddy. | Least exposure, same reasoning as Plan 049 D4. |

## Tasks

### T0 — Owner decisions and re-measurement
**Outcome**: D3, D6 and the domain name are decided and recorded here, and the Nepal state is re-measured.
- Provider/region, the hostname, staging-host role, who owns the cloud account and billing.
- Re-measure: Nepal stations onboarded, DHM observation feed state, models available for Nepal.
- Is the NWP tiered-retention job implemented and scheduled? Expected IFS grid volume per day. Size the disk from these (D8).

**In / Out**: this file only. Out: any provisioning.
**Verification**: inspection — decisions written under "Decisions recorded" with date; measurements
quoted with the command that produced them.
**Pre-change**: N/A (documentation).

### T1 — Provision the host
**Outcome**: a hardened VM reachable over SSH and 443 only.
- **Trial first (CHF 300 credit, 3 months):** before committing, confirm from the console/price calculator the real price of the chosen size, a public IPv4, block-storage and snapshot prices, and that S3-compatible object storage works; record them under "Decisions recorded". If any unknown breaks the budget, take the Hetzner fallback (D3).
- Ubuntu LTS, Docker, non-root deploy user, key-only SSH, host firewall to 22/80/443 (per
  `docs/standards/security.md` §Firewall), automatic security updates, DNS A record for the hostname.
**In / Out**: the VM and DNS. Out: SAPPHIRE code.
**Verification**: external port scan shows only 22/80/443; `ssh` password login refused.
**Pre-change**: N/A (infrastructure).

### T2 — Deploy the stack
**Outcome**: `GET https://<host>/api/v1/health` returns OK with a valid certificate.
- Generate `./secrets/` per security.md §Secrets; `SAPPHIRE_DOMAIN=<host>`; add
  `docker-compose.nepal-cloud.yml` (config overlay for the Nepal tenant, image tags pinned by digest
  per cicd.md, host-specific bind paths); run migrations; register deployments.
- Seed the first org admin with the documented one-time CLI command; create the DHM tenant/org.
**In / Out**: `docker-compose.nepal-cloud.yml`, the Nepal config overlay, a compose-config test (no
Swiss collector services; Prefect not published). Out: onboarding stations, models.
**Verification**: the compose test; on the host, `docker compose ps` all healthy; the health URL over
HTTPS; an HSTS header present; the legacy HTML path returns 404 from outside.
**Pre-change**: RED — the compose test asserting "no Swiss collectors, Prefect unpublished" fails until
the overlay exists.

### T3 — Backups (deferred; does NOT gate v1 staging)
**Outcome**: nightly protected backups leave the host for **Cloudflare R2**, and a restore has been rehearsed.
- **Owner, 2026-09-29: backups are not the v1 staging priority.** The host may run without off-box
  backups initially; this task is scheduled after the first Nepal staging forecasts, and **before any
  restricted DHM data beyond the six-gauge delivery lands, or before the host is called more than
  staging**.
- Depends on Plan 208, which must first be updated for this host (its notice at the top lists the
  changes: R2 access key instead of an instance role, scoped write-only token, lifecycle expiry).
- Verify that R2 supports what Plan 340's protected-backup proofs need (object lock / retention).
  If not, record the gap and decide with the owner.
- Rehearse a restore into a scratch database.
**In / Out**: backup config and one rehearsal record. Out: changing the backup design.
**Verification**: a backup object exists in R2; the restore rehearsal's row counts match.
**Pre-change**: N/A (operational), baseline: no off-host copy exists.

### T4 — Map access
**Outcome**: the map's Worker reads Nepal data through the public address with its own key.
- Issue the DHM-tenant reviewer key (D5). In the map project (not edited here): set
  `SAPPHIRE_API_BASE_URL`, `SAPPHIRE_API_TOKEN` (and the Access token if used).
- Confirm `/qc/rules`, `/stations/{id}/skill` and stations respond for the DHM tenant; confirm the key
  cannot read another tenant.
**In / Out**: key issuance and a check record. Out: map-repo changes.
**Verification**: the Worker's requests return 200 for DHM stations and 404/403 for any other tenant's;
the same call without the key returns 401.
**Pre-change**: N/A (integration check).

### T5 — Runbook and standards
**Outcome**: `docs/deployment/nepal-cloud-host-runbook.md` (update, restart, rotate keys, restore,
take offline) and the `docs/standards/security.md`/`cicd.md` notes for this host.
**In / Out**: docs only.
**Verification**: inspection against T1–T4; `uv run pre-commit run --all-files` clean.
**Pre-change**: N/A (documentation).

## Exit gates

1. `https://<host>/api/v1/health` is healthy with a valid certificate; only 22/80/443 are open.
2. The Worker reads DHM-tenant data with its key; the key cannot read another tenant; no key → 401.
3. *(Deferred, not a v1 staging gate.)* An off-host R2 backup exists and a restore rehearsal matches.
4. No Swiss data or Swiss collector exists on the host.
5. The runbook is committed and the owner has recorded D3, D6 and the hostname.

## Not in this plan

Nepal station onboarding and statics; DHM observation and NWP feeds; Nepal models and forecast
cycles; thresholds and alerting; individual hydromet logins (Plan 341's human-auth system); the map's
own changes; a second/production Nepal environment.

## Risks

| Risk | Mitigation |
|---|---|
| The host is live but the map is empty (no Nepal data yet) | Stated up front; T0 re-measures; the demo claim waits on the onboarding/feed tracks. |
| DHM data lost with the VM | Owner accepted no off-box backup for early v1 staging (2026-09-29); T3 (R2 backup + rehearsed restore, via an updated Plan 208) follows before the host is treated as more than staging. |
| Public 443 exposes the API | Reviewer-key gate, legacy routes blocked (D7), tenant-bound key, host firewall. |
| Provider or residency objection from DHM | D3 is an owner decision taken before provisioning. |
| A second Nepal box appears later | D6 decides now whether this is the staging host. |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-2", "tasks": ["T1"], "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["T2"], "depends_on": ["phase-2"] },
    { "id": "phase-4", "tasks": ["T4", "T5"], "depends_on": ["phase-3"], "parallel": true },
    { "id": "phase-5", "tasks": ["T3"], "depends_on": ["phase-4"], "note": "deferred; also depends on Plan 208 being updated" }
  ]
}
```
