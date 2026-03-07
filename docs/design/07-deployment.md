---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

# Deployment and Operations

## Docker Compose

Primary deployment method. Targets bare Linux VMs with Docker installed.

### docker-compose.yml (conceptual)

```yaml
services:
  caddy:
    image: caddy:2.9
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data        # TLS certificates
    depends_on:
      - api
    restart: unless-stopped

  db:
    image: postgres:16.4
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./backups:/backups
    environment:
      POSTGRES_DB: sapphire_flow
      POSTGRES_USER: sapphire_admin
      POSTGRES_PASSWORD: ${DB_ADMIN_PASSWORD}
    command: >
      postgres
      -c wal_level=replica
      -c archive_mode=on
      -c archive_command='gzip < %p > /backups/wal/%f.gz'
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sapphire_admin"]
      interval: 10s
      retries: 5

  pgbouncer:
    image: edoburu/pgbouncer:1.23
    volumes:
      - ./pgbouncer/pgbouncer.ini:/etc/pgbouncer/pgbouncer.ini:ro
      - ./pgbouncer/userlist.txt:/etc/pgbouncer/userlist.txt:ro
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -p 6432"]
      interval: 5s
      retries: 5

  prefect:
    image: prefecthq/prefect:3.2-python3.11
    command: prefect server start --host 0.0.0.0
    environment:
      PREFECT_API_DATABASE_CONNECTION_URL: postgresql+asyncpg://sapphire_prefect:${DB_PREFECT_PASSWORD}@db/prefect
    expose:
      - "4200"          # internal only — NOT published to host
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:4200/api/health || exit 1"]
      interval: 10s
      retries: 10
      start_period: 30s

  worker:
    build: .
    command: sapphire-flow worker
    environment:
      DATABASE_URL: postgresql://sapphire_worker:${DB_WORKER_PASSWORD}@pgbouncer/sapphire_flow
      PREFECT_API_URL: http://prefect:4200/api
      SAPPHIRE_DG_API_KEY: ${SAPPHIRE_DG_API_KEY}
      STATION_API_KEY: ${STATION_API_KEY}
    depends_on:
      pgbouncer:
        condition: service_healthy
      prefect:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "sapphire-flow worker-health || exit 1"]
      interval: 30s
      retries: 3
      start_period: 15s

  api:
    build: .
    command: sapphire-flow serve
    environment:
      DATABASE_URL: postgresql://sapphire_api:${DB_API_PASSWORD}@pgbouncer/sapphire_flow
      DATABASE_URL_DIRECT: postgresql://sapphire_admin:${DB_ADMIN_PASSWORD}@db/sapphire_flow
      PREFECT_API_URL: http://prefect:4200/api
      SECRET_KEY: ${SECRET_KEY}
    expose:
      - "8000"          # internal only — Caddy proxies to this
    depends_on:
      pgbouncer:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8000/api/v1/ping || exit 1"]
      interval: 10s
      retries: 3

volumes:
  pgdata:
  caddy_data:
```

Key security changes from the basic setup: (1) Only Caddy (ports 80/443) is exposed to the network. (2) Prefect UI is internal-only — access via SSH tunnel: `ssh -L 4200:localhost:4200 server`. (3) PostgreSQL uses WAL archiving for point-in-time recovery. (4) Docker images are pinned to specific minor versions. (5) Separate database passwords per service. (6) PgBouncer enforces per-service database credentials — each service authenticates with its own PostgreSQL user, preserving the least-privilege model. See 01-architecture.md.

### PgBouncer multi-user configuration

`pgbouncer/pgbouncer.ini`:
```ini
[databases]
sapphire_flow = host=db port=5432 dbname=sapphire_flow

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = plain
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
default_pool_size = 25
max_db_connections = 100
```

`pgbouncer/userlist.txt` (generated during setup):
```
"sapphire_api" "<hashed_password>"
"sapphire_worker" "<hashed_password>"
```

Only `sapphire_api` and `sapphire_worker` are allowed through PgBouncer. The `sapphire_admin` user connects directly to PostgreSQL (bypassing PgBouncer) for migrations only. The `sapphire_prefect` user also connects directly (Prefect manages its own pool).

### Caddyfile

```
your-domain.example.com {
    # API and dashboard
    reverse_proxy api:8000

    # Stricter rate limit on auth endpoints (brute-force mitigation)
    @login path /auth/login
    rate_limit @login {remote.host} 10r/m

    # Rate limiting
    rate_limit {remote.host} 100r/m

    # Security headers
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        Content-Security-Policy "default-src 'self'; script-src 'self' https://unpkg.com https://cdn.plot.ly; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'"
    }
}
```

For deployments without a domain name (e.g. direct IP), use `tls internal` for self-signed certificates, or provide your own certificate files.

### Secrets management

**Production deployments must use Docker secrets** — `.env` files are not
acceptable for government infrastructure.

```yaml
# Production: Docker secrets (default)
services:
  db:
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/db_admin_password
    secrets:
      - db_admin_password

secrets:
  db_admin_password:
    file: ./secrets/db_admin_password.txt  # chmod 600, owned by root
```

For AWS deployments, use AWS Secrets Manager with ECS or a secrets-fetching sidecar.

#### Development only: .env file

For local development and testing only, a `.env` file is acceptable:

```
DB_ADMIN_PASSWORD=<generated>
DB_API_PASSWORD=<generated>
DB_WORKER_PASSWORD=<generated>
DB_PREFECT_PASSWORD=<generated>
SECRET_KEY=<generated>
SAPPHIRE_DG_API_KEY=<from provider>
STATION_API_KEY=<from hydromet>
```

File permissions: `chmod 600 .env`. Never commit to git (already in .gitignore).

### Database users (least privilege)

The initial migration creates three application users with minimal privileges:

- `sapphire_api` — SELECT on all tables, INSERT/UPDATE on observation_edits, forecast_adjustments, bulletins, audit_log
- `sapphire_worker` — SELECT/INSERT/UPDATE on observations, forecasts, forecast_values, alert_events, model_skill
- `sapphire_prefect` — full access to the `prefect` database only, no access to `sapphire_flow` tables

The `sapphire_admin` user (from POSTGRES_USER) is only used for migrations and maintenance.

## Deployment steps (for hydromet IT)

```bash
# 1. Install Docker + Docker Compose and enable on boot
#    (one-time, standard Linux packages)
sudo systemctl enable docker
sudo systemctl enable containerd

# 2. Clone the repository
git clone https://github.com/hydrosolutions/SAPPHIRE_flow.git
cd SAPPHIRE_flow

# 3. Create secrets (production) or .env (development)
# Production:
mkdir -p secrets && chmod 700 secrets
openssl rand -base64 32 > secrets/db_admin_password.txt
openssl rand -base64 32 > secrets/db_api_password.txt
openssl rand -base64 32 > secrets/db_worker_password.txt
openssl rand -base64 32 > secrets/db_prefect_password.txt
openssl rand -base64 32 > secrets/secret_key.txt
chmod 600 secrets/*.txt
# Development only: cp .env.example .env && nano .env && chmod 600 .env

# 4. Copy and edit the deployment config
cp config.example.toml config.toml
nano config.toml  # configure adapters, QC params, schedules (NOT station config)

# 4b. Import station metadata (from hydromet API or CSV)
docker compose exec api sapphire-flow import-stations --source=api
# Or from CSV: docker compose exec api sapphire-flow import-stations --csv=stations.csv

# 5. Start everything
docker compose up -d

# 6. Run initial database migration
docker compose exec api sapphire-flow migrate
# Note: migrations connect directly to PostgreSQL (DATABASE_URL_DIRECT),
# bypassing PgBouncer. PgBouncer's transaction pooling is incompatible
# with Alembic's advisory locks.

# 6b. Set up the Caddyfile with your domain or IP
#     Edit Caddyfile, then: docker compose restart caddy

# 7. Create the first admin user
docker compose exec api sapphire-flow create-user --admin

# 7b. Configure Nepal localization (v1.0)
# Set in config.toml:
#   [localization]
#   timezone = "Asia/Kathmandu"
#   calendar = "bikram_sambat"     # displays BS dates alongside Gregorian
#   language = "en"                # interface language

# 8. Set up SSH tunnel for Prefect UI access
ssh -L 4200:localhost:4200 yourserver

# 9. Verify
curl https://localhost/api/v1/health
# Open http://localhost:4200 for Prefect UI (via SSH tunnel)
# Open https://your-domain.example.com for dashboard
```

Total: ~10 commands. No Python installation, no virtual environments,
no system dependencies to manage.

## Upgrades

Always use tagged releases — never `git pull` from main.

```bash
cd SAPPHIRE_flow
git fetch --tags
git checkout v1.2.3                  # specific release tag
docker compose build
docker compose up -d
docker compose exec api sapphire-flow migrate  # if release notes say so
```

### Before upgrading
- Read the release notes for breaking changes and required config updates
- Back up the database (see backup section)
- Test on a staging environment if possible (especially during flood season)

### Rollback
If an upgrade fails:
```bash
git checkout v1.2.2                  # previous version
docker compose build
docker compose up -d
docker compose exec api sapphire-flow migrate --downgrade  # if migration was applied
```

Alembic downgrade scripts are tested as part of the release process.

## Backup strategy

### Database — WAL archiving + daily dumps

WAL archiving is enabled by default in the Docker Compose config above.
This provides continuous backup with point-in-time recovery (PITR).

Additionally, a daily logical backup runs via host cron:

```bash
# /etc/cron.d/sapphire-backup (runs at 02:00 daily)
0 2 * * * root bash -c 'set -o pipefail && cd /path/to/SAPPHIRE_flow && \
  docker compose exec -T db pg_dump -U sapphire_admin sapphire_flow | \
  gzip | \
  gpg --symmetric --cipher-algo AES256 --passphrase-file /root/.backup-key \
  > backups/daily/sapphire_$(date +\%Y\%m\%d).sql.gz.gpg'
```

Backups are encrypted with GPG (AES-256) before storage.

**Backup key custody procedure:**
- The encryption passphrase is stored in two independent locations, each accessible to a different named custodian
- **Primary custodian**: Head of IT (or designated deputy). Key stored in the organization's password manager (e.g. KeePass database on an encrypted USB drive)
- **Secondary custodian**: Director of hydromet operations. Key stored in a sealed envelope in a locked safe at the office
- Both custodians are documented by name and role in the deployment runbook
- Key recovery procedure: either custodian can independently decrypt backups. If one custodian is unavailable, the other can still restore
- Key rotation: when a custodian changes roles, the backup key is rotated and both storage locations are updated
- **Quarterly tested restores** (see below) verify that the current custodians can actually access the key and complete a restore

### Offsite replication

At minimum, copy daily backups to a second location:
- AWS phase: S3 bucket with versioning enabled
- Own servers: rsync to a second machine, or USB external drive rotated weekly

### Backup retention

- Daily dumps: keep 30 days
- WAL archives: keep 7 days (sufficient for PITR within the week)
- Monthly snapshots: keep 12 months

### Backup monitoring

- A daily check verifies that a backup file was created today and is non-zero size.
  On failure, an operational alert is sent via the configured notification channel.
- WAL archive volume usage is checked hourly. Alert at 70%, critical at 85%.
  **If WAL archiving fails, PostgreSQL stops accepting writes** — this is a full
  system outage. A cleanup cron job removes WAL archives older than the retention
  period (7 days) daily.
- The operations summary includes backup status (last successful backup time,
  WAL archive volume usage).

### Tested restores

A full restore from backup must be tested quarterly. The restore procedure
is documented in the operations runbook. The test verifies:
1. Database restores without errors
2. Application connects and serves data correctly
3. Most recent data is present (no silent data loss)

### Model artifacts

Model weight files are backed up alongside the database. Store in a
`models/` volume that is included in the backup rotation.

### Configuration

`config.toml` and `.env` are version-controlled (`.env` in a private
location, not the public repo). Include in encrypted backups.

## Monitoring

### Health checks

- Docker healthchecks restart failed containers automatically
- `GET /api/v1/health` returns:
  - Database connectivity
  - Last successful ingest time (weather + stations)
  - Last successful forecast time
  - Number of stations with stale data
  - Count of active alerts by severity
  - Disk usage estimate

**Staleness definition**: A station's data is "stale" when no new observation has
arrived within a configurable threshold per station kind:
- River gauges with telemetry: 2 hours (expected interval: 10-60 min)
- Manual reporting stations: 48 hours (expected interval: daily)
- Weather forecast data: 12 hours (expected interval: 6 hours)

These defaults are configurable in `config.toml` under `[monitoring.staleness]`.
The health endpoint and operations summary use these thresholds. Stations newly
added (with no observations yet) are not counted as stale.

### Failure notifications (mandatory)

Flow failure notifications are **not optional**. Configure at least one channel
during deployment:

- **Email**: SMTP configuration in config.toml (works without internet for local SMTP)
- **Webhook**: POST to a URL on failure (integrates with SMS gateways, Telegram bots, etc.)

All flow failures trigger an immediate notification. During flood season,
a silent ingest failure could mean stale forecasts are presented as current.

### Daily summary

The `GET /api/v1/operations/summary` endpoint returns a machine-readable
summary suitable for a daily digest email or dashboard widget:
- Flows run in last 24h (success/failure counts)
- Stations with fresh vs. stale data
- Active alerts
- Disk usage

### External uptime monitoring (mandatory)

Internal monitoring cannot detect its own outage. An external uptime monitor
must be configured during deployment:

- **Option A** (recommended): Use a service like Healthchecks.io or UptimeRobot.
  The daily operations summary flow pings an external URL on success. If no ping
  is received within 24 hours, the external service alerts the operations team.
- **Option B**: A cron job on a separate server (or even a developer's workstation)
  polls `GET /api/v1/ping` every 5 minutes and alerts on 3 consecutive failures.

This is not optional for a flood warning system. A server that has been down for
12 hours with no one noticing is an operational failure.

### Disk space

Monitor the PostgreSQL data volume. Alert at 80% capacity.
With the data volumes in 02-data-model.md, plan for 20-30 GB/year
including indexes and WAL, not just raw row estimates.

## Intermittent connectivity handling

| Scenario                       | Behavior                                          |
|--------------------------------|---------------------------------------------------|
| Weather API unreachable        | Retry 3x with backoff, use cached forecast, flag  |
| Station API unreachable        | Retry 3x, run forecast with available data, flag  |
| Internet down for hours        | All ingests fail gracefully, forecasts use cache   |
| Internet restored              | Next scheduled ingest catches up, triggers forecast|
| Prefect server unreachable     | Worker queues locally, reconnects automatically    |

The system degrades gracefully — it never crashes due to connectivity.
Stale data is clearly flagged in the API and dashboard.

## Resource requirements

### Minimum (small hydromet, ~50 stations)

- 2 CPU cores
- 8 GB RAM
- 50 GB disk (grows ~20-30 GB/year including indexes and WAL)

### Recommended (large hydromet, ~500 stations)

- 4 CPU cores
- 16 GB RAM
- 200 GB disk

### Training (if on same server)

- 4+ CPU cores (or GPU for faster training)
- 16 GB RAM
- Training is a batch job, not always running

These are well within a 5-year-old server's capabilities.

## AWS deployment (Jan 2027 phase)

For the AWS testing phase:

- **EC2 instance**: t3.large (2 vCPU, 8 GB) or t3.xlarge
- **EBS**: gp3 volume, 100 GB. Enable EBS encryption for data at rest.
- **Same Docker Compose** — no changes to the deployment
- **Recommended**: RDS for PostgreSQL (managed backups, PITR, encryption at rest)
- **Security group**: allow only ports 443 and 80. SSH (22) restricted to admin IPs. All other ports internal only.
- **Data residency**: deploy in an AWS region that complies with the hydromet's national data sovereignty requirements.

### On-premise encryption at rest

For on-premise deployments, the server's data volume must be encrypted:

- **Linux**: Use LUKS (dm-crypt) for full-disk or partition encryption of the volume hosting Docker data (`/var/lib/docker`) and backups
- **Setup**: `cryptsetup luksFormat /dev/sdX` during server provisioning. The volume is unlocked at boot via a key file or passphrase
- This protects against physical theft of server hardware — a real risk in some hydromet office settings

EBS encryption (for AWS) and LUKS (for on-premise) together ensure data-at-rest encryption across all deployment modes.

### Database connection encryption

PostgreSQL connections within the Docker network are unencrypted by default.
For v1.0, the internal Docker network provides sufficient isolation. For
production hardening (v2.0), configure PostgreSQL with `ssl = on` and set
`sslmode=require` in all `DATABASE_URL` values. Self-signed certificates are
acceptable for internal Docker network connections.

The beauty of Docker Compose: the exact same setup runs on AWS EC2
and on the hydromet's own servers.

## Localization

### v1.0: Nepal

- **Timezone**: Asia/Kathmandu (UTC+5:45) — all display times converted from UTC
- **Calendar**: Bikram Sambat dates shown alongside Gregorian in the dashboard and bulletins
- **Language**: English interface. Bulletin templates may use Nepali text (managed by the hydromet via ieasyreports templates)
- **Number formatting**: Standard decimal notation (no localization needed for v1.0)

### v3.0: Central Asia

- Additional timezone support (e.g. UTC+5, UTC+6)
- Russian language interface
- Local language support (Kyrgyz, Tajik, Uzbek) — translation workflow TBD
- Season definitions: April–September (configurable per deployment)

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
