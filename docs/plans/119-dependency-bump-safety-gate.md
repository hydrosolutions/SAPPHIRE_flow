---
status: DRAFT
created: 2026-07-15
plan: 119
title: Dependency-bump safety gate — make CI catch dangerous major bumps before merge
scope: A CI gate + policy that flags stateful/breaking dependency bumps that green CI cannot catch on its own.
depends_on: []
blocks: []
---

# Plan 119 — Dependency-bump safety gate

## Status

**DRAFT.** Do not implement until promoted to READY.

## Provenance

Dependabot **PR #78** (`postgis/postgis:16-3.4 → 17-3.4`) passed **every** CI check and was one click
from merge — yet merging it would have taken staging down, because a PostgreSQL **major** version bump
cannot boot against an existing PG16 data directory (see Plan 118). **CI was green because CI always
starts from an empty database and never sees a persistent volume**, so it is structurally blind to this
entire class of break.

The near-miss generalises: **green CI is not a merge criterion for dependency bumps that change stateful
or environment-coupled behaviour.** Other members of the same class:
- a database/broker **major** version (data-directory / wire-protocol incompatibility);
- a base-image OS bump that changes glibc / available system libs;
- a Python **minor** bump (3.14 → 3.15) that CI may not even run yet;
- a lockfile change that silently pulls a transitively-vulnerable or yanked version.

CI green + a `chore(deps)` title reads as "safe to click." This plan makes the dangerous subset **loud**
so a human decision is forced, and the safe subset stays frictionless.

## Objective

A CI job on every Dependabot / dependency PR that **classifies the bump** and, for the dangerous class,
**fails or hard-flags** with an actionable message — so a stateful major bump can never again slip
through on green tooling checks. The common, safe bumps (patch/minor of a normal library, a CI-action
patch) stay auto-mergeable.

## Non-goals

- Not a replacement for the existing test suite — an **addition** that catches what tests cannot.
- Not blanket "block all Dependabot" — that trains people to rubber-stamp, which is the current failure.
- Does not itself perform migrations (Plan 118 owns the Postgres one).

## Design

### 1. Classify the bump

A `dependency-safety` CI job (triggered on PRs labelled `dependencies`, or authored by
`app/dependabot`) inspects the diff and computes a **risk tier**:

- **BLOCK (dangerous — fail the job):**
  - a **stateful service image** major bump — `postgres`/`postgis`, `redis`, `rabbitmq`, any image
    holding a persistent named volume. Detect by: the image appears in `docker-compose.yml` **with a
    `volumes:` mount**, and the semver **major** (or the `postgis` `NN-` prefix) increased.
  - a **base-image** major/distro change in the `Dockerfile` (e.g. `python:3.14-slim` → `3.15-slim`, or
    `-slim` → a different base).
  - a **Python `requires-python`** change in `pyproject.toml`.
- **REVIEW (hard-flag — job passes but posts a sticky PR comment + requires a human label):**
  - a **major** bump of any runtime library in `pyproject.toml`.
  - a change touching the **FI / recap git-pin** or the wheel-guard machinery (`ci.yml` §wheel-only-guard).
- **ALLOW (silent — the common case):**
  - patch/minor of a normal library; a GitHub-Action patch bump; a dev-dependency patch.

Encode the rules as **data** (a small `deps-safety.toml` or inline in the workflow), not scattered
`if`s — the list of stateful images and the tier boundaries will grow.

### 2. The BLOCK message must tell the reader exactly why and what to do

Not "failed". Something like:
> 🛑 `postgis/postgis` **16 → 17** is a PostgreSQL **major** bump. It cannot start against the existing
> PG16 data volume without a migration. See **Plan 118**. This PR must not merge until the migration is
> executed on the target host. To override (you have run the migration), apply the `db-migrated` label.

An explicit, auditable override label beats an unexplained red X that people learn to force-merge past.

### 3. Branch protection

Add `dependency-safety` to the **required** checks on `main`, so a BLOCK genuinely prevents merge — the
whole point is that green *other* checks can no longer carry a dangerous bump through.

### 4. Optional, higher-value: an "upgrade against real data" smoke

For the stateful class specifically, a nightly (not per-PR — too slow/stateful) job that:
- restores a **small anonymised fixture dump** into the OLD version,
- starts the NEW version against that data directory,
- asserts it boots and `alembic upgrade head` + a row-count census pass.

This would have caught #78 directly rather than by static classification. Scope it as a **follow-up** —
the classifier (§1-§3) is the cheap 80% and should land first.

## Verification / exit gate

- A **test PR** that bumps `postgis` major (a throwaway branch) **fails** `dependency-safety` with the
  Plan-118 message — proving the gate catches the exact miss that motivated it.
- A test PR bumping a normal library **patch** passes silently (no false-positive friction).
- `dependency-safety` is a required check on `main`.

```bash
# lint the workflow + the rules file
uv run ruff check .github/  2>/dev/null || true
# (the real gate is the two test PRs above, run in CI)
```

## References

- Dependabot PR #78 (the near-miss).
- **Plan 118** (the Postgres migration a BLOCK points at).
- `docs/standards/cicd.md` (CI topology, required checks).
- `.github/workflows/ci.yml` (where the job lands; the wheel-only-guard is the existing precedent for a
  bespoke dependency-safety CI step).
- `.github/dependabot.yml` (grouping/labels the classifier keys off).
