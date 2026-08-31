---
status: READY
created: 2026-08-31
plan: 218
title: Ship the operator scripts in the runtime image so a deployment can be reproduced
scope: Copy a curated set of operator scripts into the runtime image, plus the .dockerignore hygiene that keeps build junk out. No change to any script's behaviour.
depends_on: []
blocks: []
source: 2026-08-31 — scripts/import_caravan_attributes.py, merged by Plan 188 T1-T3, is absent from the deployed image. NOT a blocker for 188 T4, which prescribes bind-mounting instead; this plan is about reproducibility on a new host
---

# Plan 218 — the operator CLI we shipped isn't in the image

## Status

**READY.** Owner confirmed 2026-08-31 after two independent Codex rounds and a direct verification of the curated list (D3a).

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
and `docker/`. **It never copies `scripts/`.** The runtime stage copies `/app/.venv`, `/app/src`, `/app/alembic.ini` and
`/app/alembic` from the builder (`Dockerfile:130-133`) — but not `scripts/`. **The exact fix is a
single curated runtime-stage `COPY --chown=app:app … /app/scripts/` inserted after
`Dockerfile:133`; no builder-stage copy is needed.** Confirmed on the mini 2026-08-31:

```
$ docker exec sapphire_flow-api-1 ls /app/
alembic  alembic.ini  config  config.toml  docker  src
```

(Plain `ls` hides `/app/.venv`, which is also present — the point is only that `scripts` is absent.)

This is **not** a `.dockerignore` exclusion — `scripts` is not mentioned there. The directory is
simply never copied.

**Consequence:** Plan 188 T1-T3 built, reviewed and merged an *operator CLI*
(`scripts/import_caravan_attributes.py`) whose entire purpose is to be run against a deployment.
It cannot be run *from* any deployment image. Every future operator tool hits the same wall
silently — the tool merges green, and the gap only appears when someone tries to use it.

**⚠️ This does NOT block Plan 188 T4, and an earlier draft of this plan wrongly said it did.**
T4 already carries a reviewed recipe that works *without* any image change: it bind-mounts both
the CLI and the parquet into a one-off `prefect-worker`-based container
(`docs/plans/188-caravan-statics-operational-import.md` § T4). That recipe additionally requires
**both** private-repo secrets at build time, preserving `/entrypoint.sh` (where `DATABASE_URL` is
assembled, `docker/entrypoint.sh:10-23`), and **explicitly resolving the one-off
`WITH_AQUACAST=1` tag** — because `docker-compose.yml:80-83` pins `image: sapphire-flow:${VERSION}`
and would otherwise quietly run the ordinary image without aquacast, which fails the D2 preflight
and *looks like* a model-registration problem.

So this plan's value is the owner's stated one — **reproducing a deployment on another machine** —
not unblocking T4. It should be judged on that, and it is not on T4's critical path.

**Owner's framing (2026-08-31):** *"we might want to re-deploy on a different machine and then
it's good if we can fully reproduce what we have done here."* An image that cannot run the
procedures that built the deployment cannot reproduce it.

**Verified 2026-08-31:** the parquet sits on the mini at
`data/caravan/bafu_static_attributes.parquet` (host) and is **not visible inside any container**;
`docker cp` cannot fix that because the containers run a read-only rootfs
(`docs/standards/security.md:454`). A bind mount is genuinely required, exactly as 188 T4 says.

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
  `backfill_era5_land_history.py`, `validate_forcing_reference.py`. **`regenerate_icon_grid_asset.py` is excluded** — it writes into the source tree
  (`_ASSET_PATH.parent.mkdir(...)`, `scripts/regenerate_icon_grid_asset.py:112`) and every
  deployment service sets `read_only: true` (`docker-compose.yml:122,177,248`), so it would
  fail with `EROFS`. It is a build-time asset generator, not an operator tool.
  **`check_readiness.py` is excluded** — it inspects review
  metadata in plan/design documents (`scripts/check_readiness.py:110`) and `docs/` is not shipped
  (`.dockerignore:9`), so it could never work in the image; it is development tooling. **Excluded:** host provisioning (`bootstrap-mac-mini.sh`,
  `launchd/`), dev tooling (`codex-review.sh`, `check_readiness.py`), research (`dhm_precip/`,
  `audit_distribution_shift.py`, `063_e2e_verify.py`, `plan100_*.py`), and anything Nepal-stack
  specific unless the owner wants it. **The boundary is an owner decision** — T1 must not
  silently widen it.

- **D3 — Proving the script *runs* requires model resolution, not `--help`.** An earlier draft of
  this decision justified the check by sibling imports; **that rationale was false** — no curated
  script does `from import_caravan_attributes import …`, and plain execution already puts
  `/app/scripts` on `sys.path`. The real gap is later: `--help` exits inside `parse_args()`
  (`scripts/import_caravan_attributes.py:358`) **before** `resolve_required_static_names()` runs
  (`:369`), and that call reads the required statics from the **discovered** `cmal_pool_pt`
  adapter. `discover_models()` swallows an import failure and omits the model
  (`services/model_registry.py:125`), so on the ordinary image the operator command still dies
  with "model not registered" while `--help` exits 0.

  **This is not a missing plan — it is an existing build arg.** `WITH_AQUACAST=0` is the default
  (`Dockerfile:39`) and Plan 188 T4 already specifies a one-off
  `docker build --build-arg WITH_AQUACAST=1` image (`Dockerfile:32-44`). T1 must therefore verify
  model resolution **in a `WITH_AQUACAST=1` image**, not treat `--help` on the default image as
  proof.

- **D3a — the curated list is VERIFIED against the image, not chosen by name.** Both review rounds
  found an entry that could not work there (`check_readiness.py` needs `docs/`, `.dockerignore:9`;
  `regenerate_icon_grid_asset.py` writes the source tree against `read_only: true`). All five
  survivors were then checked directly (2026-08-31): `onboard.py`,
  `backfill_meteoswiss_history.py` and `backfill_era5_land_history.py` resolve
  `_REPO_ROOT / "alembic.ini"` to `/app/alembic.ini`, which the image **does** contain;
  `validate_forcing_reference.py` reads through a `HistoricalForcingStore` from the DB
  (`services/validation_gate.py:385-396`), **not** from `tests/fixtures` (excluded,
  `.dockerignore:8`); `import_caravan_attributes.py` needs only the bind-mounted parquet. No entry
  now depends on a path the image excludes.

- **D4 — `__pycache__` needs no work.** It is already excluded at `.dockerignore:3`. Recorded so a
  reviewer does not re-raise it; **no `.dockerignore` edit is required by this plan.**

## Task

**T1 — copy the curated scripts into the runtime image and prove they run.**
*In:* one runtime-stage `COPY` in `Dockerfile` after `:133` (per D1/D2's curated list), and one line in `docs/deployment/mac-mini-staging.md` recording
that operator scripts live at `/app/scripts` and how to invoke one.
*Out:* changing any script; moving scripts under `src/`; a console-scripts migration (§ Deferred);
adding scripts beyond D2's list without saying so.
*Exit:* a built image contains exactly D2's set at `/app/scripts`; `docker exec … ls /app/scripts`
matches the curated list; image size delta recorded. **And, per D3, in a `WITH_AQUACAST=1` image:
`resolve_required_static_names()` returns a non-empty set** — i.e. `cmal_pool_pt` resolves. `--help`
on the default image is a necessary smoke check, not the gate.

## Deferred (not drafted)

**Promote operator scripts to console entry points** (`[project.scripts]` in `pyproject.toml`), so
they install into the venv and are on `PATH` with no `sys.path` handling and no `COPY` at all.
Cleaner, and it removes D3's importability question entirely — but it touches packaging for every
script and is a bigger change than unblocking Plan 188 needs today.

## Non-goals

Rewriting scripts · moving them into `src/` · a packaging migration · shipping host-provisioning
or research code · changing what any script does · Plan 188 T4 itself — **and this plan does not unblock it**; T4 has its own
recipe (see above).

## Exit gates

- `docker exec <container> ls /app/scripts` lists exactly D2's curated set.
- `--help` exits 0 on the default image (smoke check).
- **In a `WITH_AQUACAST=1` image, `cmal_pool_pt` resolves and `resolve_required_static_names()`
  returns a non-empty set** — this proves the CLI is *operational in the image*. It does **not**
  prove Plan 188 T4 can run: T4 additionally needs the parquet bind-mounted, the DB URL from
  `/entrypoint.sh`, and the backend network (188 § T4).

**Station-count note (2026-08-31):** Plan 188 § "T4's premise is FALSE" records 2 river stations
against a 148-code manifest. **That is now stale** — the fleet finished onboarding and the DB holds
**148**, verified today. T4's station precondition is met; do not cite the old figure.
- The deployment doc records where operator scripts live and how to run one.
