# SAPPHIRE Flow — IT & Infrastructure Guide (Nepal Deployment)

**Audience**: DHM IT department — system administrators responsible for hosting and operating SAPPHIRE Flow.
**Document version**: April 2026

### Deployment stages

| Stage | Environment | Duration | Purpose |
|-------|-------------|----------|---------|
| Testing & validation | AWS (managed by SAPPHIRE team) | ~6–12 months | Model training, pipeline validation, skill evaluation using DHM data. DHM accesses the system remotely for review and feedback. |
| Production | DHM VM (Ubuntu, on-site or DHM-managed) | Permanent | Full operational deployment under DHM's control. |

During the AWS stage, the SAPPHIRE team manages infrastructure and security. The sections below describe the requirements for the production DHM deployment which will only take place in Q1 2028 (earlier if project progress is faster).

---

## 1. System Overview

SAPPHIRE Flow ingests processed weather and snow forecast data from the Sapphire Data Gateway (which sources ECMWF ensemble weather forecasts and SnowMapper snow forecasts) and river station observations from DHM, runs hydrological models to produce probabilistic water level forecasts, checks flood thresholds to raise alerts, and serves results via a REST API. It runs as a set of Docker containers on a single Ubuntu VM. All external systems — including the DHM forecast dashboard and other government agencies — connect to this API to retrieve forecasts and alerts.

```
[Sapphire Data Gateway]  ──→ ┌─────────────────────────────────────┐
  (ECMWF + SnowMapper)       │         SAPPHIRE Flow (VM)          │
[DHM Station Data]   ──→     │  ┌──────────┐  ┌──────────────────┐ │ ──→ [REST API :443]
                             │  │ Database │  │ Worker processes │ │ ──→ [Flood alert webhooks]
                             │  └──────────┘  └──────────────────┘ │
                             │  ┌──────────┐  ┌──────────────────┐ │
                             │  │   API    │  │   Scheduler      │ │
                             │  └──────────┘  └──────────────────┘ │
                             │  ┌──────────┐                       │
                             │  │  Proxy   │  (Caddy, handles TLS) │
                             │  └──────────┘                       │
                             └─────────────────────────────────────┘
```

The system recovers automatically from crashes and reboots via a systemd service. No manual intervention is required for normal restarts.

---

## 2. Infrastructure Requirements

### VM Specifications (for Runoff Forecasting alone)

| Resource | Minimum | Notes |
|---|---|---|
| OS | Ubuntu 24.04 LTS | 26.04 LTS support will be tested after its release but support cannot be guaranteed at this point |
| CPU | 16 cores | 16 cores recommended |
| RAM | 32 GB | 32 GB recommended |
| Disk | 1 TB SSD | Reviewed quarterly; plan upgrade to 2 TB before reaching 70% utilization |
| Network | Stable internet | Outbound HTTPS required (see below) |

Please note that backup volumes are not included in the storage resources needs for the VM running the runoff forecasting.

Resource estimates for all separate modules being developed and deployed in this project have been shared in January 2026 for the first time and are re-shared together with this document.

### Software Requirements

- **Docker Engine** (latest stable)
- **Docker Compose v2** (included with Docker Engine)
- No database software to install separately — the database runs inside Docker

### Network Requirements

| Direction | Destination | Port | Purpose |
|---|---|---|---|
| Outbound | Sapphire Data Gateway | 443 (HTTPS) | Weather forecasts (ECMWF) and snow forecasts (SnowMapper) |
| Outbound | Alert webhook consumers | 443 (HTTPS) | Flood alert webhook delivery |
| Inbound | DHM dashboard, other authorized consumers | 443 (HTTPS) | REST API access |
| Inbound | Operations team | 22 (SSH) | Server administration |

**Firewall note**: Only the two inbound ports (443 and 22) need to be opened in the VM's firewall. The outbound connections are initiated by the application to different remote servers — multiple outbound destinations using port 443 is standard HTTPS and does not cause any port conflict. Most firewalls allow outbound HTTPS by default.

All internal service communication happens inside Docker's private network and is not reachable from outside the VM.

---

## 3. Service Topology

### Docker Services

| Service | What it does | Image |
|---|---|---|
| `postgres` | Database — stores forecasts, observations, alerts, user accounts | `postgis/postgis:16-3.4` |
| `pgbouncer` | Database connection pool — manages efficient DB access under load | `pgbouncer/pgbouncer` |
| `prefect-server` | Job scheduler — tracks pipeline runs, schedules, and run history | `prefecthq/prefect:3-python3.11` |
| `prefect-worker-ops` | Runs daily forecast pipelines and data ingestion | custom (`sapphire-flow`) |
| `prefect-worker-hindcast` | Runs historical simulation jobs | custom (`sapphire-flow`) |
| `prefect-worker-training` | Runs model training jobs (resource-intensive, runs one at a time) | custom (`sapphire-flow`) |
| `api` | REST API — serves forecasts, alerts, and observations to external consumers | custom (`sapphire-flow`) |
| `caddy` | Reverse proxy — handles TLS certificates and HTTPS termination | `caddy:2` |
| `init` | One-time startup job — sets up the database on first boot (runs once, then exits) | custom (`sapphire-flow`) |

### Named Volumes (persistent data)

| Volume name | What it stores | Backed up |
|---|---|---|
| `pg_data` | All database data (forecasts, observations, users, alerts) | Yes — daily |
| `model_artifacts` | Trained hydrological model files | Yes — daily |
| `cold_storage` | Long-term historical data archive (Parquet files) | Yes — daily |
| `prefect_data` | Scheduler run history and logs | No — reconstructible |

Backup volumes are extra, see backup section further below.

### Startup Order

Services start in dependency order. Docker Compose handles this automatically.

```
postgres ──→ pgbouncer ──→ api ──→ caddy
    │                 ↗
    └──→ prefect-server ──→ prefect-worker-ops
                       ──→ prefect-worker-hindcast
                       ──→ prefect-worker-training

    init  (runs first, exits after database setup)
```

All services are configured with `restart: unless-stopped` — they restart automatically if they crash. The `init` service runs only once and does not restart.

### Crash and Power Failure Behaviour

**PostgreSQL and power cuts**: PostgreSQL uses write-ahead logging (WAL) with `fsync` enabled by default. If power is cut mid-write, the database recovers automatically on restart by replaying the WAL — no data corruption occurs. The Docker volume mount uses default filesystem settings (ext4 with journaling), which provides additional protection. **A UPS (uninterruptible power supply) is strongly recommended** for the VM to allow clean shutdown during extended power cuts, but the system is designed to survive hard power loss without corruption.

**PostgreSQL crash while pipelines are running**: If the database container crashes or restarts while a forecast pipeline is in progress, the pipeline run will fail with a database connection error. Prefect marks the run as failed. The pipeline does not leave orphaned state — each pipeline run is atomic at the database level (individual transactions, not one large transaction). On the next scheduled cycle, the pipeline runs normally. No manual cleanup is required. A system or model admin can manually re-trigger a failed pipeline run.

**Cold restart after power failure**: After the VM reboots, the systemd service starts all containers automatically. Typical time from boot to healthy API: **2–3 minutes** (PostgreSQL recovery + service startup). If the `pg_data` volume is intact (normal case), no manual intervention is needed. The only scenario requiring manual intervention is physical disk failure — which requires the full recovery procedure (section 7).

### Health Checks

| Service | Health check | Check interval |
|---|---|---|
| `postgres` | Internal DB readiness check | Every 10 seconds |
| `pgbouncer` | Connection pool readiness check | Every 10 seconds |
| `prefect-server` | `GET /api/health` returns OK | Every 15 seconds |
| `api` | `GET /api/v1/health` returns OK | Every 15 seconds |
| `caddy` | TCP connection on port 443 | Every 10 seconds |

Worker containers do not have Docker-level health checks. Their health is monitored by the application's internal watchdog pipeline (Flow 4), which reports status at `/api/v1/health/detail`.

---

## 4. Security — Responsibility Split

Security for this deployment is a shared responsibility. The table below defines the boundary clearly.

### What SAPPHIRE Flow handles (application level)

- Role-based access control — each user sees only what their role permits
- API key management for external consumers (scoped per agency)
- All passwords and secrets stored encrypted — never in plain text
- Database encrypted at rest
- Full audit log of all login events, forecast changes, and model promotions
- Rate limiting and brute-force protection on all API endpoints

### What DHM IT must configure (infrastructure level)

These are the IT team's responsibility for the production DHM deployment. SAPPHIRE Flow does not implement or verify any of these. During the AWS testing stage, the SAPPHIRE team handles these items; DHM IT should have them ready before the production handover.

| Task | Why | Priority |
|---|---|---|
| **SSH: key-only authentication** — disable password-based SSH login | Password-based SSH is the most common attack vector for internet-facing servers | Critical |
| **SSH: IP allowlisting** — restrict SSH access to known office/VPN IP ranges only. Ideally SSH is only accessible via VPN, not directly from the internet | Prevents remote brute-force attacks even if a key is compromised | Critical |
| **Firewall** — open only port 443 (HTTPS) and port 22 (SSH). Block all other ports at the OS level | Reduces attack surface | Critical |
| **fail2ban** — block IPs after repeated failed SSH attempts | Stops automated brute-force tools | High |
| **Full disk encryption (LUKS)** — encrypt the server's disk | Protects data if a physical disk is stolen | High |
| **OS security patches** — enable unattended security updates | Keeps the OS patched against known vulnerabilities | High |
| **`auditd`** — OS-level logging of SSH sessions, `sudo` usage, and file access | Detects unauthorized access to the server | High |
| **`./secrets/` permissions** — `chmod 600`, owned by root | Prevents other OS users from reading application secrets | High |
| **`pgaudit` extension** — PostgreSQL query logging | Detects direct database access that bypasses the application | Recommended |

### Secrets Managed by SAPPHIRE Flow

Secrets are stored as Docker secrets — mounted as files inside containers, never passed as environment variables in production. The secrets file directory (`./secrets/`) on the VM must be owned by root and readable only by root (`chmod 600`).

Alert delivery is webhook-only; email and SMS integrations are out of scope for this release.

| Secret name | What it is |
|---|---|
| `db_password` | PostgreSQL database password |
| `secret_key` | JWT signing key (session tokens) |
| `totp_encryption_key` | Encryption key for two-factor authentication seeds |
| `sapphire_dg_api_key` | Sapphire Data Gateway API key |
| `backup_repo_password` | Planned restic repository password; not used by the interim Plan 340 protected evidence bundle (see §7) |

**Secret rotation schedule**: `db_password` and `secret_key` annually, or immediately if compromise is suspected. Rotation requires a coordinated restart of all containers. The SAPPHIRE team coordinates rotation with DHM IT.

---

## 5. Deployment & Operations

**Implementation status**: The procedures in this section describe the target production deployment. The system service, host-level watchdog, upgrade procedure and init container bootstrap do not exist yet for the Central Asia deployment and will be built during the AWS testing phase before DHM handover. Actual workflow may change slightly when implemented.

### First Boot — Step by Step

Run these steps once when deploying on a fresh VM.

1. Install Docker Engine and Docker Compose v2 (follow Docker's official Ubuntu installation guide)
2. Create the deployment directory: `mkdir -p /opt/sapphire && cd /opt/sapphire`
3. Copy the deployment package (provided by the SAPPHIRE team) into `/opt/sapphire/`
4. Create the secrets directory and populate all secrets files:
   ```bash
   mkdir -p /opt/sapphire/secrets
   chmod 700 /opt/sapphire/secrets
   # Write each secret to its file, e.g.:
   echo "your-db-password" > /opt/sapphire/secrets/db_password
   chmod 600 /opt/sapphire/secrets/*
   ```
5. Pull all Docker images: `docker compose pull`
6. Start the stack: `docker compose up -d`
7. The `init` container runs automatically and:
   - Waits for the database to be ready
   - Creates the database schema and required extensions (skipped if already present)
   - Runs any pending database migrations (upgrades the schema to the current version)
   - Creates internal database users with correct permissions (skipped if already present)
   - Loads initial configuration (alert thresholds, station definitions) from `config.toml`
   - Exits when complete

   **Idempotency**: The `init` container is safe to re-run on an existing database — this is expected during upgrades (step 3 of the upgrade procedure). Schema creation and user setup are skipped if already present. Database migrations run only if new migrations exist. Configuration loading uses **upsert** semantics: new entries from `config.toml` are added, existing entries are updated if the config has changed, and entries that exist in the database but are absent from `config.toml` are left untouched (never deleted). This means re-running `init` will not overwrite station configurations, thresholds, or user accounts that were modified through the dashboard after initial deployment.
8. Verify the system is running: `curl https://localhost/api/v1/health` — should return `{"status": "ok"}`
9. Create the first admin account (see §6 — Bootstrap)
10. Install the systemd service for auto-start on reboot (see below)

### Auto-Restart on Reboot (systemd)

Create the file `/etc/systemd/system/sapphire-flow.service` with the following content, then enable it:

```ini
[Unit]
Description=SAPPHIRE Flow
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
WorkingDirectory=/opt/sapphire
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable sapphire-flow
systemctl start sapphire-flow
```

After this, the entire SAPPHIRE stack starts automatically whenever the VM boots.

### Upgrade Procedure

When a new release is provided by the SAPPHIRE team:

1. Pull the new images: `docker compose pull`
2. Stop the worker containers gracefully: `docker compose stop prefect-worker-ops prefect-worker-training`
3. Run database migrations: `docker compose run --rm init`
4. Restart all services: `docker compose up -d`

Rollback: if a release causes problems, restore from the most recent backup and redeploy the previous image version. The SAPPHIRE team will advise on the previous version tag.

### Host-Level Watchdog

An independent cron job on the VM polls the health endpoint every 5 minutes and sends an alert if the API is unreachable. This watchdog runs outside Docker — it will catch failures that affect the entire stack (including Docker crashes or VM resource exhaustion).

The file `/etc/cron.d/sapphire-watchdog` is installed during deployment:

```
*/5 * * * * root curl -sf http://localhost:8000/api/v1/health || \
    /opt/sapphire/scripts/alert.sh "SAPPHIRE health check failed"
```

The `alert.sh` script runs independently of the application — it uses a simple `curl` webhook call from the host OS to notify DHM's systems. It works even when the entire Docker stack is down, as long as the VM has outbound internet connectivity.

### Layered Monitoring — Who Watches the Watchdog

SAPPHIRE uses two independent monitoring layers to avoid a single point of failure:

| Layer | Runs inside Docker? | What it monitors | Catches |
|---|---|---|---|
| **Flow 4 (application watchdog)** | Yes — as a Prefect pipeline | Data freshness, worker health, forecast timeliness, disk usage | Application-level problems (stale data, failed pipelines, slow models) |
| **Host-level cron watchdog** (above) | No — runs on the VM OS | Whether the API health endpoint responds | Infrastructure-level problems (Docker crash, Prefect crash, PostgreSQL crash, VM resource exhaustion) |

If Prefect itself crashes, Flow 4 stops running — but the host-level cron watchdog continues independently and will detect the failure within 5 minutes (the API health endpoint reports `degraded` or `down` when workers are unresponsive). This layered design ensures that no single component failure goes undetected.

**A third layer exists on the realized mac-mini staging deployment** (not this target-production design, but worth noting as the answer to "who watches the watchdog" taken to its conclusion): a dead-man's switch (Plan 163). The host-level watchdog itself is a launchd LaunchAgent, which dies with the login session — a dead watchdog is indistinguishable from a healthy, silent one without an independent signal. After every tick that completes and persists, it POSTs an off-box heartbeat to an external service (Healthchecks.io), which alerts via Slack + email when the heartbeat stops. See `docs/plans/archive/163-watchdog-deadman-and-http-hardening.md`.

Please note, the name Flow 4 refers to the naming that will become clear in a separate document (currently being written) detailing the data flows of the forecast system.

### Monitoring Endpoints

| Endpoint | Authentication | What it shows |
|---|---|---|
| `GET /api/v1/health` | None (public) | `{"status": "ok / degraded / down"}` — for uptime monitors |
| `GET /api/v1/health/detail` | IT admin or org admin login required | Per-component status: database, workers, data freshness, disk usage |

The detailed health endpoint shows:
- Database connectivity and response time
- Worker heartbeat age (detects stalled workers)
- Age of last NWP weather data delivery
- Number of stations with stale observations
- Age of last forecast cycle
- Disk usage percentage and free space

Configure your external uptime monitor (if DHM has one) to poll `GET /api/v1/health` and alert on non-200 responses or `status != "ok"`.

### Prefect Scheduler UI

The Prefect scheduler web interface (port 4200) is not exposed to the internet. To access it, use an SSH tunnel:

```bash
ssh -L 4200:localhost:4200 user@your-vm-address
```

Then open `http://localhost:4200` in your browser. This is used for inspecting pipeline run history and diagnosing failures.

---

## 6. Access Management

### The Five Roles

| Role | What this person can do |
|---|---|
| **Org admin** | Creates and manages all user accounts; issues and revokes API keys for external agencies; can see all data |
| **IT admin** | Triggers data ingestion pipelines manually; monitors production workflows; accesses detailed health status |
| **Model admin** | Manages hydrological model configuration; approves or rejects model updates after retraining; acknowledges pipeline alerts |
| **Forecaster** | Reviews, adjusts, and publishes daily forecasts via the dashboard; acknowledges flood alerts |
| **API consumer** | Read-only access via a scoped API key; cannot log in to the dashboard; sees only the data their key is scoped to |

All human roles (org admin, IT admin, model admin, forecaster) require two-factor authentication (TOTP authenticator app). This cannot be bypassed.

**Recommendation**: Assign at least two people to each role — especially org admin — to avoid single points of failure during staff transitions. If all org admins leave without creating a replacement, recovery requires shell access to the server to run the CLI bootstrap command again. Multiple users per role is fully supported and has no technical limitations.

**Review dashboards (reviewer token, Plan 401).** Each of our review dashboards (the Swiss/BAFU one, the Nepal one) holds one **reviewer** token on its server — never in a browser. It reads what an API consumer with the same station scope reads — and, once Plan 402 adds them, will also read the review routes (QC rule sets, station skill) — and can change nothing: publishing a forecast is a named person's act, not a token's. Issue one on the server with:

```bash
docker compose exec api /entrypoint.sh python -m sapphire_flow.cli.access_tokens create-reviewer \
    --name "<dashboard label>" --tenant <tenant code> [--station <station UUID> ...]
```

`--tenant` is required; the token stays bound to that one tenant. Keep it on an explicit station list unless every station in the tenant belongs to that dashboard's client (`docs/standards/security.md` § Reviewer tokens).

### Bootstrap: Creating the First Admin Account

After first boot, the system has no users. Run this command on the server to create the first org admin account:

```bash
docker compose exec api /entrypoint.sh python -m sapphire_flow.cli.access_tokens create-admin \
    --name "Admin Name"
# `/entrypoint.sh` supplies DATABASE_URL (docker compose exec bypasses the ENTRYPOINT);
# create-admin takes only --name (+ optional --expires-days) — it is always unscoped/global.
```

This command:
1. Creates an org admin account
2. Prints a **temporary password** to the terminal — note it down immediately (shown once only)
3. Displays a **TOTP QR code** — scan it with an authenticator app (Google Authenticator, Authy, etc.)

On first login, the org admin must change the temporary password.

**After bootstrap**, all user management goes through the web dashboard — no more command-line user management is needed:

| Action | Who | How |
|---|---|---|
| Create IT admin, model admin, forecaster accounts | Org admin | Dashboard |
| Issue API keys for external agencies | Org admin | Dashboard |
| Unlock a locked account | Org admin | Dashboard |
| Disable or remove a user | Org admin | Dashboard |

---

## 7. Backup & Disaster Recovery

### Current forecast-evidence protection (Plan 340)

New operational forecasts store their original output and an immutable evidence
record of the inputs, QC and configuration used. A capture gap is recorded as
`evidence_incomplete`; forecasts from before migration 0057 have no such record.
This lets the team diagnose what a run received after source observations or
rating curves change. It does not yet provide automatic model replay or
post-event accuracy scoring.

The existing Prefect database backup keeps seven recent copies for operational
recovery. A separate **host-side protected evidence backup** now makes a full
PostgreSQL dump, retains referenced Docker image bytes, verifies hashes, and
restores a representative forecast/evidence chain in a disposable database.
It records an attestation only after that check passes. The host command must
be scheduled and monitored daily at deployment; it is not the future restic
service or an automatic monthly whole-system restore. The current check proves
one representative forecast per bundle, not every forecast in that bundle.

**DHM IT and the deployment team must provide and record** a protected target
on a different filesystem device from the active PostgreSQL volume, encryption
and access controls for that target, its path and capacity, the daily host
schedule, and an operations alert when `health` is not `verified`. SAPPHIRE
Flow supplies the backup, restore, hash-check and status commands. Use
`docs/standards/cicd.md` § Protected forecast-evidence backup for their exact
arguments and the restore/reconciliation procedure. The Mac mini test host has
no separate target, so CHWRR publication remains disabled there. The DHM
target path, encryption and Nepal-sized capacity/restore time still need
deployment measurements.

From the deployed checkout, use the host command with the configured target
and active PostgreSQL volume paths:

```text
uv run python -m sapphire_flow.ops.evidence_backup_host backup --target <protected-directory> --database-volume <host-postgres-volume-path>
uv run python -m sapphire_flow.ops.evidence_backup_host health --target <protected-directory> --database-volume <host-postgres-volume-path>
uv run python -m sapphire_flow.ops.evidence_backup_host assess --forecast-id <forecast-UUID> --target <protected-directory> --database-volume <host-postgres-volume-path>
```

`assess` reports immutable `capture_status`, derived
`effective_preservation_status`, attestation ID and remaining reasons.
`evidence_incomplete` at capture time remains in history even if a later
verified backup closes an image-byte-only gap. Monitor the daily backup exit
code and `health` JSON status (`verified`, `missing`, `stale`, `invalid`). A
missing or stale protected backup closes the future CHWRR publication gate;
the evidence capture itself can continue.

No cleanup may delete evidence-linked forecast values, snapshots, artifact
metadata or retained image bytes while the longer-term archive in Plan 344 is
unproved. `evidence_retention_days` has a minimum of 2,192 days after forecast
valid time but does **not** authorize deletion at that age. Protected bundles
also remain unpruned for now. Size the target from measured full dumps, daily
growth and image archives; the earlier 500 GB–1 TB restic estimate is not a
capacity sign-off for this interim method.

For recovery on a fresh volume, use the detailed four-step procedure in
`docs/standards/cicd.md` § Protected forecast-evidence backup. It selects and
validates a completed `backup-<UUID>.json`/`.dump` pair, loads and checks the
manifest's pinned image archives, restores the dump into a fresh PostgreSQL
database, bootstraps the scoped roles, then runs host
`reconcile --backup-id <UUID>`. Reconciliation verifies live rows and restores
the attestation created after the dump. Check `assess --forecast-id` and
`health` before restarting forecast workers or consumer routes; make a new
backup if the restored one is beyond the freshness limit. Verify the pipeline
separately before resuming operational use.

The broader design for encrypted, deduplicated restic backups, off-site copies,
cold Parquet storage and six-year-old replay remains future work in
`docs/architecture-context.md` and Plan 344. Do not treat those design details
as an active backup schedule or completed preservation proof.

---

## 8. Questions for DHM IT

Questions are grouped by urgency. Numbered for point-by-point response.

### Must know — answers affect system design

These questions need confirmed answers early, as the answers influence architectural decisions or provider integrations.

**1. Internet outage tolerance — accepted offline duration**

Nepal may experience multi-day internet outages, particularly during monsoon season — exactly when the forecasting system is most critical. SAPPHIRE requires outbound internet to fetch weather forecasts from the Sapphire Data Gateway. When connectivity drops:

- **First 3 hours**: the system waits for the next Data Gateway delivery.
- **3–12 hours**: falls back to the most recent available forecast cycle (forecasts become progressively stale but are still produced).
- **Beyond 12 hours**: no new forecasts can be produced. Observation-based alerts (from DHM stations transmitting via GPRS/GSM directly to WISKI on the local network) continue to function. The API continues serving the most recent forecasts with a staleness warning.

We need to understand DHM's connectivity situation: (a) How frequent are internet outages at the DHM office where the VM will be hosted, and what is the typical duration? (b) Is there a backup internet connection (e.g. a second ISP or mobile data failover)? (c) What is the maximum acceptable duration without new forecasts before DHM considers the system non-operational?

*Why we are asking*: If multi-day outages are common during monsoon, we may need to explore architectural changes or forecast model alternatives.

**2. Recovery time objective (RTO) — accepted downtime after hardware failure**

If the VM's hardware fails (disk failure, motherboard failure, etc.), recovery requires provisioning a new VM, restoring from backup, reconciling forecast-evidence attestations and restarting services (section 7). The recovery time for a Nepal-sized protected bundle has not yet been measured on the DHM target.

During this recovery window, no forecasts are produced, no alerts are raised, and no API data is served. Questions: (a) Is 60 minutes of downtime acceptable during monsoon season? (b) If not: does DHM have the infrastructure to run a second standby VM (warm spare) that can be activated quickly? A warm spare significantly reduces recovery time but requires a second server and additional configuration.

*Why we are asking*: If the accepted RTO is shorter than 60 minutes, we need to design a high-availability setup (automatic failover to a standby VM). This requires additional infrastructure from DHM and additional engineering work from the SAPPHIRE team. The decision should be made before deployment planning.

### Can be resolved during the AWS testing phase

These questions are important for the production deployment but do not block system design. They can be addressed while the system is running on AWS for validation.

**3. Outbound HTTPS access**

Are there firewall restrictions on outbound HTTPS connections (port 443) from the VM to the internet? SAPPHIRE requires outbound access to the Sapphire Data Gateway for weather and snow forecast data.

**4. TLS certificates**

Who manages TLS certificates for the SAPPHIRE API domain name? Caddy (the reverse proxy) can obtain certificates automatically via Let's Encrypt if the VM has a public DNS name and outbound internet access. If DHM uses an internal CA or a different certificate management process, we need to configure this manually.

**5. Existing monitoring infrastructure**

Does DHM have existing monitoring tools (such as Grafana, Nagios, Zabbix, or similar)? If so, we can integrate SAPPHIRE's health endpoint into your existing dashboards.

**6. Backup storage — second server**

Is a separately mounted, encrypted backup volume available on a different filesystem device from the live database volume? An off-site second copy is part of the longer-term backup design; can DHM also provide a second server or equivalent target for that stage?

**7. Network bandwidth**

What is the expected network bandwidth between the VM and the internet? Weather and snow forecast data is downloaded from the Sapphire Data Gateway on a schedule; we need to confirm this fits within available bandwidth without affecting other systems.

**8. OS patching schedule**

Does DHM have a standard OS patching window (e.g. monthly maintenance window)? We should coordinate SAPPHIRE upgrades and restarts with your patching schedule to minimize disruption.

**9. Designated IT contact**

Who will be the primary DHM IT contact for SAPPHIRE operations? This person will receive pipeline alert notifications and will be the point of contact for the SAPPHIRE team during incidents. We have Mr Santa K. Maharjan: santakumarmaharjan.dhm@gmail.com — please confirm if this is correct and if there are additional contacts to include.
