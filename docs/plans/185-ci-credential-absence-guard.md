---
status: DRAFT
created: 2026-08-18
revised: 2026-08-18
plan: 185
title: CI credential-absence guard — a withheld secret must degrade loudly, not fail red
scope: Guard the `unit` job's private-extra install so a STRUCTURALLY absent credential (Dependabot, fork PR) degrades with a visible warning, while an absent credential on a run that should have had it — or on a PR that changes `uv.lock` — still fails. Two tasks: one `ci.yml` change, one paragraph in `docs/standards/cicd.md`. Explicitly NOT fork-PR support, NOT other secrets, NOT any new file.
depends_on: []
blocks: []
---

# Plan 185 — CI credential-absence guard

## Status
**DRAFT.** Not for implementation until the owner confirms.

## ⚖️ Proportionality — read before proposing any addition

**This is a ~2-file change**: one step in `ci.yml`, one paragraph in
`docs/standards/cicd.md`. It is a guard around an install command, not a subsystem.

**Ask what to REMOVE before asking what to add.** An addition earns its place only by
naming the concrete failure it prevents. Explicitly **not** wanted: abstraction over CI
config, a credential framework, retry/backoff, matrix expansion, any new file under
`tools/` or `tests/`, or extending the guard to secrets other than `AQUACAST_TOKEN`.

> **This budget has already failed once.** A `/plan` loop grew this doc 151 → 496 lines
> and stalled, having proposed *a Python re-implementation of GitHub Actions expression
> semantics* to test a two-branch conditional. An independent Codex pass returned
> NEEDS_CHANGES on proportionality grounds and its delete-list is what this rewrite
> applies. If a reviewer's suggestion is larger than the conditional it guards, it is
> wrong by construction.

## Why this exists

On 2026-08-18 the `unit` job began installing a private extra —
`uv sync --frozen --extra aquacast` (`ci.yml:103`). Within ~10 minutes all four open
Dependabot PRs went red. The failure reads as a dependency error
(`fatal: could not read Username for 'https://github.com'`) but the cause is that
**GitHub structurally withholds Actions secrets from Dependabot-triggered runs**; they
see only the Dependabot secret store.

The outage is fixed — `AQUACAST_TOKEN` was mirrored into the Dependabot store, matching
`RECAP_DG_CLIENT_TOKEN`. **This plan is about durability**: the same shape recurs for
fork PRs, for a rotated-and-not-yet-mirrored token, and for the next private dependency
— and each time it presents as a broken build rather than a missing credential.

**The trap in the obvious fix:** simply skipping the extra when the token is missing
re-opens the hole the extra was added to close. The shim's tests sit behind a
module-level `pytest.importorskip("aquacast")` (`tests/unit/models/test_aquacast_shim.py:28`),
so without the extra they skip into silence. Trading a red build for zero coverage is
worse, not better — hence D4.

## Decisions

- **D1 — three outcomes.**
  1. **Token present** → install the extra (today's behaviour, unchanged).
  2. **Token absent, Dependabot PR, `uv.lock` NOT touched** → plain
     `uv sync --frozen`, plus a `::warning::` naming the lost coverage.
  3. **Token absent, anything else** → **fail**, with a message naming
     `AQUACAST_TOKEN` and the store it belongs in.
  Case 3 covers both "somebody deleted the secret" and "this PR changes `uv.lock`",
  which is where the extra's resolution is most likely to break (per OD-2 below).
- **D2 — presence is read from a job-level env value, not a detect step.** Secrets are
  permitted in a job-level `env:` block, and a step-level `if:` can read the `env`
  context; only `if:` itself cannot reference `secrets.*` directly. So:
  ```yaml
  unit:
    env:
      AQUACAST_TOKEN_PRESENT: ${{ secrets.AQUACAST_TOKEN != '' }}
  ```
  and each branch is `if: env.AQUACAST_TOKEN_PRESENT == 'true'`. The token itself stays
  confined to the existing auth step (`ci.yml:96`). This removes an entire class of
  bug — an earlier draft's detect step read `$AQUACAST_TOKEN` with no `env:` binding of
  its own, which would have classified **every** run as credential-absent.
- **D3 — degraded coverage emits `::warning::`** so it is visible in the checks UI, not
  only in a log line.
- **D4 — the installed case must PROVE the shim tests ran.** Run the one node that
  genuinely requires the package and assert on pytest's own summary:
  ```bash
  set -o pipefail
  uv run pytest 'tests/unit/models/test_aquacast_shim.py::TestRealDiscovery::test_discover_models_returns_the_aquacast_model' -q --color=no | tee "$RUNNER_TEMP/aquacast-shim.txt"
  grep -Fq '1 passed' "$RUNNER_TEMP/aquacast-shim.txt"
  ```
  **Exit status alone is not sufficient** — verified locally: without the extra that
  node reports `no tests collected` and exits non-zero-but-not-failed, and a skip never
  prints `1 passed`. Because exactly one node is requested, no other test can satisfy
  it. No JUnit parser, no new test file.
- **D5 — scope is `AQUACAST_TOKEN` only.** `RECAP_DG_CLIENT_TOKEN` gates a
  *non-optional* dependency: without it no job syncs at all, so there is nothing to
  degrade to. Fork-PR support is a non-goal.
- **D6 — a `uv.lock` change is detected exactly, not by proxy.**
  ```bash
  gh pr diff "${{ github.event.pull_request.number }}" --name-only
  ```
  matched on an exact `uv.lock` line. **Capture the command's success before testing
  its output**, so an API failure fails closed rather than reading as "no lock change".
  Two implementation checks: the step needs `GH_TOKEN`, and the `unit` job declares no
  `permissions:` block today (only `build-image-and-scan` does, `ci.yml:232`) — confirm
  the default token can read PRs, or add `pull-requests: read`.
  *Rejected:* a Dependabot **branch-name prefix** proxy — it misclassifies `uv`-group
  PRs that do not touch the lock. *Rejected:* `fetch-depth: 0` — correct but
  disproportionate; every checkout is depth-1 today (`ci.yml:74`).

## Tasks

- **T1 — the guard** (`.github/workflows/ci.yml`). Job-level `AQUACAST_TOKEN_PRESENT`
  (D2), the D6 lock test, the three D1 branches, the D4 assertion on the installed
  path. Also correct the now-stale comment at `ci.yml:97`, which describes the install
  as unconditional.
- **T2 — the runbook paragraph** (`docs/standards/cicd.md`). State that a private
  dependency's token must be mirrored into **both** the Actions and Dependabot secret
  stores, and what the guard does when it is not. One paragraph, beside the existing
  private-clone notes.

```json
{"phases": [{"id": "T1", "tasks": ["T1"], "parallel": false},
            {"id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"]}]}
```

## Exit

Four observable outcomes:

1. A Dependabot PR that does not touch `uv.lock` is **green**, with a visible warning
   that aquacast coverage was skipped.
2. A PR that touches `uv.lock` without a reachable token **fails** with the D1-case-3
   message — not a git auth error, and not a silent skip.
3. A credentialed run **proves** the shim test passed (D4), and fails if it skipped.
4. `docs/standards/cicd.md` states the both-stores requirement.

## Non-goals

Fork-PR CI support. Other secrets (D5). Changing the aquacast pin, the `rich`
override, or the extra's contents. Migrating to a private index (Plan 080). Any change
to `lint`, `integration`, `wheel-only-guard`, or the image scan. Reconciling
`tools/gate_parity_check.py` — it is advisory, not wired into CI, and already reports
13 pre-existing drift rows; making it green is not this plan's job.

## Open items

- ~~**OD-1**~~ — RESOLVED: a `::warning::` annotation, not a PR comment (D3).
- ~~**OD-2**~~ — RESOLVED (owner, 2026-08-18): **PRs that touch `uv.lock` must run the
  extra**, folded into D1 case 3. Marked **"for now"** — reversible. Two things would
  justify revisiting: the extra pulls **torch**, so every weekly `uv`-group Dependabot
  PR pays the heaviest install in CI; and it assumes the token stays reachable from
  bot-authored branches.
- **OD-3** — a **fork** PR touching `uv.lock` is structurally unsatisfiable: forks never
  receive the secret, so D1 case 3 makes it a hard fail. Defensible — you cannot
  validate a lock change without the private dependency — but it means fork
  contributions could not change dependencies. Acceptable while there are no external
  contributors; confirm before treating as settled.

## Review provenance

Owner grill-me (OD-2) → `/plan` loop (3 rounds, **escalated/stalled**, grew the doc to
496 lines) → independent Codex pass (NEEDS_CHANGES on proportionality; supplied the D2
job-level-env simplification, the D4 `1 passed` check, and the D6 exact-diff mechanism)
→ this rewrite. The Codex pass also found two **pre-existing repo** faults unrelated to
this plan: a broken `bump-my-version` config (fixed, `d01a809`) and the advisory
gate-parity drift (out of scope, above).
