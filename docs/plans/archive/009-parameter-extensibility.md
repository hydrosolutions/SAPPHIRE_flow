---
status: DONE
created: 2026-03-27
scope: types (enum extension only)
depends_on: []  # Independent — can land whenever
---

# 009 — Parameter Extensibility

## Problem

The `ParameterDomain` enum in `src/sapphire_flow/types/enums.py` has only 2 of the 5 values
defined in the spec (`types-and-protocols.md`): `RIVER` and `WEATHER`. The spec also defines
`WATER_QUALITY`, `GROUNDWATER`, and `SOIL`. `v0-scope.md` §G mandates the full type system
from the spec (all enums except `UserRole`, `AuditEventType`, `AdjustmentType`, `Calendar`),
so `ParameterDomain` is already required to have all 5 values. This plan brings the enum in
line with both documents.

The broader parameter extensibility machinery (config-driven registration, `ParameterStore.register()`,
Flow 0 step 0.6, CHECK constraint removal) is **deferred** — see §Deferred below for rationale.

### Why the full machinery is premature for v0

Blast-radius analysis showed that `ParameterStore` is not called by any flow, service, or adapter
in the application. The store exists as a protocol + implementation exercised only in integration
tests. Plan 009's original `register()` + config-driven loading would replace a 5-line Alembic
migration with significant machinery — marginal value at v0 scale.

The **real extensibility barriers** when adding a new forecast-target parameter are elsewhere:

| Barrier | Location | Nature |
|---|---|---|
| Hard `Literal` type union | `StationThreshold.parameter`, `ExceedanceResult.parameter` (`types/domain.py`) | Blocks new targets at type-check time |
| Hardcoded QC rules | `config/qc_rules.py`, `config/forecast_qc_rules.py` | New parameter gets no QC rules — observations/forecasts skip QC silently |
| `"discharge"` fallbacks | `services/onboarding.py`, `hindcast.py`, `training_data.py` | Stations with only the new target silently fall back to discharge |
| Hardcoded assertion | `test_parameter_store.py` (`len == 10`) | Breaks on any new seed parameter |

None of these are addressed by the registration machinery. They require targeted code changes
when a new forecast target is actually needed — and those changes are needed regardless of
whether parameters are registered via config or via migration.

Adding a **weather-domain** parameter (e.g. soil moisture) is already trivial: a single migration
INSERT. No code branches on specific weather parameter names.

---

## Changes (v0)

### Phase 1A — Extend `ParameterDomain` enum

`src/sapphire_flow/types/enums.py`: Add `WATER_QUALITY`, `GROUNDWATER`, `SOIL` to match spec.

### Note: intentional enum/DB asymmetry

After Phase 1A, the Python enum has 5 values but the DB CHECK constraint still says
`IN ('river', 'weather')`. This is safe: no seed rows use the new values and no code path
inserts them. The asymmetry resolves when D2 lands.

**Pre-existing spec deviation:** The CHECK constraint itself is a deviation from both
`architecture-context.md` (line 2411: *"The DB column remains TEXT with no CHECK constraint —
the enum is advisory, not a gate"*) and `v0-scope.md` §C (*"parameters — as designed"*).
D2 is therefore a spec-compliance fix, not a future enhancement.

### Test

- Unit test in `tests/unit/types/test_enums.py`: `ParameterDomain` enum has exactly 5 values.

### File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/enums.py` | Add 3 `ParameterDomain` values | 1A |
| `docs/conventions.md` | Update enum table line ~393 to list all 5 values (resolves inconsistency with prose at lines 102–106 which already names all 5) | 1A |
| `docs/spec/database-schema.md` | Update inline comments at lines ~30 and ~454 from `"river \| weather"` to include all 5 domains | 1A |

---

## Deferred (implement when parameter registration is actually needed)

The full registration machinery is well-specified in `types-and-protocols.md` (§ParameterStore,
lines 2044–2055) and `architecture-context.md` (§Flow 0 step 0.6). When implementing, address
these design issues identified during review:

### D1. `ParameterStore.register()` protocol + implementations

Add `register(self, definition: ParameterDefinition) -> None` to the protocol per spec.
Implement in `PgParameterStore` (upsert) and `FakeParameterStore`.

**Review finding (C1):** The upsert must update only `display_name`, `unit`, `aggregation_method`
on conflict — NOT `parameter_domain`. The spec is explicit about these three fields
(`types-and-protocols.md:2051-2054`). Updating `parameter_domain` could silently reclassify
seed parameters, breaking downstream threshold/alerting logic.

**Review finding (C2):** `ParameterDefinition.created_at` is a required field (no default).
The caller must construct a full `ParameterDefinition` to call `register()`. The store should
use `server_default=sa.func.now()` on INSERT and not update `created_at` on conflict.
Document that the passed `created_at` is a construction requirement of the frozen dataclass
but the DB column default is authoritative for new inserts.

### D2. Migration: drop CHECK constraint on `parameter_domain`

Remove `parameter_domain IN ('river', 'weather')` CHECK from the `parameters` table. Must
land in the same commit as the corresponding `metadata.py` change to avoid Alembic autogenerate
drift.

### D3. Config-driven parameter loading (Flow 0 step 0.6)

**Review finding (H1):** The Pydantic boundary model should be named `_ParameterInput` (not
`ParameterConfig`) to match the existing convention in `config/deployment.py` where all internal
models use `_*Input` prefix.

**Review finding (H2):** `load_config()` must `data.pop("parameters", [])` before
`DeploymentConfig.model_validate(data)`.

**Review finding (H3):** The spec (`types-and-protocols.md:2310-2312`) says `[[parameters]]`
is loaded separately from `DeploymentConfig`, not embedded in it. Either return a tuple from
`load_config()` or add a separate `load_parameter_definitions()` function.

**Review finding (M3):** The structured warning for unknown domains should follow `logging.md`
event naming: `parameter.domain_unrecognized` (`{entity}.{action}` pattern with past-tense
verb, per `logging.md` line 225). Pass `domain` as a keyword argument for context.

### D4. Read-path safety for unknown domains

**Review finding (C3):** `_row_to_domain()` in `parameter_store.py` does
`ParameterDomain(row["parameter_domain"])` — this crashes with `ValueError` for any domain
string not in the enum. If the CHECK constraint is removed and unknown domains are stored,
the read path must handle them leniently. Resolve before implementing D1-D3.

### D5. Non-deletion invariant

**Review finding (M1):** The spec says `register()` "does not delete parameters absent from
config (seed data is preserved)." Test this explicitly: re-running with fewer `[[parameters]]`
entries must leave all seed parameters intact.

### D6. Wider extensibility barriers (separate plans)

These are the real barriers to adding new forecast-target parameters and require their own plans:

- **Widen `StationThreshold.parameter`** from `Literal["discharge", "water_level"]` to `str`
  (also `ExceedanceResult.parameter`)
- **Make QC rules config-driven** instead of hardcoded per parameter
- **Remove `"discharge"` fallbacks** in onboarding, hindcast, and training_data services
- **Extend `StationKind`** for boreholes, lysimeters
- **New forcing sources** for non-river models (soil moisture, recharge)

---

## Open Items

1. **API endpoint for parameter registration** — Not needed for v0. When needed, add
   `POST /api/v1/parameters` that calls `ParameterStore.register()`. Note: `security.md`
   line 31 requires session-token auth for all POST routes.
