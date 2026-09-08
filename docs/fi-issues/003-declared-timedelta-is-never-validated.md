# FI issue draft — a declared `timedelta` is never checked against the timestamps it describes

**Status:** ✅ **FILED 2026-09-08 as
[`hydrosolutions/ForecastInterface#9`](https://github.com/hydrosolutions/ForecastInterface/issues/9).**
Awaiting maintainer response on the two questions in *What we are asking for*. Per the lesson recorded
in `002`, watch for the resolving version and record "resolved in vX, adopt by doing Y" here — do not
leave this file without an adoption marker.
**Raised by:** SAPPHIRE Flow (SAP3), 2026-09-08, from the time-grid plan family (Plans 252 / 254 / 248).
**Affected version:** `forecastinterface` **v0.1.20**, commit `ad19597e02f7bbd77b69f6339db3351847fe9791`
(the revision SAP3 pins). Reproduced on Python 3.12.10, Polars 1.43.2.
**Related:** `hydrosolutions/ForecastInterface#7` (target temporal semantics) — adjacent, and we think
complementary; see *Relationship to #7* below. Also `docs/fi-issues/002` (`future_steps` "at most"),
resolved in v0.1.20.
**Blocking us?** No. Latent today — see *Severity*.

---

## Summary

`VariableMetadata.timedelta` declares a variable's cadence, and `forecast_horizon` and `offset` are
both **counts of steps of that length** (`forecast_interface/output/metadata.py:11`). The declared
`timedelta` is therefore what gives the whole output frame its meaning.

FI validates the frame in two ways, and neither of them is the one that matters here:

- `validate_temporal_columns` (`forecast_interface/output/_validators.py:18`) checks that
  `issue_datetime` and `datetime` are **present** and are `pl.Datetime`. Nothing more.
- `VariableOutput._validate_forecast_horizon` (`.../output/variable_output.py:132`) cross-checks the
  **row count** against `forecast_horizon`.

So the number of rows is checked, and their **spacing never is**. The `timedelta` field itself is
validated only for positivity (`metadata.py`, `_positive_timedelta`). A model may declare `1 day`,
emit three rows one hour apart, and FI accepts it — because three rows is what a horizon of three
asks for.

## Reproduction

Public API only, against v0.1.20:

```python
import datetime as dt
import polars as pl
from forecast_interface.common.units import Unit
from forecast_interface.output.metadata import VariableMetadata
from forecast_interface.output.variable_output import (
    DeterministicData, VariableOutput, VariableStatus,
)

issue = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

# DECLARE a daily cadence ...
metadata = VariableMetadata(
    unit=Unit.M3_PER_S, timedelta=dt.timedelta(days=1), forecast_horizon=3, offset=1,
)
# ... then EMIT three rows one hour apart.
df = pl.DataFrame({
    "issue_datetime": [issue] * 3,
    "datetime": [issue + dt.timedelta(hours=h) for h in (1, 2, 3)],
    "value": [1.0, 2.0, 3.0],
})

vo = VariableOutput(
    metadata=metadata,
    deterministic=DeterministicData(data=df),
    status=VariableStatus.SUCCESS,
)

print(vo.metadata.timedelta)                                    # 1 day, 0:00:00
print(vo.deterministic.data["datetime"].diff().drop_nulls().unique().to_list())
#   [datetime.timedelta(seconds=3600)]   <- one hour
```

No error is raised. The object is valid, self-inconsistent, and reports a cadence its own timestamps
contradict.

## Why it matters to a consumer

SAP3 reads the declaration and trusts it: the adapter takes
`time_step = var_output.metadata.timedelta` (`adapters/forecast_interface.py:221`) and the store
persists `int(ensemble.time_step.total_seconds())` into `forecasts.time_step_seconds`
(`store/forecast_store.py:62`). Running the frame above through our adapter, it is accepted: a stored
row then records a one-day step whose own timestamps are one hour apart — the two disagree with each
other in the database, and nothing raises.

To be accurate about where the gap sits: **SAP3 is not unable to notice this — it chooses not to.**
The adapter holds the metadata and the frame at the same point, so it could compare them. We would
rather the check lived at the boundary that owns the declaration, where it protects every FI consumer
instead of only the one that wrote its own guard. That is a contract question, which is why this is an
issue rather than a local patch — and it matches how `docs/fi-issues/002` was resolved.

## Severity

Latent, not an incident. SAP3 measured the per-model gap across all five models running on staging on
2026-09-08: it is exactly `1 day` for every one, matching what aquacast declares. Declaration and
reality agree today. We would rather close the gap while that is still true.

## Proposed resolution

Validate the declaration against the data where FI already validates the frame, with access to the
variable's `timedelta`:

- the spacing of `datetime` within each `issue_datetime` group equals the declared `timedelta`;
- non-uniform spacing is rejected, since `forecast_horizon` and `offset` are counts of uniform steps
  and have no meaning otherwise.

Open questions, which is why this is an issue and not a PR:

1. **Reject, or warn first?** Rejecting is a breaking change for any model currently mis-declaring. A
   deprecation window may be the kinder path.
2. **Where does the check live?** `validate_temporal_columns` does not receive the metadata; changing
   its signature affects callers, whereas a separate `validate_cadence(df, metadata)` called from the
   same place would not. `_validate_forecast_horizon` already has both and may be the natural home.
3. **Single-row outputs** have no measurable spacing. They should presumably pass rather than fail,
   but it is worth stating. SAP3 has a matching hazard here — a reader that fabricated a one-hour
   cadence for a single-step row (our Plan 241).

## A second, separable gap in the same validator

`validate_temporal_columns` also **accepts timezone-naive datetimes**, because
`isinstance(dtype, pl.Datetime)` is true whether or not a time zone is set. Same construction as
above, with `dt.datetime(2026, 1, 1)` and no `tzinfo`:

```
tz-naive frame ACCEPTED; dtype = Datetime(time_unit='us', time_zone=None)
```

This is separable from the cadence gap and can be split into its own issue; we raise it here only
because a fix touches the same validator. Two reasons it is worth doing:

**The contract already says these are UTC, and the shipped example does not follow it.** FI documents
both temporal columns as UTC, while its own README example constructs naive datetimes — so the
convention is stated but neither enforced nor demonstrated.

**It is a real exposure for us.** SAP3 is extending to Nepal, where local time is **UTC+05:45**. A
naive timestamp read as UTC is off by 345 minutes, which **can** land it in the wrong day. We are not
currently protected: `ensure_utc()` (`types/datetime.py:7-10`) does reject naive datetimes, but it
guards the **top-level** `ModelOutput.issue_datetime` only. A variable's own valid times take a
different path (`adapters/forecast_interface.py:367-376`), which casts the column, and the comment
there states the behaviour plainly:

```python
# FI datetimes are UTC by contract; tz-naive values are localized, not shifted.
```

"UTC by contract" is exactly the assumption at issue: the contract says it, and nothing checks it. We
confirmed a naive midnight stays midnight and simply acquires a UTC label. The frame's own
`issue_datetime` column is likewise accepted naive and then ignored during conversion.

Suggested resolution: require `pl.Datetime` with a non-null `time_zone`, on the same
reject-or-deprecate question as above.

## Relationship to #7

Issue #7 says the interface cannot **declare** what a value means over its step — sum, mean, or
sampled at the stamp. This issue says the declaration that *does* exist, the cadence, is never
**checked**. Same shape, opposite halves: one is a missing statement, the other an unenforced one.

#7 ends on an open question — whether `AggregationMethod` can express "instantaneous / sampled at the
stamp", and whether that needs a fourth member. We have just settled the equivalent question on our
side by adopting the **CF Conventions** vocabulary: `cell_methods` distinguishes `time: point` from
`time: sum` / `time: mean` / `time: maximum` exactly, it is the standard our upstream NetCDF sources
already carry, and it separates *what a value is* from *how to aggregate it* — which is the confusion
#7 is circling. We offer it as a candidate for #7 rather than a new invention, and we are happy to
write it up there if useful.

## What we are asking for

Agreement on (1) whether FI should enforce the cadence it declares, and (2) whether a timezone-aware
`datetime` column should be required. We are happy to open the PR once the shape is agreed.
