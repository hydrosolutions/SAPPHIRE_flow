# Admit the documented 12z radiation horizon

status: READY

## Problem and evidence

The first Nepal CMAL run on release 0.1.1190 failed before prediction because
`qualified_interval_duration` assigns a 72-hour limit to every non-midnight IFS
cycle. The read-only live check on 24 November found finite 23 November 12z
solar-radiation intervals at leads 72 through 258 hours. The 00z checks passed.

[ECMWF's IFS open-data schedule](https://www.ecmwf.int/en/forecasts/datasets/open-data)
declares both 00z and 12z control forecasts through 360 hours: end steps every
three hours through 144, then every six hours through 360. For the Gateway's
already-qualified START labels, these are starts 0–141 and 144–354 respectively.
This corrects the older radiation plan's classification of 12z as a short cycle.

## Change

1. Centralize the IFS maximum lead in `types/recap_intervals.py`, admitting the
   existing 360-hour interval schedule for hours 0 and 12. Use it in the duration
   helper and the three duplicate ceilings in `types/operational_weather.py`
   (daily support, sample validation, daily reduction). Retain the current
   72-hour admission for 06z/18z; qualifying those products is outside this task.
2. Test 12z conversion at the old boundary, the three-to-six-hour transition,
   and the final interval, including negative thermal energy. Add rejection
   tests for off-grid/out-of-range rows and short cycles. Explicitly cover 12z
   starts 69, 72, 141, 144 and 354; reject -3, 145, 357, 360 and off-hour starts.
   For 06z/18z, admit start 69 and reject start 72. Exercise a complete daily
   reduction crossing the 141/144 transition, temperature's terminal state,
   precipitation's terminal interval and daily-support selection after lead 72.
   Daily support must admit the complete 12z day starting at lead 324 and refuse
   the next UTC day starting at lead 348, whose end would exceed lead 360.
3. Update the radiation runbook and touchpoint map, and annotate the superseded
   statement in the older radiation plan. No FI, timestamp, unit, cache identity,
   contract-revision, or model changes: previously admitted intervals retain
   identical durations and conversions. Success removes the erroneous duration
   rejection; it does not establish sufficient live forecast coverage.

The live native-start attestation is
`gateway-docs:6bcc6d949a33ddda042f844d36bc2710620a9ccb`. Its pinned client README
documents START-labelled accumulated windows; it does not attest that every
cycle's full tail is available. The 12z maximum here is supported separately by
the ECMWF schedule and the captured live post-72h intervals. Starts beyond 258
were not confirmed live. Missing support must still fail completeness checks.

Retain `rad-int-v1`: it describes the existing energy/duration conversion and
START-labelled support, neither of which changes for any previously admitted
sample. Successful acquisition identities do not serialize a maximum lead;
invalid samples fail before successful series caching. Earlier failed-run
diagnostics remain historical failures, not reusable successful inputs. The
first replacement-model run stored no forecasts. Do not rewrite old evidence
or imply that the documentation pin alone proves 360 hours of Gateway data.

## Separate operational limit

The deployed client already requests `horizon_days=15`; the sampled Gateway
responses currently end after 264 source hours. That does not establish eleven
complete days for every eligible cycle on CMAL's START18UTC grid. This patch
must not fill missing intervals, lower the configured eleven-day floor, enable
recurrence, or claim live qualification. A temporary shorter horizon requires
the owner's separate decision; the longer Gateway tail is an upstream task.

## Gates

Independent plan review precedes implementation. Run interval/conversion,
Gateway-adapter and operational-weather regressions, formatting/lint and changed
module type checks. Obtain independent patch review; full CI must pass before
owner merge. No host hot patch or unapproved release follows from this plan.

```json
{"phases":[{"id":"review","depends_on":[]},{"id":"implementation","depends_on":["review"]},{"id":"verification_review","depends_on":["implementation"]},{"id":"owner_merge","depends_on":["verification_review"]}]}
```
