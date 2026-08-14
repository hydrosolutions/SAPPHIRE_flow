---
status: DRAFT
created: 2026-08-14
plan: 161
title: DATABASE_URL credential handling — a `/` in the password silently killed every backup
scope: Fix the live backup outage caused by an un-encoded `/` in the worker DB password, and close the failure CLASS behind it: a credential spliced into a URL string without encoding, then parsed by consumers of differing strictness. Covers the consumer (`flows/backup.py` uses `urlparse`, unlike every other consumer), the producer (`docker/entrypoint.sh` builds the URL by sed-substituting the raw password), and the alert that reported it 24h late and then once every 5 minutes. Relocating the backup target off the boot disk stays a Plan 158 follow-on; verifying dump restorability is a named follow-on.
depends_on: []
blocks: []
supersedes: []
---

# Plan 161 — DATABASE_URL credential handling

## Status
**DRAFT** (2026-08-14). Operational reliability (category **A**). Fixes a **live, ongoing** backup outage:
**the mac-mini has had no successful database backup since 2026-08-13 02:01 UTC.**

## Problem

The nightly `backup-database` flow has failed on every run since the Plan 147 deploy. Observed on the mini
2026-08-14:

```
flow_run backup-2026-08-14T0200  FAILED after 1.03s
Flow run encountered an exception: ValueError: Port could not be cast to integer value as '0r'
```

`dump_database_task` parses the connection string with `urlparse` (`src/sapphire_flow/flows/backup.py:57-58`)
and then reads `parsed.port` (`:64`). **`urlparse` terminates the netloc at the first `/` after the scheme.**
The worker's DB password contains a `/` at index 2, so the netloc becomes `sapphire_worker:0r` and `'0r'` — the
first two characters of the live password — is parsed as the port.

Verified directly on the host against the real secret, without printing it:

```
index of first / in password: 2
port  -> PORT ERROR: Port could not be cast to integer value as '0r'
quote(password) -> port: 5432   host: postgres
```

**Why it started on 2026-08-13.** Plan 147 Slice D gave each service its own DB credential
(`DB_PASSWORD_SECRET=/run/secrets/sapphire_worker_db_password`, `docker/entrypoint.sh:5-17`). The newly minted
44-character base64 worker password happens to contain `/` near the front (base64's alphabet includes `+` and
`/`). The 02:00 backup on 2026-08-13 ran *before* that deploy and succeeded; the first backup after it failed.
**This is our own change, not upstream drift.**

**Why only backups.** `flows/backup.py:58` is the **only** `DATABASE_URL` consumer that uses `urlparse`. Every
other consumer goes through SQLAlchemy (`db/engine.py:9` → `create_engine`), whose URL regex captures the
password as `[^@]*` and therefore tolerates an un-encoded `/`. So the whole pipeline — forecast cycle, both BAFU
collectors, ingest — stayed green while backups were dead. **The system looked perfectly healthy.**

### The class, not just the instance

The producer splices the raw password into a URL with `sed` (`docker/entrypoint.sh:22`, and identically for
Prefect at `:27`):

```sh
export DATABASE_URL=$(echo "${DATABASE_URL_TEMPLATE}" | sed "s|://\([^@]*\)@|://\1:${DB_PASSWORD}@|")
```

Two distinct hazards, only one of which has fired:

1. **URL ambiguity (fired).** The password is never percent-encoded, so any of `/ @ # ?` makes the resulting URL
   ambiguous to a conforming RFC-3986 parser. Strict parsers (`urlparse`) break; lenient ones (SQLAlchemy)
   happen not to.
2. **sed replacement injection (not yet fired, worse).** `&`, `\` and the `|` delimiter are special in a sed
   *replacement*. A password containing `&` would expand to the whole matched text; one containing `|` would
   corrupt the expression itself. The current password (specials: `+ / =`) contains none of these — **but the
   next rotation is one `&` away from breaking every container at once**, not just backups.

### Two further defects the incident exposed

3. **The failure was reported ~24h late.** Nothing alerts on a Prefect **flow-run failure**; the only signal was
   backup *staleness* crossing a 26h threshold (`watchdog.py:760`). The flow failed at 02:00:00 and the first
   alert fired the next morning.
4. **Then it alerted every 5 minutes.** `watchdog.py:761` states the intent plainly — *"For simplicity, alert
   every tick on backup staleness"* — so a single stale backup produces ~288 Slack messages/day. The state field
   `last_backup_alert_iso` is written (`:771`) but never used to suppress. Alert fatigue is itself an outage
   risk: the July blackout was missed for 14 days.

5. **The password prefix leaked into logs.** `'0r'` — the first two characters of the live worker password — is
   now in the Prefect flow-run state message and the worker logs.

## Goal

A credential containing any legal character cannot break any consumer, and a backup that fails is visible within
minutes and reported once.

## Non-goals

- **Relocating the backup target.** `/Volumes/sapphire-backup` is *not* a mounted volume (`mount` shows nothing;
  it is a plain directory on the boot disk), so dumps sit on the same disk as the database. Real, but already a
  named Plan 158 follow-on.
- **Verifying dump restorability.** Nothing checks that a written dump can actually be restored. Named follow-on.
- **Changing the secret-generation alphabet.** Excluding awkward characters from generated passwords would mask
  this class rather than fix it, and cannot constrain a human-set password.

## Open decisions (owner)

- **D1 — how far to fix.** (a) Consumer only: `backup.py` → `make_url`. Fixes the live outage, minimal risk,
  leaves hazard 2 live. (b) Consumer + producer: also percent-encode at composition. **Recommended (b)** — (a)
  leaves a rotation-triggered total outage armed. T1 is separable and shippable first either way.
- **D2 — how the producer should encode.** Percent-encoding the password means the URL carries `%XX`; SQLAlchemy
  decodes automatically and `backup.py` (post-T1) reads the decoded value. This changes the URL for **every**
  service at once, so it needs an atomic deploy plus a consumer audit. Alternative considered: stop building a
  URL at all and pass discrete `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD` to the backup path, sidestepping parsing —
  cleaner for `pg_dump`, but does not help the other consumers.
- **D3 — flow-failure alerting.** Defect 3 argues for alerting on any Prefect flow-run failure. That overlaps the
  dead-man's-switch work (Plan 158 D11) and may belong there rather than here.

## Tasks

### T1 — `backup.py` must parse the URL the way the rest of the codebase does (Repo)

*In:* `src/sapphire_flow/flows/backup.py:57-70`. *Out:* the dump/retention logic, the schedule, the target dir.

Replace `urlparse` with `sqlalchemy.engine.make_url` — the parser already used by `db/engine.py` and therefore
the one every other consumer is validated against. `make_url` returns the password already decoded, so the
existing `unquote(...)` calls (`:66`, `:69`) must be **removed**, not kept — leaving them would corrupt any
password containing a literal `%`.

**Red-first (the point of the task):** a test building a connection URL whose password contains `/` (and, as
separate cases, `@`, `%`, `+`) and asserting `pg_dump` is invoked with `--host=postgres --port=5432
--username=sapphire_worker --dbname=sapphire` and `PGPASSWORD` equal to the **exact original password**. Must be
proven to fail against current `backup.py` with the `/` case reproducing `ValueError: Port could not be cast`.

**Verify:** `uv run pytest tests/unit/flows/test_backup.py -q`; full suite; ruff; pyright ratchet.

### T2 — the producer must not splice a raw secret into a URL (Repo, gated on D1/D2)

*In:* `docker/entrypoint.sh:19-28`. *Out:* which secret file is read (Plan 147 Slice D), the drop-to-app-user step.

Percent-encode the password before it enters the URL, and compose without a sed *replacement* (so no `&`/`\`/`|`
interpretation) — e.g. build the string in the shell from an encoded value rather than substituting into a
pattern. Applies to **both** `DATABASE_URL` (`:22`) and `PREFECT_API_DATABASE_CONNECTION_URL` (`:27`).

**Consumer audit is part of this task, not an afterthought:** every `os.environ["DATABASE_URL"]` reader
(`db/engine.py:9`, `flows/onboard.py:132`, `flows/collect_bafu_forecasts.py:138`,
`flows/collect_bafu_observations.py:117`, `flows/run_forecast_cycle.py:1625`, `flows/ingest_observations.py:422`,
`flows/compute_skills.py:283,349`, `flows/onboard_model.py:709`, `flows/train_models.py:310`,
`flows/run_hindcast.py:176`, `flows/ingest_recap_reanalysis.py:413`, `flows/ingest_weather_history.py:393`,
`flows/backup.py:57`, `tools/observation_coverage_summary.py:149`, `cli/import_basin_package.py`) must be shown
to decode percent-encoding, i.e. to route through SQLAlchemy or `make_url`. Any that does not is a T2 blocker.

**Red-first:** a shell-level test that composition with a password containing `& | \ / @ %` yields a URL which
`make_url` parses back to **exactly** that password. Prove it fails against the current `sed` line — the `&` case
should visibly corrupt.

**Verify:** `shellcheck` + `bash -n` on the entrypoint; the composition test; a container smoke test that every
service still connects.

**Deploy note:** this changes the URL for all services simultaneously. It must ship as one deploy with a
post-deploy check that each service connects, and a rollback to the previous image.

### T3 — a stale backup must alert once, not 288 times a day (Repo)

*In:* `src/sapphire_flow/ops/watchdog.py:750-771`. *Out:* the 26h threshold, the check itself.

Use the already-persisted `last_backup_alert_iso` (`:164`, `:771`) to suppress repeats — alert on transition into
stale, then at a defined re-notify interval, and once on recovery. Match whatever hysteresis the BAFU checks
already use rather than inventing a second convention.

**Red-first:** a test driving several consecutive stale ticks and asserting exactly one alert plus the defined
re-notify, and a recovery alert on the transition back. Must fail against current code, which alerts every tick.

**Verify:** `uv run pytest tests/unit/ops/test_watchdog.py -q`.

## Exit gates

Full `uv run pytest`, `ruff format --check` + `ruff check`, pyright ratchet, `shellcheck` + `bash -n` for T2 —
and each of T1/T2/T3's locking tests **proven red** against current committed code.

## Acceptance (host, after deploy)

The next scheduled `backup-database` run reaches `COMPLETED`, a new dump appears with a **current** timestamp,
and the watchdog posts a single recovery message. Confirm on the mini via the Prefect flow-run state — not by the
absence of alerts, which is exactly the evidence that failed us in July.

## Follow-ons

- **Rotate the worker DB password** — its first two characters are in the logs (defect 5). Do this *after* T1/T2,
  so the rotation lands on code that tolerates any character.
- Relocate the backup target off the boot disk (Plan 158 follow-on).
- Verify dump restorability as part of the backup flow.
- Alert on Prefect flow-run failure (D3 — likely Plan 158 D11 / dead-man's-switch work).
