---
status: DONE
created: 2026-03-26
scope: docs + spec consistency fix
---

# 002 — Fix station_ids type: tuple for ordered batch, frozenset for unordered sets

## Problem

`GroupModelInputs.station_ids` and `GroupTrainingData.station_ids` use `frozenset[StationId]`
in `docs/spec/types-and-protocols.md` but `tuple[StationId, ...]` in the design doc. The
tuple is correct because ordering is load-bearing for tensor creation:

```python
# Tensor path requires consistent station ordering
arr = df.sort("station_id", "timestamp").to_numpy()
tensor = arr.reshape(n_stations, seq_len, n_features)
```

The batch dimension maps to `station_ids[0], station_ids[1], ...` — ordering matters.

**Convention**: `tuple` for ordered collections where position matters (batch dimension),
`frozenset` for unordered membership sets (`StationGroup.station_ids`, `OnboardingUnit.station_ids`).

## Changes

### 1. `docs/spec/types-and-protocols.md`

Update `GroupModelInputs.station_ids` and `GroupTrainingData.station_ids` from
`frozenset[StationId]` to `tuple[StationId, ...]`.

Leave `StationGroup.station_ids` as `frozenset[StationId]` (membership, not ordering).
Leave `OnboardingUnit.station_ids` as `frozenset[StationId]` (membership).

### 2. `docs/design/v0-flow13-model-onboarding.md`

Already uses `tuple` for `GroupModelInputs`/`GroupTrainingData` — verify no
`frozenset` variants crept in for these types. No change expected.

### 3. `docs/architecture-context.md`

Check if `station_ids` type is mentioned — update if needed. Likely no change
since the architecture doc doesn't specify Python types at this granularity.

## Verification

- Grep for `station_ids.*frozenset` in spec — should only match `StationGroup` and
  `OnboardingUnit`, not `GroupModelInputs` or `GroupTrainingData`
- Grep for `station_ids.*tuple` in spec — should match `GroupModelInputs` and
  `GroupTrainingData`
