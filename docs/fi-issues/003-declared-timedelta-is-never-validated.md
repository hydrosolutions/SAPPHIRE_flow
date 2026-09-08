# FI issue draft — a declared `timedelta` is never checked against the timestamps it describes

**Status:** DRAFT, ready to file at `hydrosolutions/ForecastInterface`.
**Raised by:** SAPPHIRE Flow (SAP3), 2026-09-08, from the time-grid plan family (Plans 252 / 254 /
248).
**Related:** `docs/fi-issues/002-future-steps-at-most-semantics.md` — same shape: the contract
carries a declaration that nothing enforces, and both sides work around it in prose.

---

## Summary

`VariableMetadata.timedelta` declares a variable's cadence, and `forecast_horizon` and `offset` are
both **counts of steps of that length** — so the declared `timedelta` is what gives the entire output
frame its meaning. But FI never checks that the `datetime` column is actually spaced at the declared
`timedelta`.

`validate_temporal_columns` (`forecast_interface/output/_validators.py`) checks column **presence and
dtype only**:

```python
TEMPORAL_COLUMNS = ("issue_datetime", "datetime")

def validate_temporal_columns(df: pl.DataFrame) -> None:
    for col in TEMPORAL_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"DataFrame must contain a '{col}' column")
        if not isinstance(df.schema[col], DATETIME_DTYPE):
            raise ValueError(f"'{col}' column must be Datetime, got {df.schema[col]}")
```

`VariableMetadata` validates that `timedelta` is *positive* (`_positive_timedelta`,
`forecast_interface/output/metadata.py`) and nothing more. So a model may declare `1 day` and emit
hourly rows, and FI accepts it.

## Demonstration

Run against the currently pinned FI:

```python
import polars as pl, datetime as dt
from forecast_interface.output._validators import validate_temporal_columns

hourly = pl.DataFrame({
    "issue_datetime": [dt.datetime(2026, 1, 1)] * 3,
    "datetime": [dt.datetime(2026, 1, 1, h) for h in (0, 1, 2)],
})
validate_temporal_columns(hourly)   # accepted, under a declared timedelta of 1 day
```

## Why this matters to a consumer

SAP3 reads the declaration and trusts it: `adapters/forecast_interface.py` takes
`time_step = var_output.metadata.timedelta`, and `store/forecast_store.py` persists
`int(forecast.ensemble.time_step.total_seconds())` into `forecasts.time_step_seconds`.

A model that declares one cadence and emits another therefore produces a stored row whose recorded
step **contradicts its own timestamps**, permanently and undetectably — the timestamps and the step
are both in the database, agreeing with nothing. Nothing on either side of the boundary is positioned
to notice: FI has the frame but does not check it, and SAP3 has only the declaration.

Today's models are well-behaved — SAP3 measured the per-model gap across all five real models on
staging 2026-09-08 and it is exactly `1 day` for every one, matching what aquacast declares. So this
is a latent contract gap, not a live incident. We would rather close it while that is true.

## Why we are not fixing this on our side

Per SAP3's own rule (`CLAUDE.md` § ForecastInterface Adherence), a model that does not fit the
contract is fixed in the model, and a contract that cannot express what is needed is changed
upstream — never patched around in SAP3. A SAP3-side spacing check would be exactly such a
workaround: it would catch our own models and no one else's, it would duplicate a validation FI is
already the right place for, and it would leave every other FI consumer exposed. Hence this issue
rather than a local guard.

## Proposed resolution

Validate the declaration against the data at the point where FI already validates the frame — one
check, in `validate_temporal_columns` or beside it, with access to the variable's `timedelta`:

- **the spacing of `datetime` within each `issue_datetime` group equals the declared `timedelta`**;
- a frame whose spacing is not uniform is rejected, since `forecast_horizon` and `offset` are counts
  of uniform steps and have no meaning otherwise.

Open questions for co-design, which is why this is an issue and not a PR:

1. **Reject, or warn?** Rejecting is a breaking change for any model currently mis-declaring. A
   deprecation window that warns first may be the kinder path.
2. **Where does the check live?** `validate_temporal_columns` does not currently receive the metadata.
   Passing it in changes that function's signature; a separate `validate_cadence(df, metadata)`
   called from the same place would not.
3. **Single-row outputs.** A one-timestamp forecast has no measurable spacing. It should presumably
   pass rather than fail, but that needs saying explicitly — SAP3 has a matching hazard here (a
   reader that fabricates a 1-hour cadence for a single-step row, Plan 241).

## A second, separable gap in the same function

`validate_temporal_columns` also **accepts timezone-naive datetimes**. `isinstance(dtype,
pl.Datetime)` is true whether or not a time zone is set, so:

```python
naive = pl.DataFrame({"issue_datetime": [dt.datetime(2026, 1, 1)],
                      "datetime":       [dt.datetime(2026, 1, 2)]})
validate_temporal_columns(naive)   # accepted
```

This is separable from the cadence gap and could be split into its own issue — we raise it here only
because a fix touches the same six lines. It matters to us for a specific reason: SAP3 is extending
to Nepal, where local time is **UTC+05:45**. A naive timestamp that is silently read as UTC is off by
345 minutes, which is not a rounding error — it lands in the wrong day. SAP3 normalises to UTC at its
own boundaries (`ensure_utc()`), so we are protected on the inbound path; a model receiving a naive
frame from anywhere else is not.

Suggested resolution: require `pl.Datetime` with a non-null `time_zone`, on the same
reject-or-deprecate question as above.

## What we are asking for

Agreement on (1) whether FI should enforce the cadence it declares, and (2) whether a timezone-aware
`datetime` column should be required. We are happy to open the PR once the shape is agreed.
