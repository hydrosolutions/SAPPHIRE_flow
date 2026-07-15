---
status: READY
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
- a lockfile change that silently pulls a **yanked** release. (Transitively-vulnerable
  versions with a fixed upstream are **already** caught — the `lint` job runs a Trivy filesystem
  scan over `uv.lock`/`pyproject.toml` on every PR, failing on HIGH/CRITICAL fixable CVEs, `ci.yml:24-43`.
  So CI is *not* blind to that subset; only *yanked-but-not-CVE* releases remain, and those are
  mitigated today by the 48 h Dependabot cooldown, `dependabot.yml:32-37`. This class is therefore
  **out of scope** for the new gate — see the Design note — to avoid duplicating Trivy.)

CI green + a `chore(deps)` title reads as "safe to click." This plan makes the dangerous subset **loud**
so a human decision is forced, and the safe subset stays frictionless.

## Objective

Stop the dangerous class of dependency bump from reaching a merge-ready state on green tooling checks —
**by preventing the dangerous PR from opening at all where we can (Dependabot `ignore:` rules), and by a
CI classifier that hard-flags the residual, human-authored cases** with an actionable message. The
common, safe bumps (patch/minor of a normal library, a CI-action patch) stay auto-mergeable.

## Non-goals

- Not a replacement for the existing test suite — an **addition** that catches what tests cannot.
- Not blanket "block all Dependabot" — that trains people to rubber-stamp, which is the current failure.
- Does not itself perform migrations (Plan 118 owns the Postgres one).

## Design

The controls are layered. §0 is prevention (strongest, cheapest — no PR ever opens). §1 is a classifier
that catches the residual cases §0 cannot prevent (manual edits + fields Dependabot never touches). §2–§3
are the message and the enforcement story. §4 is a follow-up. §5 is the mandatory gate-parity audit.

### 0. Prevention first — Dependabot `ignore:` rules (primary control for image majors)

The strongest, simplest control for the stateful-image and base-image **major** classes is to stop the
dangerous PR from ever being opened. `.github/dependabot.yml` already governs exactly these updates and
already carries per-ecosystem cooldown/grouping (`dependabot.yml:45-69`). It supports per-package
`ignore:` on `version-update:semver-major`, e.g. under the `docker-compose` ecosystem entry:

```yaml
    ignore:
      - dependency-name: "postgis/postgis"
        update-types: ["version-update:semver-major"]
```

and under the `docker` ecosystem entry, for the Python base image (which Dependabot tracks as the
`python:3.14.6` tag — see `Dockerfile:3,29` — where the risk axis is the **minor**, so ignore both):

```yaml
    ignore:
      - dependency-name: "python"
        update-types: ["version-update:semver-major", "version-update:semver-minor"]
```

**Audit every stateful image, not just postgis.** A `docker-compose` `ignore:` rule must exist for
*each* image that holds a persistent named volume, per the stateful-class definition (§1). Auditing
`docker-compose.yml` today (grep for `volumes:` mounts on `image:` services) yields **two** such
services, not one:
- `postgis/postgis:16-3.4` (`docker-compose.yml:6`, volume at `:11`) — the motivating case; add the
  `semver-major` ignore above (removed together with the Plan 118 migration).
- `prefecthq/prefect:3-python3.11` (`docker-compose.yml:38`, volume `prefect_data:/data/prefect` at
  `:60`) — add a `semver-major` ignore for `prefecthq/prefect` too. **Note (documented decision, not a
  blind omission):** Prefect Server's authoritative state lives in the *external* Postgres
  (`PREFECT_API_DATABASE_CONNECTION_URL=…@postgres:5432/prefect`, `docker-compose.yml:41`), so the
  `/data/prefect` volume is far less load-bearing than PG's data directory; the ignore rule is
  precautionary (a Prefect major can still bring server/worker API-schema skew) rather than a proven
  data-loss vector like postgis.

```yaml
    ignore:
      - dependency-name: "prefecthq/prefect"
        update-types: ["version-update:semver-major"]
```

**Caveat on the "primary control" framing (postgis `NN-EXTVERSION` tag).** postgis tags are
`NN-EXTVERSION` (`16-3.4`), **not** standard semver, and this plan has **not** verified that
Dependabot's `docker-compose` parser classifies a leading-`NN` bump (`16-3.4 → 17-3.4`) as
`version-update:semver-major` for `ignore:` purposes — we only know Dependabot *opened* PR #78, not that
the `ignore:` update-type filter would *suppress* a future one. The exit gate adds an explicit check
(below); **if the ignore rule does not fire for the `NN-EXTVERSION` format, §1's path-independent
classifier is the real primary control for this case** and §0 degrades to best-effort. §1 fires
regardless of PR author or Dependabot's tag parsing, so the dangerous case is caught either way.

The `ignore:` set **fails closed by construction** (no PR = nothing to force-merge), needs **zero new CI code** and
**no branch-protection wiring** for this subset — strictly stronger than "open the PR, then hope the
classifier fires and nobody force-merges past a red X."

Trade-offs (noted, not hidden):
- An ignored major never surfaces even as a *notification*. Mitigate with a comment block in
  `dependabot.yml` listing each ignored package, why, and the plan that unblocks it, so re-enabling is a
  deliberate, auditable act. Re-enabling the postgis rule is part of the **Plan 118** exit gate (the
  migration and the ignore-rule removal land together).
- `ignore:` only binds Dependabot. A **human** who hand-edits `docker-compose.yml` to bump the postgis
  tag bypasses it entirely — which is why §1 keeps a defense-in-depth classifier rule for that same edit.

### 1. Classifier for the residual cases (`dependency-safety` job)

§0 prevents the *Dependabot-proposed* image majors. A classifier is still needed for cases that arrive as
**human-authored** PRs Dependabot never proposes, plus defense-in-depth against manual edits to the files
§0 protects:
- a **manual** stateful-image bump in `docker-compose.yml` (bypasses the `ignore:` rule);
- a **manual** base-image re-pin in the `Dockerfile` (`FROM python:3.14.6 → 3.15.0`);
- a **`requires-python`** floor change in `pyproject.toml` — Dependabot's `uv` ecosystem bumps dependency
  *constraints*, never the interpreter-floor field; only a human ever edits it, typically alongside a
  manual base-image bump (exactly the coordinated high-risk PR this plan targets).

**Trigger on content, not labels.** Label-gating (the original draft's "PRs labelled `dependencies`")
is unreliable: `requires-python` PRs are hand-authored with no guarantee the author remembers a label,
and GitHub's default `pull_request` event types are `[opened, synchronize, reopened]` — a label applied
*after* PR creation would not (re-)run the job unless `types: [labeled]` is added explicitly.

**Do NOT use a top-level `paths:` filter (incompatible with the §3 required-check plan).** A required
status check whose triggering workflow **never runs** (because a `paths:` filter did not match) is held
by GitHub as permanently **`Expected`/pending**, not "passed" — documented GitHub behaviour. So the
moment §3 makes `dependency-safety` a required check, a top-level `paths:` filter would wedge **every**
ordinary PR that does not touch the watched files in a permanent pending state — a severe regression from
today's zero-protection baseline. The trigger must be **unconditional**; the *scoping* moves inside the
job via a changed-files step that **exits success (skip)** when no watched file changed. This keeps
required-status-check semantics intact (the check always reports a concrete pass/fail, never "Expected"):

```yaml
on:
  pull_request:            # unconditional — no top-level paths: filter (see §3)

jobs:
  dependency-safety:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@…        # fetch-depth: 0 (see below)
      - name: Detect watched-file changes
        id: changed
        run: |
          # git diff the PR base against HEAD for the watched set;
          # set steps.changed.outputs.any=true|false
      - name: Classify (only if watched files changed)
        if: steps.changed.outputs.any == 'true'
        run: …            # BLOCK/REVIEW/ALLOW logic below
      # When no watched file changed the job runs, does nothing, and PASSES —
      # so an unrelated docs-typo PR reports dependency-safety = success, never Expected/pending.
```

The **watched-file set** (used by the changed-files step, not a trigger filter) is:

```
docker-compose.yml
Dockerfile
pyproject.toml
uv.lock
.github/workflows/ci.yml     # second postgis pin — see "Second pin location" below
```

**Diff against the PR base.** The classifier compares old-vs-new (old major vs new major), so it needs
the base commit. Every `actions/checkout` in `ci.yml` uses the default fetch-depth 1 (single commit, no
base history). The new job's checkout must set `fetch-depth: 0` (or fetch
`${{ github.event.pull_request.base.sha }}` explicitly) and diff the watched files against the base commit.

**Second pin location — `.github/workflows/ci.yml:108`.** The exact postgis pin
(`postgis/postgis:16-3.4@sha256:…`) is **duplicated** as the `integration` job's service container
(`ci.yml:108`), independent of `docker-compose.yml:6`. This matters twice:
- **Dependabot coverage:** Dependabot's `docker` ecosystem tracks `Dockerfile`/compose `image:` refs; it
  does **not** scan a workflow's `services:` blocks (those are neither `github-actions` `uses:` pins nor
  a Docker manifest it parses), so this pin does **not** auto-bump and the §0 `ignore:` rule does not
  need to cover it. A **human** editing `ci.yml`, however, can bump it — hence `ci.yml` is in the §1
  watched set.
- **Risk class:** the CI service container is **ephemeral** (started empty each run, **no volume mount**,
  `ci.yml:106-119`), so a major bump there does **not** trigger the data-directory break that motivates
  the BLOCK rule. The classifier therefore treats a postgis-major in `ci.yml`'s `services:` as **REVIEW**
  (advisory — "CI-only, ephemeral; verify the image tag still matches `docker-compose.yml` so CI keeps
  testing against the deployed engine version"), **not** BLOCK. Keeping the two pins in lockstep is the
  real value here; document this so the exclusion from BLOCK is a decision, not a gap.

**Parse the machine-readable field — never the comment.** CRITICAL: read the version from the `image:`
value itself — the segment **before** `@sha256:...` — never the trailing `# name:tag` comment. Dependabot
does **not** keep that comment in sync: PR #78's branch bumps the real tag to
`postgis/postgis:17-3.4@sha256:...` while leaving the comment reading `# postgis/postgis:16-3.4`
(identical before/after the exact major bump this plan exists to catch), and on `main` today caddy's
comment already reads `# caddy:2.9` while the pinned tag is `caddy:2.11.4` (`docker-compose.yml:212`,
stale since commit a97f6bf). A classifier that grepped the friendly comment would see zero diff on the
one PR the plan is built around and silently pass it. **Lock a regression fixture** built from the PR #78
diff (and the caddy staleness) so this failure mode cannot regress silently.

**BLOCK (dangerous — fail the job):**
- a **stateful-service image** major bump in `docker-compose.yml` — `postgres`/`postgis`,
  `prefecthq/prefect` (holds `prefect_data:/data/prefect`, `docker-compose.yml:60`), `redis`,
  `rabbitmq`, any image holding a persistent named volume. Detect by: the image appears with a
  `volumes:` mount, and the version parsed from the `image:` field (the `postgis` `NN-` prefix, or the
  semver major) increased. This is the **generic rule** — it fires on any newly-volume-mounted image, so
  it does not need a hardcoded per-image list and covers prefect-server as well as postgis. (Primary
  prevention is §0; this rule is the manual-edit backstop.)
- a **base-image** change in the `Dockerfile`. For CPython's `X.Y.Z` tag scheme the risk axis is the
  **minor** `Y`, **not** semver-major: `3.14 → 3.15` is major `3` on both sides yet is exactly the
  "CI may not even run 3.15 yet" threat (Provenance, `Dockerfile:3,29`). Flag when `Y` changes, when the
  base flavour changes (`-slim` → another base), or when `X` changes. Do **not** reuse a generic
  "semver-major increased" test here.
- a **`requires-python`** change in `pyproject.toml` — any change to the field.

**REVIEW (advisory flag — job passes, emits a notice; see §3 for why this is advisory):**
- a change touching the **FI / recap git-pin** or the wheel-guard machinery (`ci.yml` §wheel-only-guard)
  — environment-coupled, not fully exercised by tests.
- a **major** bump of a **native / compiled-extension** runtime dependency whose behaviour is
  environment-coupled and not fully caught by the suite — specifically `cfgrib`, `rioxarray`,
  `exactextract`, `forecastinterface`. **Ordinary pure-Python library majors (pandas, pydantic, …) are
  NOT flagged**: the `unit`/`integration` jobs (`ci.yml:46-134`) already exercise them, so CI is *not*
  blind to them; flagging every library major would reintroduce exactly the rubber-stamp fatigue the
  Non-goals reject, for a risk PR #78 did not demonstrate.

**ALLOW (silent — the common case):**
- patch/minor of a normal library; a GitHub-Action patch bump; a dev-dependency patch.

Detection rules should stay easy to extend (the native-extension list and the base-image scheme will
change), but the **encoding mechanism is left to the implementer** — a small data file *or* a handful of
inline conditionals. Today there are two volume-mounted stateful images (postgis, prefect-server) in
`docker-compose.yml` and a four-name extension list, and the stateful BLOCK rule is generic (any
volume-mounted image), so inline conditionals may well be sufficient; do not over-engineer a rules DSL up
front.

**Lockfile / CVE class is out of scope here (no duplication of Trivy).** This job does **not** diff
`uv.lock` for transitive CVEs — the existing Trivy fs scan already gates HIGH/CRITICAL fixable vulns on
every PR (`ci.yml:24-43`). The only residual gap (yanked-but-not-CVE releases) is left out of scope and
mitigated by the 48 h Dependabot cooldown (`dependabot.yml:32-37`), as noted in Provenance.

### 2. The BLOCK message must tell the reader exactly why and what to do

Not "failed". For a manual stateful-image bump, something like:
> 🛑 `postgis/postgis` **16 → 17** is a PostgreSQL **major** bump. It cannot start against the existing
> PG16 data volume without a migration. See **Plan 118**. This PR must not merge until the migration is
> executed on the target host.

**Override — via a committed allowlist entry, not an ephemeral PR label.** The original draft proposed
"apply the `db-migrated` label to override". That cannot work: `ci.yml` triggers only on
`[opened, synchronize, reopened]` (no `types: [labeled]`), so applying a label to an already-open PR does
**not** re-run `dependency-safety` — the BLOCK check would stay red forever and a required-check gate
(§3) could never be satisfied by that path. Instead, the override is a committed, code-reviewed entry in
a small allowlist file (mirroring the existing `.trivyignore` precedent, `ci.yml:28`): pushing that
commit re-runs CI via `synchronize`, clears the check, and leaves a durable audit trail in git history
(an ephemeral label leaves none once the PR merges). Label lifecycle question thereby dissolved — there
is no override label to create, scope, or clean up.

### 3. Enforcement — and the branch-protection reality

**`main` has no branch protection and no rulesets today** (verified: `gh api
repos/hydrosolutions/SAPPHIRE_flow/branches/main/protection` → 404 "Branch not protected"; `…/rulesets`
→ `[]`; `docs/standards/cicd.md:452` independently states "branch protection does not require it"). So a
human can already merge a PR with red CI, and there is **no existing required-checks list to append to** —
making `dependency-safety` bite is a repo-governance decision, not a one-line addition. Consequences:

- The §0 `ignore:`-rule BLOCKs need **no** branch protection — they work by preventing the PR.
- The §1 classifier BLOCK needs enforcement to be more than advisory. **Decision point (must be resolved
  before READY):**
  - **(a)** stand up full required-checks branch protection now — scope which of
    `lint`/`unit`/`integration`/`build-image-and-scan`/`wheel-only-guard`/`dependency-safety` become
    required, plus the PR-approval count; or
  - **(b)** ship `dependency-safety` as a required check via a **narrow GitHub ruleset scoped to only
    that check**, deferring the broader policy.
  **Recommendation: (b)** for this plan — minimal, and it does not force a repo-wide policy change in the
  same PR; (a) is tracked as a separate governance task. Until (a) or (b) exists, the classifier BLOCK is
  **advisory** (a red X a human can still bypass); the plan states this rather than claiming enforcement
  it does not yet have.
  **Prerequisite for either (a) or (b): the §1 trigger MUST be unconditional (no top-level `paths:`).**
  A required check backed by a path-filtered trigger reports `Expected`/pending — never a pass — on any
  PR that skips the filter, permanently blocking merges (see §1). The §1 design already runs the job on
  every PR and skip-passes when no watched file changed, which is precisely what makes it safe to mark
  required; do not re-introduce a `paths:` trigger when wiring the ruleset.

- **REVIEW tier is advisory-only** under today's zero-protection state: GitHub enforces nothing on a
  "passing job + notice", so nothing mechanically stops a merge without acknowledgement. Do not claim
  "requires a human label." If we later want REVIEW to *block*, it must become a second required check
  (via (a)/(b)) that fails until an acknowledgement commit lands — same mechanism as the §2 allowlist
  override. Deferred; called out so the language stays honest.

- **Emit REVIEW notices via the check-run / step summary, not a PR comment.** GitHub restricts the
  `GITHUB_TOKEN` to **read-only** on Dependabot-triggered runs (a hardening against a malicious manifest
  bump exfiltrating secrets), and **no `pull_request`-triggered workflow in `.github/workflows/` sets a
  `permissions:` block or posts PR comments** — the load-bearing precedent for going token-free. (The one
  workflow that does set a `permissions:` block, `live-lindas-weekly-autoretry.yml:36`, is
  `workflow_run`-triggered and grants only `actions: write` to re-run a failed schedule — immaterial
  here; the earlier "no `permissions:` block anywhere" framing was an overclaim.) A
  `permissions: pull-requests: write` grant may also be silently overridden for Dependabot events in some
  org configs. Writing to `$GITHUB_STEP_SUMMARY` (the check-run summary) needs **no** write token and
  works uniformly for human- and Dependabot-authored PRs — adopt that instead of a sticky PR comment.

### 4. Optional, higher-value: an "upgrade against real data" smoke

For the stateful class specifically, a nightly (not per-PR — too slow/stateful) job that:
- restores a **small anonymised fixture dump** into the OLD version,
- starts the NEW version against that data directory,
- asserts it boots and `alembic upgrade head` + a row-count census pass.

This would have caught #78 directly rather than by static classification. Scope it as a **follow-up** —
§0 + the classifier (§1–§3) is the cheap 80% and should land first.

### 5. Mandatory gate-parity audit + affected-doc updates (repo convention)

Adding `run:` steps to `ci.yml` triggers this repo's mandatory gate-parity audit
(`tools/gate_parity_check.py`, Plan 070), which classifies every CI `run:` step as
covered-by-check / covered-by-uv-sync / allowlisted-ci-only and must be run whenever `ci.yml` changes.
After adding the `dependency-safety` job, run `uv run python tools/gate_parity_check.py` and add any
needed `CI_ONLY_ALLOWLIST` entries for the new job's steps, or the audit will report drift.

`gate_parity_check.py` is a machine check only — it does **not** satisfy CLAUDE.md's "every code change
updates affected docs" rule. Adding a new CI job with a BLOCK/REVIEW/ALLOW policy **also** requires two
human-readable doc updates, both part of the exit gate:
- **`docs/standards/cicd.md` § CI workflow tiers** (the per-`run:`-step table, `cicd.md:356`+, already
  flagged "Extended by Plan 070 §C1"): add a row per `dependency-safety` `run:` step (local equivalent +
  CI-only reason), so the table stays the authoritative per-step inventory it claims to be.
- **`docs/standards/security.md` § Supply chain** (`security.md:372`+, the canonical home for scan
  *policy rationale* per `cicd.md:350`'s cross-reference): add a subsection stating **why** the gate
  exists, the BLOCK/REVIEW/ALLOW criteria, and the §2 committed-allowlist override mechanism — alongside
  the existing `### Image pinning` / `### Wheel-only dependency-update guard` / `### CVE scanning layers`
  entries.

## Verification / exit gate

- **§0 prevention (postgis):** after adding the `ignore:` rules, re-running Dependabot does **not** open
  a postgis-major (or python-minor base-image) PR — the dangerous PR never exists. **Verify the
  `NN-EXTVERSION` mechanism explicitly:** confirm (via `dependabot.yml` validation tooling / a
  GitHub-hosted dry-run, or by observing that a synthetic `16-3.4 → 17-3.4` is suppressed) that
  `ignore: version-update:semver-major` actually engages for postgis's non-semver tag format. If it does
  **not**, record plainly that §1's classifier is the primary control for this case and §0 is
  best-effort (§0 already flags this caveat).
- **§0 prevention (prefect):** the `prefecthq/prefect` `semver-major` ignore rule is present, with the
  documented "state lives in external Postgres" rationale.
- **§1 classifier — postgis BLOCK:** a throwaway PR that **manually** edits the `postgis` tag in
  `docker-compose.yml` to a new major **fails** `dependency-safety` with the Plan-118 message — proving
  the manual-edit backstop catches the exact miss that motivated the plan (and that it parses the
  `image:` field, not the stale comment; the locked PR #78 fixture asserts this).
- **§1 classifier — Dockerfile minor BLOCK (locked fixture):** a PR bumping `Dockerfile` `FROM
  python:3.14.6-slim → 3.15.0-slim` (major digit `3` **unchanged**, minor `Y` changes) **fails** — with
  a locked regression fixture, since this is the same "major digit unchanged" deception as the postgis
  stale-comment case (§1 line: "Do not reuse a generic semver-major-increased test here"). A base-flavour
  change (`-slim` → non-slim) and an `X` change are covered by the same fixture family.
- **§1 classifier — `requires-python` BLOCK (locked fixture):** a PR changing `requires-python` in
  `pyproject.toml` **fails**, with its own locked fixture (Dependabot never edits this field, so only a
  human PR exercises it).
- **§1 classifier — REVIEW / ALLOW:** a test PR bumping a normal pure-Python library **patch/minor**
  passes silently (no false-positive friction); a `cfgrib`/`rioxarray`/`exactextract`/`forecastinterface`
  **major** emits a REVIEW notice; a postgis-major edit confined to `ci.yml`'s ephemeral `services:`
  container emits **REVIEW** (not BLOCK) with the "keep in lockstep with docker-compose.yml" notice.
- **§1 required-check no-wedge (blocker regression):** a PR that touches **only** an unrelated file
  (e.g. a docs typo) runs `dependency-safety`, hits the changed-files skip path, and reports **success** —
  **never** `Expected`/pending — once the §3 required-check ruleset is enabled. This proves the
  unconditional-trigger + skip-pass design does not wedge ordinary PRs.
- The enforcement decision (§3 (a) vs (b)) is resolved and `dependency-safety` is a required check on
  `main` via the chosen mechanism (with an unconditional trigger, per §3) — or, if deferred, the plan
  explicitly records that the classifier BLOCK ships **advisory** until protection exists.
- **Affected-doc updates (CLAUDE.md hard rule):** `docs/standards/cicd.md` § CI workflow tiers has a row
  per new `run:` step, and `docs/standards/security.md` § Supply chain has the gate's policy-rationale
  subsection (§5).
- `uv run python tools/gate_parity_check.py` reports no drift after the `ci.yml` change.

```bash
# lint the workflow + the rules file (if any)
uv run ruff check .github/  2>/dev/null || true
# gate-parity audit after editing ci.yml
uv run python tools/gate_parity_check.py
# (the real gate is the test PRs above, run in CI)
```

## References

- Dependabot PR #78 (the near-miss); branch `origin/dependabot/docker_compose/postgis/postgis-17-3.4`
  (real tag bumped to `17-3.4`, trailing comment left at `# postgis/postgis:16-3.4`).
- **Plan 118** (the Postgres migration a BLOCK points at; owns re-enabling the §0 postgis `ignore:` rule).
- `.github/dependabot.yml` (`:32-37` cooldown, `:45-69` docker/-compose ecosystems — where §0 `ignore:`
  rules land; also the grouping/labels the classifier keys off).
- `.github/workflows/ci.yml` (`:24-43` existing Trivy fs scan; `:108` the **second** postgis pin, in the
  ephemeral `integration` service container; §wheel-only-guard the existing precedent for a bespoke
  dependency-safety CI step; no `pull_request`-triggered `permissions:` block; default checkout fetch-depth).
- `.github/workflows/live-lindas-weekly-autoretry.yml:36` (the only `permissions:` block in the repo —
  `workflow_run`-triggered, `actions: write`, immaterial to the PR-comment-vs-step-summary decision).
- `Dockerfile:3,29` (`FROM python:3.14.6-slim` — CPython `X.Y.Z`, risk axis = minor).
- `docker-compose.yml:6` (postgis image + comment, `:11` its volume), `:38` (prefect-server image,
  `:60` its `prefect_data` volume, `:41` its external-Postgres state), `:212` (caddy stale-comment evidence).
- `tools/gate_parity_check.py` (Plan 070 gate-parity audit, run on any `ci.yml` change).
- `docs/standards/cicd.md:452` (confirms `main` currently has no branch-protection requirement); `:356`+
  (CI workflow tiers table to extend, §5); `docs/standards/security.md:372`+ (§ Supply chain, policy home, §5).

## Plan-review resolution (2026-07-15)

3-round adversarial plan-review loop (Claude planner ↔ reviewer). It **escalated** — but the escalation
was a **round-limit artifact**, not an unresolved-defect signal: round 3's revision addressed all seven
residual findings; the loop stopped at `maxRounds` before a clean confirming review could run, so its
stall-detector reported the pre-revision count. Verified by inspection — every residual is handled in the
body above:

- **BLOCKER — the gate never fired on its own human-authored targets** (`requires-python`, the FI
  git-pin). Fixed: the job triggers **unconditionally on every `pull_request`** and classifies by
  file-diff internally (§1). Owner chose "trigger on files, not author"; the loop's unconditional trigger
  is the *correct implementation* of that intent — a top-level `paths:` filter would wedge a **required**
  check into permanent-pending on every unrelated PR (§3), so file-scoping happens inside the job, not on
  the trigger.
- **MAJOR — Dependabot's read-only token can't post a PR comment.** Fixed: REVIEW notices emit via the
  **check-run / `$GITHUB_STEP_SUMMARY`**, token-free (§3), not a PR comment.
- **MAJOR — branch protection isn't as-code and doesn't exist today.** Fixed: §3 names it an
  **owner-only manual action** (a narrow ruleset scoped to `dependency-safety`) and puts it in the exit
  gate as a human checkbox.
- **MAJOR — the classifier must not read the friendly tag comment.** Fixed: §1 parses the `image:` field
  before `@sha256:` and **never** the trailing `# name:tag` comment — which is stale for caddy *today*
  and would show zero diff on PR #78 itself. A regression fixture from the #78 diff locks it.
- **MAJOR — caddy is a third stateful image.** Handled: the **generic** volume-mounted-image rule (§1)
  covers caddy without a hardcoded list; §0 audits it. *(Also surfaced a real bug: caddy's compose
  comment reads `# caddy:2.9` while pinned at `2.11.4` — noted for a separate cleanup.)*
- **MAJOR — proportionality (native-package REVIEW tier).** Owner decision: **KEEP it.** `cfgrib`,
  `rioxarray`, `exactextract`, `forecastinterface` majors carry ABI/GDAL/wheel risk a fresh-env CI may
  not surface; REVIEW only flags, never blocks — a modest, deliberate extension beyond the triggering
  incident.
- **MINOR — prefect tag-format rigor.** The exit gate's "verify the `ignore:` rule actually engages"
  step now covers both the postgis `NN-EXTVERSION` and the prefect `3-python3.11` tag shapes.

**Status: review-converged, pending owner READY.** Two forks were owner-decided (file-triggering; keep
the native REVIEW tier); both align with the converged design above.
