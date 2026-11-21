---
status: READY
owner: orchestrator
created: 2026-11-20
---

# Bounded CMAL evidence capacity

The owner authorized raising the evidence limit on 20 November after the six-station
native-history measurement exceeded 32 MiB. This supersedes the capacity stop in
the net-radiation plan; timestamp qualification and real-input activation remain separate.

## Change

Keep the configured capture default at 32 MiB. Raise the absolute selected-weather
decoded evidence ceiling to 128 MiB and allow an explicit deployment setting
`operational_weather.max_evidence_bytes = 134217728`. The existing setting scopes
capture to a deployment; this is not a per-model setting. All readers must accept
the same absolute ceiling so stored evidence remains readable after a configuration
change. Combined capture uses that hard ceiling as today. Keep response/cache
defaults, evidence versions, hashes, native support, compression, and grouping unchanged.
No schema migration, FI change, dependency update or live configuration change.

In `config/operational_weather.py`, add `DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES`
at 32 MiB for `default=` and change `MAX_SELECTED_WEATHER_EVIDENCE_BYTES` to
128 MiB for `le=`. Existing MAX imports in `selected_weather_evidence.py` and
`forecast_evidence.py` stay unchanged; combined capture explicitly permits
128 MiB. Legacy non-selected `restore_snapshot` is outside this change.

128 MiB gives headroom over the measured 67 MiB six-station history lower bound.
Qualification requires a complete six-station capture (380-day retained history,
15-day future receipts and input frames) <=96 MiB, preserving 25% capacity headroom.
Measure peak process memory during capture/restore, not just compressed bytes.
This does not qualify a 21-station capture or increased concurrency. Retain sequential
preparation and require deployment memory headroom before activation.

Memory qualification: on the six-station fixture, capture, selected restore and
retry-key reconstruction must each stay below 2 GiB process peak RSS. Exercise
two concurrent reader processes below 4 GiB aggregate RSS. The refreshed worker
limit is 8 GiB; API has no container limit and host currently has ~13 GiB available.
Before activation reserve at least 4 GiB available host memory for those two reads,
and verify worker baseline plus measured evidence peak fits below 6 GiB (25% worker
headroom). This qualifies at most two simultaneous large reads, not unbounded
API load; production load qualification remains an activation task. Measure
compressed storage size and document group evidence duplication per forecast row
and WAL growth; no evidence retention/deletion policy is changed.

## Phases

1. Independent Claude plan review; orchestrator resolves findings and sets READY.
2. Separate default and hard-ceiling constants; update configuration validation,
   affected bounded-read tests and operational documentation. Verify all store,
   API, retry and replay readers use the shared hard ceiling. Keep strict positive
   integer validation and bounded decompression, EOF/trailing-data checks.
3. Test default refusal above 32 MiB, explicit acceptance above 32 MiB, refusal
   above 128 MiB, exact boundary behavior and malformed/truncated/trailing streams.
   Exercise actual selected group capture/restore and API/store admission, preserving
   hashes and native intervals. Record full-group bytes and process memory locally.
4. Independent Claude patch review, targeted tests/lint/types and full CI. One
   release-target patch bump, PR held for owner merge. Full reports remain local.

Update `tests/unit/config/test_operational_weather.py` and the oversized payload
in `tests/integration/api/test_selected_weather_evidence.py`. Preserve the existing
legacy-isolation test's rejection of an oversized payload masquerading as selected
weather (do not change non-selected semantics), the 21-station/30-day size assertion,
and cycle fixture's configured-limit fault. Test exact/default/max boundaries,
one-byte-over, a high-ratio compressed oversized stream, and restore of an opted-in
capture with default configuration. Update limits in `docs/spec/types-and-protocols.md`,
`docs/standards/security.md`, the onboarding runbook and radiation plan. Only the
group 24/32 MiB gate is superseded by 96/128 MiB decoded ASCII JSON bytes; the
12/16 MiB station-cache gate remains unchanged.

If the full capture exceeds 96 MiB or measured process memory is unsuitable for
the runtime budget, report the specific result before activation; do not silently
raise the new limit again. Rollback must retain a reader that accepts all evidence
already stored. Lowering capture configuration never makes retained evidence disposable.

```json
{"C1": [], "C2": ["C1"], "C3": ["C2"], "C4": ["C3"]}
```

Independent Claude re-review returned READY; orchestrator accepted the plan before implementation. Reports remain local.


## Qualification results

Actual six-station typed v1 capture with 380 hourly four-channel history days,
15 future days, archive identities/receipts, 78 statics and input frames:
74,755,566 decoded bytes, 1,604,257 compressed bytes. Capture/restore/retry peak
process RSS on macOS was 915,619,840 bytes; two simultaneous standalone readers
peaked at 591,757,312 and 589,070,336 bytes (sum 1,180,827,648). These pass the
96 MiB decoded / 2 GiB process / 4 GiB paired-reader qualification thresholds.
This synthetic evidence is not live runtime/model qualification. Linux CI and
runtime baseline/headroom checks remain required; no host setting was changed.
At the measured compression ratio, six duplicated per-station snapshots are
about 9.2 MiB per issuance before row/index/WAL/backup overhead. No retention
policy changes or data deletion accompany the increase.
