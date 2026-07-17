---
status: DRAFT
created: 2026-07-17
plan: 122
title: Package operational scripts into the deployed image (loose scripts/ → sapphire_flow.cli entry points)
scope: Operational one-off scripts must be runnable on a deployed host. Today several are loose `scripts/*.py` the Dockerfile does not copy, so they only run via a bind-mount workaround.
depends_on: []
---

# Plan 122 — Package operational scripts into the deployed image

## The gap (evidenced on staging 2026-07-17)

Running the 115b3 GO/NO-GO validation on the mac-mini staging host exposed a real
deployment defect: **the Dockerfile does not copy `scripts/` into the image.** It copies
`src/` (hence everything under `sapphire_flow/`), `alembic/`, `alembic.ini`, and
`config.toml` — but **not `scripts/`** (`Dockerfile:62-65`). So a loose `scripts/*.py`
file is simply **absent** from the deployed image:

```
$ docker compose run --rm prefect-worker python scripts/backfill_meteoswiss_history.py --help
python: can't open file '/app/scripts/backfill_meteoswiss_history.py': [Errno 2] No such file or directory
```

The 115 staging validation only proceeded by **bind-mounting the host repo's `scripts/`**
into a one-off container (`-v ~/SAPPHIRE_flow/scripts:/app/scripts:ro`) — a manual
workaround, not a supported operational path.

### Current split — loose vs packaged

| Location | In the image? | Files |
|---|---|---|
| `scripts/*.py` (loose) | **NO** (not copied) | `onboard.py`, `backfill_meteoswiss_history.py`, `validate_forcing_reference.py`, `check_readiness.py`, `regenerate_icon_grid_asset.py`, `063_e2e_verify.py`, `plan100_forecast_feed_resilience.py` |
| `src/sapphire_flow/cli/*.py` (packaged) | **YES** (via `COPY src/`) | `check.py`, `register_deployments.py` |
| `[project.scripts]` console entry points | — | only `check = "sapphire_flow.cli.check:main"` |

The repo **already has the right pattern** — `register_deployments` and `check` live in
`sapphire_flow.cli.*`, are baked into the image, and (for `check`) are exposed as a console
entry point. The loose `scripts/` files never got that treatment.

### Why it matters (beyond 115)

- **`onboard.py` is an OPERATIONAL script** (station onboarding, incl. the 115b2 §2C
  backfill-or-hold wiring) and is **loose → not in the image.** Station onboarding on a
  deployed host currently depends on the same bind-mount workaround (or has not been
  exercised on the deployed image at all — NEEDS-CONFIRM against how onboarding is actually
  run on the mini).
- **`backfill_meteoswiss_history.py` (115b2)** and **`validate_forcing_reference.py`
  (115b3)** are the immediate blockers this surfaced — the whole b1→b4 chain's operational
  gate could not run from the shipped image.
- Any FUTURE plan that ships an operational script inherits this bug silently — a loose
  script passes CI (tests import it fine from the source tree) yet is un-runnable in prod.

## Options

- **(A) `COPY scripts/ scripts/` in the Dockerfile.** Cheapest — one line. But it ships
  dev/verification scripts (`063_e2e_verify.py`, `plan100_forecast_feed_resilience.py`) into
  the production image too, and keeps `python scripts/foo.py` path-based invocation (no
  discoverable entry point, still fragile to WORKDIR).
- **(B) Promote OPERATIONAL scripts to `sapphire_flow.cli.*` console entry points**
  (the established pattern). Discoverable (`docker compose run --rm prefect-worker
  backfill-meteoswiss-history`), no path/bind-mount fragility, and it draws a clean line:
  **operational = packaged in `cli/`; dev-only = loose in `scripts/`.** More work (move each
  script's logic into a `cli/` module + a thin `main()`), but it is the correct shape.
- **(C) Hybrid — recommended:** do (B) for the operational scripts; leave genuinely
  dev-only scripts loose. Optionally also (A) as a belt-and-suspenders so nothing loose is
  *ever* un-runnable, but scoped to avoid shipping e2e/dev harnesses to prod.

**Recommendation: (C).** Promote the operational set to `cli/` entry points; keep dev-only
scripts loose and out of the image.

### Triage (proposed — confirm at plan-review)

| Script | Classification | Action |
|---|---|---|
| `onboard.py` | OPERATIONAL | → `sapphire_flow.cli.onboard` + `[project.scripts]` entry |
| `backfill_meteoswiss_history.py` | OPERATIONAL (115b2) | → `sapphire_flow.cli.backfill_meteoswiss` + entry |
| `validate_forcing_reference.py` | OPERATIONAL (115b3) | → `sapphire_flow.cli.validate_forcing` + entry |
| `check_readiness.py` | OPERATIONAL | → `cli` + entry (confirm) |
| `regenerate_icon_grid_asset.py` | OPERATIONAL (maintenance) | → `cli` + entry (confirm) |
| `063_e2e_verify.py` | DEV / verification | leave loose |
| `plan100_forecast_feed_resilience.py` | DEV / one-off audit | leave loose |

Keep the thin `scripts/*.py` wrappers (or delete them) — a wrapper can simply
`from sapphire_flow.cli.foo import main; main()` so existing docs/runbook invocations keep
working during the transition. Decide keep-vs-delete at plan-review.

## Tasks (phase 1)

- **1A — move operational script logic into `sapphire_flow/cli/<name>.py`** with a `main()`
  entry, preserving each script's existing argparse interface and behaviour verbatim
  (no behaviour change — this is a relocation + packaging change).
- **1B — register `[project.scripts]` console entry points** for each promoted script
  (kebab-case names, e.g. `backfill-meteoswiss-history`, `validate-forcing`, `onboard`).
- **1C — thin compatibility wrappers** (or deletion) of the loose `scripts/*.py` per the
  keep-vs-delete decision, so runbook/docs commands don't silently break.
- **1D — doc sync:** update `docs/deployment/mac-mini-staging.md` + any runbook that invokes
  `python scripts/…` to the packaged entry point form; note in `docs/standards/cicd.md` that
  **operational scripts live in `sapphire_flow.cli` and are baked into the image; loose
  `scripts/` are dev-only and not shipped.**
- **1E — (optional) Dockerfile belt-and-suspenders:** if the triage keeps any operational
  script loose, add a scoped `COPY` for exactly those — but prefer the entry-point route.

## Tests

- **Entry points import + expose `main`:** each promoted `cli.<name>` imports cleanly and
  `--help` runs (argparse intact). *Soundness: fails if a promoted module has a broken
  import or lost its CLI.*
- **Behaviour parity:** the promoted module produces the same argparse surface (options) as
  the old loose script — pin the option set so a silent CLI regression fails.
- **In-image smoke (integration / deploy-gate, NOT a unit test):** on a built image,
  `docker compose run --rm --no-deps prefect-worker <entry-point> --help` exits 0 **without
  any bind-mount** — the regression this plan exists to kill. *Must fail against the current
  image (script absent).*

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/           # ratchet vs baseline
uv run pytest
```
Plus a **deploy-gate**: build the image and confirm each promoted entry point runs
(`--help`) inside a one-off container with **no bind-mount**.

## Provenance

Surfaced 2026-07-17 while running the 115b3 staging GO/NO-GO validation over SSH — the
backfill (115b2) and validation (115b3) scripts were absent from the deployed image and only
ran via a manual `scripts/` bind-mount. Owner flagged the follow-up as important. DRAFT —
plan-review (incl. an independent Codex pass) before READY; confirm the triage and the
keep-vs-delete decision for the loose wrappers.
