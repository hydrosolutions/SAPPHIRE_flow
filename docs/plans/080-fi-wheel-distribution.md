---
Status: DRAFT
Priority: LOW (deferred — no trigger met yet)
Created: 2026-06-24
Plan: 080
Title: Publish ForecastInterface as a versioned wheel and drop the CI guard exception
Branch: TBD
PR: TBD
Supersedes: the temporary exception introduced by Plan 079
---

# Plan 080 - FI wheel distribution + remove temporary wheel-guard exception

## Status / why this is deferred

This plan is **DRAFT and low priority**. It is the documented removal path for the
*temporary* arrangement that landed with Plan 076 (FI adherence) and Plan 079 (CI
wheel-guard exception), both merged to `main` on 2026-06-24 (PR #25 → `cf1e4e0`).

Nothing here should be implemented until the **removal trigger** below is met. It
exists so the temporary state is tracked against a concrete migration, not lost.

## Current (temporary) state on main

- `forecastinterface` is a **git-pinned** dependency:
  `pyproject.toml` declares it, and `[tool.uv.sources]` pins it to
  `https://github.com/hydrosolutions/ForecastInterface.git`, rev `v0.1.17`
  (`uv.lock`: commit `303aa422da45e293070ef1522251c782bbbf2b7b`). FI is public,
  pure-Python (depends only on `polars` + `pydantic`), and not published to any
  package index.
- Because a git pin has no published wheel, the supply-chain `wheel-only-guard`
  cannot use a single `uv sync --no-build`. Plan 079 implemented a scoped
  **two-step guard exception** in `.github/workflows/ci.yml` (step 1 keeps
  `--no-build` and guards every non-FI package; step 2 reinstalls **only**
  `forecastinterface`), documented in `docs/standards/security.md` and
  `docs/standards/cicd.md`.
- The Dockerfile **builder** stage carries `git` (added in Plan 079 follow-up) so
  `uv sync` can clone the FI git pin at image-build time. The runtime stage does
  not.

## Removal trigger (do not start before this is true)

ForecastInterface is published as a **versioned wheel** (`forecastinterface==0.1.x`)
to a **hydrosolutions private package index**, and SAPPHIRE Flow can resolve it
from that index in CI and Docker builds. (PyPI is technically viable but the
project decision is a private index — see [[project_fi_packaging_distribution]].)

## Goal

Migrate `forecastinterface` from the git pin to a registry/index wheel dependency,
then delete the temporary CI exception and its supporting docs/Dockerfile changes,
restoring the single-command `uv sync --no-build` wheel-only guard for **all**
packages.

## Non-goals

- Internalizing FI into `sapphire_flow` (explicitly rejected —
  install-footprint killer; keep FI a small separate distribution).
- Publishing FI to public PyPI (project prefers a private index).
- Any change to FI adapter behavior, the FI contract, or model code.

## Tasks (high-level — to be expanded to phased tasks when promoted to READY)

1. **Publish FI wheel** — build and publish `forecastinterface==0.1.x` to the
   hydrosolutions private index (FI repo work, coordinated with Sandro; out of
   this repo's tree but a prerequisite). Confirm the index is reachable from
   GitHub-hosted CI and the Docker build (auth/token strategy decided here).

2. **Migrate the dependency** — in `pyproject.toml`, change `forecastinterface`
   to a versioned constraint (`forecastinterface==0.1.x` / `>=0.1.x,<0.2`),
   **remove** the `[tool.uv.sources]` git pin, configure the private index
   (`[[tool.uv.index]]` / `extra-index-url` + credentials via CI secret), and
   `uv lock`. Confirm the lock now records FI from the index with a wheel.

3. **Restore the wheel-only guard** — in `.github/workflows/ci.yml`, replace the
   Plan 079 two-step block with the single canonical
   `uv sync --frozen --no-build --no-cache --no-install-project` guard (now that
   FI resolves to a wheel, `--no-build` must pass for every package). Revert the
   exception wording in `docs/standards/security.md` and `docs/standards/cicd.md`
   so the guard command is back in sync across all three files (the Plan 064
   invariant).

4. **Drop builder `git`** — remove `git` from the Dockerfile builder apt list if
   nothing else in the build needs it (a wheel install no longer clones FI).
   Verify the image still builds.

5. **Gates + verification** — full local gate suite (ruff, pyright ratchet,
   pytest); confirm the wheel-only-guard, build-image-and-scan, and FI smoke/
   conformance tests stay green; version bump + tag per convention.

## References

- [[project_fi_packaging_distribution]] — packaging/distribution decision
- [[project_forecast_interface_contract]] — FI contract relationship
- Plan 079 (`docs/plans/079-fi-ci-wheel-guard-exception.md`) — the exception this
  plan removes
- Plan 064 — wheel-only guard / supply-chain policy this restores in full
