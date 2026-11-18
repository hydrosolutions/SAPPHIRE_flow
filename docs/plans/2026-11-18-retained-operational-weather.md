---
status: READY
---

# Retain operational weather for Nepal forecast continuity

Base: remote main `7f4eed38`, merged into the planning branch as `e61ab2dc`.
Owner: this planning session. Scope: Nepal MVP, existing selected daily Recap
route. READY after independent review and owner approval of the recommended
changes, including the 72-hour retained-past lead limit. Implement W1 before W2.

Review outcome, 18 Nov: independent AI hydrology (Claude) and forecast-system
developer (Codex) reviews completed, followed by targeted closure of their
findings. Both approve the amended plan; the owner has accepted the recommended
72-hour past-lead limit, resolving the remaining condition. These are review
perspectives, not measured Nepal skill results
or a licensed human's certification. Full reports remain outside the repository.
Implementation started on `feat/retained-operational-weather`; deployment remains
a separate post-merge step.

## Outcome and boundaries

Keep the last usable operational weather product until a usable replacement
arrives. Use its actual forecast samples as days pass to produce a **new runoff
forecast**, with honest older-weather provenance. When newer usable weather
arrives, update the runoff forecast. Do not wait for gateway archive support.

The gateway's current operational call accepts a history start date, not a
forecast-cycle selector. Its documented product combines ERA5 history, recent
IFS fill and today's 00Z forecast tail. New 00Z delivery around 10–12 UTC is
expected. The owner has asked the developer about historical product requests;
archive existence, exact-replay semantics and retention remain unconfirmed.

This is a last-product cache, not a historical product archive. Preserve existing
forecast evidence when replacing the cache. No new provider, hydrological model,
interpolation algorithm, time-grid activation, FI change, 12Z qualification,
alert redesign, email warning or general resilience framework is included.

Retaining data does not extend its physical coverage or relax the existing
60-hour future-weather age limit. Full required model support remains mandatory.
Long unsupported gaps remain unsupported; existing short-gap policy is unchanged.

**Owner-approved maximum original forecast lead for IFS samples reused as past
weather: 72 hours (three days).** This is the MVP configuration default and Nepal
setting. It covers the reconstructed 16–17 November missing support from the 15 November
run; it is a pragmatic operating limit, not a validated skill threshold.
Bound precipitation by interval end minus source run,
and temperature by the end of its existing coverage step minus source run; every
sample supporting a daily value must pass. Do not turn policy-excluded samples
into interpolatable gaps or revive them through the stored-donor path. Retention
of the full product is independent of eligibility for use.

Use one typed/config-validated retained-past IFS lead limit on this selected
route, not per-station heuristics. Keep its use/provenance distinct from the
existing future cycle-age limit.

## Evidence and existing mechanisms

- On 18 November, 06:00/10:00/14:00 Nepal attempts selected 17 November 00Z future
  weather for all 17 stations, but failed on two missing past precipitation and
  temperature values each. The 17:00 retry received complete past weather and
  delivered all 17 stations at 17:22. The exact upstream failure is unproven.
- The prior successful forecast used a retained 15 November operational
  snapshot and stored IFS substitution for 16 November. This is evidence of
  stale-cache rollover, not proof that a fresh gateway payload had two holes.
- `RecapGatewayOperationalAdapter._reduce_pair` retains requested past support
  plus at most one day (`rollover_buffer_days` is capped at one), discarding the
  remainder of the downloaded forecast tail.
- `complete_weather_entry` already preserves the old snapshot when no new
  snapshot exists, but replaces it whenever a new snapshot is supplied, even if
  that response contains gaps. Cache transactions and generation guards exist.
- Past snapshots already contain native sample provenance, hashes and dates.
  `project_past_weather` selects the requested window from them. Genuine retained
  samples do not need stored-IFS donor receipts or interpolation.
- Future weather has its own cycle-addressed fetch, stored rows and receipts;
  its working selection and 60-hour policy remain unchanged. Past and future
  source cycles may differ, as they already do; expose each accurately.
- `_already_delivered_result` currently returns before weather refresh after
  finding any delivery on the automatic provisional route. It would prevent an
  afternoon weather improvement after a successful morning fallback.

## Proposed minimal contract

### 1. Keep the useful operational tail

Extend the existing paired past snapshot to retain all consecutive, complete
daily periods supplied in the verified operational tail, up to the provider's
already qualified horizon (currently at most 15 days from a 00Z source run).
Do not require all 15 days to be present: retain the actual complete support,
without inventing endpoint values or discarding a shorter useful tail.

Preserve native P/T samples, variable semantics, real source runs and interval
bounds. Reuse existing daily reduction without changing temperature weighting,
precipitation totals, UTC day boundaries or model requirements. No historical
forcing rows or fictitious receipt records are created. Retained forecast
samples becoming past weather remain IFS-derived, never relabelled ERA5.

Use the existing version-1 window/snapshot representation if its existing
invariants permit the wider end time; do not change old serialized payloads or
hashes. A single active snapshot per station/binding remains bounded by the
existing byte limits. Verify realistic 30-day history plus the native tail fits
before implementation; a size failure preserves the prior usable snapshot.
A synthetic check with 30 days of hourly P/T plus 15 days of qualified IFS
support encoded 1,608 native samples / 45 daily periods in 502,767 bytes, below
the existing 2 MiB per-station limit. This checks feasibility, not every payload.
No new table, object store or compression format is planned. Keep the dormant
v2 path unchanged: its window deliberately cannot extend beyond its cutoff.

### 2. Replace only with usable paired support

Refresh P and T as a pair. A failed, empty or unusable refresh must not erase the
last usable product. Define a pure usability predicate over each actual snapshot
and explicit current requirements, not the latest attempt's SUCCESS flag. It
checks native coverage, binding, allowed source leads, current lookback, next
rollover day and full requested future horizon. A newer eligible product may
replace an older one even if its unused tail is shorter; comparing last covered
dates alone must not reject a scientifically better usable update.

Selection order, applied to the complete paired snapshot:

1. Fresh native-complete eligible product, with a non-older qualified tail cycle.
2. Retained native-complete eligible product.
3. Fresh product usable under the **existing** one-day gap policy, with a
   non-older qualified tail cycle; otherwise retained product usable under that
   same policy. Native samples and separate gap-fill provenance remain distinct.
4. No usable candidate: retain the old cache unchanged and refuse that forecast.

The future horizon must be native-complete in every case; the short-gap policy
applies only to its already-approved past-weather inputs. This truth table also
covers no-old/new-partial and old-partial/new-partial cases: only a usable
candidate can replace, and a native-complete retained candidate takes precedence
over a newly gappy response. No new durable quality flag is needed. Partial
source data used with approved filling must never be called native-complete or
stored with invented samples. No cell-by-cell product stitching is introduced.
If retained native past support is complete, stored-forecast donors do not run;
this intentionally preserves the selected product's provenance.

Retain the selected product across process/container restarts and indefinitely
until replacement; validity for a forecast is still checked against actual
support, source identity, no-lookahead and unchanged forward-weather limits.
Recoverable missing/transport failures can reuse it. Auth, binding, corrupt-data
and unknown-attempt failures keep their existing refusal semantics; do not hide
configuration faults as usable weather. Classify only an already parsed,
verified per-station snapshot that exceeds the cache-size budget as recoverable
capacity/missing support; preserve the old snapshot. Transfer, decode and hash
integrity errors remain fatal. Isolate the per-station size decision so one large
snapshot cannot fail every station's refresh. Do not raise size limits blindly.

When a refresh succeeds but is unusable for replacement, record that explicitly
as missing support and retain the earlier snapshot's retrieval time and source
identity. Do not let a new HTTP success or attempt ID make old data CURRENT.
For affected forecasts use the existing degraded forcing flag/warnings and
weather evidence, with wording such as “Using retained operational weather;
latest usable product retrieved …”. Keep observation and weather warnings
separate. No new public endpoint is needed.

Retain per-sample source runs and support in the existing weather evidence;
report the range/oldest relevant run and maximum original lead consumed, not
just the newest run (which could hide an older contributor). Prefer factual
wording because older-weather use may be the normal morning delivery path.

### 3. Use retained native support when the future request also fails

Past-only retention is insufficient: yesterday's future receipt usually covers
yesterday's model horizon, not today's new final day. After live future-cycle
selection exhausts candidates recoverably, qualify a local candidate from the
selected retained product's native IFS samples for **today's** full requirements.
Require one actual source run, HRES member 0, matching station/HRU/polygon and
qualified product/grid, canonical units without a second conversion, the existing
60-hour limit and the unchanged one-cycle group contract. Never use ERA5 or
mixed historical cycle slabs as future forcing.

Reuse existing candidate completeness and `commit_track` archive write/readback
plus `prove_future_receipt`. This is a real SAP3 proof of stored source rows,
not a successful gateway response. Conflicting archived values refuse proof.
Explicitly establish operational HRES / `ifs_ecmwf` product equivalence from
the qualified contract and frozen mapping; equal variable names are insufficient.

Keep the current HTTP leg UNAVAILABLE and selected future use STALE. Add an
explicit in-memory retained-support receipt channel to refresh completion,
validated against the originating hash-verified snapshot and current request,
then merge proofs into the existing persisted receipt collection. Bind this
channel into completion digests, retaining legacy serialization when empty.
Keep generation guards, receipt capacity/age checks and immutable as-used
evidence. For this Nepal CMAL route, past weather is always required: require
the retained future candidate to originate from the exact selected past snapshot.
Its ID/hash/retrieval time is already in `PastWeatherUse`; validate the cross-leg
origin before completion and capture that existing evidence together with the
future native rows. Do not claim new provider delivery from a new local proof.
This validates origin at creation and preserves as-used samples plus origin
identity; a hash alone is not an archive of the full original response. Do not
infer this new cross-leg origin for older evidence or ordinary retained receipts
from the separate IFS endpoint.
Models without a selected past leg keep their existing path; broader standalone
retained-product provenance is out of scope.
Do not fake SUCCESS to fit today's completion validator. Implementation must
prove the cross-leg invariant is retained on capture/replay; if it cannot, stop
at that specific evidence gap rather than dropping provenance to avoid a schema
change.

### 4. Update forecasts when weather improves

On the opted-in automatic Nepal provisional flow, remove the early
`_already_delivered_result` return and run the four existing scheduled ticks.
This scope includes ordinary forecasts emitted by this flow when no rating
observations were consumed; it does not redesign the separate STANDARD ledger.

This is simpler than a new pre-prediction change detector. It costs roughly four
22-minute runs per day instead of one plus cheap skips, and later observations
may also affect updates. Do not change schedules or the separate STANDARD daily
ledger. Correct persistence once so identical retries do not republish output.

Add a narrowly scoped selected-weather scientific-input/provenance discriminator
to the existing retry decision table (Plan 327) and superseding rows. Today the
classifier ignores source cycle and weather content: equal runoff values keep
old evidence even after weather improves. Compare consumed native values,
source cycles/periods, allowed gap-fill provenance and meaningful weather-quality
state; exclude snapshot IDs, attempt IDs, retrieval clock times and regenerated
row IDs. Compare via already-validated evidence rather than a new ledger.
Unchanged scientific weather plus identical values remains an identical retry;
changed weather/effective quality can supersede even with equal runoff values.
Preserve existing QC-verdict and evidence-version refusal rules and all legacy
retry behaviour; do not broaden unrelated rating or observation policy.

Use existing forecast supersession/persistence and by-ID evidence behaviour:
keep an earlier successful forecast available if the update fails, publish the
successful replacement as current, and preserve prior IDs/evidence for review.
Keep the hydrological issue date and actual creation time distinct. Verify the
existing retry policy can express this before wiring the update path; never
overwrite an already-published forecast's evidence in place. No schedule change.

## Work sequence and verification

```json
{
  "milestones": [
    {"id": "W1", "depends_on": [], "status": "READY"},
    {"id": "W2", "depends_on": ["W1"], "status": "READY"}
  ]
}
```

**W1 — retain and reuse.** Adapter/config, existing weather window/cache,
projection, retained future proofs and evidence/quality handling. Test complete retained P/T for two
consecutive missing refresh days: required past slots come from real retained
samples and remain gapless without relaxing the one-day interpolation cap.
Test restart, paired-variable failure, partial new response, short tail, size
bound, unchanged binding, source-run cutoff and native precipitation boundary
coverage. Test no retained product and support exhaustion honestly refuse or use
only the previously allowed short-gap path. Preserve existing v1 evidence hashes
and dormant v2 behaviour; no Nepal time-basis change.

**W2 — same-day recovery.** Remove the narrow delivery skip, run each existing
tick and extend existing supersession as specified above. End-to-end fixture: morning old operational
product plus eligible older forward cycle -> newly calculated degraded runoff
forecast; later identical weather and runoff -> no duplicate publication;
afternoon newer usable product ->
new current forecast; failed afternoon prediction/store -> morning forecast
remains accessible. Cover all 17 stations including the ordinary-output cases,
partial earlier deliveries and duplicate retry. Verify unchanged Swiss/default
flow behaviour. Keep existing future-cycle age/coverage refusal tests.

Also test both gateway requests unavailable across midnight, with today's last
required future day beyond the prior receipt but inside retained native support;
prove/archive/readback for the extended scope must succeed with honest STALE
provenance. Test conflicting archive values and mixed source cycles refusing.
Test equal runoff values with newer weather superseding old degraded evidence,
and an identical subsequent retry producing no duplicate publication. Verify
past-lead policy cannot be bypassed through interpolation/donors.

Use meaningful deterministic tests/fakes and a focused PostgreSQL persistence
test, not live gateway dependence. Ruff and changed-module type gates, then the
full suite locally or in CI after the final code change. Update API guide only
where warning/replacement behaviour needs clarification, the relevant weather
spec, config reference and operations runbook. Both milestones must be delivered
before claiming the user's continuity-and-update requirement is complete.

Rollout is separately authorized staging work after PR approval/merge. It must
seed and verify a complete last-good product while the gateway is available;
the currently truncated cache cannot recover discarded tail data. Verify a
normal scheduled morning fallback and subsequent fresh-weather update using
stored evidence. No invented operational archive or live historical replay.

## Review scope completed

Hydrology: is retaining real IFS support across consecutive days sound under the
unchanged source-age/coverage limits? Are source labels and degradation honest?
Does the replacement rule reject useful updates or allow scientifically worse
ones? Identify only necessary MVP corrections, not a new uncertainty framework.

Forecast systems: can the existing cache/evidence types express this without a
migration? Check partial refreshes, idempotency, existing supersession policy,
ordinary outputs within the provisional flow and byte limits. Prefer a smaller
complete solution to a generalized product store. Name any unresolved decision
that genuinely blocks implementation; full review reports stay outside Git.
