# Synthetic onboarding scenario

Fully synthetic and agency-neutral: no real station, no real data, no real
coordinates. Safe to load into a sandbox to exercise onboarding end to end.

| File | Content |
|---|---|
| `scenario.json` | the station in `create_station` argument form (`--key value`; the tenant is chosen by the operator): code `SYN001`, network `synthetic`, lon 0.0, lat 0.0, timezone `UTC`, parameters `discharge` |
| `discharge_hourly.csv` | `time,value`; `time` ISO UTC (`2025-01-02T21:00:00Z`); 72 contiguous hourly values ending `2025-01-02T23:00:00Z` |
| `discharge_daily.csv` | `time,value`; `time` the station-local day `YYYY-MM-DD` (zone `UTC`); 90 days ending `2025-01-02` |

Units: m3/s. Values are written at 6 decimals (full generator precision).

## How they were made

`sapphire_flow.services.synthetic_series.render_scenario(spec, rule_set)` with
`HOURLY_SPEC` and `DAILY_SPEC`: baseflow on an exponential recession plus
gamma-shaped storm pulses (`(x**shape) * exp(shape * (1 - x))`, `x` = time since
storm onset / rise time), no noise, no sine, no clipping. Storm onsets and
amplitudes come from `random.Random(seed)`. QC limits (range, rate of change,
spike) are read from the resolved rule set passed in (here the Swiss built-in
default, `SAPPHIRE_CONFIG` unset); storm amplitudes shrink by 0.8 per round until
the series sits inside them.

| | hourly | daily |
|---|---|---|
| seed | 540 | 541 |
| baseflow | 18.0 m3/s, decay 40 d | 18.0 m3/s, decay 60 d |
| storm peak | up to 14.0 m3/s | up to 14.0 m3/s |
| rise time / shape | 0.75 d / 2.0 | 6.0 d / 2.0 |
| storms per 30 d | 15.0 | 2.5 |

## Verification

`tests/unit/services/test_synthetic_series.py` runs both files through the real
`Stage1QualityChecker.check_with_coverage` under the Swiss built-in default rule
set (no overrides, no baselines, network `synthetic`): no flags at all and every
reading judged.

- Hourly (3600 s group): `range_check`, `rate_of_change`, `spike` run.
  `gross_outlier` did NOT run: a new station has no climatology baseline, so it
  judges nothing (asserted). There is no `frozen_sensor` rule in the hourly group.
- Daily (86400 s group): `range_check`, `rate_of_change`, `frozen_sensor`, `spike`
  run. `gross_outlier` did NOT run, same reason.
- `rate_of_change` cannot judge the first reading and `spike` the first and last
  (no neighbour); other rules judge them.

## Regeneration rule

The CSVs equal the generator output for the recorded seed and parameters (a test
enforces it). Change them only by a deliberate seed or parameter change, recorded
in this README in the same commit.
