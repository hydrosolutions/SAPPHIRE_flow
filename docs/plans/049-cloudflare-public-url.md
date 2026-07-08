# Plan 049 — Cloudflare Public URL for SAPPHIRE Staging

**Status**: DRAFT
**Date**: 2026-04-17
**Depends on**: Plan 046 (revised) IN_PROGRESS — the staging host is operational (Mac mini
on LAN; Stream D validation open), `cloudflared` image verified, `scripts/cloudflared/config.yml`
skeleton present, `docker-compose.macmini.yml` overlay skeleton present,
`docs/standards/security.md` amended with edge-TLS and
`slack_webhook_url` / `cloudflared_credentials` entries.
**Scope**: Publish the Mac-mini staging API at `https://sapphire-staging.hydrosolutions.ch`
using Cloudflare Tunnel (outbound-only from the Mac mini — no router config) + Cloudflare
Access (auth perimeter). Internal team SSO via Microsoft 365 / Entra ID OIDC. External
viewers (DHM Nepal, reviewers) via one-time-PIN email with IP-pinned sessions.

---

## Context

### Why now

Plan 046 delivers a Mac mini running the full SAPPHIRE stack on the office LAN, reachable
only via SSH tunnel from laptops on the trusted office WiFi. That access model is adequate
for the initial validation run but not for:

- Team members working from home or travelling.
- External collaborators (DHM Nepal, reviewers) who need read access without installing
  anything.
- The 7-day unattended run (Plan 046 D4) being observable from any device.

Plan 049 adds the missing public URL at zero router-configuration cost and with a
full SSO auth perimeter in front of the API.

### Inputs (assumptions)

- Mac mini is operational on the LAN per Plan 046; the Prefect stack, API, and Caddy are
  all healthy.
- `hydrosolutions.ch` DNS is managed at Hostpoint (registrar + DNS host).
- The team has a Microsoft 365 / Entra ID tenant (`@hydrosolutions.ch`).
- The IT specialist owns end-to-end execution of this plan.
- No external email addresses for DHM are known yet; the external-viewer whitelist starts
  empty and is populated at onboarding time.
- `cloudflared` arm64 image is confirmed available (Plan 046 C0).

### What Plan 046 already delivered (baseline)

- `docker-compose.macmini.yml` overlay skeleton (adds `cloudflared` service).
- `scripts/cloudflared/config.yml` skeleton.
- `cloudflared_credentials` and `slack_webhook_url` added to
  `docs/standards/security.md` §Secrets management.
- Edge-terminated TLS staging exception added to `docs/standards/security.md`
  §Network policy.
- `src/sapphire_flow/ops/watchdog.py` probing `localhost:8000`.
- Caddy on `frontend` network with `tls internal` (no ACME attempts).

Plan 049 activates and hardens what Plan 046 sketched.

### Problem statement

1. The staging API is LAN-only; team members off-network and external collaborators
   cannot reach it without SSH tunnels.
2. No identity perimeter exists in front of the v0 API (which has no built-in auth).
3. Cloudflare Access requires **zone ownership** for Access policies to be enforced —
   a CNAME-only delegation at Hostpoint does not give Cloudflare zone ownership. The
   original Plan 046 C2a design (subdomain NS delegation without full-zone migration)
   would leave the staging URL publicly reachable without auth enforcement. This plan
   corrects that with a full-zone migration.
4. The `prefect-server` service is on the `frontend` Docker network. Once `cloudflared`
   joins `frontend`, an attacker who compromises the tunnel can POST to the unauthenticated
   Prefect API at `http://prefect-server:4200/api/flow_runs/` and trigger arbitrary flows.
   This must be fixed before `cloudflared` goes live.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Full-zone migration of `hydrosolutions.ch` to Cloudflare** (change NS records at Hostpoint to the two Cloudflare nameservers). Cloudflare becomes authoritative for the whole domain; existing records are auto-imported. Main website continues to serve unchanged. | Cloudflare Access policies — the entire point of this plan — require zone ownership. Partial-zone setup (subdomain NS delegation or CNAME delegation) is a **Business plan feature ($200/month)** and is not available on the free tier. Full-zone migration is free, reversible (change NS back at Hostpoint), and takes ~10 minutes. Rollback: revert NS records at Hostpoint; propagation completes within the registrar's TTL. |
| D2 | **`cloudflared` as a service in `docker-compose.macmini.yml` overlay** (not installed on macOS host). `networks: [frontend]` only (reaches `api:8000`; no `backend` access). | Co-locates the tunnel with the stack; lifecycle managed by Docker Compose (`restart: unless-stopped`). Container isolation limits blast radius. `cloudflared`'s internal reconnection logic handles Cloudflare edge disconnects — `restart: unless-stopped` exists for container OOM/panic, which is a separate, rare event. |
| D3 | **Remove `prefect-server` from the `frontend` network** in `docker-compose.yml`. `prefect-server` remains on `backend`. `api` is already on `[backend, frontend]` and continues to reach Prefect over `backend`. | One-line change. Eliminates the blast-radius path from `cloudflared` → `prefect-server:4200` (unauthenticated Prefect API). Zero functional impact; `api` and `prefect-worker` both reach Prefect via `backend`. |
| D4 | **Internal SSO: Microsoft 365 (Entra ID)** via Cloudflare Access OIDC. Policy scoped to `@hydrosolutions.ch` email domain, IdP = Entra, 24h session. | Team's existing identity. OIDC is a first-class Cloudflare Access integration. No per-user provisioning needed; anyone with a `@hydrosolutions.ch` M365 account is authorized after IdP verification. |
| D5 | **External viewers: OTP via email + binding cookie** (IP-pinned sessions). 8h session duration. Whitelist starts empty; IT specialist adds DHM email addresses via the Cloudflare Access dashboard when needed. | No install, no account, no client. Works from any browser. Binding cookie prevents session-sharing from a different IP — required because external sessions cannot be Entra-scoped. |
| D6 | **Watchdog external probe via Cloudflare Access service token** (`CF-Access-Client-Id` + `CF-Access-Client-Secret` headers). Separate from user auth; non-expiring; specifically for non-interactive clients. | The existing `localhost:8000` watchdog probe (Plan 046 C5) does not verify the public URL or the Cloudflare Access perimeter. A service token probe validates the full path end-to-end without consuming a user seat. |
| D7 | **Edge-terminated TLS accepted for staging** (already documented in Plan 046 C5 `docs/standards/security.md` amendment). Traffic on `cloudflared → api:8000` is plaintext inside the Docker network. | The v0 API has no user credentials and no sensitive write operations from external callers. Plaintext inside a Docker bridge network is an accepted staging risk. Production MUST terminate TLS on the application host — this is already documented in `docs/standards/security.md` §Network policy. |

---

## Stream A — Cloudflare zone + tunnel provisioning

All steps in this stream are performed in the Cloudflare and Hostpoint dashboards
(no code changes). Stream A runs first; Stream B and Stream C depend on it.

### A0 — Audit existing Cloudflare accounts

Before creating anything new:

1. Check whether hydrosolutions already has a Cloudflare account or free-tier zone
   for `hydrosolutions.ch`. Avoid creating duplicate zones.
2. If an existing zone or account exists, take stock of its configuration before
   proceeding.

**Exit**: single authoritative Cloudflare account identified; no duplicate zones.

### A1 — Full-zone migration of `hydrosolutions.ch` to Cloudflare

**Before-state verification** (run from any machine):

```bash
dig hydrosolutions.ch +short          # current A record — must be preserved
dig NS hydrosolutions.ch +short       # current nameservers (Hostpoint)
dig MX hydrosolutions.ch +short       # MX records — must be preserved
```

Record the output. This is the rollback baseline.

**Steps**:

1. In Cloudflare dashboard → Add a Site → enter `hydrosolutions.ch`.
2. Select **Free plan**. Cloudflare scans and auto-imports all existing DNS records.
   **Verify the import**: confirm the main website A record, MX records, and any other
   active records appear in the Cloudflare DNS editor. Add any that are missing.
3. Cloudflare provides two nameservers (e.g., `abby.ns.cloudflare.com` and
   `bob.ns.cloudflare.com` — exact names vary per account).
4. At Hostpoint DNS → Change nameservers for `hydrosolutions.ch` to the two Cloudflare
   nameservers. This is the point of no return until rollback (if needed).
5. Wait for propagation: `dig NS hydrosolutions.ch +short` should return the two
   Cloudflare nameservers within minutes to a few hours (TTL-dependent).

**After-state verification**:

```bash
dig hydrosolutions.ch +short          # must return the same IP as before
dig NS hydrosolutions.ch +short       # must return Cloudflare NS
dig MX hydrosolutions.ch +short       # must return same MX records as before
```

**Rollback procedure** (if needed):

1. At Hostpoint DNS, change nameservers back to the original Hostpoint nameservers.
2. Wait for propagation (up to the previous NS TTL).
3. Remove the `hydrosolutions.ch` zone from Cloudflare.
4. Verify: `dig NS hydrosolutions.ch +short` returns Hostpoint nameservers; `dig
   hydrosolutions.ch +short` returns the correct A record.

The main website is at risk only during the propagation window. Total rollback time
is bounded by the NS record TTL at Hostpoint (typically 300–3600 s).

**Exit**: `dig NS hydrosolutions.ch +short` returns Cloudflare nameservers AND
`dig hydrosolutions.ch +short` returns the same IP as before migration.

### A2 — Create tunnel `sapphire-staging`

1. Cloudflare Zero Trust dashboard → Tunnels → Create a tunnel.
2. Name: `sapphire-staging`. Type: Cloudflared.
3. Download the tunnel credential JSON (`<uuid>.json`).
4. Copy to `./secrets/cloudflared_credentials.json` on the Mac mini. `chmod 600`.
   This file must **never** be committed to git (already covered by `secrets/`
   in `.gitignore`).

**Credential rotation procedure** (document in runbook and `docs/standards/security.md`):

- Cloudflare Zero Trust → Tunnels → `sapphire-staging` → Rotate secret.
- Download the new credential JSON.
- Replace `./secrets/cloudflared_credentials.json` on the Mac mini.
- `chmod 600` on the new file.
- `docker compose -f docker-compose.yml -f docker-compose.macmini.yml restart cloudflared`
- Verify: `docker compose logs cloudflared` shows a successful connection.
- Proposed rotation cadence: annual, or immediately on any suspected leak.

**Exit**: credential JSON on disk, `chmod 600`, not committed.

### A3 — DNS record for `sapphire-staging.hydrosolutions.ch`

In the Cloudflare DNS editor for `hydrosolutions.ch`:

- Type: `CNAME`
- Name: `sapphire-staging`
- Target: `<tunnel-uuid>.cfargotunnel.com`
- Proxy status: **Proxied** (orange cloud — required for Access policies to apply)

**Verification**: `dig sapphire-staging.hydrosolutions.ch +short` returns a Cloudflare
anycast IP (not the Mac mini IP — that is expected; the tunnel is the ingress path).

### A4 — `scripts/cloudflared/config.yml`

Populate the config file (skeleton present from Plan 046):

```yaml
tunnel: <tunnel-uuid>
credentials-file: /run/secrets/cloudflared_credentials.json

ingress:
  - hostname: sapphire-staging.hydrosolutions.ch
    service: http://api:8000
  - service: http_status:404
```

This file is bind-mounted read-only at `/etc/cloudflared/config.yml` inside the
`cloudflared` container (see C2). Because the container runs with `read_only: true`,
the mount must be explicit — the default path `/etc/cloudflared/config.yml` is not
writable. The `volumes:` entry in C2 handles this.

---

## Stream B — Entra ID SSO + Access policies + OTP

Depends on **A1 complete** (zone active in Cloudflare).

### B1 — Entra ID app registration

In Azure portal → App registrations → New registration:

1. Name: `SAPPHIRE Staging Access`
2. Supported account types: Accounts in this organizational directory only
   (`@hydrosolutions.ch` tenant).
3. Redirect URI: obtain from Cloudflare Zero Trust → Settings → Authentication →
   Add Microsoft Azure AD → the redirect URI Cloudflare specifies (typically
   `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`).
4. After registration: note the **Application (client) ID** and **Directory (tenant) ID**.
5. Under Certificates & secrets → New client secret: set expiry 24 months; record the
   secret value immediately (shown once).

### B2 — OIDC identity provider in Cloudflare Zero Trust

Zero Trust → Settings → Authentication → Add provider → Azure AD / Entra ID.

Inputs: Client ID, Client Secret, Tenant ID (from B1). Enable **Proof Key for Code
Exchange (PKCE)**. Test the connection using Cloudflare's built-in test button before
proceeding.

**Dry run (mandatory before B3)**: Add a single test `@hydrosolutions.ch` account to the
Cloudflare test flow. Confirm the Entra OIDC round-trip completes without errors (redirect
URI mismatch, tenant restriction, scope errors are all common). Only continue to B3 after
this succeeds.

### B3 — Access application

Zero Trust → Access → Applications → Add application → Self-hosted.

- Name: `SAPPHIRE staging`
- Application domain: `sapphire-staging.hydrosolutions.ch`
- URL pattern: `https://sapphire-staging.hydrosolutions.ch/*`
- Session duration: set at policy level (see B4, B5)
- **Settings → Binding cookie: ENABLED** — required for external-viewer IP pinning (D5).

### B4 — Policy: Internal team

Attach to the `SAPPHIRE staging` application:

- Policy name: `Internal team`
- Action: Allow
- Rules:
  - Emails ending in: `@hydrosolutions.ch`
  - Identity provider: Entra ID (enforces SSO, not OTP, for this policy)
- Session duration: 24 hours

### B5 — Policy: External viewers

Attach to the `SAPPHIRE staging` application:

- Policy name: `External viewers`
- Action: Allow
- Rules:
  - Authentication method: One-time PIN
  - Emails: _(empty list — populate when DHM or reviewers are onboarded)_
- Session duration: 8 hours
- Binding cookie: confirmed enabled at application level (B3)

**Onboarding a new external viewer** (30-second procedure, document in runbook):

1. Zero Trust → Access → Applications → `SAPPHIRE staging` → Policies →
   `External viewers` → Edit.
2. Add the new email address to the email list.
3. Save. The person can now visit `https://sapphire-staging.hydrosolutions.ch`,
   enter their email, receive an OTP, and access the API.

### B6 — Service token for watchdog

Zero Trust → Access → Service tokens → Create service token.

- Name: `sapphire-staging-watchdog`
- Token duration: non-expiring (or 1 year with a renewal reminder)
- Record `CF-Access-Client-Id` and `CF-Access-Client-Secret` values.

Store as files on the Mac mini:
- `./secrets/cf_access_client_id` (`chmod 600`)
- `./secrets/cf_access_client_secret` (`chmod 600`)

These are consumed by `sapphire_flow.ops.watchdog` for the external probe (C3).

Attach the service token to the `SAPPHIRE staging` Access application:

Zero Trust → Access → Applications → `SAPPHIRE staging` → Policies →
Add policy → Action: Service Auth → Service token: `sapphire-staging-watchdog`.

**Service token rotation** (annual or on suspected leak):

1. Zero Trust → Access → Service tokens → `sapphire-staging-watchdog` → Rotate.
2. Update `./secrets/cf_access_client_id` and `./secrets/cf_access_client_secret`
   on the Mac mini.
3. `docker compose ... restart` — watchdog reads the files at startup; no code change.

### B7 — Dry run: both policy paths

Before team-wide rollout:

1. **Internal team path**: visit `https://sapphire-staging.hydrosolutions.ch/api/v1/health`
   from a team laptop on a non-office network (e.g., phone hotspot). Confirm Entra ID
   login redirect, M365 auth, return to API. Confirm session lasts 24h.
2. **External viewer path**: add a personal test email to the `External viewers` whitelist.
   Visit the URL. Confirm OTP email arrives, code accepted, API reachable. Then attempt to
   copy the session cookie and replay it from a different IP (e.g., switch from WiFi to
   cellular). Confirm the session is rejected — binding cookie working.
3. **Unauthenticated**: confirm an unauthenticated request is redirected to the Cloudflare
   Access login page (HTTP 302), not to the API.

---

## Stream C — Compose overlay + hardening

Depends on **A2, A3, A4 complete**. Stream C can begin in parallel with Stream B after A4.

### C1 — Remove `prefect-server` from `frontend` network

In `docker-compose.yml`, change:

```yaml
  prefect-server:
    networks: [backend, frontend]
```

to:

```yaml
  prefect-server:
    networks: [backend]
```

This is a one-line change. Verify that:
- `api` is on `[backend, frontend]` — it reaches Prefect over `backend`. ✓ (confirmed in
  current `docker-compose.yml`.)
- `prefect-worker` is on `[backend]` — it reaches Prefect over `backend`. ✓
- `init` is on `[backend]` — it reaches Prefect over `backend`. ✓

No functional impact. Exits `prefect-server` from any network that `cloudflared`
will join (`frontend`).

**Verification** (Exit Gate 3): after deploying, from the `cloudflared` container:
```bash
docker compose exec cloudflared wget -qO- http://prefect-server:4200/api/health
```
Must time out or return a connection refused error — `prefect-server` is not
reachable from `cloudflared`'s network.

### C2 — `docker-compose.macmini.yml` — `cloudflared` service (activate and harden)

The skeleton from Plan 046 is populated with the following:

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:2025.4.0   # pin to a specific version tag
    command: tunnel run
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp
    user: "65532:65532"    # cloudflared ships with a non-root user at this UID
    restart: unless-stopped
    networks:
      - frontend
    volumes:
      - ./scripts/cloudflared/config.yml:/etc/cloudflared/config.yml:ro
    secrets:
      - cloudflared_credentials
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

secrets:
  cloudflared_credentials:
    file: ./secrets/cloudflared_credentials.json
```

Key notes:
- **`volumes:` config bind-mount is required** because `read_only: true` prevents
  `cloudflared` from writing to the default location `/etc/cloudflared/`. The explicit
  bind-mount provides the config at the expected path without a writable filesystem.
- **`restart: unless-stopped`** covers container OOM/panic. Cloudflare edge
  disconnects are handled by `cloudflared`'s internal reconnection logic — this
  is not what `restart:` solves. Both mechanisms coexist independently.
- **Image tag is pinned** (not `latest`) to avoid surprise upgrades. Update annually
  or when a security advisory requires it.
- **`networks: [frontend]` only** — `cloudflared` can reach `api:8000` (which is on
  `[backend, frontend]`) but not `postgres:5432` or `prefect-server:4200` (both
  `[backend]` only after C1).

**Start command**:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d
```

**Verify tunnel connected**:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml logs cloudflared
# should contain: "Registered tunnel connection"
```

Then verify name resolution from inside the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml exec cloudflared \
    wget -qO- http://api:8000/api/v1/health
# must return {"status":"ok"}
```

### C3 — Watchdog external probe

Extend `src/sapphire_flow/ops/watchdog.py` to add a second probe path alongside the
existing `localhost:8000` probe:

- Probe URL: `https://sapphire-staging.hydrosolutions.ch/api/v1/health`
- Auth headers: `CF-Access-Client-Id` and `CF-Access-Client-Secret`, read from
  `./secrets/cf_access_client_id` and `./secrets/cf_access_client_secret`.
- Separate structlog event: `pipeline.external_probe_completed` with fields
  `url`, `status_code`, `duration_ms`, `probe_type: "external"`.
- Failure behavior: same as the local probe — post to Slack webhook with host,
  timestamp, HTTP status, and the public URL as the deep-link.
- The two probes (local and external) are independent: a failure of either
  triggers a Slack alert. Both emit structured log events.

The `./secrets/cf_access_client_id` and `./secrets/cf_access_client_secret` files are
read outside any container (the watchdog runs as a launchd periodic job on the macOS
host via `uv run python -m sapphire_flow.ops.watchdog`). This pattern is consistent
with the existing `./secrets/slack_webhook_url` handling described in Plan 046 C5.

### C4 — `docs/standards/security.md` amendments

The following additions are required (Plan 046 C5 added `slack_webhook_url` and
`cloudflared_credentials` to the secrets list and the edge-TLS note to Network policy;
Plan 049 adds the service token secrets and the rotation procedure):

**§Secrets management → Required secrets** — add:

```
- `cloudflared_credentials` — tunnel credential JSON (staging overlay only).
  Rotation: Cloudflare Zero Trust → Tunnels → sapphire-staging → Rotate secret;
  replace file; chmod 600; restart cloudflared container. Cadence: annual or on
  suspected leak.
- `cf_access_client_id` / `cf_access_client_secret` — Cloudflare Access service
  token for the watchdog external probe (non-interactive client auth). Not Docker
  secrets; read by the watchdog host process from `./secrets/` on the Mac mini.
  Rotation: Zero Trust → Access → Service tokens → sapphire-staging-watchdog →
  Rotate; replace files; chmod 600. Cadence: annual or on suspected leak.
```

**§Network policy** — amend the edge-TLS staging note (present from Plan 046) to
additionally reference the service-token probe:

```
Staging MAY use edge-terminated TLS (Cloudflare terminates at edge; traffic on
cloudflared → api:8000 is plaintext inside the Docker network). Production MUST
terminate TLS on the application host. The watchdog external probe authenticates
via Cloudflare Access service token (CF-Access-Client-Id / CF-Access-Client-Secret
headers) — this token is for non-interactive service-to-service auth and consumes
one user seat against the 50-seat free-tier cap.
```

---

## Stream D — Validation

Depends on **Stream B complete** and **Stream C complete**.

### D1 — Team laptop off-network: internal SSO path

From a laptop connected to a non-office network (phone hotspot or home WiFi):

```
GET https://sapphire-staging.hydrosolutions.ch/api/v1/health
```

Expected: Cloudflare Access redirects to M365 login → auth completes → API returns
`{"status":"ok"}`. Record the HTTP status code and round-trip latency.

**Exit**: health endpoint reachable via Entra SSO from off-network. Latency acceptable
(no hard threshold; note baseline for D6 report).

### D2 — External viewer OTP path + binding cookie verification

1. Add a test email address (outside `@hydrosolutions.ch`) to the `External viewers`
   policy whitelist.
2. Visit `https://sapphire-staging.hydrosolutions.ch/api/v1/health` from a browser.
   Confirm OTP email arrives, code accepted, API reachable.
3. **Binding cookie test**: copy the `CF_Authorization` cookie from the browser's
   DevTools. Import it into a second browser on a different IP (switch from WiFi to
   cellular, or use a VPN exit node). Attempt to access the URL with the copied cookie.
   The session must fail — Cloudflare Access rejects the cookie because the IP has
   changed. Confirm 302 redirect back to the OTP login page.

**Exit**: OTP flow works; copied session from a different IP is rejected.

### D3 — Tunnel container restart recovery

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml restart cloudflared
```

Watch logs:

```bash
docker compose logs -f cloudflared
```

Expected: `cloudflared` reconnects to the Cloudflare edge and logs
`"Registered tunnel connection"` within 60 seconds of restart. The API remains
reachable (requests may fail during the ~10 s reconnection window — acceptable).

**Exit**: tunnel reconnects within 60 s; API reachable within 90 s of restart.

### D4 — Simulated edge disconnect (cloudflared internal reconnection)

Block `cloudflared`'s outbound connectivity to Cloudflare's edge for 5 minutes
using macOS packet filter:

```bash
# Get cloudflared container IP
CF_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    $(docker compose ps -q cloudflared))

# Block outbound from the container's subnet to cloudflare.com
sudo pfctl -e
echo "block out from $CF_IP to any" | sudo pfctl -f -

# Wait 5 minutes
sleep 300

# Re-enable connectivity
sudo pfctl -d
```

During the 5 minutes: observe `cloudflared` logs for reconnection attempts (expected:
exponential backoff). After re-enabling: confirm `cloudflared` reconnects without a
container restart. The `restart: unless-stopped` policy must NOT have triggered — the
container must be the same PID as before.

**Exit**: tunnel reconnects after the simulated outage without a container restart;
`cloudflared` internal reconnection logic handled the disconnect.

### D5 — 7-day stability run

Continue from the existing Plan 046 D4 unattended run (or restart the stability
window if needed). Over 7 days:

- Watchdog external probe (`pipeline.external_probe_completed`) must not flap
  (defined as: no more than 3 consecutive external probe failures per day).
- Local watchdog probe continues as per Plan 046.
- Record Cloudflare Access log events (Zero Trust → Logs → Access requests) to
  verify expected login activity.
- Free-tier seat count: Zero Trust → Settings → Billing → Active seats. Record the
  count; confirm it is within the 50-seat free-tier cap.

**Seat monitoring note**: every unique authenticated email (Entra ID SSO or OTP)
consumes one seat against the 50-user free-tier cap. Each plan execution cycle is
likely to involve fewer than 10 seats (core team + a few external reviewers). If the
project expands, the upgrade path is Cloudflare Zero Trust Teams (pay-as-you-go per
seat above 50). Export the Access + tunnel configuration before any plan changes so
the setup can be recreated quickly.

**Exit**: 7-day run with no external-probe flapping; seat count within cap.

### D6 — Go/no-go report

Commit `docs/deployment/cloudflare-public-url-YYYY-MM-DD.md` with:

- Verification results for D1–D5.
- Baseline latency: Cloudflare edge → Mac mini → response (from D1).
- Seat count at end of stability period.
- Any incidents and how they were resolved.
- Outstanding issues → follow-up plans.
- **Go / no-go**: the public URL is / is not ready for team use and external viewer
  onboarding.

---

## Dependency graph

```json
{
  "stream-a": {
    "tasks": ["A0", "A1", "A2", "A3", "A4"],
    "sequential": true,
    "depends_on": ["Plan 046 IN_PROGRESS (staging host operational)"]
  },
  "stream-b": {
    "tasks": ["B1", "B2", "B3", "B4", "B5", "B6", "B7"],
    "sequential": true,
    "depends_on": ["A1"]
  },
  "stream-c": {
    "tasks": ["C1", "C2", "C3", "C4"],
    "parallel": "C1 and C2 first (sequential), then C3 and C4 in parallel",
    "depends_on": ["A2", "A3", "A4"]
  },
  "stream-d": {
    "tasks": ["D1", "D2", "D3", "D4", "D5", "D6"],
    "sequential": true,
    "depends_on": ["stream-b", "stream-c"]
  }
}
```

Critical path: `A0 → A1 → B-tail → D6`. Stream C is the shorter arm of the
fork after A4 and can complete before Stream B finishes.

---

## Files to create

| Path | Stream | Purpose |
|---|---|---|
| `docs/plans/049-cloudflare-public-url.md` | — | This plan |
| `scripts/cloudflared/config.yml` | A4 | Tunnel ingress config (populate skeleton from Plan 046) |
| `docs/deployment/cloudflare-public-url-runbook.md` | B5, C3 | How to add/remove external viewers; service token rotation; seat monitoring |
| `docs/deployment/cloudflare-public-url-YYYY-MM-DD.md` | D6 | Go/no-go validation report |

---

## Files to modify

| Path | Stream | Change |
|---|---|---|
| `docker-compose.yml` | C1 | Remove `prefect-server` from `frontend` network (one-line change: `networks: [backend, frontend]` → `networks: [backend]`) |
| `docker-compose.macmini.yml` | C2 | Activate and harden `cloudflared` service: pinned image tag, `user: "65532:65532"`, `cap_drop: [ALL]`, `read_only: true`, `tmpfs: [/tmp]`, explicit config bind-mount at `/etc/cloudflared/config.yml:ro`, `cloudflared_credentials` Docker secret, `networks: [frontend]`; add `cloudflared_credentials` to `secrets:` block |
| `src/sapphire_flow/ops/watchdog.py` | C3 | Add external probe path: reads `CF-Access-Client-Id` / `CF-Access-Client-Secret` from `./secrets/cf_access_client_id` and `./secrets/cf_access_client_secret`; probes `https://sapphire-staging.hydrosolutions.ch/api/v1/health`; emits `pipeline.external_probe_completed` structlog event |
| `docs/standards/security.md` | C4 | §Secrets management: add `cloudflared_credentials` rotation procedure, `cf_access_client_id` / `cf_access_client_secret` service token secrets; §Network policy: extend edge-TLS note with service-token probe and seat-cap reference |

---

## Exit gates

1. `dig NS hydrosolutions.ch +short` returns Cloudflare nameservers AND
   `dig hydrosolutions.ch +short` returns the same A record as before migration
   (D1 in Stream A — naming collision avoided with Stream D; called A1-post-verification).
2. Team laptop off-network can reach `https://sapphire-staging.hydrosolutions.ch/api/v1/health`
   via Entra ID SSO (M365 login) without any setup assistance — verified by the
   hydrologist or ML expert (not the IT specialist/implementer).
3. External OTP flow works for a test email outside `@hydrosolutions.ch`; session
   cookie from a different IP is rejected (binding cookie verified).
4. `docker compose exec cloudflared wget -qO- http://prefect-server:4200/api/health`
   times out or returns connection refused — `prefect-server` is not reachable from
   `cloudflared`'s network (`frontend` only) after C1.
5. Watchdog external probe (`pipeline.external_probe_completed`) runs without
   flapping for 7 days; Slack alert fires on a simulated external probe failure.
6. `docs/standards/security.md` amendments committed (C4).
7. Seat count confirmed within the 50-seat free-tier cap at end of D5.
8. Go/no-go report committed (D6).
9. User saves memory entries: "staging public URL lives at
   `sapphire-staging.hydrosolutions.ch` behind Cloudflare Tunnel + Access",
   "`hydrosolutions.ch` DNS is now authoritative at Cloudflare (full-zone migration
   from Hostpoint)", "Entra ID is the internal SSO for staging", "external viewers use
   Cloudflare Access OTP with binding cookie".

---

## Deferred to follow-up plans

- Seat monitoring dashboard (Cloudflare Analytics → automated alerting at 80% cap).
- Separate tunnel / Access application for Prefect UI (currently LAN-only via SSH
  tunnel; useful if the hydrologist or ML expert needs remote access to Prefect).
- Service token expiration automation (auto-rotate annually via a Prefect scheduled flow).
- Migration of tunnel to AWS / hydromet for Nepal production (different host, same
  Access policies — drop the tunnel, update DNS, keep Access).
- Cloudflare Access audit log export to structured storage for compliance.

---

## Risks

| Risk | Mitigation |
|---|---|
| **Partial-zone confusion**: CNAME-only / NS-delegation approach silently disables Access auth enforcement | D1 uses full-zone migration. A1 includes pre/post `dig` verification. Exit Gate 1 enforces NS check before Stream B proceeds. |
| **DNS propagation delay breaks main website during migration** | A1 includes before/after `dig hydrosolutions.ch +short` verification. Rollback procedure is documented inline with estimated propagation times. The main website is at risk only during the propagation window (minutes to hours). |
| **Prefect API accessible from `cloudflared` if C1 is skipped** | Exit Gate 4 explicitly verifies inaccessibility from the `cloudflared` container before go/no-go. |
| **Entra ID OIDC surprise** (app-registration scope mismatch, redirect URI quirk, tenant restriction) | B2 mandatory dry run with one test user before policy is attached. Fallback: use email-OTP for internal team too (degraded UX but unblocks progress). |
| **OTP session sharing** | Binding cookie enabled at application level (B3). D2 explicitly verifies cookie rejection from a different IP. |
| **Tunnel credential leak** | Rotation procedure documented in A2 and C4. Credential is scoped to this tunnel only (not a Cloudflare account-level token). `./secrets/` is gitignored. |
| **Free-tier 50-seat cap** | D5 monitors seat count. Upgrade path documented (Zero Trust Teams, pay-per-seat above 50). Export tunnel + Access config so recreation is fast if tier changes. |
| **Cloudflare free-tier policy changes** | Pin `cloudflared` image version. Export Access + tunnel configuration. If worst case, fallback is Tailscale in a follow-up plan. |
| **Edge-terminated TLS** | Acceptable for staging (v0 API has no user credentials or sensitive write ops from external callers). Documented in `docs/standards/security.md` §Network policy. Not acceptable for production — noted explicitly. |
| **`cloudflared` container `read_only: true` with default config path** | Explicit config bind-mount at `/etc/cloudflared/config.yml:ro` in C2. No writable filesystem needed for config. |
| **Service token expiration** | Propose non-expiring or 1-year token with a calendar reminder. Annual rotation procedure documented in B6 and C4. |

---

## Open questions

Not blocking DRAFT → READY:

1. **Tunnel credential rotation cadence**: proposed annual (or immediate on suspected
   leak). User to confirm or adjust.
2. **Service token duration**: proposed 1 year (vs. non-expiring). Non-expiring is
   simpler; 1 year enforces hygiene. User to confirm preference.
3. **External viewer email list at launch**: DHM email addresses are not yet known.
   Plan confirms the whitelist starts empty; IT specialist adds addresses when DHM
   is ready to connect.
4. **Cloudflare Access seat budget**: proposed soft alert at 40/50 seats (80%).
   User to confirm threshold before D5 monitoring begins.
