---
status: DEFERRED
---

# Plan 049 — Swiss demo backend: public HTTPS address for the Mac mini

> **Deferred 2026-09-29 (owner).** v1 (Nepal) takes priority; the Swiss/BAFU demo waits and Plan 511 is
> built first. The owner also prefers not to expose the office-LAN Mac mini through a tunnel: the mini
> stays on the office network, so a compromised tunnel container would sit on it. When this resumes,
> the recommended shape is **a read-only Swiss demo copy on a Swiss-hosted cloud server** (API +
> database only; the mini pushes a filtered export outward; a snapshot up to a day old is acceptable),
> built from Plan 511's host recipe — not this tunnel design. Measured 2026-09-29 for that future
> sizing: mini Postgres 104 GB / 148 stations, of which the tables a map read needs are a few GB
> (observations 2.7 GB, forecast values 0.8 GB); NWP grid volume 448 GB, not needed to serve. The body
> below is kept as the tunnel fallback if the cloud copy is ever dropped.

**Date**: rewritten 2026-09-29 (original 2026-04-17)
**Related**: Plan 109 (owns the `prefect-server` network fix), Plan 511 (Nepal cloud host — the other half of this split), Plan 401 (reviewer key),
Plan 402 (map read routes), Plan 404 (rejected-forecasts route), Plan 111 (BAFU licence gate),
Plan 046 (Mac-mini staging host)
**Scope**: give the flow-map's Cloudflare Worker a reachable HTTPS address for the **Swiss**
SAPPHIRE Flow instance that runs on the Mac mini, using an outbound-only Cloudflare Tunnel, so the
Swiss forecasts can be demoed. The Nepal instance is **not** here — it runs on a cloud server
(Plan 511) and needs no tunnel.

---

## Why this was rewritten

The April draft published the mini's API to **people** — Entra ID single sign-on for the team, email
one-time-PIN for external viewers — behind a full-zone move of `hydrosolutions.ch` to Cloudflare.
Three things changed:

1. **The consumer is the map's Worker, not a person.** The flow-map (`SAPPHIRE-flow-map`) fetches
   `/api/v1` from a Cloudflare Worker that holds the reviewer key server-side and refuses any request
   the map does not make (`worker/apiProxy.ts`). Browsers never call the API. People are let in by
   the map's own passphrase gate. So the API needs a **machine** credential in front of it, not human
   sign-in; the Worker already accepts an optional Cloudflare Access service token
   (`SAPPHIRE_ACCESS_CLIENT_ID` / `_SECRET`) and requires an `https://` origin.
2. **Nepal moved off the mini.** The April plan's own "deferred" list named a migration to a cloud
   host for Nepal. That is now Plan 511. This plan is only the Swiss/mini half.
3. **Plans 401, 402 and 404 are merged.** The reviewer key, the QC-rules / skill routes and the
   rejected-forecasts route exist. The map's remaining upstream blocker is this address, not any plan.

Human single sign-on and OTP viewers are **deferred** (see the end), not dropped: the map's shared
passphrase gate covers the demo, per the owner decision of 2026-09-29 that the dashboard stays a
read-only demo.

## Context

- The mini serves the API on the office LAN only (`docker-compose.macmini.yml` publishes `8000`); the
  standing access model is an SSH tunnel. The mini's router/network is not ours to reconfigure, which
  is exactly what an outbound-only tunnel avoids. **Whether the office network allows the outbound
  tunnel connection is unverified** — T1 checks it first.
- Plan 401's reviewer key is the application-level gate for `/qc/rules` and `/skill`; Plan 404's
  rejected-forecasts route admits reviewer or admin tokens (and named humans). Nothing in this plan
  changes those.
- `prefect-server` is on the `frontend` network in `docker-compose.yml`. A tunnel container joining
  `frontend` could reach the unauthenticated Prefect API there. The April draft's fix stands (D3).
- The Swiss data is BAFU research-only material; Plan 111 G1 gates *publishing* it. The demo is
  reachable only by the passphrase-holders the owner names, but that is an owner call, recorded in T0.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Cloudflare Tunnel from a `cloudflared` container in the mac-mini overlay**, joined to `frontend` only. | Outbound-only; no router change; lifecycle under Compose. Unchanged from the April draft. |
| D2 | **Perimeter = Cloudflare Access with ONE service-token policy** (the Worker's token) and no human policies. Default deny for everyone else. | The only legitimate caller is the Worker. Human policies add seats, an identity provider and a support burden for no demo benefit. |
| D3 | **`prefect-server` must be off the `frontend` network before the tunnel starts.** Plan 109 (DRAFT) already owns that one-line change; this plan does not duplicate it. If 109 has not landed when T2 starts, T2 lands the line and records it in 109. | `api` and workers reach Prefect over `backend`. Removes the tunnel → unauthenticated Prefect path. Measured 2026-09-29: `docker-compose.yml:75` still reads `[backend, frontend]`. |
| D4 | **Tunnel ingress only routes `^/api/v1/`.** Everything else (legacy HTML, `.json` exports, OpenAPI UI) answers 404 at the tunnel. | The map reads `/api/v1` only. Least exposure; the admin-only legacy surfaces stay off the internet. |
| D5 | **A dedicated Swiss reviewer key**, issued under Plan 401 for this deployment only, separate from any other key. | Revocable without touching anything else; scope-limited to the Swiss tenant. |
| D6 | **Domain: OWNER DECISION (T0).** Recommendation: a **small dedicated domain** on Cloudflare, not a full-zone migration of `hydrosolutions.ch`. | Access needs Cloudflare to be authoritative for the hostname's zone. Subdomain delegation is a paid feature. Moving `hydrosolutions.ch` puts the company website's DNS and mail at risk for a demo. A ~$10/yr domain has no such blast radius. The April draft's full-zone procedure (with before/after `dig` checks and rollback) is the fallback if the owner prefers the company domain. |
| D7 | **Edge-terminated TLS accepted for this demo host**; production still terminates on the application host (`docs/standards/security.md` §Network policy). | Unchanged. |

## Tasks

### T0 — Owner decisions and audit
**Outcome**: the three open decisions are recorded in this plan and an existing Cloudflare
account/zone audit is done.
- Domain (D6); who may see the Swiss demo (named people only, Plan 111 G1 — record who decided and
  when); whether an existing Cloudflare account or `hydrosolutions.ch` zone already exists (avoid a
  duplicate zone).

**In / Out**: this file only. Out: any Cloudflare change.
**Verification**: inspection — the three answers are written under "Decisions recorded".
**Pre-change**: N/A (documentation).

### T1 — Reachability probe and Cloudflare provisioning
**Outcome**: a named tunnel and Access application exist, and the mini proves it can hold the
outbound connection.
- First, from the mini: confirm outbound connections to Cloudflare's tunnel endpoints (port 7844,
  QUIC/HTTP2) succeed. If blocked, stop and escalate — the fallback is a small cloud VM for the Swiss
  instance, a different plan.
- Create the zone (per D6), the tunnel, the proxied DNS record, the Access application with a single
  Service-Auth policy, and the service token. Credential JSON to `./secrets/cloudflared_credentials.json`
  (`chmod 600`, never committed); token values to the Worker's secrets, not the mini.

**In / Out**: Cloudflare dashboard + `./secrets/` on the mini. Out: repo code.
**Verification**: `cloudflared tunnel info` shows a connector; an unauthenticated request to the public
hostname is redirected/denied by Access; a request with the service-token headers reaches the origin.
**Pre-change**: N/A (infrastructure). Baseline: hostname does not resolve / answers nothing today.

### T2 — Compose overlay and network hardening
**Outcome**: the tunnel runs from `docker-compose.macmini.yml`, routes only `/api/v1/`, and cannot
reach Prefect.
- `docker-compose.yml`: `prefect-server` `networks: [backend]` (D3) — via Plan 109 if it has landed, else here.
- `docker-compose.macmini.yml`: `cloudflared` service — pinned image tag, `cap_drop: [ALL]`,
  `read_only: true`, `tmpfs: [/tmp]`, `restart: unless-stopped`, `networks: [frontend]`, read-only
  config bind-mount, `cloudflared_credentials` Docker secret.
- `scripts/cloudflared/config.yml`: ingress rule with `path: ^/api/v1/` → `http://api:8000`, catch-all
  `http_status:404` (D4).

**In / Out**: those three files plus a compose-config test. Out: the API, Caddy.
**Verification**: a test asserting `prefect-server` is not on `frontend` and `cloudflared` is on
`frontend` only; then on the mini `docker compose exec cloudflared wget -qO- http://prefect-server:4200/api/health`
fails and `…/api/v1/health` succeeds; a request for a non-`/api/v1/` path through the public name
returns 404.
**Pre-change**: RED — the compose test fails on `main` today because `prefect-server` is on `frontend` (skip only if Plan 109 has already fixed it).

### T3 — Watchdog external probe
**Outcome**: the host watchdog also probes the **public** health URL with the service-token headers and
alerts on failure.
- Extend `src/sapphire_flow/ops/watchdog.py`; new structlog event `pipeline.external_probe_completed`
  (`url`, `status_code`, `duration_ms`); same Slack failure path as the local probe; token read from
  `./secrets/` like `slack_webhook_url`.

**In / Out**: `watchdog.py` + its unit tests. Out: the Access configuration.
**Verification**: unit tests with a fake HTTP client — success logs the event; a non-200 and a
timeout each alert; the local probe is unaffected.
**Pre-change**: RED — no external-probe test exists; the first test fails on the missing function.

### T4 — Standards and runbook
**Outcome**: docs match the deployed shape.
- `docs/standards/security.md`: §Secrets (`cloudflared_credentials`, the watchdog's service token,
  rotation cadence) and §Network policy (edge-TLS exception, service-token perimeter).
- `docs/deployment/cloudflare-public-url-runbook.md`: rotate the tunnel credential and the service
  token; how to rotate the dedicated reviewer key (D5); how to take the demo offline (stop
  `cloudflared`).

**In / Out**: docs only.
**Verification**: inspection against T1–T3; `uv run pre-commit run --all-files` clean.
**Pre-change**: N/A (documentation).

### T5 — End-to-end check with the map, and go/no-go
**Outcome**: the deployed map reads Swiss data through the public address, and a short report records it.
- Issue the D5 key. In the map project (out of scope for edits here): set `SAPPHIRE_API_BASE_URL`,
  `SAPPHIRE_API_TOKEN`, `SAPPHIRE_ACCESS_CLIENT_ID/_SECRET`. Confirm the map's QC-rules and station-skill
  views load Swiss data. Restart `cloudflared`; confirm reconnect within 60 s. Leave it running 72 h
  and confirm the watchdog's external probe does not flap.
- The map's proxy currently leaves `/rejected-forecasts` (Plan 404) off its allowlist; adding it is a
  map-side change, recorded as a hand-off, not done here.
- `docs/deployment/cloudflare-public-url-YYYY-MM-DD.md` records results, latency and a go/no-go.

**In / Out**: the report file. Out: map-repo changes.
**Verification**: the report exists and shows each check; a non-passphrase browser cannot reach data.
**Pre-change**: N/A (validation).

## Exit gates

1. The Worker reads `/api/v1/qc/rules` and `/stations/{id}/skill` for a Swiss station via the public
   address; the same request without the service token is denied at Access.
2. A non-`/api/v1/` path through the public name returns 404; Prefect is unreachable from `cloudflared`.
3. The external watchdog probe runs 72 h without flapping and alerts on a simulated failure.
4. `docs/standards/security.md` and the runbook are updated; the go/no-go report is committed.
5. The owner has recorded who may see the Swiss demo (T0).

## Deferred

- Human single sign-on (Entra ID) and one-time-PIN viewers at the Access perimeter — only if the
  passphrase gate stops being enough.
- Publishing an additional Prefect UI hostname.
- Automated rotation of the tunnel credential and service token.
- Moving the Swiss instance to a cloud host (only if the mini's network blocks the tunnel).

## Risks

| Risk | Mitigation |
|---|---|
| Office network blocks outbound tunnel traffic | T1 probes first and stops; fallback is a cloud VM. |
| Moving `hydrosolutions.ch` DNS breaks the website or mail | D6 recommends a separate domain; the full-zone route keeps the April draft's pre/post `dig` checks and rollback. |
| Tunnel reaches Prefect | D3 (Plan 109) + the T2 test and on-host check. |
| Swiss (BAFU) data exposed beyond named people | Access default-deny, the map's passphrase gate, the T0 record; Plan 111 G1 stays the authority. |
| Reviewer key or service token leaks | Dedicated key (D5), rotation runbook (T4), tunnel offline switch. |
| The mini is a single home-office host | Accepted for a demo; the watchdog alerts and the demo can be paused. |

## Dependency graph

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T0"] },
    { "id": "phase-2", "tasks": ["T1", "T2"], "depends_on": ["phase-1"], "parallel": true },
    { "id": "phase-3", "tasks": ["T3", "T4"], "depends_on": ["phase-2"], "parallel": true },
    { "id": "phase-4", "tasks": ["T5"], "depends_on": ["phase-3"] }
  ]
}
```
