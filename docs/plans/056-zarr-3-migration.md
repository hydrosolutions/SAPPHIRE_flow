# Plan 056 — Zarr 2→3 library migration (unblocks Plan 046 A2)

**Status**: DONE (2026-04-18)
**Date**: 2026-04-18
**Depends on**: none
**Blocks**: Plan 046 Stream A (currently paused at A2)
**Scope**: Upgrade `zarr`, `numcodecs`, and `xarray` to versions that publish
linux/arm64 wheels, unblocking the `uv sync --frozen` step inside the Docker
builder stage. **On-disk archive format stays at v2** for this plan — we
upgrade the runtime library to `zarr-python 3.x` but keep writing v2-format
Zarr stores via `zarr_format=2`, preserving byte-for-byte compatibility with
archives already written by Plan 045. A future optional follow-up can migrate
to format v3 once a v3-only feature (e.g. sharding) becomes useful.

---

## Context

### Why now

Plan 046 A2 (first `docker compose up`) on 2026-04-18 surfaced a Docker-build
blocker at `uv sync --frozen --no-dev`:

```
Caused by: `numcodecs` (v0.15.1) wheel build failed
  [numcodecs] command 'gcc' failed: No such file or directory
```

`numcodecs 0.15.1` publishes no linux/arm64 wheel. On Apple Silicon Docker
Desktop builds native linux/arm64 images by default, so uv falls back to the
sdist, which needs a C toolchain. `python:3.11.12-slim` ships no `gcc`.

Two possible fixes:
1. Install `build-essential` in the Dockerfile builder stage (accepts stale
   dep versions, carries a ~200 MB intermediate layer cost).
2. Bump `numcodecs` to `>=0.16` so a linux/arm64 wheel is available. `numcodecs
   0.16` dropped `zarr 2` support, forcing a paired `zarr>=3` bump. `xarray`
   2026.04.0 also requires `zarr>=3`, so that rolls in too.

Option 2 is the one we are taking: the modern stack is the right long-term
base (we are still in v0 infrastructure-building, not locked-in), and the
alternative would leave us on library versions that no longer get upstream
bug fixes or feature work.

The research phase confirmed that migration scope is **much smaller than the
label suggests** — `zarr-python 3` is backward-compatible with the v2 on-disk
format, xarray's top-level `to_zarr` / `open_zarr` API is preserved, and our
only direct `numcodecs` or `zarr` call site is one line in
`src/sapphire_flow/store/zarr_nwp_grid_store.py`. The plan is a dep bump plus a
~3-line code edit to pin the on-disk format.

### Inputs (researched, referenced)

- Current pins (`pyproject.toml`): `numcodecs<0.16` (line 22), `zarr>=2.18,<3`
  (line 37), `xarray` unpinned but locked at 2026.2.0 (`uv.lock:5123`).
- Only direct zarr/numcodecs usage:
  `src/sapphire_flow/store/zarr_nwp_grid_store.py:8, 42, 48, 76`.
- Tests asserting v2-format on-disk structure:
  `tests/unit/store/test_zarr_nwp_grid_store.py:53–55, 62–65` — these remain
  green under the chosen approach (format stays v2).
- Zarr 3 migration guide (`zarr-developers/zarr-python:docs/user-guide/v3_migration.md`)
  confirms zarr-python 3 reads and writes v2 archives when `zarr_format=2` is
  specified.
- xarray 2026.04.0 release notes (`github.com/pydata/xarray/releases/tag/v2026.04.0`)
  bump the minimum `zarr` to `>=3.0`. v2-format on-disk archives remain readable
  and writable via zarr-python 3 when `zarr_format=2` is passed explicitly.
- **Empirical probe (2026-04-18)** in a scratch venv (`zarr 3.1.x` + `numcodecs
  0.16.5` + `xarray 2026.04.0`) confirmed the primary codec path: passing
  `numcodecs.Zstd(level=3)` as `encoding[var]["compressor"]` with `zarr_format=2`
  writes a clean v2 archive (`.zarray["compressor"] == {"id": "zstd", "level": 3}`,
  no `zarr.json`, round-trip via `xr.open_zarr(..., consolidated=True)` OK). The
  `numcodecs.zarr3` subpackage is deprecated upstream (0.16.5 emits
  `DeprecationWarning`) and is rejected by zarr-python 3's v2 path with
  `ValueError`, so no codec fallback is needed.
- Dask is **not** a direct or transitive dependency — no fan-out there.
  `rioxarray` (pinned `>=0.19.0` in `pyproject.toml`, resolved to `0.22.0` or
  later after `uv lock --upgrade`) supports zarr 3 upstream; T1 runs
  `uv tree --package rioxarray` before commit to confirm the resolved version.
- Plan 052 (DRAFT, `docs/plans/052-nwp-gridded-path-hardening.md`) overlaps on
  the same file but at a different layer (atomic-swap crash safety, datetime
  rejection, NaN fail-fast). No semantic conflict; sequence either way works.
- Plan 045 is DONE (archived); its `ZarrNwpGridStore` implementation stays.

### Problem statement

1. `numcodecs 0.15.1` has no linux/arm64 wheel. Any arm64 Docker build falls
   through to sdist → needs `gcc` → our slim base image has none → build fails.
2. `numcodecs<0.16` is a hard pin in `pyproject.toml`. Bumping requires a
   paired `zarr 2→3` bump because `numcodecs 0.16` dropped zarr 2 support.
3. Plan 046 A2 is blocked until the Docker build succeeds. The staging
   deployment cannot start, and Plan 052 (which depends on the same file)
   cannot be validated end-to-end in a compose stack.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Bump library, keep on-disk format at v2** (`zarr_format=2` explicit on every write). | Zero on-disk diff — archives written by Plan 045 on Mac mini staging or developer machines remain byte-identical. Lowest-risk migration. Format-v3 adoption gets its own future plan once a v3-only feature (sharding, variable-length chunks) actually buys something. |
| D2 | **Keep `numcodecs.Zstd(level=3)`** as the compressor literal — no fallback. | Empirical probe (2026-04-18, scratch venv with `zarr 3.1.x` + `numcodecs 0.16.5` + `xarray 2026.04.0`): `encoding[var]["compressor"] = numcodecs.Zstd(level=3)` with `zarr_format=2` writes a pure v2 archive (`.zarray["compressor"] == {"id": "zstd", "level": 3}`, no `zarr.json`), round-trips cleanly via `xr.open_zarr(..., consolidated=True)`, and preserves the existing `test_zarr_uses_zstd_compression` assertion unchanged. The previously-considered fallback `numcodecs.zarr3.Zstd(level=3)` is both **unnecessary** (the primary path works) and **broken for this use case**: `numcodecs.zarr3` is deprecated in numcodecs 0.16.5 (emits `DeprecationWarning` on import) and is explicitly rejected by zarr-python 3's v2 write path with `ValueError: Invalid compressor. Expected None, a numcodecs.abc.Codec, or a dict representation...`. `zarr.codecs.ZstdCodec(level=3)` is the format-v3 equivalent and is out of scope here. |
| D3 | **Install minimal build tooling (`build-essential`, `cmake`, `libgeos-dev`) in the Dockerfile builder stage only.** | Bumping `numcodecs>=0.16.1` fixed that wheel, but T4 (2026-04-18) surfaced a second arm64 blocker: `exactextract` has **never** published a `manylinux_aarch64` wheel across its entire 0.x release history — on arm64 uv falls back to the sdist and needs CMake + GEOS. The pre-existing `v0-scope.md §A11` claim that "exactextract ships pre-built wheels" is wrong for arm64 (the claim held only for macos-arm64 and linux-amd64). The Dockerfile is already multi-stage: build tooling lives in the builder stage and is discarded in the final image, which only receives the compiled `.venv`. Runtime image remains slim; builder stage grows by ~200 MB (ephemeral layer). Alternative (switching away from exactextract) would require rewriting `GridExtractor` — out of scope. |
| D4 | **Defer any zarr 3 format adoption** to a separate future plan. | Avoid scope creep. Plan 056 is "unblock A2" not "adopt every zarr 3 feature." Callouts in the store module and in the archived design doc note the deferral so a future reader knows format-v3 was an informed choice. |
| D5 | **Land Plan 056 before Plan 052** (preferred, not required). | Plan 052 touches the same file; easier if 052 works in the new-library world from the start. Codebase audit (2026-04-18) confirms the actual line overlap is **zero** (056 edits line 48; 052 edits lines 51–55), so either ordering works without rebase conflicts. |

---

## Phase ladder

### T1 — Pin bump + lock refresh

**Scope**: in — edit `pyproject.toml` pins and regenerate `uv.lock`; verify
resolved versions. Out — no code, no test, no Dockerfile change.

- Edit `pyproject.toml`:
  - Line 22: `"numcodecs<0.16"` → `"numcodecs>=0.16.1,<0.17"` (note: `numcodecs 0.16.0` is yanked on PyPI per [issue #748](https://github.com/zarr-developers/numcodecs/issues/748); the explicit `>=0.16.1` lower bound makes the plan independent of uv's yank-skipping behaviour).
  - Line 37: `"zarr>=2.18,<3"` → `"zarr>=3.0,<4"`
  - Line 36 (`"xarray"`, unpinned): pin `"xarray>=2026.04.0"` for clarity — `xarray 2026.04.0` is the first release that requires `zarr>=3`, and the explicit pin makes the migration intent self-documenting (resolves open question 1).
- `uv lock --upgrade-package zarr --upgrade-package numcodecs --upgrade-package xarray` to refresh resolution without touching unrelated deps.
- Verify the resolved versions: `zarr 3.x`, `numcodecs >= 0.16.1`, `xarray>=2026.04.0`.
- Verify `rioxarray` still resolves and imports cleanly. **Actuals (2026-04-18)**: resolver holds rioxarray at `0.19.0` because `0.22.0+` requires Python `>=3.12` and this repo pins `requires-python = ">=3.11"`. rioxarray `0.19.0` declares `xarray>=2024.7.0` (no upper bound) and no zarr pin, so it is compatible with the new stack; smoke test + the 8 tests in `tests/unit/preprocessing/test_exact_extract_grid_extractor.py` pass unchanged. The earlier draft's expectation of `>=0.22.0` is superseded — do not bump the `pyproject.toml` pin.
- `uv sync` to materialise in `.venv`.

**Exit**: `uv.lock` shows `numcodecs >= 0.16.1` and `zarr 3.x`; `uv sync` exits 0;
`uv run python -c "import zarr, numcodecs, xarray; print(zarr.__version__, numcodecs.__version__, xarray.__version__)"` prints sensible versions.

### T2 — Store edit (format-v2 explicit)

**Scope**: in — add `zarr_format=2` kwarg to the single `ds.to_zarr(...)` call
and one comment explaining the format choice. Out — no codec change (empirical
probe confirmed `numcodecs.Zstd` works as-is under zarr-python 3 with
`zarr_format=2`), no encoding-dict restructuring, no read-path change.

- `src/sapphire_flow/store/zarr_nwp_grid_store.py` line 48: add
  `zarr_format=2` to the `ds.to_zarr(...)` call:
  ```python
  ds.to_zarr(
      tmp_path,
      mode="w",
      consolidated=True,
      encoding=encoding,
      zarr_format=2,  # load-bearing: zarr-python 3 defaults to v3 on-disk
  )
  ```
  **`zarr_format=2` is mandatory, not stylistic** — zarr-python 3's default
  on-disk format is v3. Omitting this kwarg would silently write `zarr.json`
  instead of `.zarray`/`.zgroup`, breaking byte-for-byte compatibility with
  Plan-045-era archives. T3 adds a format-assertion test to catch any
  accidental regression.
- No other line changes expected. `numcodecs.Zstd(level=3)` stays as-is —
  empirical probe (see D2) confirms it writes a clean v2 archive under
  zarr-python 3.
- Add a one-line comment above the `encoding` dict noting the format choice:
  ```python
  # zarr v2 on-disk format under zarr-python 3 runtime (Plan 056 D1); migrate
  # to v3 when sharding or variable chunks become useful.
  ```
- Run `uv run ruff format` and `uv run ruff check --fix` on the file.

**Exit**: file lints clean; `git diff` shows only the intended changes.

### T3 — Test audit + format-assertion test

**Scope**: in — confirm existing v2-marker tests still pass; add one
format-plus-codec-content assertion test. Out — no refactor of existing tests,
no new fixture generation, no changes to any other test file.

- Read `tests/unit/store/test_zarr_nwp_grid_store.py:53–55, 62–65`. These
  tests read `.zarray` directly — they keep passing under zarr-python 3 when
  the archive is v2 format. Confirm by running just this file.
- Add a new test asserting the archive really is v2 format **and** that the
  zstd codec metadata landed in v2-namespaced form (not v3-namespaced):
  ```python
  def test_archive_is_zarr_format_v2(tmp_path: Path) -> None:
      # ... write via ZarrNwpGridStore ...
      archive_path = tmp_path / "cycle_2026-04-18T00.zarr"
      # Structural: v2 markers present, v3 marker absent.
      assert (archive_path / ".zgroup").exists(), "v2 format marker missing"
      assert not (archive_path / "zarr.json").exists(), "v3 format marker should not appear"
      # Codec content: v2-style "compressor" key with v2-namespaced codec id.
      # If a future contributor swaps in numcodecs.zarr3.* (v3-namespaced),
      # the id becomes "numcodecs.zstd" and this assertion fires.
      import json
      zarray = json.loads((archive_path / "precipitation" / ".zarray").read_text())
      assert zarray["zarr_format"] == 2
      assert zarray["compressor"]["id"] == "zstd"
  ```
  The intent: zarr-python 3's default on-disk format is v3, and v3-namespaced
  numcodecs codecs would also silently corrupt v2-compat. This single test
  catches both regressions.
- `uv run pytest tests/unit/store/ -q` — must be green.

**Exit**: all unit tests in `tests/unit/store/` pass; the new format-assertion
test is present and passing.

### T4 — Dockerfile builder tooling + Docker build validation (unblock A2)

**Scope**: in — add build tooling to the Dockerfile builder stage (per
revised D3), then build every service that uses the shared `sapphire-flow`
image. Out — do NOT `docker compose up` (that is Plan 046 A2's job), no
runtime / integration validation, no image-size benchmarking, no changes to
the runtime stage of the Dockerfile.

**T4.1 — Dockerfile edit** (revised per D3):

Add a build-tooling apt install block to the **builder stage** of
`Dockerfile`, between line 5 (`WORKDIR /app`) and line 7 (the `COPY
pyproject.toml` line), before `uv sync --frozen --no-dev`:

```dockerfile
# Build tooling for sdist-only deps on linux/arm64 (exactextract publishes no
# linux/aarch64 wheel; see Plan 056 D3). Builder stage only — the final image
# copies .venv and excludes these packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake libgeos-dev \
    && rm -rf /var/lib/apt/lists/*
```

Do NOT modify the runtime stage (everything from `FROM python:3.11.12-slim`
on line 17 onwards stays unchanged).

**T4.2 — Build all services** that share the Dockerfile (`init`, `api`,
`prefect-worker` per `docs/standards/cicd.md` §Custom image):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build --no-cache init api prefect-worker
```

Docker layer caching deduplicates the `uv sync` work across services once
the first build completes, so the incremental cost of the extra services
is small — but explicitly naming them guarantees all Compose-declared
build contexts exercise the new wheel resolution.

- Must succeed on the current host (Apple Silicon, linux/arm64 image target).
- The failure modes this replaces:
  - `numcodecs` sdist build with "command 'gcc' failed" (fixed by T1's pin bump).
  - `exactextract` sdist build with "CMAKE_C_COMPILER not set" (fixed by
    T4.1's build-tooling install).
- Expected success signal: `#X DONE` lines for the builder stage of each
  service; `uv sync --frozen --no-dev` completes without error.

**Exit**: all three `docker compose build` invocations exit 0. Do NOT proceed
to `up -d` — that is Plan 046 A2's job.

### T5 — Full test suite + doc touch-up

**Scope**: in — run full pytest; apply three targeted doc edits in the same
commit. Out — no edits to archived plans (see note below), no MEMORY.md
edits by the subagent (orchestrator-only).

- `uv run pytest tests/ -q` — must match or exceed the pre-056 baseline.
  Baseline as of 2026-04-18: 901 unit tests, 217 integration tests passing
  (post-FI-removal state, `aaa458b`). Expect 902 after T3 adds one test. Any
  new failure is a regression — stop and investigate.
- Doc touch-ups (all in the same commit):
  - **`docs/v0-scope.md` §A11** (currently line 193, "The `eccodes`/`cfgrib`
    and `exactextract` libraries ship pre-built wheels — no build-time
    obstacles") — append one sentence: "`numcodecs>=0.16.1` is also required
    so that a linux/arm64 wheel is available; earlier versions fall back to
    sdist and fail in the `python:3.11-slim` builder stage (see Plan 056)."
  - **`docs/v0-scope.md` §I "Not risks (safe to defer)" table** (at the
    bottom of §I) — add one row: `| Zarr on-disk format migration (v2→v3)
    | Additive migration via read-v2/write-v3 pass (zarr-python 3 supports
    both); plan before Nepal v1 if sharding or variable-length chunks
    become useful. See Plan 056 §D1/D4. |`. This fits the table's style
    better than a standalone I6 entry since the item is a future-task
    awareness, not a v0 coding invariant.
  - **`docs/standards/cicd.md` §Custom image** (around line 28, where the
    `python:3.11-slim` base is declared) — append one sentence: "Dependency
    constraint: `numcodecs>=0.16.1` is required so that a linux/arm64 wheel
    is available; earlier versions fall back to sdist and fail on
    `python:3.11-slim` (see Plan 056)." Operators reading only `cicd.md`
    need this context to understand the arm64 wheel pin.
  - **`docs/architecture-context.md`** — after the existing zarr mention at
    line 1225 ("Raw gridded NWP follows the same lifecycle (Zarr, already
    zstd-compressed internally)"), add a short paragraph: "The Zarr archive
    uses the v2 on-disk format under the zarr-python 3 runtime (Plan 056).
    v2-format archives are fully supported by zarr-python 3; format v3 will
    be adopted when a v3-only feature (sharding, variable-length chunks)
    becomes useful. See Plan 056 §D1/D4 for rationale."
- **Not edited**: `docs/plans/archive/021-phase3-meteoswiss-adapters.md`.
  Archived plans are immutable historical records. The in-code comment
  added in T2, plus the architecture-context.md paragraph and the
  §D1/§D4 references above, provide sufficient traceability for a future
  reader of archive/021.
- **MEMORY.md note**: the orchestrator adds a one-line project memory after
  commit ("zarr-python 3 in use since Plan 056 (2026-04-18); on-disk format
  stays at v2 pending a v3-feature driver"). The subagent does not touch
  memory files.

**Exit**: full pytest tier green; three doc files updated (v0-scope §A11 +
§I "Not risks" row, cicd.md §Custom image, architecture-context line 1225).

### T6 — Commit + version bump

**Scope**: in — patch version bump, stage listed files, single commit, tag.
Out — no push to origin, no changelog edits, no unrelated file staging.

- `uv run bump-my-version bump patch` (current 0.1.304 → 0.1.305). Modifies
  `pyproject.toml` and `src/sapphire_flow/__init__.py` (no auto-commit —
  `commit = false` in bump-my-version config).
- Stage:
  - `pyproject.toml`
  - `uv.lock`
  - `src/sapphire_flow/__init__.py`
  - `src/sapphire_flow/store/zarr_nwp_grid_store.py`
  - `tests/unit/store/test_zarr_nwp_grid_store.py`
  - `Dockerfile`
  - `docs/v0-scope.md`
  - `docs/standards/cicd.md`
  - `docs/architecture-context.md`
- Commit with the HEREDOC pattern from CLAUDE.md:
  ```
  feat(plan-056): migrate to zarr-python 3 + numcodecs 0.16 (format v2 retained)
  ```
  Body references Plan 046 A2 as the driver; notes zero on-disk change;
  mentions D1/D4 deferrals to a future plan.
- `git tag v$(uv run bump-my-version show current_version)`.
- Do NOT push.

**Exit**: `git log --oneline -1` shows the feat commit; tag applied; clean
working tree (modulo Plan 046's known untracked files).

---

## Dependency graph

```json
{
  "phases": [
    {"id": "T1", "tasks": ["T1"], "parallel": false, "depends_on": []},
    {"id": "T2", "tasks": ["T2"], "parallel": false, "depends_on": ["T1"]},
    {"id": "T3", "tasks": ["T3"], "parallel": false, "depends_on": ["T2"]},
    {"id": "T4", "tasks": ["T4"], "parallel": false, "depends_on": ["T3"]},
    {"id": "T5", "tasks": ["T5"], "parallel": false, "depends_on": ["T4"]},
    {"id": "T6", "tasks": ["T6"], "parallel": false, "depends_on": ["T5"]}
  ]
}
```

Strictly sequential. Entire plan under a day.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `pyproject.toml` | T1, T6 | Bump `numcodecs` to `>=0.16.1,<0.17`, `zarr` to `>=3.0,<4`, pin `xarray>=2026.04.0`; verify `rioxarray` lock resolution. T6 additionally bumps the package version field `0.1.304 → 0.1.305`. |
| `uv.lock` | T1 | Regenerated via `uv lock --upgrade-package ...` |
| `src/sapphire_flow/__init__.py` | T6 | `__version__` bumped by `bump-my-version` (`0.1.304 → 0.1.305`) |
| `src/sapphire_flow/store/zarr_nwp_grid_store.py` | T2 | Add `zarr_format=2` kwarg; one-line comment noting D1 |
| `tests/unit/store/test_zarr_nwp_grid_store.py` | T3 | Add `test_archive_is_zarr_format_v2` (checks format markers + codec content) |
| `Dockerfile` | T4 | Add `build-essential`, `cmake`, `libgeos-dev` to the builder stage only (revised D3) |
| `docs/v0-scope.md` | T5 | Revise §A11 (numcodecs arm64 wheel sentence + correct the exactextract claim); new row in §I "Not risks" table |
| `docs/standards/cicd.md` | T5 | §Custom image: note the builder-stage build tooling + arm64-wheel constraints |
| `docs/architecture-context.md` | T5 | One-paragraph zarr-runtime-and-format rationale near existing zarr mention at line 1225 |

No other files touched. No edits to the Dockerfile runtime stage (builder-stage
edit only — final image stays slim). No edits to `docs/plans/archive/`
(archived plans are immutable historical records).

---

## Exit gates for Plan 056

1. `uv sync` resolves `zarr 3.x`, `numcodecs >= 0.16.1`, `xarray>=2026.04.0`.
2. `uv run pytest tests/ -q` fully green at a count ≥ baseline + 1 (for the
   new format-assertion test).
3. `docker compose -f docker-compose.yml -f docker-compose.dev.yml build --no-cache init api prefect-worker` succeeds on linux/arm64 without `gcc` in the image.
4. Existing Plan-045-era Zarr archives on disk are unchanged and readable
   (confirmed indirectly by T3's round-trip test passing).
5. Three doc files updated in the same commit: `docs/v0-scope.md` (§A11
   sentence + §I "Not risks" table row), `docs/standards/cicd.md` (§Custom
   image sentence), `docs/architecture-context.md` (paragraph after line
   1225).
6. Commit landed on main, tagged.
7. `MEMORY.md` updated with the migration note by the orchestrator (manual
   follow-up after commit).

After all gates pass, **Plan 046 Stream A resumes at A2**.

---

## Risks

| Risk | Mitigation |
|---|---|
| xarray's encoding key for compressors renames (`"compressor"` singular → `"compressors"` plural) for v2-format archives under zarr-python 3. | Existing `test_zarr_uses_zstd_compression` reads `.zarray["compressor"]["id"] == "zstd"`, and T3's new `test_archive_is_zarr_format_v2` re-reads `.zarray["compressor"]["id"]` — any silent rename or drop fails both tests loudly. The empirical probe confirms `"compressor"` still works with `zarr_format=2` in xarray 2026.04.0. |
| `consolidated=True` interacts unexpectedly with `zarr_format=2` under zarr-python 3. | T3 round-trip test exercises the read path via `xr.open_zarr(..., consolidated=True)`. The empirical probe confirmed the combination works (the `.zmetadata` file is written and readable). |
| `rioxarray` fails to resolve or breaks at runtime against xarray 2026.04.0 / zarr 3. | Resolved in T1 (2026-04-18): rioxarray stays at `0.19.0` (held back by `requires-python>=3.11`; 0.22.0 needs 3.12+). rioxarray 0.19.0 declares `xarray>=2024.7.0` with no upper bound and no zarr pin, and the repo's 8 rioxarray-exercising tests pass unchanged. Bumping Python is out of scope for Plan 056. |
| Plan 052 lands between 056's drafting and execution and touches the same file. | Codebase audit (2026-04-18) confirms actual line overlap is **zero** — Plan 052 modifies the atomic-swap block at lines 51–55; Plan 056 modifies the `to_zarr` call at line 48. Either sequencing works without rebase conflicts. (Note: Plan 052 currently references the file as `src/sapphire_flow/stores/...` (plural `stores`) but the actual path is singular `store/`; that is a Plan 052 bug, not a Plan 056 concern.) |
| Accidental adoption of format v3 or of a v3-namespaced numcodecs codec in a later commit goes undetected. | T3's assertion test checks both the format marker (`.zgroup`/`zarr.json`) and the codec id (`"zstd"` vs `"numcodecs.zstd"`). Both would regress loudly. |
| Stale archive on Mac mini staging from before Plan 056 is unreadable post-056. | Zarr-python 3 auto-detects v2 archives from `.zgroup`/`.zarray`; no `zarr_format=2` kwarg needed on the read path. Archives stay valid. No wipe required. |
| `numcodecs 0.16.0` is yanked on PyPI, leaving a confusing install error if uv's yank-skipping is disabled. | Pin `"numcodecs>=0.16.1,<0.17"` in T1 (explicit floor above the yanked release). |
| No CI job builds the Docker image on linux/arm64, so a future dependency bump could silently re-introduce the sdist-fallback failure on Apple Silicon / arm64 staging hosts. | `uv.lock` freeze prevents silent regression until someone runs `uv lock --upgrade`. Out of scope for Plan 056; a follow-up plan can add an arm64 Docker build to CI if the risk materialises. |

---

## Deferred to follow-up plans

- **Format-v3 adoption** (write `zarr.json` instead of `.zarray` / `.zgroup`,
  use `zarr.codecs.ZstdCodec` / `BytesCodec`, unlock sharding and variable
  chunking). Candidate trigger: when we actually need sharding for Nepal v1
  NWP archive scale, or when we want variable chunk shapes for ragged member
  dimensions.
- **Dockerfile `build-essential` removal** (if it had been added defensively)
  — not applicable here because we never added it; kept as a note in case a
  developer added it locally.
- **CI arm64 Docker build job** — guards against future dependency bumps
  silently re-introducing the sdist-fallback failure on Apple Silicon / arm64
  staging hosts. Currently the `uv.lock` freeze is the only guard.

---

## Open questions

Not blocking DRAFT → READY:

1. **Plan 052 sequencing** — the plan author recommends landing 056 first,
   but the codebase audit confirms zero line overlap with Plan 052 (056 edits
   line 48; 052 edits lines 51–55), so either order works. The user picks
   when readying Plan 052.

Resolved in this revision:

- *(was Q1)* `xarray>=2026.04.0` pin is now explicit in T1 (self-documents
  the zarr-3 requirement).
- *(was Q3)* Architecture-context.md paragraph is now a concrete T5 subtask,
  not an open question.
- *(was D2 codec fallback)* Empirical probe (2026-04-18, scratch venv)
  confirmed `numcodecs.Zstd(level=3)` works unchanged under zarr-python 3 for
  `zarr_format=2` writes; the `numcodecs.zarr3.Zstd` fallback was both
  unnecessary (primary path works) and actively broken for v2 writes
  (raises `ValueError` and is deprecated upstream). Fallback removed from
  T2 / D2 / Risks.
