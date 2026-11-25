# BAFU danger threshold import

The operator command previews official, explicitly labelled BAFU discharge bands
for a tenant's BAFU discharge stations. It does not re-onboard stations, fit
models, compute skill, change alert switches or activate model artifacts.

```sh
uv run python -m sapphire_flow.cli.import_bafu_thresholds \
  --config config.toml --tenant sapphire --operator NAME \
  --report-dir /data/threshold-preview --station 2150
```

Omit `--station` to select the tenant's BAFU discharge roster; repeat it to select
several stations. Inspect `report.json`, per-station JSON evidence and source HTML.
Run with `--apply` to import. This fetches current source again: compare the new
digest with the preview when values matter. Each station's set commits atomically
with its audit entry. Unchanged values are left alone; differing values or source
classification produce a conflict, never overwrite. Missing or malformed source
values remain unavailable. A nonzero exit reports errors or conflicts; successful
stations in the same batch remain committed.

The source is the German official station page, with explicit discharge units
and level labels. Pages labelled l/s use the existing discharge-unit normalizer
to convert values to canonical m³/s; the original page remains in the evidence.
Water-level thresholds are not converted into discharge. Numeric levels 2–5 map to the configured Swiss names Moderate,
Considerable, High, Very High. There is no level-1 boundary. The old 40-station
colour-derived JSON is historical evidence and is not imported by this command.
Station-detail API threshold rows use `source=authority`.

Use the worker database credentials, which already have threshold and audit
write grants. Forecast alerts must remain disabled for this initial import.
The owner explicitly approved observation-alert records on 2026-11-24; the
existing observation-alert switch is preserved and recorded in the report.
No notification setting is changed. Source snapshots identify today's published bands, not their
historical effective dates. Comparing daily mean forecasts with these boundaries
must be labelled daily-mean exceedance, not instantaneous peak or window-event
probability. Probability/skill wiring is a separate follow-on.

Production station onboarding invokes the same importer for BAFU discharge
stations before training/skill. Source failures are logged and ordinary onboarding
continues. Evidence is retained under the configured artifact directory's
`threshold-imports` subdirectory. Existing stations use the explicit CLI backfill.
Other networks are unaffected. Existing onboarding skill reads canonical thresholds
instead of an always-empty list; quantile threshold scores remain omitted until
representation-correct probability wiring is implemented.

## Review probabilities (contract 3.3; deployment pending)

`GET /api/v1/stations/{id}/review-series` adds a `thresholds` snapshot and each
available point's `threshold_probabilities`, keyed by the snapshot's `key`.
Keys encode parameter, danger level and source, so authority/inferred thresholds
remain separate. The snapshot records canonical m3/s values, source and update time;
it uses current stored thresholds, not historically effective thresholds.

Probabilities mean strict `P(forecast value > threshold)` at that valid time.
Members use their empirical fraction. Quantile points use explicitly labelled
piecewise-linear inverse-CDF interpolation, with distribution-free bounds.
Outside resolved quantiles, the point probability is null and bounds remain:
for example `[0, 0.05]` means **at most 5%**, not zero or exactly 5%.
Tied quantiles use the rightmost tied probability for the interpolation assumption.
Comparison uses stored floats without an epsilon. Duplicate/crossing quantiles
are unavailable; unlike the legacy alert helper, duplicates are not averaged.

An empty threshold snapshot means thresholds not configured. Rejected/missing
forecast points have no probability entries; an unsupported forecast unit has an
explicit unavailable result. Unchecked QC remains labelled at the point level.
Temporal support is still unknown in this API: do not label these as instantaneous
flood peaks or a probability of an event at any time during a window. The route
keeps existing access/publication gates and 2 MiB cap; narrow large hourly requests
if the cap is reached. These fields do not change alerts or compute skill.
