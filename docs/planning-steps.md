# Planning Steps for LLM-Driven Implementation

Before writing code, complete these planning steps in order. Each step
produces an artifact that downstream implementation sessions depend on.

## Step 1: Fill CLAUDE.md project description ✅

Replace `SHORT PROJECT DESCRIPTION` with a concise summary (3-4 sentences)
covering what SAPPHIRE Flow is, its current phase, and the immediate goal.
Every Claude Code session reads this first — it must orient the agent fast.

**Artifact**: Updated `CLAUDE.md`
**Status**: Done.

## Step 2: Consolidated types and protocols spec ✅

Scatter across 9 design docs, type definitions need a single authoritative
reference. Create `docs/spec/types-and-protocols.md` with:

- Every `NamedTuple`, `NewType`, `Enum`, `Literal` with exact field names,
  types, and invariants (validation in `__new__`)
- Every `Protocol` with exact method signatures
- Mapping from types to the design doc section they originate from

This is the single highest-leverage artifact. It is the contract everything
else implements, and it is exactly what an LLM needs to stay consistent
across sessions.

**Artifact**: `docs/spec/types-and-protocols.md`
**Status**: Done. Spec created and verified through 3 rounds of cross-document
consistency review.

## Step 3: Implementation plan with dependency chains ✅

Create `docs/spec/implementation-plan.md` describing *what order* to build,
with explicit dependency chains:

| Phase | What                          | Depends on |
|-------|-------------------------------|------------|
| 0a    | Domain types + Protocols      | Step 2 spec |
| 0b    | Test fakes for all Protocols  | 0a         |
| 0c    | Services layer (pure funcs)   | 0a, 0b     |
| 0d    | Store layer (PostgreSQL impl) | 0a         |
| 0e    | Adapters (MeteoSwiss, hydro_scraper) | 0a, 0d |
| 0f    | Prefect flows (wiring)        | 0c, 0d, 0e |
| 0g    | API + dashboard               | 0c, 0d     |
| 0h    | Docker Compose integration    | all above  |

Each phase lists exact files to create, what to test, and acceptance criteria.

**Artifact**: `docs/spec/implementation-plan.md`
**Status**: Done. ~23-35 sessions estimated across 8 phases.

## Step 4: Task-sized work units ✅

Break each phase into tasks small enough for a single Claude Code session
(~one module + its tests). Document as a checklist, e.g.:

- `[ ] types/observation.py` — Observation NamedTuple, QualityFlag enum
- `[ ] types/forecast.py` — ForecastEnsemble, ModelInputs
- `[ ] protocols/stores.py` — All store Protocols
- `[ ] services/qc.py + tests/test_qc.py`

**Artifact**: Checklist section within `docs/spec/implementation-plan.md`
**Status**: Done. Tasks 0a.1–0h.7 defined in the implementation plan.

## Step 5: Verification criteria per task ✅

For each task in Step 4, define how to verify correctness:

- "All types pass `pyright --strict`"
- "Tests pass with `uv run pytest tests/test_<module>.py`"
- "Protocol is `runtime_checkable` and a fake exists in `tests/fakes/`"

**Artifact**: Verification column in the task checklist
**Status**: Done. Every task row includes a Verify column.

## Step 6: Design test fakes upfront ✅

Specify fake implementations of each store Protocol *before* implementing
real ones. Services can be coded and tested against fakes without PostgreSQL.

**Artifact**: `tests/fakes/` directory spec in the implementation plan
**Status**: Done. Phase 0b tasks cover all 8 store Protocol fakes + factories.

## Step 7: Resolve remaining open questions ✅

The design docs flag unknowns. Resolve or explicitly defer with a placeholder:

- ~~Flood threshold datums (units/reference datum)~~ — **Resolved**: `FloodThreshold.unit` field added (e.g. `"m_gauge_zero"`, `"m_asl"`, `"m3s"`). Adapter must populate from source; store maps to DB `unit_note` column. Remaining question (which datum each source uses) resolved during adapter implementation.
- ~~MeteoSwiss API format (ensemble vs deterministic)~~ — **Resolved**: ICON-CH2-EPS ensemble (21 members, GRIB2). Training uses SMN station observations. NWP archived permanently. See 03-adapters.md.
- Event-mode forecasting approach — still open (`00-overview.md`)
- ~~FloodThreshold.level str vs FloodLevel enum~~ — **Resolved**: str in adapter type, parsed to enum at store boundary via `FloodLevel(value)` (string-valued enum)
- ~~ForecastStore.get_active_rating_curve placement~~ — **Resolved**: moved to RatingCurveStore
- ~~UUID vs str for IDs in Protocols~~ — **Resolved**: StationConfig in flow context, str codes at adapter boundaries
- ~~BulletinStore return type~~ — **Resolved**: Bulletin NamedTuple (not bare dict)

**Artifact**: Updated open questions in `00-overview.md` and `types-and-protocols.md`
**Status**: Done. 5 questions resolved in types spec. 2 remain open in `00-overview.md`
(MeteoSwiss format, event-mode forecasting) — these require external research during
v0 implementation, not spec work.

## Step 8: Project-specific conventions ✅

Capture patterns not already in CLAUDE.md:

- Naming conventions for modules, tables, API routes
- How adapters register themselves
- How Prefect flows discover models
- Error handling strategy at adapter boundaries

**Artifact**: `docs/conventions.md`
**Status**: Done. Covers naming (Python, DB, API, env vars, Prefect), adapter
registration, model discovery via entry points, error handling at boundaries,
ID/timestamp conventions, DB connection patterns, concurrency control,
partitioning, alert lifecycle, and forecast status workflow.

---

## Execution order

Steps 1-2 first (highest leverage, unblocks everything).
Step 3 next (structures all subsequent work).
Steps 4-6 together (they form the task backlog).
Steps 7-8 can happen in parallel with 4-6.

## Spec consistency review (completed 2026-03-06)

Three rounds of automated cross-document consistency review were performed
across `types-and-protocols.md`, `implementation-plan.md`, `01-architecture.md`,
`05-flows.md`, and `06-api.md`. Each round: fix → verify → review from multiple
perspectives (cross-doc consistency, type system soundness, implementability).

Key fixes applied:
- DB-backed enums use string values matching PostgreSQL (not `auto()`)
- All Protocol signatures fully typed with domain enums (not raw strings)
- Mutable NamedTuple defaults eliminated (`metadata={}` → `None`)
- Missing Protocol methods added (RatingCurveStore, BulletinStore)
- Bulletin NamedTuple created (replaced bare `dict` returns)
- ModelRegistry added to spec and implementation plan
- Flow code uses enum types consistently (ForecastStatus, FloodLevel, AlertSource, BulletinScope)
- Clock injection added to flows (no `datetime.now()` in business logic)
- `train_model` flow rewritten with correct types and validation
- `run_forecasts` passes `rating_curve_store` to `forecast_station`

Final verification: 29/29 checks passed across all 4 key files.
