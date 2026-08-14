---
status: READY
created: 2026-08-14
plan: 161
title: DATABASE_URL credential handling — a `/` in the password silently killed every backup
scope: Fix the live backup outage caused by an un-encoded `/` in the worker DB password, and close the failure CLASS behind it: a credential spliced into a URL string without encoding, then parsed by consumers of differing strictness. Covers the consumer (`flows/backup.py` uses `urlparse`, unlike every other consumer), BOTH producer sites (`docker/entrypoint.sh` sed-substitutes the raw password; `docker-compose.yml:54` splices the raw OWNER password into `prefect-server`'s DB URL and never touches our entrypoint), and the alert that reported it 24h late and then once every 5 minutes. Relocating the backup target off the boot disk stays a Plan 158 follow-on; verifying dump restorability is a named follow-on.
depends_on: []
blocks: []
supersedes: []
---

# Plan 161 — DATABASE_URL credential handling

## Status
**READY** (2026-08-14) — T1 **BUILT, MERGED (#152) and DEPLOYED** (mini at 0.1.721, alembic 0048).
**T1 is confirmed working — and deploying it uncovered two further defects, T4 and T5, both live.**
Backups are still failing: T4 blocks them, and T5 means the failure is now *silent*. Next build scope: **T4+T5**. Operational reliability (category **A**). Fixes a **live, ongoing** backup outage:
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
other consumer goes through SQLAlchemy (`db/engine.py:9` reads the env, `:10` calls `sa.create_engine`), whose URL regex captures the
password as `[^@]*` and therefore tolerates an un-encoded `/`. So the whole pipeline — forecast cycle, both BAFU
collectors, ingest — stayed green while backups were dead. **The system looked perfectly healthy.**

### The class, not just the instance

There are **two physical producer locations carrying three URL constructions**, not one *(terminology per Codex: `entrypoint.sh:22` and `:27` are two constructions in one location, plus `docker-compose.yml:54`; `:27` was not overlooked, it is grouped into Site A)*.

**Site A — the entrypoint sed.** `docker/entrypoint.sh:22` splices the raw password into the URL with `sed` (and
identically for Prefect at `:27`):

```sh
export DATABASE_URL=$(echo "${DATABASE_URL_TEMPLATE}" | sed "s|://\([^@]*\)@|://\1:${DB_PASSWORD}@|")
```

**Site B — the `prefect-server` inline command in compose.** `docker-compose.yml:54`:

```yaml
export PREFECT_API_DATABASE_CONNECTION_URL=\"postgresql+asyncpg://${DB_USER:-sapphire}:$$(cat /run/secrets/db_password)@postgres:5432/prefect\" &&
```

This site was missed by an earlier draft and matters for three grounded reasons:

- **It never runs our entrypoint.** `prefect-server` uses the stock `prefecthq/prefect:3-python3.11` image
  (`docker-compose.yml:51`, inline `command:` at `:52-55`); nothing in `docker/entrypoint.sh` executes for it, so a
  fix confined to Site A leaves this URL raw.
- **It is the OWNER credential.** It reads `/run/secrets/db_password` (`docker-compose.yml:69`) — the same
  secret as `POSTGRES_PASSWORD_FILE` (`:22`), i.e. the superuser, not a per-service Plan-147 role. Blast radius
  on breakage is larger than the worker credential that actually fired.
- **The entrypoint's Prefect branch is currently dead.** `entrypoint.sh:26-27` only fires when
  `PREFECT_API_DATABASE_CONNECTION_URL` is already set, and no compose file sets it as a template (`grep -n
  'PREFECT_API_DATABASE_CONNECTION_URL' docker-compose*.yml` matches only `:54`). So Site B is the **only live**
  producer of the Prefect DB URL today.

Site B carries hazards 1 and 3 below in full. It does **not** carry hazard 2 (sed) — there is no sed — and,
correcting a review claim: it is **not** a shell/command-injection vector either. POSIX shell does not re-parse
the result of a command substitution for quotes, backticks, or further expansion, and `$$(cat ...)` sits inside
double quotes, so a password containing `` ` `` or `"` is inserted literally. It is a URL-encoding bug, same
class as Site A, not a worse one.

Three distinct hazards, only one of which has fired:

1. **URL ambiguity (fired).** The password is never percent-encoded, so any of `/ @ # ?` makes the resulting URL
   ambiguous to a conforming RFC-3986 parser. Strict parsers (`urlparse`) break; lenient ones (SQLAlchemy)
   happen not to.
2. **sed replacement injection (not yet fired, worse).** `&`, `\` and the `|` delimiter are special in a sed
   *replacement*. A password containing `&` would expand to the whole matched text; one containing `|` would
   corrupt the expression itself. The current password (specials: `+ / =`) contains none of these — **but the
   next rotation is one `&` away from breaking every container at once**, not just backups.
3. **Percent-sign decode ambiguity (silent, hits every consumer).** Every consumer decodes percent-escapes in the
   password — `unquote()` in today's `backup.py:65,68`, and SQLAlchemy's own decoding for everyone else. A raw
   (un-encoded) `%` followed by two hex digits is therefore silently *decoded* into a different password. Verified
   in this repo's environment:

   ```
   make_url("postgresql://u:ab%41cd@h:5432/db").password  -> 'abAcd'      # wrong, silently
   make_url("postgresql://u:ab%2541cd@h:5432/db").password -> 'ab%41cd'   # correct, once producer-encoded
   ```

   This is **not** fixed by swapping `urlparse` for `make_url` (T1) — both decode identically. It is closed only
   when the producer percent-encodes (T2), i.e. when consumers decode only what was actually encoded. Same
   verification confirms `quote(pw, safe="")` at the producer + `make_url` at the consumer round-trips `@`, `/`,
   `%41`, `%zz` and a trailing `%` exactly.

### Three further defects the incident exposed

4. **The failure was reported ~24h late.** Nothing alerts on a Prefect **flow-run failure**; the only signal was
   backup *staleness* crossing a 26h threshold (`watchdog.py:500`, `BACKUP_STALE_THRESHOLD`). The flow failed at
   02:00:00 and the first alert fired the next morning.
5. **Then it alerted every 5 minutes.** `watchdog.py:510-513` states the intent plainly — *"For simplicity, alert
   every tick on backup staleness"* — so a single stale backup produces ~288 Slack messages/day (`log.warning` +
   unconditional `slack_poster` at `:514-517`). The state field `last_backup_alert_iso` (`:109`, loaded `:124`,
   dumped `:138`) is **written** at `:520` but **never consulted for deduplication or control flow** — it is loaded (`:124`) and re-serialized (`:138`), so "read nowhere" would be literally false; it is inert, which is why deleting or repurposing it is safe. Alert fatigue
   is itself an outage risk: the July blackout was missed for 14 days.

6. **The password prefix leaked into logs.** `'0r'` — the first two characters of the live worker password — is
   now in the Prefect flow-run state message and the worker logs.

## Goal

A credential containing any legal character cannot break **or be silently altered by** any consumer, and a backup
that fails is visible within minutes and reported once.

Note the coupling: hazards 1 and 2 are consumer- and producer-side respectively, but hazard 3 is only closed by
producer-encoding + consumer-decoding *as a pair*. **T1 alone does not reach this Goal** — see D1.

## Non-goals

- **Relocating the backup target.** `/Volumes/sapphire-backup` is *not* a mounted volume (`mount` shows nothing;
  it is a plain directory on the boot disk), so dumps sit on the same disk as the database. Real, but already a
  named Plan 158 follow-on.
- **Verifying dump restorability.** Nothing checks that a written dump can actually be restored. Named follow-on.
- **Changing the secret-generation alphabet.** Excluding awkward characters from generated passwords would mask
  this class rather than fix it, and cannot constrain a human-set password.

## Open decisions (owner)

- **D1 — how far to fix. ✅ DECIDED (owner, 2026-08-14): ship T1 now.** Backups have been dead since
  2026-08-13 02:01 and a restart-requiring macOS update (Tahoe 26.6.1) is pending on the host, so the most
  recent restorable dump ages by a day every day. Restoring backups today outweighs closing the whole class in
  one atomic deploy. **Accepted with an explicit, bounded risk:** T1 alone arms the `@` regression documented in
  T1 below (`make_url` silently truncates a password at the first `@`). This cannot fire against the **current**
  credentials, which are generated base64 (alphabet `A–Za–z0–9+/=`, no `@`) — **so T1 carries a hard
  precondition: verify no live DB secret contains `@` before deploying, and do not set a manual password until
  T2 lands.** T2 remains specified and is the actual fix for the class; T3 is independent. Original options
  retained below for the record.
- **D1 (original framing).** (a) Consumer only: `backup.py` → `make_url`. Fixes the live outage, minimal risk, but
  leaves hazards 2 **and 3** live. (b) Consumer + producer: also percent-encode at composition. **Recommended
  (b).** Be explicit about what (a) does *not* buy: it restores backups today, but a `&` rotation still breaks
  every container (hazard 2), and a password containing a literal `%HH` (valid-hex) sequence is still silently mis-decoded by every
  consumer including post-T1 `backup.py` (hazard 3, verified above — `make_url` decodes `%XX` exactly as
  `unquote` does). **Only T1+T2 together meet the stated Goal**; T1 alone is a partial fix, chosen for speed. T1
  is separable and shippable first either way.
- **D2 — how the producer should encode. ✅ DECIDED (owner, 2026-08-14): (a)+(c) — "construct, don't splice".**
  `pg_dump` moves to discrete `PG*` vars (no URL at all), and **every remaining URL is CONSTRUCTED with
  SQLAlchemy's `URL.create()`**, never assembled by string interpolation — in Python, replacing the `sed` at Site
  A and the inline `cat` at Site B. Rationale: parser choice only decides *which* passwords break; the defect is
  that we **build** credential URLs by splicing, creating an encode/decode contract both ends must honour
  perfectly forever. `URL.create()` removes the contract by construction — you cannot forget to encode because
  you never write the string. Verified in this repo's env: `URL.create()` → `render_as_string()` → `make_url()`
  round-trips `/`, `@`, `%41`, `a&b|c\d`, `p@ss/w%rd=+` and `x"y\`z$(id)` **all exactly**, and its default
  rendering is `postgresql+psycopg://u:***@h:5432/d` — i.e. **it would also have prevented the `'0r'` leak**
  (defect 6). Original options retained below for the record.
- **D2 (original framing).** Options:
  - **(a) Percent-encode the password into the URL** (`quote(pw, safe="")` at composition). SQLAlchemy decodes
    automatically and `backup.py` (post-T1) reads the decoded value. This changes the URL for **every** service at
    once, so it needs an atomic deploy plus the consumer audit in T2.
  - **(b) Discrete `PG*` vars instead of a URL, everywhere.** Rejected: the SQLAlchemy consumers all want a URL,
    so this is a large refactor with no gain for them.
  - **(c) Combined — discrete `PG*` vars for the backup/`pg_dump` path only, percent-encoding for the rest.**
    `pg_dump` reads `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` natively with **zero URL parsing**, and
    host/port/user/db are already plain non-secret literals in the compose templates. Benefit: takes the one
    component actually on fire out of the URL hazard class *by construction* rather than by parser choice, and
    out of T2's consumer audit. Cost: an entrypoint + compose change, hence a deploy, whereas T1 is pure code and
    shippable now — so T1 is still the fastest path to restoring backups even under (c). T2's producer-side
    encoding ships regardless, for the SQLAlchemy consumers. Owner picks (a) or (a)+(c); if (c) is chosen, T1's
    `make_url` swap is superseded for `backup.py` and its red-first test is rewritten against the `PG*` path
    (same assertions, no URL). Implementation detail deliberately left to build time.
- **D3 — flow-failure alerting.** Defect 4 argues for alerting on any Prefect flow-run failure. That overlaps the
  dead-man's-switch work (Plan 158 D11) and may belong there rather than here.

## Tasks

> **Build scope 2026-08-14: T1 only.** T2 is blocked on **D2** (encode-in-place vs `PG*` for the backup path) and
> needs its own deploy; T3 is independent and can follow. Do not build T2/T3 in the T1 change.


### T1 — `backup.py` must parse the URL the way the rest of the codebase does (Repo)

*In:* `src/sapphire_flow/flows/backup.py:57-70`. *Out:* the dump/retention logic, the schedule, the target dir.

Replace `urlparse` with `sqlalchemy.engine.make_url` — the parser already used by `db/engine.py` and therefore
the one every other consumer is validated against. `make_url` returns the password already decoded, so the
existing `unquote(...)` calls (`flows/backup.py:65`, `:68`) must be **removed**, not kept — double-decoding would
corrupt any password containing an encoded `%`.

**T1 does not close hazard 3.** `make_url` decodes `%XX` exactly as `unquote` does (verified above), so a
password carrying a *raw* `%HH` sequence such as `%41` is still silently mis-read after T1 — and a sequence decoding to a non-UTF-8 byte (e.g. `%cd`) becomes an **undecodable replacement character**, a third corruption mode. A lone `%`, a trailing `%`, `%4` or `%zz` are preserved unchanged. Only T2's producer-side encoding fixes that.
The `%` case in the test below therefore asserts the *post-T2* contract and must be written against a
**producer-encoded** URL; a raw-`%` URL is expected to decode "wrong" until T2 lands, and the test must say so
rather than pretending T1 fixed it.

**⛔ `@` MUST NOT be a T1 red case — `make_url` REGRESSES it** *(Codex blocker, verified empirically in this
repo's env)*. `urlparse` splits userinfo at the **last** `@` and so preserves a raw `@` in a password;
SQLAlchemy's `[^@]*` password grammar splits at the **first** one and silently truncates:

| password  | `urlparse`            | `make_url`   |
|-----------|-----------------------|--------------|
| `ab/cd`   | **raises** (this bug) | `'ab/cd'` ✅ |
| `ab@cd`   | `'ab@cd'` ✅          | **`'ab'`** ❌ silent |
| `ab%41cd` | `'ab%41cd'` ✅        | `'abAcd'` ❌ silent |
| `ab%cd`   | `'ab%cd'` ✅          | `'ab\ufffd'` ❌ undecodable |
| `ab%zzcd`, `abcd%`, `ab%4` | preserved | preserved (not `%HH`) |

So T1 in isolation would trade a **loud crash** for a **silent wrong password** (an auth failure, not a
traceback) for any password containing `@`. It is safe *today* only because generated credentials are base64
(alphabet `A–Za–z0–9+/=`, no `@`) — but that is a property of the current generator, not a guarantee, and a
human-set password would arm it.
**Therefore:** the T1 red case is **`/` only**. `@` and raw `%HH` become **T1+T2 integration** cases asserted
against a *producer-encoded* URL, where `quote(pw, safe="")` + `make_url` round-trips all of
`/ @ %41 %cd %zz` and a trailing `%` exactly (verified). Ship T1 and T2 atomically, or accept the documented
`@` regression window in between.

**Red-first (the point of the task):** a test building a connection URL whose password contains `/` and
asserting `pg_dump` is invoked with `--host=postgres --port=5432 --username=sapphire_worker
--dbname=sapphire` and `PGPASSWORD` equal to the **exact original password**. Must be proven to fail against
current `backup.py`, reproducing `ValueError: Port could not be cast`. `+` and *encoded* `%` are **regression**
cases (they already pass today), not red cases — label them as such so a green run is not mistaken for proof.

**Verify:** `uv run pytest tests/unit/flows/test_backup.py -q`; full suite; ruff; pyright ratchet.

### T2 — the producer must not splice a raw secret into a URL (Repo, gated on D1/D2)

*In:* **both** producer sites — `docker/entrypoint.sh:19-28` (Site A) **and** `docker-compose.yml:52-55`, the
`prefect-server` inline command (Site B). *Out:* which secret file is read (Plan 147 Slice D), the
drop-to-app-user step, and the choice of stock image for `prefect-server`.

**Approach (per the D2 decision): construct, never splice.**

1. **`pg_dump` takes `PG*`, not a URL.** `dump_database_task` receives `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/
   `PGDATABASE` and parses **nothing**. This supersedes T1's `make_url` swap for `backup.py` and takes the one
   component that actually caught fire out of the hazard class permanently — including the `@` regression T1
   knowingly accepted. T1's red-first `/` test is rewritten against the `PG*` path with the same assertions.
2. **Every remaining URL is built with `sqlalchemy.engine.URL.create()`** from typed parts
   (`drivername, username, password, host, port, database`) and rendered with
   `render_as_string(hide_password=False)` **only** at the point of use. No `sed`, no `printf`, no f-string
   assembly of a credential URL anywhere.
3. **Composition moves into Python.** The `sed` at Site A cannot be made safe by escaping — `&`, `\` and the `|`
   delimiter are all special in a replacement. Both runtime images ship Python (app: `python:3.14.6-slim`,
   `Dockerfile:42`; Prefect: `prefecthq/prefect:3-python3.11`, `docker-compose.yml:51`), so use it. **Pass the
   secret as an `argv` element or on stdin — never interpolate it into the `-c` program text**, which would
   reintroduce string-splicing one layer down.
4. **Site B (`prefect-server`) is the one place a URL string is genuinely unavoidable** — it is a stock image
   consuming `PREFECT_API_DATABASE_CONNECTION_URL`. Build that string with `URL.create()` too, in a `python -c`
   replacing the inline `cat` splice.
5. **Never log a rendered URL.** `URL.render_as_string()` defaults to `hide_password=True`; rely on that default
   and pass `hide_password=False` only where the value is consumed, never where it is logged. This closes defect
   6 (the `'0r'` prefix reaching a Prefect state message).

**Belt-and-braces, explicitly NOT the fix:** generating secrets as **URL-safe** base64 (`-_` for `+/`) is worth
doing separately, but it would merely have hidden this bug, and cannot constrain a human-set password — so it
must not substitute for the above. Tracked as a follow-on, not part of T2's acceptance.

**Round-trip guard:** assert at composition time that parsing the constructed URL back yields the **exact**
original secret; fail loudly rather than starting a service with a silently-altered credential.

**Consumer audit is part of this task, not an afterthought.** Enumerate the consumers **at implementation time
with grep** — do not trust a list pinned in this doc (line numbers rot; an earlier draft of this plan cited
`run_forecast_cycle.py:1625`, which is an unrelated `SAPPHIRE_REQUIRE_NWP` check — the real read is at `:1563`).
The grep must cover **the compose files too**, not just Python packages — Site B is exactly the site a
`src/ tools/ cli/ flows/`-only grep misses:

```sh
grep -rn 'DATABASE_URL' src/ tools/ cli/ flows/ 2>/dev/null
grep -rn "os\.environ\[.DATABASE_URL.\]\|os\.getenv(.DATABASE_URL." src/
grep -rn 'DATABASE_URL\|CONNECTION_URL\|/run/secrets/' docker-compose*.yml docker/
```

Every hit must be shown to decode percent-encoding, i.e. to route through SQLAlchemy (`db/engine.py` →
`create_engine`) or `make_url`. Any hit that hand-parses the URL is a **T2 blocker**. (At drafting time the audit
found ~15 readers across `db/engine.py`, the `flows/*` modules, `tools/observation_coverage_summary.py` and
`cli/import_basin_package.py`; all but `flows/backup.py` route through SQLAlchemy. Treat that as a prior to
re-verify, not as the checklist.) If D2 option (c) is taken, `flows/backup.py` leaves this audit entirely.

**⛔ Red-first, corrected** *(Codex blocker — the drafted test would not have proven producer encoding)*. The
weakness: a combined `& | \ / @ %` credential is red today **because of `@` alone**, so an implementation that
encoded only `@` and left `/` raw would turn it green while fixing nothing. `make_url` *accepts* a raw `/`, and a
lone/non-hex `%` is unchanged — neither can carry the proof. Do **not** claim Site B's `/` fails under
`make_url`; it fails only under `urlparse`.

The test must therefore:
1. Include a literal **`%41`** in the password (the one character class that is silently *mis-decoded* rather
   than rejected), alongside `& | \ / @` and a trailing `%`.
2. Assert the emitted userinfo is **exactly `quote(password, safe="")`** — a direct assertion on the encoding,
   not an indirect one via a parser that may tolerate the un-encoded form.
3. Additionally assert `make_url(url).password == password` (round-trip), which the encoded form satisfies for
   every case in the T1 table.
4. **Execute the real composition** — the actual `docker/entrypoint.sh` lines and the actual `sh -c` string
   extracted from `docker-compose.yml` — never a re-implementation of them in the test. A test that duplicates
   the producer logic proves only that the duplicate agrees with itself.

Prove each fails today: Site A's `&` case visibly corrupts via the sed replacement; both sites' `%41` case
decodes to `A`; and per-character assertions make it impossible to pass by encoding a proper subset.

**Verify:** `shellcheck` + `bash -n` on the entrypoint; the composition test for both sites; a container smoke
test that every service still connects — explicitly including `prefect-server` reaching its `prefect` database,
since Site B is the only live producer of that URL.

**Deploy note:** this changes the URL for all services simultaneously, now including `prefect-server`. It must
ship as one deploy with a post-deploy check that each service connects, and a rollback to the previous image.
Trade-off accepted: folding Site B in widens the T2 blast radius from the app containers to the Prefect server
itself, so the smoke test above is a gate, not a formality. The alternative — deferring Site B — was rejected
because it would leave the owner credential in exactly the hazard class this plan exists to close.

**T2 pickups from the T1 PR review (Codex APPROVE, 2 minors — deliberately NOT fixed in T1):**
- `_to_libpq_url` (`flows/backup.py:18`) is now **semantically redundant** — `make_url` accepts SQLAlchemy driver
  suffixes natively, so the regex normalisation is dead weight (plus five tests and a cross-reference at
  `docker/bootstrap-roles.sh:27`). Not fixed in T1 because **T2 deletes this whole code path** when `pg_dump`
  moves to `PG*`; churning it now would cost a version bump, a full gate run and another review round while
  backups are down.
- `TestPgDumpArgConstruction` (`tests/unit/flows/test_backup.py:113`) calls `make_url` **directly**, so despite
  its name it does not exercise production argv construction. Pre-existing (it previously called
  `urlparse`+`unquote` the same way) and *not* a weakening — the new locking tests do cover the production path —
  but the description is misleading. **T2 must either drive these through `dump_database_task.fn` or extract a
  real argument-builder helper**, since T2 rewrites argv construction anyway.

### T3 — a stale backup must alert once, not 288 times a day (Repo)

*In:* `src/sapphire_flow/ops/watchdog.py:498-520` (the backup-staleness block) plus the `WatchdogState` fields at
`:104-142`. *Out:* the 26h threshold, the staleness check itself (`newest_backup_mtime`, `:499-500`).

**Reuse the existing counter-based convention — do not invent a timestamp scheme.** The repo's hysteresis
convention is a `consecutive_*_failures: int` on `WatchdogState` fed to the *generic*
`should_alert_health(prev_failures, current_ok, current_fail)` — already reused verbatim for the health probe and
both BAFU checks (forecasts and observations), with a comment at the BAFU forecast call site stating outright
that "the decision function is generic, not health-specific". `last_backup_alert_iso` is **not** part of that
convention: it is written in the staleness block and read nowhere.

(Line numbers deliberately omitted below — this section is rationale, not a scope boundary, and this same plan
warns in T2 that pinned line numbers rot. Refer to the symbols by name; the In/Out citations above mark scope.)

Therefore:

1. Add `consecutive_backup_failures: int = 0` to `WatchdogState`, with the same `raw.get(..., 0)`
   backward-compatible `load`/`dump` treatment as the BAFU counters.
2. Drive the backup alert through `should_alert_health(...)`, mirroring the BAFU forecast call site — first stale
   tick, periodic re-notify, and **one recovery alert** on the transition back (which today does not exist at all).
3. Delete `last_backup_alert_iso` rather than building on it. Removal is state-file safe: `load` reads keys via
   `raw.get`, so a stale key in an existing state file is simply ignored.

**Re-notify cadence — a real trade-off, stated not hidden.** `ALERT_REPEAT_EVERY = 6` means "every 6th
consecutive failure ≈ 30 min at the 5-minute tick". Applied unchanged to a *daily* backup that is stale for a
day, that is ~48 messages/day — a 6× improvement on today's ~288, but not the "once" in this task's title. To
keep exactly one convention while making the cadence proportionate to a daily check, add a **keyword-only**
`repeat_every: int = ALERT_REPEAT_EVERY` parameter to `should_alert_health` and pass a backup-specific constant
(e.g. `BACKUP_ALERT_REPEAT_EVERY = 288` ≈ once per 24h at a 5-minute tick). Defaulting the parameter leaves the
health and both BAFU call sites bit-identical. If the owner prefers zero signature change, fall back to
`ALERT_REPEAT_EVERY` and accept ~48/day — but say so explicitly rather than letting the title over-promise.

**Red-first:** a test driving several consecutive stale ticks through `run_once` with state persisted between
calls (as production does) and asserting exactly one alert plus the configured re-notify, and a recovery alert on
the transition back to fresh. Must fail against current code, which alerts unconditionally on **every** tick and
never alerts on recovery. Also assert that a `WatchdogState` loaded from a pre-existing state
file (no `consecutive_backup_failures` key) defaults to 0 rather than raising.

**Verify:** `uv run pytest tests/unit/ops/test_watchdog.py -q`.

### T4 — the backup role cannot read the database it backs up (Repo + Host) — **NEW, live blocker**

**Found by deploying T1 (2026-08-14 14:24 UTC).** T1 worked: the URL now parses and `pg_dump` is actually
invoked. It then failed on the *next* bug, from the same Plan 147 Slice D change:

```
RuntimeError: pg_dump failed (exit 1): pg_dump: error: query failed:
ERROR:  permission denied for table access_tokens
```

The backup flow runs inside `prefect-worker` and therefore connects as **`sapphire_worker`**
(`DATABASE_URL_TEMPLATE=postgresql+psycopg://sapphire_worker@postgres:5432/sapphire`). Verified on the host:
`sapphire_worker` holds **no grants at all** on `access_tokens` — only `sapphire` (owner) and `sapphire_api` do.
**That is correct least-privilege design and must not be reverted** — Slice D exists precisely so no app
container can read credentials. The defect is that a *whole-database* backup was left running on a
*deliberately partial* role. So Plan 147 Slice D produced **two** independent backup failures, and the first
(the `/` password) masked the second.

**Do NOT fix by granting `sapphire_worker` more privilege** — that re-opens exactly what Slice D closed.
**Fix:** a dedicated read-only role, e.g. `sapphire_backup` with the built-in `pg_read_all_data` (confirmed
available: PostgreSQL **16.4**, role present), its own secret, mounted only where the backup runs. Consider
moving the backup out of the worker into its own job so the worker never holds a read-all credential.
Composes cleanly with T2: that credential reaches `pg_dump` as `PG*` vars, so it is never parsed.

**Red-first:** a test asserting the backup path connects as the backup role, not the worker role. Host
acceptance: `backup-database` reaches `COMPLETED` **and** the dump passes `pg_restore --list`.

### T5 — a FAILED backup leaves a 0-byte dump that SILENCES the staleness alert (Repo) — **NEW, severity-raising**

The failed run above left `sapphire_20260814_142409.dump` at **0 bytes**. `newest_backup_mtime`
(`ops/watchdog.py:258-263`) globs `*.dump` and takes the newest **mtime** — it never checks size or validity.
So a failed backup produces a **fresh-looking artifact that clears the stale alert**.

**This means T1, alone, converted a loud failure into a silent one.** Before T1 the `ValueError` was raised
*before* `pg_dump` ran, so no file was created and staleness was correctly detected (confirmed: no other 0-byte
dumps exist on the host). After T1, `pg_dump` runs, creates the file, fails — and the monitor goes quiet. The
0-byte file was removed manually; the behaviour is still latent for **any** future `pg_dump` failure (disk full,
network, permissions).

**Fix, both halves:**
1. **Producer:** delete the partial dump on failure — `dump_database_task` must remove `dump_file` before
   raising, so a failed run leaves no artifact.
2. **Monitor:** `newest_backup_mtime` must ignore non-viable dumps — at minimum size > 0; better, the newest
   dump validates (`pg_restore --list` succeeding is the real test, and is what proved both safety dumps).
   A monitor that accepts any file named `*.dump` cannot distinguish a backup from a touch.

**Red-first:** (a) a `pg_dump` failure leaves **no** file behind; (b) a 0-byte (and a truncated) `*.dump` does
**not** satisfy the freshness check. Both must fail against current code.

## Exit gates

Full `uv run pytest`, `ruff format --check` + `ruff check`, pyright ratchet, `shellcheck` + `bash -n` for T2's
Site A plus `docker compose config -q` for Site B — and each of T1/T2/T3's locking tests **proven red** against
current committed code.

## Acceptance (host, after deploy)

The next scheduled `backup-database` run reaches `COMPLETED`, a new dump appears with a **current** timestamp,
and the watchdog posts a single recovery message. Confirm on the mini via the Prefect flow-run state — not by the
absence of alerts, which is exactly the evidence that failed us in July.

## Follow-ons

- **Rotate the worker DB password** — its first two characters are in the logs (defect 6). Do this *after* T1/T2,
  so the rotation lands on code that tolerates any character.
- Relocate the backup target off the boot disk (Plan 158 follow-on).
- Verify dump restorability as part of the backup flow.
- Alert on Prefect flow-run failure (D3 — likely Plan 158 D11 / dead-man's-switch work).
