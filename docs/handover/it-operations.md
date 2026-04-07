# SAPPHIRE Flow — IT & Infrastructure Guide (Nepal Deployment)

**Audience**: DHM IT department — system administrators responsible for hosting and operating SAPPHIRE Flow.
**Document version**: April 2026

### Deployment stages

| Stage | Environment | Duration | Purpose |
|-------|-------------|----------|---------|
| Testing & validation | AWS (managed by SAPPHIRE team) | ~6–12 months | Model training, pipeline validation, skill evaluation using DHM data. DHM accesses the system remotely for review and feedback. |
| Production | DHM VM (Ubuntu, on-site or DHM-managed) | Permanent | Full operational deployment under DHM's control. |

During the AWS stage, the SAPPHIRE team manages infrastructure and security. The sections below describe the requirements for the production DHM deployment — DHM IT should use this time to prepare the VM environment and resolve the open questions in section 8.

---

## 1. System Overview

SAPPHIRE Flow ingests processed weather and snow forecast data from the Sapphire Data Gateway (which sources ECMWF ensemble weather forecasts and SnowMapper snow forecasts) and river station observations from DHM, runs hydrological models to produce probabilistic water level forecasts, checks flood thresholds to raise alerts, and serves results via a REST API. It runs as a set of Docker containers on a single Ubuntu VM. All external systems — including the DHM forecast dashboard and other government agencies — connect to this API to retrieve forecasts and alerts.

```
[Sapphire Data Gateway]  ──→ ┌─────────────────────────────────────┐
  (ECMWF + SnowMapper)      │         SAPPHIRE Flow (VM)           │
[DHM Station Data]   ──→     │  ┌──────────┐  ┌──────────────────┐ │ ──→ [REST API :443]
                          │  │ Database │  │ Worker processes │ │ ──→ [Flood alerts / SMS]
                          │  └──────────┘  └──────────────────┘ │
                          │  ┌──────────┐  ┌──────────────────┐ │
                          │  │   API    │  │   Scheduler      │ │
                          │  └──────────┘  └──────────────────┘ │
                          │  ┌──────────┐                        │
                          │  │  Proxy   │  (Caddy, handles TLS)  │
                          │  └──────────┘                        │
                          └─────────────────────────────────────┘
```

The system recovers automatically from crashes and reboots via a systemd service. No manual intervention is required for normal restarts.

---

## 2. Infrastructure Requirements

### VM Specifications

| Resource | Minimum | Notes |
|---|---|---|
| OS | Ubuntu 24.04 LTS | 26.04 LTS support will be tested after its release but support cannot be guaranteed at this point |
| CPU | 4 cores | 8 cores recommended for training workloads |
| RAM | 16 GB | 32 GB recommended |
| Disk | 1 TB SSD | Reviewed quarterly; plan upgrade to 2 TB before reaching 70% utilization |
| Network | Stable internet | Outbound HTTPS required (see below) |

### Software Requirements

- **Docker Engine** (latest stable)
- **Docker Compose v2** (included with Docker Engine)
- No database software to install separately — the database runs inside Docker

### Network Requirements

| Direction | Destination | Port | Purpose |
|---|---|---|---|
| Outbound | Sapphire Data Gateway | 443 (HTTPS) | Weather forecasts (ECMWF) and snow forecasts (SnowMapper) |
| Outbound | SMTP provider | 587 or 465 | Email notifications |
| Outbound | SMS gateway provider | 443 (HTTPS) | Flood alert SMS (provider TBD — see §8) |
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

**PostgreSQL crash while pipelines are running**: If the database container crashes or restarts while a forecast pipeline is in progress, the pipeline run will fail with a database connection error. Prefect marks the run as failed. The pipeline does **not** leave orphaned state — each pipeline run is atomic at the database level (individual transactions, not one large transaction). On the next scheduled cycle, the pipeline runs normally. No manual cleanup is required.

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

- User login with two-factor authentication (TOTP authenticator app — mandatory for all staff). To be validated, see open questions
- Role-based access control — each user sees only what their role permits
- API key management for external consumers (scoped per agency)
- All passwords and secrets stored encrypted — never in plain text
- Database encrypted at rest
- Full audit log of all login events, forecast changes, and model promotions
- Rate limiting and brute-force protection on all API endpoints
- Automatic flood alerts via SMS and email. To be validated, see open questions

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

| Secret name | What it is |
|---|---|
| `db_password` | PostgreSQL database password |
| `secret_key` | JWT signing key (session tokens) |
| `totp_encryption_key` | Encryption key for two-factor authentication seeds |
| `sapphire_dg_api_key` | Sapphire Data Gateway API key |
| `notification_smtp_password` | Email notification credentials |
| `notification_sms_api_key` | SMS gateway credentials (Nepal) |
| `backup_repo_password` | Backup encryption password (see §7) |

**Secret rotation schedule**: `db_password` and `secret_key` annually, or immediately if compromise is suspected. Rotation requires a coordinated restart of all containers. The SAPPHIRE team coordinates rotation with DHM IT.

---

## 5. Deployment & Operations

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

The `alert.sh` script runs independently of the application — it uses a simple `curl` webhook call from the host OS to notify DHM's systems (e.g. the same DHM endpoint used for application alerts). This is cost-free and requires no SMS or email provider. It works even when the entire Docker stack is down, as long as the VM has outbound internet connectivity.

### Layered Monitoring — Who Watches the Watchdog

SAPPHIRE uses two independent monitoring layers to avoid a single point of failure:

| Layer | Runs inside Docker? | What it monitors | Catches |
|---|---|---|---|
| **Flow 4 (application watchdog)** | Yes — as a Prefect pipeline | Data freshness, worker health, forecast timeliness, disk usage | Application-level problems (stale data, failed pipelines, slow models) |
| **Host-level cron watchdog** (above) | No — runs on the VM OS | Whether the API health endpoint responds | Infrastructure-level problems (Docker crash, Prefect crash, PostgreSQL crash, VM resource exhaustion) |

If Prefect itself crashes, Flow 4 stops running — but the host-level cron watchdog continues independently and will detect the failure within 5 minutes (the API health endpoint reports `degraded` or `down` when workers are unresponsive). This layered design ensures that no single component failure goes undetected.

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

### Bootstrap: Creating the First Admin Account

After first boot, the system has no users. Run this command on the server to create the first org admin account:

```bash
docker compose exec api python -m sapphire_flow.cli create-admin \
    --username "admin@dhm.gov.np" \
    --name "Admin Name"
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

### Responsibility split

Backup automation is built into SAPPHIRE Flow itself — it is not something the IT team needs to set up or script. The application runs its own daily backup job and monthly restore rehearsal internally. This ensures consistent, tested backups across all deployments.

**SAPPHIRE Flow handles**: scheduling, running `pg_dump`, collecting files, encrypting and deduplicating with `restic`, enforcing retention policy, running monthly restore rehearsals, alerting on backup failure.

**DHM IT provides**: the backup storage targets (external disk and/or SFTP server), physical connectivity to those targets, and secure offline storage of the backup encryption password.

### Schedule

Automated daily backups run at 02:00 UTC. The backup tool is `restic`, which handles encryption, deduplication, and retention automatically.

### What Is Backed Up

| Data | Backup method |
|---|---|
| PostgreSQL database (forecasts, observations, alerts, users, audit log) | `pg_dump` — consistent snapshot |
| Trained model files (`/data/artifacts/`) | File copy |
| Long-term data archive (`/data/cold/`) | File copy |
| Prefect scheduler state | **Not backed up** — reconstructible from flow definitions |

### Backup Storage

Two copies, stored separately:

| Copy | Storage target | Managed by |
|---|---|---|
| Copy 1 | Local external disk attached to the VM | DHM IT |
| Copy 2 | SFTP on a second server (or equivalent off-site target) | DHM IT or SAPPHIRE team |

**Storage sizing**: The backup tool (restic) uses deduplication — daily backups share most data, so the repository grows slowly. Estimated backup storage after 18 months of operation at Nepal scale (~170 stations):

| What | Size |
|---|---|
| Database snapshot (compressed) | ~20–70 GB |
| Model files | < 1 GB |
| Long-term data archive (Parquet) | ~50–150 GB |
| **Total repository (all snapshots, deduplicated)** | **~100–400 GB** |

**Recommendation**: Each backup target (external disk and SFTP server) should provide at least **500 GB**, ideally **1 TB**. Reviewed quarterly alongside primary disk utilization.

Backup storage target is configured in `config.toml` during deployment.

### Encryption

All backups are encrypted with AES-256 by `restic`. The backup repository password (`backup_repo_password`) is available on the VM at runtime as a Docker secret (mounted in-memory, never written to disk inside containers) — restic needs it to perform each backup.

In addition to the runtime copy, **a recovery copy of the password must be stored separately from the VM** so that backups can be decrypted if the VM is lost:
- One copy with the DHM IT administrator (printed and stored offline, or in a password manager)
- One copy with the SAPPHIRE project team

### Retention Policy

| Snapshot type | How many kept |
|---|---|
| Daily snapshots | 7 (last week) |
| Weekly snapshots | 4 (last month) |
| Monthly snapshots | 12 (last year) |

Older snapshots are pruned automatically.

### Restore Testing

A monthly automated restore rehearsal runs as a scheduled job:
1. Restores the latest backup to a temporary location
2. Starts a temporary database instance from the dump
3. Verifies the schema and that recent forecasts and model files are present
4. Records the result — visible in `/api/v1/health/detail`
5. Sends a critical alert if the restore test fails

**DHM IT should also perform a manual restore test** at least once per year to verify the full recovery procedure works end to end.

### Full Recovery Procedure (fresh VM)

Use this procedure if the VM is lost and must be rebuilt from scratch:

1. Provision a fresh Ubuntu VM with Docker and Docker Compose installed
2. Copy the deployment package to the new VM
3. Restore secrets from secure backup to `/opt/sapphire/secrets/`
4. Restore model artifacts from restic: `restic restore latest --target /data/artifacts/`
5. Restore cold storage from restic: `restic restore latest --target /data/cold/`
6. Start PostgreSQL only: `docker compose up postgres -d` — wait for healthy
7. Restore the database: `pg_restore` the backed-up database dump
8. Start all services: `docker compose up -d`
9. Verify: `curl https://localhost/api/v1/health` returns `{"status": "ok"}`
10. Wait 30 minutes for the next pipeline run and confirm no stale-data alerts appear

---

## 8. Questions for DHM IT

Questions are grouped by urgency. Numbered for point-by-point response.

### Must know — answers affect system design

These questions need confirmed answers early, as the answers influence architectural decisions or provider integrations.

1. **Flood alert notifications — scope and channels** — SAPPHIRE can push flood alerts via three channels: **webhook** (free — sends a structured message to DHM's own systems), **email** (low cost — requires SMTP provider), and **SMS** (highest cost — requires SMS gateway with per-message fees, but reaches phones without internet). If DHM already handles alert distribution from their own dashboard, webhook alone may be sufficient — no external provider needed. Questions: (a) Does DHM want SAPPHIRE to send notifications at all, or will DHM poll the API and handle alerting independently? (b) If yes: is webhook to Bipad/DHM dashboard sufficient, or are SMS/email also needed? (c) If SMS or email: which providers, and who bears the ongoing per-message costs?

2. **Two-factor authentication method** — SAPPHIRE requires two-factor authentication for all staff logins. Our current design uses a TOTP authenticator app (such as Google Authenticator or Authy) on each user's smartphone. The advantage is that it works even when internet or email is unreliable — important during flood events. The alternative would be sending a one-time code via email at each login, which is simpler to set up but fails if email is down. Can DHM staff be expected to install and use an authenticator app on their phones? Or would email-based codes be preferred?

3. **Internet outage tolerance — accepted offline duration** — Nepal experiences multi-day internet outages, particularly during monsoon season — exactly when the forecasting system is most critical. SAPPHIRE requires outbound internet to fetch weather forecasts from the Sapphire Data Gateway. When connectivity drops:

   - **First 3 hours**: the system waits for the next Data Gateway delivery.
   - **3–12 hours**: falls back to the most recent available forecast cycle (forecasts become progressively stale but are still produced).
   - **Beyond 12 hours**: no new forecasts can be produced. Observation-based alerts (from DHM stations transmitting via GPRS/GSM directly to WISKI on the local network) continue to function. The API continues serving the most recent forecasts with a staleness warning.

   We need to understand DHM's connectivity situation: (a) How frequent are internet outages at the DHM office where the VM will be hosted, and what is the typical duration? (b) Is there a backup internet connection (e.g. a second ISP or mobile data failover)? (c) What is the maximum acceptable duration without new forecasts before DHM considers the system non-operational?

   *Why we are asking*: If multi-day outages are common during monsoon, we may need to explore architectural changes — for example, co-locating a Data Gateway cache on the local network, or pre-fetching multiple forecast cycles ahead. These are significant design decisions that affect deployment topology.

4. **Recovery time objective (RTO) — accepted downtime after hardware failure** — If the VM's hardware fails (disk failure, motherboard failure, etc.), the full recovery procedure (section 7) requires provisioning a new VM, restoring from backup, and restarting all services. Estimated recovery time: **30–60 minutes** minimum, assuming a trained IT administrator is available and backups are accessible.

   During this recovery window, no forecasts are produced, no alerts are raised, and no API data is served. Questions: (a) Is 30–60 minutes of downtime acceptable during monsoon season? (b) If not: does DHM have the infrastructure to run a second standby VM (warm spare) that can be activated quickly? A warm spare significantly reduces recovery time but requires a second server and additional configuration.

   *Why we are asking*: If the accepted RTO is shorter than 30 minutes, we need to design a high-availability setup (automatic failover to a standby VM). This requires additional infrastructure from DHM and additional engineering work from the SAPPHIRE team. The decision should be made before deployment planning.

### Can be resolved during the AWS testing phase

These questions are important for the production deployment but do not block system design. They can be addressed while the system is running on AWS for validation.

5. **Outbound HTTPS access** — Are there firewall restrictions on outbound HTTPS connections (port 443) from the VM to the internet? SAPPHIRE requires outbound access to the Sapphire Data Gateway for weather and snow forecast data.

6. **TLS certificates** — Who manages TLS certificates for the SAPPHIRE API domain name? Caddy (the reverse proxy) can obtain certificates automatically via Let's Encrypt if the VM has a public DNS name and outbound internet access. If DHM uses an internal CA or a different certificate management process, we need to configure this manually.

7. **Existing monitoring infrastructure** — Does DHM have existing monitoring tools (such as Grafana, Nagios, Zabbix, or similar)? If so, we can integrate SAPPHIRE's health endpoint into your existing dashboards.

8. **Backup storage — second server** — Is a second server or a dedicated external disk available for off-site backup storage? Backups should not be stored on the same physical machine as the live data.

9. **Network bandwidth** — What is the expected network bandwidth between the VM and the internet? Weather and snow forecast data is downloaded from the Sapphire Data Gateway on a schedule; we need to confirm this fits within available bandwidth without affecting other systems.

10. **OS patching schedule** — Does DHM have a standard OS patching window (e.g. monthly maintenance window)? We should coordinate SAPPHIRE upgrades and restarts with your patching schedule to minimize disruption.

11. **Designated IT contact** — Who will be the primary DHM IT contact for SAPPHIRE operations? This person will receive pipeline alert notifications and will be the point of contact for the SAPPHIRE team during incidents. We have Mr Santa K. Maharjan: santakumarmaharjan.dhm@gmail.com — please confirm if this is correct and if there are additional contacts to include.
