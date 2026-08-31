---
status: DRAFT
created: 2026-08-31
plan: 215
title: Ship the operator scripts in the runtime image so a deployment can be reproduced
scope: Copy a curated set of operator scripts into the runtime image, plus the .dockerignore hygiene that keeps build junk out. No change to any script's behaviour.
depends_on: []
blocks: [188]
source: 2026-08-31 — Plan 188 T4 could not run because scripts/import_caravan_attributes.py, merged in T1-T3, is absent from the deployed image
---

# Plan 215 — the operator CLI we shipped isn't in the image

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

This adds **one or two `COPY` lines to a Dockerfile** and a curated list. It changes no script's
behaviour and no runtime code path.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.**
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no script registry, no plugin system, no entrypoint
   framework, no packaging migration (console-scripts / `[project.scripts]` is § Deferred).
4. **Do not propose rewriting the scripts** or moving them under `src/`.
5. **Adding length is a cost.**

## The defect — measured, not inferred

`Dockerfile` copies `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `alembic.ini`, `alembic/`,
and `docker/`. **It never copies `scripts/`.** The runtime stage copies only `/app/.venv` and
`/app/src` from the builder (`Dockerfile:130-131`). Confirmed on the mini 2026-08-31:

```
$ docker exec sapphire_flow-api-1 ls /app/
alembic  alembic.ini  config  config.toml  docker  src
```

This is **not** a `.dockerignore` exclusion — `scripts` is not mentioned there. The directory is
simply never copied.

**Consequence, already realised:** Plan 188 T1-T3 built, reviewed and merged an *operator CLI*
(`scripts/import_caravan_attributes.py`) whose entire purpose is to be run against a deployment.
It cannot be run against any deployment. T4 is blocked on this, and every future operator tool
will hit the same wall silently — the tool merges green, and the gap only appears when someone
tries to use it in production.

**Owner's framing (2026-08-31):** *"we might want to re-deploy on a different machine and then
it's good if we can fully reproduce what we have done here."* An image that cannot run the
procedures that built the deployment cannot reproduce it.

## Decisions

- **D1 — Ship a CURATED set, not `scripts/` wholesale.** The directory is **3.0 MB across 19
  entries** and includes things that have no business in a production image: `__pycache__/`,
  `codex-review.sh` (development tooling), `dhm_precip/` (research code), and
  `bootstrap-mac-mini.sh` + `launchd/` (host provisioning that runs *outside* any container).
  Shipping those enlarges the image and the attack surface for no operational benefit.

  **Verified, so it is not the reason for curating:** no script embeds a secret. The
  `password`/`secret`/`token` matches in `scripts/import_caravan_attributes.py` and
  `scripts/bootstrap-mac-mini.sh` are a comment about *avoiding* password leakage in a log line
  and references to secret *paths*. Curation is about relevance and image hygiene, not
  exfiltration risk.

- **D2 — The curated set is the scripts an operator runs AGAINST a deployment.** Proposed:
  `import_caravan_attributes.py`, `onboard.py`, `backfill_meteoswiss_history.py`,
  `backfill_era5_land_history.py`, `check_readiness.py`, `validate_forcing_reference.py`,
  `regenerate_icon_grid_asset.py`. **Excluded:** host provisioning (`bootstrap-mac-mini.sh`,
  `launchd/`), dev tooling (`codex-review.sh`), research (`dhm_precip/`,
  `audit_distribution_shift.py`, `063_e2e_verify.py`, `plan100_*.py`), and anything Nepal-stack
  specific unless the owner wants it. **The boundary is an owner decision** — T1 must not
  silently widen it.

- **D3 — The scripts must be importable, not just present.** `scripts/` is not a package and the
  script does `from import_caravan_attributes import ...` style imports only when `scripts/` is on
  `sys.path`. T1 must verify the shipped script actually *runs* in the container, not merely that
  the file exists — the failure this plan fixes was itself a "looks fine, isn't there" failure.

- **D4 — `__pycache__` must not ship.** Add it to `.dockerignore` if a curated `COPY` would
  otherwise carry it. Stale bytecode in an image is a real source of confusing behaviour.

## Task

**T1 — copy the curated scripts into the runtime image and prove they run.**
*In:* the `COPY` line(s) in `Dockerfile` (builder and/or runtime stage as needed), the
`.dockerignore` hygiene from D4, and one line in `docs/deployment/mac-mini-staging.md` recording
that operator scripts live at `/app/scripts` and how to invoke one.
*Out:* changing any script; moving scripts under `src/`; a console-scripts migration (§ Deferred);
adding scripts beyond D2's list without saying so.
*Exit:* a built image contains exactly D2's set at `/app/scripts`; **`--help` runs successfully
inside the container for at least `import_caravan_attributes.py`** (proving importability per D3,
not just presence); `docker exec … ls /app/scripts` matches the curated list; image size delta
recorded.

## Deferred (not drafted)

**Promote operator scripts to console entry points** (`[project.scripts]` in `pyproject.toml`), so
they install into the venv and are on `PATH` with no `sys.path` handling and no `COPY` at all.
Cleaner, and it removes D3's importability question entirely — but it touches packaging for every
script and is a bigger change than unblocking Plan 188 needs today.

## Non-goals

Rewriting scripts · moving them into `src/` · a packaging migration · shipping host-provisioning
or research code · changing what any script does · Plan 188 T4 itself (this only unblocks it).

## Exit gates

- `docker exec <container> ls /app/scripts` lists exactly D2's curated set.
- `docker exec <container> python /app/scripts/import_caravan_attributes.py --help` exits 0.
- `.dockerignore` prevents `__pycache__` from entering the image.
- The deployment doc records where operator scripts live and how to run one.
