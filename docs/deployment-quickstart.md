# v0 Deployment Quick-Start

Minimal steps to deploy SAPPHIRE Flow on a test server.

## 1. Prerequisites

- Docker Engine 24+ and Docker Compose v2
- Git
- 50 GB disk (grows ~0.2-0.4 GB/day at ~170 Swiss stations)

```bash
git clone <repo-url> && cd SAPPHIRE_flow
```

## 2. Secrets

See [secrets-bootstrap.md](secrets-bootstrap.md) for details.

```bash
mkdir -p secrets && chmod 700 secrets
openssl rand -base64 32 > secrets/db_password
chmod 600 secrets/db_password
```

## 3. Optional configuration

Create a `.env` file to override defaults:

```bash
# .env (all optional — defaults shown)
# DB_USER=sapphire
# SAPPHIRE_DOMAIN=sapphire.example.ch   # enables auto-TLS via Let's Encrypt
# SAPPHIRE_CORS_ORIGINS=https://sapphire.example.ch
# SCHEDULE_INGEST_OBSERVATIONS=*/10 * * * *
# SCHEDULE_FORECAST_CYCLE=0 */6 * * *
# SCHEDULE_BACKUP_DATABASE=0 2 * * *
```

> **⚠ v0 only**: The default `SAPPHIRE_CORS_ORIGINS=*` allows all origins.
> This is acceptable for a test server with no authentication. Before any
> public-facing or v1 deployment, set this to an explicit origin list
> (e.g., `SAPPHIRE_CORS_ORIGINS=https://dashboard.example.ch`).

Without `SAPPHIRE_DOMAIN`, Caddy serves plain HTTP on port 80.

## 4. Build and start

```bash
docker compose build
docker compose up -d
```

The `init` service runs automatically on first boot:
1. Applies database migrations (`alembic upgrade head`)
2. Registers Prefect deployments with schedules
3. Exits

Check init completed:
```bash
docker compose logs init
```

## 5. Verify

```bash
# Health check
curl http://localhost/api/v1/health

# All services healthy
docker compose ps
```

Access the Prefect UI via SSH tunnel (not exposed by default):
```bash
ssh -L 4200:localhost:4200 user@server
# then open http://localhost:4200 in browser
```

For local development with exposed ports:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
# postgres: localhost:5438, prefect: localhost:4200, api: localhost:8010
```

## 6. First data load

Trigger station onboarding from the Prefect UI (Deployments > onboard-stations > Run),
or via the Prefect CLI:

```bash
docker compose exec prefect-worker prefect deployment run onboard-stations/onboard-stations
```

This downloads CAMELS-CH data, inserts stations and basins, imports historical
observations, runs QC, and computes baselines. Takes 10-30 minutes depending on
network speed.

After onboarding, trigger model onboarding and training via the Prefect UI.

## 7. Monitoring

```bash
# Follow all logs
docker compose logs -f

# Check specific service
docker compose logs -f api

# Health endpoint
curl http://localhost/api/v1/health
```

Scheduled flows (observation ingest every 10 min, forecast cycle every 6h,
backup daily at 02:00 UTC) start automatically after init completes.

## 8. Backup and restore

Backups run daily via the `backup-database` Prefect deployment. Dumps are stored
in the `backups` Docker volume.

Manual backup:
```bash
docker compose exec prefect-worker prefect deployment run backup-database/backup-database
```

Restore from backup:
```bash
# Stop services
docker compose down

# Start only postgres
docker compose up -d postgres
# Wait for healthy...

# Copy dump from volume and restore
docker compose exec postgres pg_restore -U sapphire -d sapphire /path/to/dump.dump

# Restart all services
docker compose up -d
```

## 9. Upgrade

```bash
git pull
docker compose build
docker compose up -d init        # run migrations + re-register deployments
docker compose up -d              # restart services with new image
```
