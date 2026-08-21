---
status: COMPLETE
created: 2026-08-18
revised: 2026-08-18
plan: 185
title: CI credential-absence guard — a withheld secret must degrade loudly, not fail red
scope: Guard the `unit` job's private-extra install so a STRUCTURALLY absent credential (a Dependabot-actor run) degrades with a visible warning, while an absent credential on a run that should have had it — or on a PR that changes `uv.lock` — still fails. Two tasks: one `ci.yml` change, one paragraph in `docs/standards/cicd.md`. Explicitly NOT fork-PR support, NOT other secrets, NOT a new tool/framework. (Amended post-implementation: one locking test file under `tests/unit/tooling/`, matching the pre-existing one-guard-one-file convention there, is in scope — see the fixer note under Proportionality.)
depends_on: []
blocks: []
---

# Plan 185 — CI credential-absence guard

## Status
**COMPLETE** — shipped on `main` as `d532a13` (Plan 185 — a withheld CI credential should degrade loudly, not fail red (#187)).
*(Status reconciled 2026-08-21 in a housekeeping pass: the plan had shipped but was still marked READY, so it read as outstanding work.)*

**READY** — owner confirmed 2026-08-18, after two independent Codex rounds
(round 1: NEEDS_CHANGES on proportionality, 496 → 161 lines; round 2: NEEDS_CHANGES on
five specifics, including a push-event guard the rewrite had dropped). No open blockers;
OD-3 (fork PRs cannot change `uv.lock`) is an accepted consequence, not a fork.

## ⚖️ Proportionality — read before proposing any addition

**This is a ~2-file change**: one step in `ci.yml`, one paragraph in
`docs/standards/cicd.md`. It is a guard around an install command, not a subsystem.

**Ask what to REMOVE before asking what to add.** An addition earns its place only by
naming the concrete failure it prevents. Explicitly **not** wanted: abstraction over CI
config, a credential framework, retry/backoff, matrix expansion, a bespoke evaluator for
GitHub Actions expression semantics, or extending the guard to secrets other than
`AQUACAST_TOKEN`.

> **This budget has already failed once.** A `/plan` loop grew this doc 151 → 496 lines
> and stalled, having proposed *a Python re-implementation of GitHub Actions expression
> semantics* to test a two-branch conditional. An independent Codex pass returned
> NEEDS_CHANGES on proportionality grounds and its delete-list is what this rewrite
> applies. If a reviewer's suggestion is larger than the conditional it guards, it is
> wrong by construction.

> **Fixer note, post-implementation.** The original "NOT any new file" line above was
> written against that rejected evaluator, but its literal wording also covered a much
> smaller thing: a locking test file. `tests/unit/tooling/` already holds one file per
> CI guard (`test_recap_wheel_guard.py`, `test_recap_dependency_pin.py`,
> `test_trivy_gate_observability.py`) — each parses `ci.yml`, selects steps by `name`,
> and asserts structurally, exactly the shape this plan's own tests use. Multi-model
> review flagged the new file as an undocumented deviation from that line; this note is
> the documented decision: the non-goal is narrowed to "no new tool, framework, or
> expression evaluator", and `tests/unit/tooling/test_ci_credential_absence_guard.py`
> stays, sized to the behavioral assertions the review demanded (exact `if:` string
> equality, not substring checks) — not expanded beyond them.

## Why this exists

On 2026-08-18 the `unit` job began installing a private extra —
`uv sync --frozen --extra aquacast` (`ci.yml:103`). Within ~10 minutes all four open
Dependabot PRs went red. The failure reads as a dependency error
(`fatal: could not read Username for 'https://github.com'`) but the cause is that
**GitHub structurally withholds Actions secrets from Dependabot-triggered runs**; they
see only the Dependabot secret store.

The outage is fixed — `AQUACAST_TOKEN` was mirrored into the Dependabot store, matching
`RECAP_DG_CLIENT_TOKEN`. **This plan is about durability**: the same shape recurs for
fork PRs and for the next private dependency
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
  **Accepted limit:** a deleted *Dependabot-store* copy is indistinguishable from
  structural withholding, so on a Dependabot run with `uv.lock` unchanged it degrades
  (case 2) rather than failing. Distinguishing them would need a reachability check,
  which is out of scope — presence is not validity.
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
  exact node reports a collector error plus `1 skipped` and exits non-zero. It never
  prints `1 passed`, which is the signal being asserted on. Because exactly one node is requested, no other test can satisfy
  it. No JUnit parser, no new test file.
- **D5 — scope is `AQUACAST_TOKEN` only.** `RECAP_DG_CLIENT_TOKEN` gates a
  *non-optional* dependency: without it no job syncs at all, so there is nothing to
  degrade to. Fork-PR support is a non-goal.
- **D6 — a `uv.lock` change is detected exactly, not by proxy.**
  *(Amended post-implementation, fixer round — independent review found real gaps in
  both mechanisms this decision originally pinned; both are corrected below, without
  reopening D1–D5.)*
  ```bash
  gh api --paginate "repos/${{ github.repository }}/pulls/${{ github.event.pull_request.number }}/files" \
    --jq '.[] | .filename, (.previous_filename // empty)'
  ```
  matched on an exact `uv.lock` line, against **both** `filename` and
  `previous_filename` so a rename-away-from-`uv.lock` still counts as a touch.
  *(Was: `gh pr diff --name-only`. A diff's name-only listing carries only the
  post-rename path — a rename-away is invisible to it — and GitHub silently truncates
  a PR diff past 300 changed files.)*
  **Capture the command's success before testing its output**, so an API failure fails
  closed rather than reading as "no lock change".

  *(Amended second fixer round — independent review found `--paginate` does NOT lift
  the pull-request-files endpoint's own hard 3000-file response cap ("The paginated
  pull-request-files endpoint has neither limit" above was wrong on that point): a PR
  with more changed files than that could have `uv.lock` sit outside the returned
  pages and read as "no change" despite the command exiting 0. Fixed by reading the
  PR's own `changed_files` count first —
  `gh api "repos/.../pulls/<number>" --jq '.changed_files'` — and failing closed,
  exactly like an API failure, both when that lookup itself fails and when the count
  exceeds 3000. Only once the count is confirmed within the cap does the paginated
  `/files` listing get trusted.)*

  Three things are pinned here, not left to implementation:
  ```yaml
  # on the job — specifying ANY permission zeroes the unspecified ones, so
  # `contents: read` must be restated or checkout breaks. The unit job declares
  # no permissions block today (only build-image-and-scan does, ci.yml:232).
  permissions: { contents: read, pull-requests: read }
  # on the step
  if: >-
    env.AQUACAST_TOKEN_PRESENT != 'true' &&
    github.event_name == 'pull_request' &&
    github.actor == 'dependabot[bot]'
  env: { GH_TOKEN: "${{ github.token }}" }
  ```
  **`github.actor`, not the PR author.** *(Was: `github.event.pull_request.user.login
  == 'dependabot[bot]'`.)* GitHub withholds Actions secrets from a run according to who
  **triggered that specific run** (`github.actor`), not who opened the PR. A maintainer
  can push a further commit onto a Dependabot-opened branch; that push's
  `pull_request` `synchronize` event runs as the maintainer, with secrets restored —
  but `github.event.pull_request.user.login` still reads `dependabot[bot]` forever (it
  is fixed at PR-open time, unaffected by who pushes afterward). The original predicate
  would have misclassified a real missing-credential failure on that human-triggered
  run as an expected degrade — exactly the class of bug D1 case 3 exists to prevent.
  `github.actor` reflects the triggering identity per-run, so a human push with the
  token absent for an unrelated reason still falls through to the fail case.
  **The event guard is load-bearing.** CI also runs on `push` (`ci.yml:4-5`), where
  `github.event.pull_request.number` is empty; `gh` treats an empty selector as *no*
  selector and falls back to guessing a PR from the current branch. On `push` the token
  is present anyway (case 1), so the lock test must never be consulted there.
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

1. A Dependabot PR **with the token absent** that does not touch `uv.lock` is
   **green**, with a visible warning that aquacast coverage was skipped.
2. A PR that touches `uv.lock` without an exposed token **fails** with the D1-case-3
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

**Post-implementation fixer round** (independent Codex diff review + Claude design
review over the committed diff): six majors — the PR-author-vs-`github.actor` gap
(D6, three sites), the `gh pr diff` rename/300-file gap (D6), non-locking substring
tests, the new test file's undocumented deviation from "NOT any new file", and the
fail-step's `if:` needing exact-string (not substring) assertions. All six resolved in
`ci.yml`, the test file, and this doc (D6 amendments + proportionality note above);
none disputed.

**Second post-implementation fixer round** (independent Codex diff review): two
majors, one minor. Major 1 — `--paginate` does not lift the pull-request-files
endpoint's own hard 3000-file cap, so a PR past that size could have `uv.lock` fall
outside the returned pages and silently read as "no change"; fixed by reading the
PR's `changed_files` count first and failing closed (as an API failure would) both
when that lookup fails and when the count exceeds 3000 (D6 amendment above). Major 2
— no test locked the detect step's grep-match→`true`/no-match→`false` branch
mapping itself (only the outer API-failure fail-closed path was pinned); added a
test that isolates the inner `if`/`else` fragment and asserts each branch's exact
assignment — proven to fail (RED) against a mutant with the assignments swapped
before being accepted. Minor — two `ci.yml` comments were stale: the `unit`-job
private-clone comment still said the job "does not install the aquacast extra by
default" though the credentialed path now does unconditionally, and the Plan 185
comment block read as if `AQUACAST_TOKEN_PRESENT` were unconditionally false on every
Dependabot-attributed run, when GitHub substitutes a same-named Dependabot-store
secret in for a withheld Actions-store one — which is exactly why mirroring
`AQUACAST_TOKEN` into the Dependabot store fixed the original outage (see "Why this
exists" above). Both comments corrected to describe the actual conditional behavior.
All three resolved in `ci.yml` and the test file; none disputed.
