# Plan 065 — Config overlays for environment variants (retire staging branches)

**Status**: READY
**Date**: 2026-04-20 (flipped to READY 2026-04-20 after two review rounds)
**Depends on**: none at the code level. Observes Plan 046 A3 progress — this
plan aims to retire the `staging-5-stations` branch workflow that Plan 046
currently relies on, so ideally lands during a lull between A3 dress
rehearsal cycles rather than mid-rehearsal.
**Scope**: Introduce a minimal environment-aware config-overlay mechanism so
a deployment can run from `main` with small config variants (5-station
staging subset, future integration-test subsets, potential per-region tweaks
for Nepal v1) without forking a branch. One base `config.toml` stays
canonical; overlays patch only the keys they need; all loaders consume the
merged result via a shared helper. After this lands, the branch-level config
workaround disappears and Plan 046 Revisions that still carry "rebase
`staging-5-stations` onto main" steps can drop them.

---

## Context

### Why now

Plan 046 Revision 9 §A1 encodes a recurring "rebase `staging-5-stations`
onto main" ceremony every time main advances. The cost isn't the rebase
itself — the `staging-5-stations` commit touches a single file
(`config.toml`) — but the structural cost: any session reading from `main`
is seeing the 169-station operational list while the staging dress rehearsal
needs 5 stations to iterate quickly. There is no current way to express
"this deployment uses a variant of config.toml" without a branch.

The same pressure will surface again:
- **Nepal v1**: DHM stations will live in a separate basin set; we will not
  want to merge them into `config.toml` before v1 is real, nor fork a Nepal
  branch indefinitely.
- **Integration tests**: a 3-station subset for CI smoke tests is a natural
  follow-up if this mechanism exists.
- **Ad-hoc debugging**: overriding a single QC threshold or NWP wait to
  reproduce a bug should not require editing the operational file.

### Principle

Base file = canonical operational truth. Overlays = deployment-scoped
patches selected at runtime. Everything merges through one helper, feeds
existing loaders, and stays subject to existing Pydantic validation.

### Non-goals

- **Not** restructuring `DeploymentConfig` schema or adding
  `extra = "forbid"`.
- **Not** changing the `SAPPHIRE_CONFIG` env-var semantics; it continues to
  point at the **base** file.
- **Not** secret injection via overlays (secrets stay in Docker secrets).
- **Not** list-append or conditional merge operators. Lists are **replaced
  wholesale** by the overlay — the only semantics we need for `basin_ids`
  and the use cases we can enumerate today. A future plan can add append
  semantics if a real need surfaces.
- **Not** moving the `staging-5-stations` branch deletion into this plan's
  exit gates — deletion is the operator's call after A3 completes. This
  plan lands the mechanism and marks the branch superseded.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **New env var `SAPPHIRE_CONFIG_OVERLAY`**, optional, comma-separated list of overlay file paths applied left-to-right (rightmost wins on conflicts). `SAPPHIRE_CONFIG` unchanged. Paths are interpreted relative to CWD when not absolute (matching how `SAPPHIRE_CONFIG` is handled today via `Path(...).read_text()` without `.resolve()`); Docker and production must use absolute paths. Missing files raise `FileNotFoundError` per D6; the error message reports the path as given. | Keeps backwards compatibility trivial: no overlay ⇒ identical behaviour to today. Comma-separated list lets us chain (e.g., `staging-5-stations.toml,debug-long-lookback.toml`) without restructuring. Absolute-in-Docker matches the existing `SAPPHIRE_CONFIG=/app/config.toml` convention. |
| D2 | **Shared merge helper `load_merged_toml(base: Path, overlays: list[Path]) -> dict`** in a new `src/sapphire_flow/config/_overlay.py`. All five config consumers (four loaders — `load_config`, `load_onboarding_config`, `load_qc_rules`, `load_forecast_qc_rules` — plus `flows/ingest_observations.py:_load_adapter_endpoint`) switch to this helper. | One merge implementation, one test suite. No drift across loaders. Today all five duplicate the TOML-read step anyway. |
| D3 | **Deep-merge dicts, replace lists.** Nested dicts are merged recursively; any list at any depth is replaced wholesale by the overlay's value. Array-of-tables (`[[danger_levels]]`, `[[seasons]]`) replaces wholesale. | Matches the only semantics we need. Preserves TOML array-of-tables ordering. Avoids the "did you mean append or replace?" footgun. |
| D4 | **Overlays live in `config/overlays/*.toml`** at repo root. Gitignored per-deployment overrides use `config/overlays/local/*.toml` (added to `.gitignore`). | Keeps canonical overlays (like `staging-5-stations.toml`) in version control so everyone sees them. Reserves a gitignored space for operator-only tweaks. |
| D5 | **Env-var resolution (`${SAPPHIRE_*}`) runs inside `load_merged_toml` using the canonical `_resolve_env_vars` from `config.deployment`**. Each file's raw text is resolved individually before `tomllib.loads` parses it; the merge then happens on the parsed dicts. Overlays can reference the same `${SAPPHIRE_*}` patterns as the base. | One resolver implementation, called the same way for base and overlays. Text-level resolution (not dict-walk) preserves the existing `load_config` behaviour bit-for-bit. Duplicate `_resolve_env_vars` in `qc_rules.py:14` is deleted as part of T2. |
| D6 | **Overlay misses are errors, not warnings.** If `SAPPHIRE_CONFIG_OVERLAY` names a path that doesn't exist, `load_merged_toml` raises `FileNotFoundError` with the full path. | Silent overlay drop during a staging run would be catastrophic (we'd silently run against 169 stations when intending 5). Fail loud. |
| D7 | **Overlay schema is not separately validated.** The overlay is a partial TOML that merges in; validation happens on the merged whole via the existing Pydantic models. | Keeps the implementation tiny. Invalid overlays surface as `DeploymentConfig` validation errors, which are already actionable. |
| D8 | **Docker Compose overlay file `docker-compose.staging.yml`** bind-mounts `./config/overlays/staging-5-stations.toml` into the same containers as the base `config.toml` and sets `SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/staging-5-stations.toml`. | Keeps the deployment story uniform: "select your compose overlays, select your config overlays, same mechanism." Plan 046's Mac-mini runbook becomes `docker compose -f docker-compose.yml -f docker-compose.staging.yml up`. |

---

## Task list

### T1 — Implement `load_merged_toml` helper

**File**: `src/sapphire_flow/config/_overlay.py` (new)

1. Function signature:
   ```python
   def load_merged_toml(
       base_path: Path,
       overlay_paths: list[Path],
   ) -> dict[str, object]:
   ```
2. Read base file; each overlay file; deep-merge per D3 semantics.
3. Run the existing `_resolve_env_vars` substitution on the **merged** TOML
   text or on the merged dict (whichever is easier — current substitution
   is text-level, so read each file's text, concatenate conceptually via
   parse-then-merge, then re-emit only if needed). Easiest: parse each to
   dict with `tomllib.loads(_resolve_env_vars(file.read_text()))`, then
   deep-merge dicts.
4. Missing overlay file → `FileNotFoundError` with absolute path per D6.
5. Empty overlay list → return base parse unchanged (backwards compatible).
6. Unit tests in `tests/unit/config/test_overlay.py` (new):
   - Empty overlay list returns base unchanged.
   - Overlay with scalar key overrides base scalar.
   - Overlay with nested dict deep-merges, preserving base keys not in overlay.
   - Overlay with list replaces base list (specifically `basin_ids`).
   - Overlay with array-of-tables replaces wholesale (e.g., `[[danger_levels]]`).
   - Multiple overlays apply left-to-right.
   - Missing overlay path raises `FileNotFoundError`.
   - Env-var substitution still works after merge.
   - `SAPPHIRE_CONFIG_OVERLAY` unset → `_resolve_overlay_paths` returns `[]`.
   - `SAPPHIRE_CONFIG_OVERLAY=""` (empty string) → returns `[]`.
   - `SAPPHIRE_CONFIG_OVERLAY="foo.toml,"` (trailing comma) → empty items filtered; returns `[Path("foo.toml")]`.
   - `SAPPHIRE_CONFIG_OVERLAY="  foo.toml , bar.toml  "` (whitespace) → items `.strip()`'d; returns `[Path("foo.toml"), Path("bar.toml")]`.

**Exit**: `uv run pytest tests/unit/config/test_overlay.py` green.

### T2 — Thread overlay through the five config consumers

**Files**:
- `src/sapphire_flow/config/deployment.py` (`load_config`)
- `src/sapphire_flow/config/onboarding.py` (`load_onboarding_config`)
- `src/sapphire_flow/config/qc_rules.py` (`load_qc_rules`)
- `src/sapphire_flow/config/forecast_qc_rules.py` (`load_forecast_qc_rules`)
- `src/sapphire_flow/flows/ingest_observations.py` (`_load_adapter_endpoint`) — the fifth direct `tomllib.loads` call; currently reads `[adapters.river_stations].endpoint` bypassing the config loaders because `[adapters]` is popped from `DeploymentConfig`.

1. Co-locate a helper `_resolve_overlay_paths() -> list[Path]` inside
   `src/sapphire_flow/config/_overlay.py`. Semantics: read
   `SAPPHIRE_CONFIG_OVERLAY`; return `[]` when the env var is unset **or
   the empty string**; otherwise split on comma, `.strip()` each item,
   filter out empty strings, return `[Path(s) for s in items]`.
2. In each of the four config loaders, replace the three-line TOML read
   with one call to `load_merged_toml`:
   ```python
   # Before (present in deployment.py, onboarding.py, qc_rules.py, forecast_qc_rules.py):
   raw_text = path.read_text()
   resolved_text = _resolve_env_vars(raw_text)
   data = tomllib.loads(resolved_text)

   # After:
   data = load_merged_toml(path, _resolve_overlay_paths())
   ```
   All post-`tomllib.loads` logic in each caller (pop sections, extract
   `archive_base_path`, map `paths_data_dir`, Pydantic validation) is
   **unchanged**.
3. In `flows/ingest_observations.py:_load_adapter_endpoint`, apply the
   same replacement so the `[adapters.river_stations].endpoint` read also
   honours overlays. The subsequent
   `.get("adapters", {}).get("river_stations", {}).get("endpoint", ...)`
   chain stays the same — it operates on the merged dict.
4. No signature changes on any function — loaders still accept an
   optional `path` and fall back to `SAPPHIRE_CONFIG`; `_load_adapter_endpoint`
   still returns a `str`. Only the internal TOML-read implementation
   changes.
5. **`_resolve_env_vars` deduplication** (per D5's "one canonical env-var
   resolver"). Currently the function is defined in two places and
   imported in three:
   - `config/deployment.py:235` — keep as canonical.
   - `config/qc_rules.py:14` — duplicate; **delete**.
   - `config/forecast_qc_rules.py:8` — already imports from `qc_rules`;
     switch import to `from sapphire_flow.config.deployment import _resolve_env_vars`.
   - `flows/ingest_observations.py:66` — currently imports from
     `qc_rules`; becomes dead after step 3 rewrites
     `_load_adapter_endpoint`. **Remove the import entirely.**
   - `config/onboarding.py:8` — already imports from `deployment`. After
     step 2 rewrites `load_onboarding_config`, the import becomes dead.
     **Remove the import.**
   - `config/_overlay.py` (new) — imports `_resolve_env_vars` from
     `config.deployment` and calls it on each raw-TOML text before
     parsing (per D5).
6. Unit tests:
   - Extend existing loader tests in `tests/unit/config/test_*.py` with
     one overlay case each (parametrized if feasible) confirming that an
     overlay patches the relevant section.
   - Add a test for `_load_adapter_endpoint` honouring an overlay that
     patches `[adapters.river_stations].endpoint`. Place in whatever file
     currently tests `ingest_observations` (likely
     `tests/unit/flows/test_ingest_observations.py` — confirm by file
     listing; if no dedicated test file exists, add a minimal one
     alongside).

**Exit**: all five consumers use `load_merged_toml`; `_resolve_env_vars`
has exactly one definition (`config/deployment.py:235`); the three
now-dead imports are removed; existing loader tests still green; new
overlay-aware tests green.

### T3 — Ship the canonical `staging-5-stations` overlay

**File**: `config/overlays/staging-5-stations.toml` (new)

1. Directory is new — create `config/overlays/.gitkeep` and
   `config/overlays/local/.gitkeep`.
2. Update `.gitignore` by appending exactly these two lines (order
   matters — the negation must come after the broader match, otherwise
   `.gitkeep` gets ignored silently and `git add` drops it):
   ```
   config/overlays/local/*
   !config/overlays/local/.gitkeep
   ```
3. Write the overlay file with **only** the patched keys. Do not restate
   keys that match the base (e.g., `data_source`) — the merge preserves
   base values for keys the overlay omits:
   ```toml
   # Staging dress rehearsal — 5-station subset.
   # Overlay for config.toml; select via SAPPHIRE_CONFIG_OVERLAY.
   # Deep-merged into [onboarding] — base's data_source is preserved.
   [onboarding]
   basin_ids = ["2004", "2009", "2033", "2085", "2091"]
   ```
4. Cross-check against the existing `staging-5-stations` branch
   (`dbdceee`) to confirm the 5 IDs match. At the time of writing:
   four rivers (2009, 2033, 2085, 2091) plus one lake (2004, Lake Murten).

**Exit**: overlay file committed; its content matches the branch's 5-station
set.

### T4 — Docker Compose staging overlay

**File**: `docker-compose.staging.yml` (new)

1. Mirror the base compose bind-mount pattern. For each service that
   currently mounts `./config.toml:/app/config.toml:ro` (prefect-worker,
   api, init per the survey: `docker-compose.yml:101, 151, 226`):
   - Add `./config/overlays/staging-5-stations.toml:/app/config/overlays/staging-5-stations.toml:ro`.
   - Set `SAPPHIRE_CONFIG_OVERLAY=/app/config/overlays/staging-5-stations.toml`
     in that service's `environment:` block.
2. Keep the overlay minimal — one env var + one bind-mount per affected
   service. Do not redeclare unrelated compose-level config.
3. Document usage in the `cicd.md` subsection (T5). The runbook command
   that operators will use is
   `docker compose -f docker-compose.yml -f docker-compose.staging.yml up`.

**Exit**: `docker compose -f docker-compose.yml -f docker-compose.staging.yml config` validates; grep confirms the env var and mount are wired on the
three services identified in the survey.

### T5 — Documentation

**Files**:
- `docs/standards/cicd.md` — add a "Config overlays" subsection describing
  the mechanism, the `SAPPHIRE_CONFIG_OVERLAY` env var, the
  `config/overlays/` directory convention (gitignored `local/` for
  operator-only tweaks), and the fail-loud policy on missing overlays.
- `docs/plans/046-mac-mini-staging-deployment.md` — add a short one-line
  note near §A1 (not a new Revision block — that belongs to Plan 046's
  owner) flagging that once Plan 065 lands, the "rebase
  `staging-5-stations` onto main" step is superseded by
  `docker compose -f docker-compose.yml -f docker-compose.staging.yml`.
  Do not edit §A1's procedure itself; do not bump Revision; do not
  rewrite existing wording. Additive note only, clearly marked as a
  cross-reference from Plan 065.
- `docs/plans/README.md` — add Plan 065 under Active.

**Exit**: cicd.md documents the overlay contract; Plan 046 flags its own
supersession; the plan index lists Plan 065.

### T6 — End-to-end smoke test from `main`

**This is a human-in-the-loop task, not a subagent task.** It runs after
T2/T3/T4 land and before the plan commit is declared DONE. The operator
(not Opus, not a subagent) performs the run because it touches the real
staging host.

1. From `main`, with `SAPPHIRE_CONFIG=config.toml` and
   `SAPPHIRE_CONFIG_OVERLAY=config/overlays/staging-5-stations.toml`,
   run the onboarding flow against the staging-5-station subset.
2. Confirm the 5 stations are registered (not 169).
3. Confirm no warning/error about the overlay in logs.
4. Confirm that unsetting `SAPPHIRE_CONFIG_OVERLAY` and re-running
   onboarding registers all 169 stations (backwards compatibility).

**Exit**: smoke test passes; Plan 046 A3 dress rehearsals can cite this
sequence instead of rebasing `staging-5-stations`. Result captured in the
Plan 065 commit message or a plan-close note.

---

## After this lands: `staging-5-stations` branch deprecation

(Formerly task T7 — removed because it was a policy statement, not a
delegatable task.)

Once T6 confirms the overlay works end-to-end from `main`, the
`staging-5-stations` branch is deprecated. Deletion is the operator's
call (e.g., `git branch -D staging-5-stations`); this plan does **not**
delete it. Branches are cheap; premature deletion during a live A3 loop
is risky.

---

## Dependency graph

```json
{
  "phases": [
    {"id": "core", "tasks": ["T1"], "parallel": false},
    {"id": "integration", "tasks": ["T2", "T3", "T4"], "parallel": true, "depends_on": ["core"]},
    {"id": "docs", "tasks": ["T5"], "parallel": false, "depends_on": ["integration"]}
  ]
}
```

Notes:

- T1 is strictly sequential first — every other code task uses the helper.
- T2, T3, T4 can run in parallel within the `integration` phase: T2 edits
  five Python modules, T3 adds new data files, T4 adds compose YAML. No
  overlap.
- **T6 is not in the graph.** It is an operator smoke test that runs
  **after the graph completes**, before the plan commit is declared
  DONE. A subagent cannot execute T6 (it touches the real staging host).
  The graph's sole job is "subagent-executable work"; T6 is explicitly
  outside that scope.

---

## Files to create

| Path | Task | Purpose |
|---|---|---|
| `src/sapphire_flow/config/_overlay.py` | T1 | `load_merged_toml` helper + `_resolve_overlay_paths` |
| `tests/unit/config/test_overlay.py` | T1 | merge-semantics unit tests |
| `config/overlays/.gitkeep` | T3 | preserves empty dir in git |
| `config/overlays/local/.gitkeep` | T3 | preserves empty dir in git; `local/*` files gitignored |
| `config/overlays/staging-5-stations.toml` | T3 | 5-station staging overlay |
| `docker-compose.staging.yml` | T4 | compose overlay wiring the env var + bind-mount |

## Files to modify

| Path | Task | Change |
|---|---|---|
| `src/sapphire_flow/config/deployment.py` | T2 | `load_config` uses `load_merged_toml`; `_resolve_env_vars` stays as canonical |
| `src/sapphire_flow/config/onboarding.py` | T2 | `load_onboarding_config` uses `load_merged_toml`; drop now-dead `_resolve_env_vars` import |
| `src/sapphire_flow/config/qc_rules.py` | T2 | `load_qc_rules` uses `load_merged_toml`; **delete duplicate** `_resolve_env_vars` at line 14 |
| `src/sapphire_flow/config/forecast_qc_rules.py` | T2 | `load_forecast_qc_rules` uses `load_merged_toml`; re-point `_resolve_env_vars` import to `config.deployment` |
| `src/sapphire_flow/flows/ingest_observations.py` | T2 | `_load_adapter_endpoint` uses `load_merged_toml`; drop now-dead `_resolve_env_vars` import |
| `.gitignore` | T3 | ignore `config/overlays/local/*` except `.gitkeep` |
| `docs/standards/cicd.md` | T5 | new "Config overlays" subsection |
| `docs/plans/046-mac-mini-staging-deployment.md` | T5 | Revision note flagging supersession of A1 rebase step |
| `docs/plans/README.md` | T5 | list Plan 065 under Active |

---

## Exit gates

1. `uv run pytest tests/unit/config/test_overlay.py` passes (new tests cover D3 semantics).
2. `uv run pytest` — full suite still green (existing loader tests unchanged in behaviour).
3. `uv run pyright --strict src/` clean (full tree, matches `docs/workflow.md` task-exit-gate convention).
4. `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean.
5. No direct `tomllib.loads` call remains inside `deployment.py`, `onboarding.py`, `qc_rules.py`, `forecast_qc_rules.py`, or `flows/ingest_observations.py`; all five route through `load_merged_toml`. `load_merged_toml` is the only module in `src/sapphire_flow/` that calls `tomllib.loads`, excepting the offline `tools/record_fixtures.py` developer script which reads different TOML files entirely.
6. `config/overlays/staging-5-stations.toml` exists, contains only the `[onboarding]` section, lists exactly `["2004", "2009", "2033", "2085", "2091"]`.
7. `docker compose -f docker-compose.yml -f docker-compose.staging.yml config` exits 0 — **operator-run; requires Docker.** If the subagent has no Docker, they report this gate as pending and the operator verifies locally before the plan is declared DONE.
8. `docs/standards/cicd.md` contains a "Config overlays" section; `docs/plans/README.md` lists Plan 065 under Active; Plan 046 carries a revision note flagging the A1 rebase step as superseded.
9. T6 smoke test: onboarding flow from `main` with `SAPPHIRE_CONFIG_OVERLAY` set registers exactly 5 stations; unset, it registers 169. Captured in commit message or plan-close note.
10. Version bump applied per CLAUDE.md.

---

## Risks

| Risk | Mitigation |
|---|---|
| Deep-merge corner case (e.g., a nested array-of-tables that should merge, not replace) surprises a future user | D3 pins semantics to "replace lists, merge dicts." Document in cicd.md. If a real need for append surfaces, that's a follow-up plan with explicit syntax (e.g., `basin_ids = { _append = [...] }`) — not free-form. |
| Silent overlay miss (e.g., typo in path) produces wrong-config runs | D6: missing overlay → `FileNotFoundError`. No fallback-to-base on typo. |
| Env-var substitution semantics change subtly when moved inside merge helper | T1 test case specifically covers `${SAPPHIRE_*}` in overlay-patched values. |
| Overlay introduces a key that Pydantic `DeploymentConfig` doesn't know about (typo or stale overlay) | Model is permissive today (no `extra = forbid`) so typo silently drops. Acceptable for v0; a follow-up can flip `extra = forbid` once all overlays are clean. Flag in open questions. |
| Plan 046 mid-A3 interference | Land this plan only during a lull, not while a dress rehearsal is in flight. Plan 065's supersession note in Plan 046 is additive — does not rewrite existing steps — so a stale Plan 046 reading still works. |
| Docker Compose overlay diverges from base as services are added | Keep `docker-compose.staging.yml` minimal (mount + env var only). Any base-compose service change that affects config-mounting auto-applies; services not needing the overlay ignore it. |
| `_resolve_env_vars` dedup (T2 step 5) touches four files' imports; one missed import becomes a silent drift or an import error | Step 5 spells out every import precisely with file:line references (`deployment.py:235` canonical; `qc_rules.py:14` delete; `forecast_qc_rules.py:8` re-point; `ingest_observations.py:66` + `onboarding.py:8` delete). Pyright in exit gate 3 catches misnamed imports; runtime import errors surface on first test load. No silent drift possible because there will be exactly one definition after the sweep. |
| T4 `SAPPHIRE_CONFIG_OVERLAY` path inside container doesn't match the bind-mount target | T4 step 1 pins both: mount at `/app/config/overlays/staging-5-stations.toml:ro`, env var set to the same path. `docker compose config` (exit gate 7) catches YAML mis-merges but not path-matching; the definitive catch is T1's `FileNotFoundError` which fires on first container start if the env-var path is not mounted. T6 (operator smoke test) confirms end-to-end. |

---

## Open questions

Not blocking DRAFT → READY:

1. **Should `DeploymentConfig` flip to `extra = "forbid"`** in a follow-up, now that overlays make typos more consequential? (Recommendation: yes, separate plan — tight scope, worth its own review.)
2. **Should `config/overlays/` include a `README.md`** explaining the convention, or is the cicd.md section enough? (Recommendation: a 10-line README.md inside `config/overlays/` would help operators who don't read cicd.md. Adds T3 step.)
3. **Should we pre-commit a `config/overlays/integration-tests.toml`** for a 3-station CI subset, or defer until a CI plan asks for it? (Recommendation: defer. Scope creep. Mention as a natural follow-up.)
4. **Does Plan 047 (Nepal v1 data sources)** belong in `config/overlays/nepal-v1.toml`, or is Nepal a full base-config swap? (Recommendation: probably a full base swap — Nepal's DHM stations, ECMWF IFS adapter, elevation-band NWP are too divergent for an overlay. Flag as Plan 047 open question.)
5. **Do Plan 060's `docker-compose.dev.yml` mount patterns** need any change to play nicely with `docker-compose.staging.yml`? (Recommendation: they should be independent and composable; the operator picks which overlays to chain. Verify during T4 with `docker compose -f base -f dev -f staging config`.)
6. **Developer tools like `src/sapphire_flow/tools/record_fixtures.py`** currently read `adapters.river_stations.endpoint` and `adapters.weather_forecast` from raw TOML (via `tomllib.load` from file handles, not `loads` from text). They bypass the overlay mechanism. (Recommendation: leave as-is; this is a developer-only offline tool, not a runtime concern. Fold into a separate plan if/when devs want overlay-aware fixture recording.)
