# DHM observation ingest: bounded polling and an admin "ingest paused" switch

status: READY

Issue: #501. Related: #455 (retire and reactivate stations), #500 (kept with the Nepal owner).

## Problem

On the Nepal host each `ingest-observations` run takes about 30 minutes but is
scheduled every 5. Of 180 requests per run, 165 go to three stations whose data
stopped weeks ago (447, 620, 647 as of 2026-11-12). Each run re-requests the whole
gap, one 24 h window at a time, because the fetch cursor is the last stored
reading (`fetch_latest_capture_timestamp`, `MAX(observations.timestamp)`) and an
empty window never advances it. The backlog never clears and the ingest worker is
permanently busy.

Worse than slow: `_preflight` marks a station RESOURCE_LIMIT when its windows exceed
the request budget, and that failure turns every DHM station's outcome into a
failure. An uncapped gap can break the whole batch.

Measured 2026-11-26 against the public BIPAD API: reachable (HTTP 200, ~2 s);
167 stations; 156 updated within 2 h; none older than 7 days. Station 447
(Trisuli at Betrawati) is not in BIPAD at all (destroyed by a flood). So the
feed is healthy and the waste is on our side. Bindings for 620 and 647 live in
the Nepal host's settings, not in this repository: not yet checked (owner is
requesting them from the host administrator).

## Decisions (owner, 2026-11-26)

- A destroyed or silent gauge keeps its forecast: people still depend on it.
  Pausing polling must therefore not stop forecasting.
- Option A: a separate per-station "ingest paused" switch, independent of
  `station_status`. An administrator sets it from the admin dashboard.
- Code-only generic fixes come first and need no flag.

The hydrologist decides whether a forecast for a station with no recent readings
is acceptable. Usual practice: such a forecast is flagged degraded, with the reason
shown. Whether that flag already exists for missing observations is a discovery
item; this plan adds no new forecast behaviour. The switch only stops polling.

MVP status: no production deployment. Keep the change minimal; defer hardening.

## Present in the code (verified on main, 2026-11-26)

- Ingest polls a station only if `station_status == operational` (and gauged for
  river/lake) via `is_ingest_station_judged`; forecasting uses the same status.
- No per-station pause, back-off, "silent since" or "next poll" state exists.
- Lookback is 1 h only for a station with no stored reading; otherwise the cursor
  is the last reading, with no cap.

## Reviews (2026-11-26)

Independent Claude and Codex reviews: both NEEDS CHANGES (minor). Both confirmed the
diagnosis and that the cap alone removes the waste. Resolved here: cursor advancing
and silent-station back-off dropped (new persistent state; "daily, last 24 h" tier
creates permanent holes); pause check kept out of `is_ingest_station_judged`;
health must cover skipped-only runs; recovery after a long outage stated honestly.

Round 2 (Claude: READY with wording fixes; Codex: NEEDS CHANGES, one point): case 1
confirmed against the code by both. Folded in: overlap rule for the recorded interval
(polling drops a reading exactly at its start, historical ingest drops one exactly at
its end), how the interval reaches the health record, the operational-status and
identity preconditions for recovery, and wording consistency.

## Part 1 — bounded lookback (no flag, no schema change)

1. **Bounded lookback.** In the adapter's polling mode only (not the historical
   path), clamp each station's start to `end - lookback` before `_preflight`.
   New `DhmConfig` field, default 7 days (a dead station then costs 7 requests,
   against 165 now), configurable. The owner may prefer 48 h.
2. **Cost of the cap (accepted for MVP).** An outage longer than the lookback no
   longer self-heals: the poll fetches `[now - lookback, now]` and the interval before
   the clamp start is abandoned. Recovery is manual, see "Recovery after a long
   outage".
3. **Run health.** Report capped stations in the fetch health record with a new
   detail key (not a failure, no WARNING), including the abandoned interval, and
   write a health record on skipped-only runs (today the no-eligible early return
   writes none). Plumbing: derive the interval in the flow, which already holds the
   cursor (`since`) and the clamp start, rather than adding a field to the adapter's
   outcome type. The recorded interval is `[old_cursor, clamp_start]` widened to end
   at the first newly stored reading, so no reading on the boundary is lost. For a
   station that stays silent the record is rewritten each run with a moving end; the
   record from the run that first stores new readings is the one to use.
4. **Schedule.** Concurrency limit 1 with cancel-new collisions on the deployment,
   so runs cannot pile up. Separate reviewed step; hand-off to the Nepal host owner.

Implementation notes (round 3, both reviewers READY):
- Clamp `start` before the `divmod` in `_preflight` (`dhm.py:~301`), only when
  `bounds_mode is DhmBoundsMode.POLLING`; historical mode shares `_fetch_batch` and
  must not be clamped.
- The flow passes `now` to the adapter as its clock, so the flow computes
  `clamp_start = now - lookback` identically; a station is capped when
  `since[s] < clamp_start`. This applies only in the `dhm_config` path; when an
  injected adapter is used (no `DhmConfig`), skip interval derivation.
- The first new reading is the minimum timestamp per station in
  `batch_result.observations`. If nothing returns, record `end: null` (still open).
- `_append_fetch_health_record` is called twice (`~1735`, and `~1773` when
  `captured_outcomes` is set); pass the capped intervals into both.

Deferred (not needed after the cap): advancing the cursor over empty windows and
silent-station back-off.

Acceptance: a dead station plans at most lookback/24 h requests per run; a replay
of the 2026-11-12 situation finishes inside the schedule interval; capped stations
and abandoned intervals (exact start and end) appear in health records; the runbook
paragraph for the three recovery cases exists.

## Recovery after a long outage

Verified: `run_historical_ingest` (`historical_ingest.py:~249`) refuses a station
unless its stored cursor is at or beyond the requested end. It is a tool for filling
holes BEHIND the latest stored reading, at most 31 days per call, one tenant, writable
tenants only. BIPAD still serves old history at the source (probe 2026-11-26,
station 25: readings present for 2022-11 and 2025-11; one probed 2024-11 window empty).

Three cases:

1. **Same gauge comes back** (flood damage repaired, power restored). No onboarding.
   The first routine poll stores new readings and moves the cursor forward; the
   abandoned interval is now behind the cursor, so the existing historical ingest
   can fill it. Recovery = one operator command per 31-day slice, with dates taken
   from the health record (Part 1 item 3); fallback without the record: the last
   stored reading before the gap and the first after it. Preconditions the runbook
   must state: the station is `operational` and gauged again (historical ingest
   rejects it otherwise, so a station suspended during the outage must be reactivated
   first), and the operator uses a writable-tenant, non-global-admin identity.
   The request must also not overlap enabled legacy-custody ratings and must not end
   in the future.
2. **Station still silent, but DHM holds data we never fetched** (our ingest was
   down, or DHM restated). The capped poll fetches the latest 7 days; if any
   arrive, the cursor moves and case 1 applies. If none arrive there is nothing newer
   and the cursor stays old: backfill refuses. Accepted for MVP: nothing to recover
   until the gauge reports again.
3. **Gauge rebuilt or replaced** (new location, datum or DHM station number).
   This is a new station, not a reactivation: use the station-onboarding tools
   (Plan 540, `docs/operations/new-station-by-hand.md`). The old station is
   retired under #455 and its history is kept; a retired station is already outside
   ingest eligibility. Use the pause switch only while it is still operational.

Decision D1 (owner, 2026-11-26): manual recovery. An operator runs the existing
historical ingest per 31-day slice, using the dates in the health record. Automatic
queuing by the flow (trigger, retries, slicing loop, write identity inside a routine
run) is rejected for MVP. Deliverables: the health record carries the exact start/end
of each abandoned interval; a runbook paragraph for cases 1-3.

## Part 2 — "ingest paused" switch and admin dashboard

1. Add a per-station boolean (default false): do not poll this station's
   observations. It does not affect forecasting, QC history, artifacts or station
   status. Needed even after the cap: station 447 is not in BIPAD, so it would
   still fail every run and hold a permanent warning.
2. Placement: filter paused stations into a separate polling subset in
   `ingest_observations.py` (the `eligible` list). Do NOT change
   `is_ingest_station_judged`: it also governs QC applicability and historical
   backfill. Native recovery bindings and pending QC work for paused stations must
   still run: `native_bindings` and `dispatch_bafu_captures` keep the full
   `eligible` list; `stations_polled` and the start log use the polling subset. Run
   health reports paused stations, including when the polling subset is empty but
   `eligible` is not.
3. Audit: who set it, when, and why (free-text reason); discovery confirms whether
   the existing audit log can carry this or a new record is needed. Unpausing needs
   no extra approval; the next run resumes from the capped lookback.
4. Admin dashboard control: backend endpoint plus UI belong to the admin dashboard
   track and to #455. This plan delivers the field, the ingest behaviour and the
   audit record; the endpoint/UI is a hand-off to #455, whose scope the owner should
   update to include this switch. No frontend edit here.

Discovery before coding: DB migration pattern (`alembic/versions/0087_*`), files
`types/station.py`, `db/metadata.py`, `store/station_store.py`, whether the admin
dashboard has a station-edit endpoint to extend, whether QC rate-of-change rules
behave across a long gap on resume.

## Out of scope

Nepal host changes (suspending 447, cancelling the Late backlog, applying the
schedule), editing Aquacast, alerting changes, any change to forecast behaviour for
stations without recent readings.

## Risk and review

High risk: database migration, scheduling and user-visible behaviour (AGENTS.md).
Independent Claude and Codex plan reviews done (above); re-review after these
changes is the owner's call. Independent patch reviews, focused tests, lint, format
and changed-module type checks, and a full suite pass are required before owner
merge. Deployment is the Nepal owner's decision.
