---
status: READY
owner: orchestrator
created: 2026-11-22
---

# CMAL evidence capacity for all 21 Nepal stations

Independent Claude review accepted the scoped budget amendment. The actual
capture plus `validate_selected_capture` check passed for all 21 stations at
2,368,376 KiB peak RSS (2.26 GiB), below the owner-approved 3 GiB budget.
Orchestrator set READY after this final gate; full reports remain local.

The imported static encoder is not yet assigned. Its live 365-day/four-channel
history and 11-day forecast produce 244,936,417 decoded evidence bytes, above the
128 MiB ceiling. The complete 380-day retained-history/15-day envelope is
255,522,016 bytes. The owner approved a 3 GiB worker capture/validation budget
on 22 November after the former six-station 2 GiB gate proved too small.

## Change

Raise the shared absolute decoded selected-weather evidence ceiling from 128 to
384 MiB (402653184 bytes). Preserve the default capture limit at 32 MiB, existing
schemas, serialization, hashes, compression, bounded decompression, EOF/trailing
checks and legacy isolation. No reader optimization, dependency or FI change.
Keep the separate station-cache ceiling and default unchanged.

The configuration setting is deployment-wide, not per-model. Combined-model
contributor-reference capture also uses the absolute ceiling; the worker memory
gate concerns station/group capture followed by validation, not model combination.
Audit store, API, retry and replay readers for the
shared bound. Opt-in capture configuration is a subsequent deployment action;
this PR does not change host configuration, schedules or assignments.

## Qualification gates

- Both complete envelopes <=288 MiB decoded (25% byte headroom).
- Worker capture plus actual `validate_selected_capture`, with all typed inputs
  retained, <3 GiB peak process RSS. Fresh API, retry and per-row store validation
  remain <2 GiB each; two concurrent readers remain <4 GiB aggregate.
- Worker baseline plus measured combined peak <6 GiB of the existing 8 GiB worker
  limit. Reserve >=4 GiB host MemAvailable after reserving paired-reader memory.
  Refresh baseline/reserve immediately before activation. Linux image results
  are authoritative; cgroup and process RSS are recorded separately, not conflated.
- Exercise 21 sequential store validations and record elapsed time and memory.
  Database/WAL growth is measured during the first authorized issuance; estimate
  storage beforehand, with no retention change or evidence deletion.

Diagnostics run on the exact deployed Linux image, without credentials/network,
with read-only inputs and a 4 GiB memory cap. Diagnostic overrides affect only
in-process copied config, imported constants and captured reader defaults; none
are used for actual forecasts or persisted to the application database. Archive
receipt UUIDs are constructed for diagnostics, not claimed as production records.
The extended envelope uses actual archived history plus explicitly synthetic
future extension beyond the Gateway's live 11 days. Only 365 declared model-input
days are passed to FI; 380 days are retained solely for the evidence envelope.

Measured original-reader results: 380/15 capture 26.97 s; subsequent exact typed
restore 25.06 s; peak 2,363,208 KiB (2.25 GiB). Fresh API 1,368,276 KiB, retry
1,612,476 KiB; concurrent sum 2,980,752 KiB. All 21 store validations passed in
483.79 s with 1,338,444 KiB peak. Worker baseline ~324 MiB; host MemAvailable
13,013 MiB during the store test. Actual capture-validation entrypoint confirmation
is required before implementation. Raw input-table and assembly-list scratch is
released at the adapter-return boundary; all typed inputs/evidence remain alive.

The larger compressed snapshot is about 12.08 MB: 21 duplicated snapshots cost
about 242 MiB per issuance before row/index/WAL/backup overhead. This is a storage
estimate, not a measured database delta. Higher concurrency is not qualified.

## Implementation and verification

1. Independently review the owner-approved budget amendment; orchestrator sets
   READY after review and final capture-validation qualification passes.
2. Change the shared ceiling. Test explicit capture/read above 128 MiB, exact
   boundaries and one-byte-over, default refusal, default-config reads of opted-in
   evidence, malformed/truncated/trailing streams and legacy isolation. Exercise
   typed 21-station capture/restore. Keep six-station compatibility coverage.
3. Update security, type specification, onboarding runbook and superseded plan
   references. Advance the release target once using the repository helper.
4. Independent patch review, targeted tests, lint/format/types, and full CI after
   final code changes. Hold PR for owner merge; keep full review reports local.
5. After compatible release approval, set Nepal capture to at most 288 MiB
   (`max_evidence_bytes = 301989888`), below the 384 MiB absolute reader ceiling.
   Larger actual payloads need separate qualification. Complete radiation
   capabilities/config and recurring refresh setup,
   assign the existing artifact, run the normal forecast flow and verify the API.
   Check the selected route's AT_MOST horizon behavior; no fabricated days or FI
   bypass. Hindcasts and skills follow operational onboarding.

## Rollback

After storing evidence above 128 MiB, 0.1.1174 is not a compatible rollback reader.
Retain the new reader-capable image; rollback means reverting capture configuration
+ assignment while retaining that reader. An older binary would fail API/retry
reads; never delete evidence to make it appear compatible. Test large evidence
remains readable with the default 32 MiB capture configuration.

Local diagnostic evidence and review reports:
`/tmp/nepal-cmal-static-20261122/`; host archived diagnostics:
`/srv/sapphire-cmal-static-20261122/capacity-diagnostics/`.
Full reports stay out of commits. The scoped worker budget supersedes the
six-station 2 GiB worker gate; fresh/paired reader gates remain unchanged.

```json
{"C1": [], "C2": ["C1"], "C3": ["C2"], "C4": ["C3"], "C5": ["C4"]}
```
