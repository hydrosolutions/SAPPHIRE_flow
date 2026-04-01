---
status: DRAFT
created: 2026-04-01
scope: |
  Fix the per-module log level override env var convention to support module names
  containing underscores. Currently lossy: all underscores become dots, making
  modules like `forecast_interface` and `camelsch_adapter` un-targetable.
depends_on: []
---

# 016 — Logging Per-Module Override: Underscore Encoding Fix

## Problem

The per-module log level override mechanism (`logging.py` line 57, `logging.md`
lines 141–146) transforms env var suffixes via `.lower().replace("_", ".")`:

```
SAPPHIRE_LOG_ADAPTERS_METEOSWISS=DEBUG  →  sapphire_flow.adapters.meteoswiss  ✓
SAPPHIRE_LOG_ADAPTERS_FORECAST_INTERFACE=DEBUG  →  sapphire_flow.adapters.forecast.interface  ✗
```

The transform is lossy — underscores and dot separators are conflated. Any Python
module with an underscore in its name (`forecast_interface.py`, `camelsch_adapter.py`,
`observation_store.py`, `weather_forecast_store.py`, etc.) cannot be individually
targeted. This is a pre-existing bug, not introduced by plan 014.

### Affected modules today

- `src/sapphire_flow/adapters/camelsch_adapter.py` — already exists, already broken
- `src/sapphire_flow/adapters/forecast_interface/` — planned (plan 014), blocked by this
- 20+ additional snake_case modules across `store/`, `services/`, `config/`, `types/`
  (e.g. `observation_store`, `weather_forecast_store`, `alert_checker`, `training_data`)

### Why it hasn't mattered yet

The only documented example (`SAPPHIRE_LOG_ADAPTERS_METEOSWISS=DEBUG`) uses a
single-word module name. No one has attempted to override a multi-word module.

## Solution

**Double-underscore escape convention:** `__` in the env var encodes a literal
underscore in the module name. Single `_` remains the dot separator.

```
SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE=DEBUG  →  sapphire_flow.adapters.forecast_interface  ✓
SAPPHIRE_LOG_ADAPTERS_CAMELSCH__ADAPTER=DEBUG     →  sapphire_flow.adapters.camelsch_adapter    ✓
SAPPHIRE_LOG_ADAPTERS_METEOSWISS=DEBUG            →  sapphire_flow.adapters.meteoswiss          ✓ (unchanged)
```

### Why this approach

| Option | Pros | Cons |
|--------|------|------|
| **(a) Ban underscores in module names** | No code change | Violates `conventions.md` snake_case rule; inconsistent with every existing multi-word module |
| **(b) Double-underscore escape** | One-line code change; no existing env vars break; snake_case preserved | Slightly less obvious env var syntax |
| **(c) TOML config section** | Eliminates encoding problem entirely | Disproportionate change; loses runtime-only env var property |
| **(d) Document limitation** | Zero code change | Leaves modules permanently un-targetable |

Option (b) is the least disruptive fix with no backwards-compatibility risk.

## Implementation Tasks

### Task 1 — Fix `logging.py` resolver

**File:** `src/sapphire_flow/logging.py` line 57

**Current:**
```python
module = key[len("SAPPHIRE_LOG_"):].lower().replace("_", ".")
```

**New:**
```python
module = key[len("SAPPHIRE_LOG_"):].lower().replace("__", "\x00").replace("_", ".").replace("\x00", "_")
```

The sentinel character `\x00` is safe — it cannot appear in env var names.

### Task 2 — Update `logging.md`

**File:** `docs/standards/logging.md`

Update the resolver code block (lines 141–146) to match the new implementation.

Add a note after line 149 documenting the convention:

> **Underscore encoding:** Single `_` maps to `.` (package separator). Double `__`
> maps to a literal `_` (for module names like `forecast_interface`). Example:
> `SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE=DEBUG` targets
> `sapphire_flow.adapters.forecast_interface`.

Update existing examples if needed (the `METEOSWISS` example is unaffected).

### Task 2b — Update `conventions.md` env var section

**File:** `docs/conventions.md` line 112 (end of §Environment variables)

Add a fourth bullet:

> - Log overrides: `SAPPHIRE_LOG_<MODULE>=<LEVEL>`. Single `_` maps to `.` (package
>   separator); double `__` maps to literal `_` (e.g.
>   `SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE=DEBUG`).

### Task 3 — ~~Update `architecture-context.md` module path~~ (done)

Resolved during plan 014 review: architecture-context.md line 1399 corrected from
`adapters/forecastinterface/` to `adapters/forecast_interface/`.

### Task 4 — Tests

Add test cases to verify the resolver:
- `SAPPHIRE_LOG_ADAPTERS_METEOSWISS=DEBUG` → `sapphire_flow.adapters.meteoswiss` (regression)
- `SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE=DEBUG` → `sapphire_flow.adapters.forecast_interface`
- `SAPPHIRE_LOG_ADAPTERS_CAMELSCH__ADAPTER=WARNING` → `sapphire_flow.adapters.camelsch_adapter`
- `SAPPHIRE_LOG_STORES_OBSERVATION__STORE=DEBUG` → `sapphire_flow.stores.observation_store`
- `SAPPHIRE_LOG_FOO___BAR=DEBUG` → `sapphire_flow.foo_.bar` (greedy left-to-right: `__` consumed first, remaining `_` becomes `.`)

## Urgency

Low standalone urgency — no module override is broken in production today (though
20+ snake_case modules are already un-targetable). Plan 014's
`ForecastInterfaceAdapter` module (v0b) depends on this fix for its per-module log
override to work. Should be completed before or alongside plan 014 Task 1.

> **Note:** Plan 014's urgency section now notes plan 016 as a prerequisite for
> Task 1. The forward-reference lives in plan 014's urgency prose, not in
> `depends_on` (which tracks upstream completed plans).

## Origin

Discovered during plan 014 review (2026-04-01). The logging convention's lossy
underscore transform is a pre-existing design flaw affecting all snake_case modules.
