# Plan 030 — TOML-Driven Station Onboarding Configuration

**Status**: READY  
**Phase**: 5 (Station Onboarding)

## Context

The v0-scope.md design says (§A4, step 1): *"Reads TOML with station definitions."* The architecture-context.md says Flow 5 step 5.1 source is: *"TOML bootstrap file (v0) or dashboard input (v1+)."*

Currently the implementation does NOT read a TOML for station selection — it relies on `--basin-ids` CLI args. This means a fresh deployment requires knowing the magic list of 167 station IDs to pass on the command line.

We identified 167 CAMELS-CH stations with EXACT_ID match to LINDAS (operational telemetry). These are the v0 Swiss station set — the "intersection of CAMELS-CH basins that also have operational LINDAS telemetry" called for in v0-scope.md. They should be configured in `config.toml` so onboarding is reproducible and self-documenting.

### Design decisions

1. **Separate loader, not DeploymentConfig.** Following the `qc_rules.py` pattern: a standalone `load_onboarding_config()` in `config/onboarding.py`. Rationale: `DeploymentConfig` is for operational runtime settings; onboarding is a one-time bootstrap concern. The `qc_rules` precedent already establishes this pattern.

2. **`[onboarding]` section in config.toml.** Flat section with `data_source` and `basin_ids`. No deeper nesting — the section name already scopes it.

3. **CLI overrides config.** `--basin-ids` on the CLI takes precedence. If neither CLI nor config provides IDs, all CAMELS-CH stations are onboarded (existing default).

4. **Service layer unchanged.** `onboard_from_camelsch()` continues to receive `basin_ids: list[str] | None` as a parameter. Config reading happens in the script/flow layer only.

5. **Frozen dataclass, not Pydantic.** `OnboardingConfig` follows project convention for internal types.

---

## Tasks

### Task 1: Create `config/onboarding.py`

New module following the `qc_rules.py` pattern.

**Contents:**
- Frozen dataclass `OnboardingConfig` with `data_source: str` and `basin_ids: tuple[str, ...]`
- `load_onboarding_config(config_path: str | Path | None = None) -> OnboardingConfig | None`
  - Resolves path from arg or `SAPPHIRE_CONFIG` env var
  - Reads TOML, resolves env vars (import `_resolve_env_vars` from `deployment.py`)
  - Returns `OnboardingConfig` if `[onboarding]` section exists, else `None`

**Files:** `src/sapphire_flow/config/onboarding.py` (new)

**Reference pattern:** `src/sapphire_flow/config/qc_rules.py:246-263` (`load_qc_rules`)

### Task 2: Add `[onboarding]` section to `config.toml`

Add after the `[[skill_interpretation]]` sections, before `[qc_rules]`:

```toml
[onboarding]
data_source = "camels-ch"
basin_ids = ["2004", "2009", ...]  # 167 EXACT_ID station codes
```

Also add `data.pop("onboarding", None)` to `load_config()` in `deployment.py:258` alongside existing `pop` calls, for explicit consistency.

**Files:** `config.toml`, `src/sapphire_flow/config/deployment.py` (one line)

### Task 3: Wire `scripts/onboard.py`

After line 170 (`basin_ids: list[str] | None = args.basin_ids`), add config fallback:

```python
if basin_ids is None:
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        from sapphire_flow.config.onboarding import load_onboarding_config
        onboarding_cfg = load_onboarding_config(config_path)
        if onboarding_cfg is not None:
            basin_ids = list(onboarding_cfg.basin_ids)
```

Also update `--basin-ids` help text to mention config fallback.

**Files:** `scripts/onboard.py`

### Task 4: Wire `flows/onboard.py`

Add the same config fallback early in `onboard_stations_flow()`, before the log statement at line 132. Same pattern as Task 3.

**Files:** `src/sapphire_flow/flows/onboard.py`

### Task 5: Update reference config and clean up

- Add `[onboarding]` section to `docs/spec/config-reference.toml` with inline documentation
- Delete `camels_ch_to_lindas.csv` from repo root (one-time analysis artifact; its result is now in config.toml)

**Files:** `docs/spec/config-reference.toml`, `camels_ch_to_lindas.csv` (delete)

### Task 6: Tests

New file `tests/unit/config/test_onboarding.py`:
- `test_load_parses_section` — minimal TOML with `[onboarding]`, verify fields
- `test_load_missing_section_returns_none` — TOML without `[onboarding]` returns `None`
- `test_load_uses_sapphire_config_env` — set env var, call without path
- `test_load_raises_without_path_or_env` — no path, no env → `ValueError`
- `test_basin_ids_are_strings` — verify string type (TOML would parse bare ints)

Verify existing tests:
- `tests/unit/config/test_deployment.py` — `load_config` still works with new `[onboarding]` section in config.toml
- Full suite: `uv run pytest --tb=short -q`

**Files:** `tests/unit/config/test_onboarding.py` (new)

---

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["1", "2"],
      "parallel": true
    },
    {
      "id": "phase-2",
      "tasks": ["3", "4", "5"],
      "parallel": true,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["6"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```

## Verification

```bash
# Unit tests for new config module
uv run pytest tests/unit/config/test_onboarding.py -x -q

# Existing config tests still pass
uv run pytest tests/unit/config/test_deployment.py -x -q

# Lint
uv run ruff check src/sapphire_flow/config/onboarding.py scripts/onboard.py src/sapphire_flow/flows/onboard.py
uv run ruff format --check src/ scripts/

# Full suite
uv run pytest --tb=short -q
```
