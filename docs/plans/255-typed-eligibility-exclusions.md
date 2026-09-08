---
status: DRAFT
created: 2026-09-08
plan: 255
title: Eligibility exclusions become typed data — stop discarding why a station was refused a MeteoSwiss binding
scope: Make the reason `eligible_meteoswiss_configs` rejects a station retrievable by its callers instead of logged and thrown away, without changing the exclusion rule or any existing call site. This is the shared prerequisite Plan 256 T1 (the promotion hold) and Plan 259 (the onboarding report) both consume. Explicitly NOT a change to `_has_valid_geometry`, NOT a change to which stations are excluded, NOT a change to promotion, NOT geometry repair, NOT the report itself.
depends_on: []
blocks: [256, 259]
source: 2026-09-08 — split out of the original Plan 255 after three independent Codex rounds. The reporting half became Plan 259; this half is small, self-contained and independently verifiable, and it is what unblocks Plan 256.
---

# Plan 255 — eligibility exclusions become typed data

## Status

**DRAFT.** Awaiting owner READY.

Split from the original Plan 255 on 2026-09-08, on the observation that this task is the only one of
its four that is small, fully specified and blocking other work. The outcome record, the report and
its delivery moved to **Plan 259**, where an unresolved infrastructure question (a writable report
destination) can be settled without holding up Plan 256.

## Why this exists

`services/reanalysis_backfill.py:119` `eligible_meteoswiss_configs` rejects any station that cannot
be given a MeteoSwiss reanalysis binding. Its docstring says such stations are "logged and excluded —
never silently dropped". They are logged. The *reason* is then discarded: the function returns only
the eligible configs, so no caller can act on, report, or even observe why a station was refused.

Two consequences, both live on staging:

- **Plan 256's promotion hold cannot fire for an excluded station.** The hold is
  `held_out_ids = meteoswiss_eligible_ids - meteoswiss_backfilled` (`services/onboarding.py:765`).
  A station that was never eligible is not in the minuend, so it cannot be held — it is promoted with
  no operational forcing behind it. Being more broken puts a station outside the guard.
- **Plan 259's report cannot name the reason.** On staging, **2024 Branson**
  (`Ring Self-intersection[7.20127964026713 46.1757792259881]`) is the only station of 148 with no
  MeteoSwiss binding and zero `meteoswiss_*` rows, and nothing in the database records why.

This plan does not fix either consequence. It creates the channel both fixes need.

## Tasks

### T1 — a partitioning function beside the existing one

**Outcome.** A caller can retrieve the eligible MeteoSwiss configs **and** a typed exclusion per
rejected station, carrying the station and an `Enum` reason.

⚠️ **Add a function; do not change the existing signature.** Four production call sites and eight
test sites depend on the current return shape, and two of them are operator scripts that would
silently pass the wrong object downstream:

| production | |
|---|---|
| `services/onboarding.py:706` | onboarding Step 4c |
| `services/reanalysis_backfill.py:161` | `bind_meteoswiss_reanalysis_fleet` |
| `scripts/backfill_meteoswiss_history.py:187` | operator script |
| `scripts/validate_forcing_reference.py:223` | operator script |

| tests | |
|---|---|
| `tests/unit/services/test_reanalysis_backfill.py` | 7 direct call sites |
| `tests/unit/scripts/test_backfill_meteoswiss_history_script.py:205` | patches the symbol |

So: add `partition_meteoswiss_eligibility(stations, basin_store)` returning eligible configs plus
exclusions, and re-express `eligible_meteoswiss_configs` as a thin wrapper over it. Every existing
caller keeps working, unmodified, by construction.

The reason `Enum` covers **every** branch the function already rejects on — not only the
self-intersection that prompted this:

`NO_BASIN_ID` · `BASIN_NOT_FOUND` · `GEOMETRY_WRONG_TYPE` · `GEOMETRY_EMPTY` · `GEOMETRY_INVALID`

Consumers decide which reasons mean what; this plan only makes them observable. Plan 256 T1 documents
which of them withhold a station and for which station kinds.

**The interface, named** — this is an interoperability boundary, so it is specified here rather than
left to the implementer:

```python
class MeteoswissExclusionReason(Enum):        # in types/, not services/
    NO_BASIN_ID = auto(); BASIN_NOT_FOUND = auto()
    GEOMETRY_WRONG_TYPE = auto(); GEOMETRY_EMPTY = auto(); GEOMETRY_INVALID = auto()

@dataclass(frozen=True, kw_only=True, slots=True)
class MeteoswissExclusion:
    station_id: StationId
    code: str
    reason: MeteoswissExclusionReason

@dataclass(frozen=True, kw_only=True, slots=True)
class MeteoswissEligibility:
    eligible: tuple[StationWeatherSource, ...]
    excluded: tuple[MeteoswissExclusion, ...]

def partition_meteoswiss_eligibility(
    stations: list[StationConfig], basin_store: BasinStore
) -> MeteoswissEligibility: ...
```

`eligible_meteoswiss_configs` keeps its exact present signature and `list` return, delegating to the
above.

**In.** `src/sapphire_flow/services/reanalysis_backfill.py`;
`src/sapphire_flow/types/reanalysis.py` (the two new types and the enum);
`tests/unit/services/test_reanalysis_backfill.py` (new cases — the task must edit this file);
`docs/spec/types-and-protocols.md:3842` (documents the new types and the exact return signature; the
existing signature is unchanged); `docs/touchpoint-maps.md:194` (documents both MeteoSwiss binding
paths).
**Out.** No change to `_has_valid_geometry`'s predicate, to which stations are excluded, to any
existing call site, or to `eligible_meteoswiss_configs`' return type. No geometry repair. No change to
onboarding, promotion or reporting.

**Verification.** Three commands, all of which must pass:

```
uv run pytest tests/unit/services/test_reanalysis_backfill.py
uv run pytest tests/unit/scripts/test_backfill_meteoswiss_history_script.py
uv run pytest "tests/unit/services/test_onboarding.py::TestMeteoswissBindingAndBackfillOrHold::test_ineligible_station_no_geometry_gets_no_binding"
```

The first two cover all eight existing test sites, which must pass **unmodified**. The third is the
node that locks today's exclusion behaviour end-to-end.

The new `TestPartitionMeteoswissEligibility` must assert, against an **independently written expected
result** — not against the wrapper:

| case | assertion |
|---|---|
| each of the five rejection reasons | the station appears in `excluded` with that exact reason |
| a valid **`Polygon`** basin | appears in `eligible` |
| a valid **`MultiPolygon`** basin | appears in `eligible` |

⚠️ **The MultiPolygon case is load-bearing and is the reason wrapper-equality is not enough.**
`_has_valid_geometry` accepts `(Polygon, MultiPolygon)`
(`services/reanalysis_backfill.py:100`), but `MultiPolygon` appears **zero times** in the entire
existing suite — every positive fixture builds a `Polygon`. An implementation that began rejecting
valid `MultiPolygon`s would keep the partition and the wrapper in perfect agreement and pass every
other assertion here. Asserting the partition equals the wrapper is circular: the wrapper delegates
to the partition, so they cannot disagree. The expected eligible sequence must be stated
independently.

**Pre-change.** RED: a test asserting that a station rejected for a self-intersecting polygon can be
retrieved **with its reason** fails today.

An earlier draft marked this `N/A` on the grounds that a missing-function failure is a mere signature
error. That was wrong, and it dodged the RED `docs/workflow.md:20` requires: here the **absence of
the channel is precisely the defect**. There is no way to learn why a station was refused, and a test
that cannot obtain the reason is failing for exactly that reason — not incidentally. The plan changes
observable behaviour (a caller can now act on the reason), so `N/A`, which is reserved for
mechanical and documentation work, does not apply.

The preservation half is carried separately by the three commands above passing unmodified, plus the
independent `Polygon` / `MultiPolygon` acceptance assertions.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] }
  ]
}
```
