---
status: READY
---
# Failed later retry: network overview (#581, T2)

HIGH RISK under `AGENTS.md` (user-visible behaviour, external API contract, shared authority
model). MVP, no production: smallest change; no frontend edit. Branch from current `main`
(contains T1, PR #688: `DailyDaySnapshot.earlier`, `retained_source`, `select_retained_member`,
`RetainedResult`, `RetainedResultOut` in `api/swiss_overview_schemas.py`, map contract 3.11).
Parent plan: `docs/plans/2026-11-26-failed-retry-current-forecast.md` (T1).

## Problem

T1 fixed the Swiss overview. The network overview (`api/network_overview.py`) still shows
nothing for a station whose newest daily attempt failed it. `_daily_references` (~:432-494)
reads `read_days_bounded` (7 days x groups, no retry history), takes each group's
AUTHORITATIVE attempt and keeps only the references of members that carry them; a member
that is not `PRODUCED` has none, and `seen_members.add(sid)` (~:485) then also stops older
days from supplying one. After a failed 06Z retry the network overview loses every station
the retry failed, although the morning forecasts are stored.

## Decisions

- Owner (2026-11-26): option A (show the earlier valid result with an explicit backend label).
- Scope: T2 = the network overview ONLY. This NARROWS the parent plan's T2 list: the
  evaluation current selection (`select_current_daily`, `services/evaluation_forecasts.py:
  ~242`, its single caller) becomes T3, a separate PR. It is deferred only for scope and
  review size, NOT because it is unwanted: a retained same-model daily result arguably should
  rank above the existing fallback to other assigned models (`EvaluationSelectionKind.FALLBACK`),
  and until T3 the evaluation detail can show a fallback model (or nothing) while the
  overviews show the earlier CMAL forecast. Record this as a known limitation in the PR body
  and `docs/spec/api-v1-review.md`; the owner confirms T3 separately.

## Change

1. Store: extract the pure-Python attempt ordering from `read_day` (~:879-912) into one helper
   used by `read_day` and the new method (scheduled attempts by `claim.ordinal`; operator
   chains by parent chain from the unique leaf). New bounded
   `PgDailyForecastStore.read_earlier_attempts(keys, authoritative_ids)` returning, per key,
   the OLDER attempts with outcomes (the suffix after the authoritative attempt, newest
   first). It loads the full attempt rows of the requested (failed) keys only, groups them by
   (tenant, deployment, group, model, issued_at), uses the same `_OPERATIONAL_SCOPE`
   filter as `read_day`, and takes the authoritative attempt ids from the bounded read (or
   asserts the helper recomputes the same authority; confirm they agree on mixed scheduled
   and operator chains). Admission limits equivalent to `read_days_bounded` (~:937+): a key
   cap (at most the same 112), the 2 s statement timeout, an attempt-id cap, and a payload and
   member cap, raising `DailyAuthorityIntegrityError` when exceeded (operator chains are
   otherwise unbounded). Callers fill `DailyDaySnapshot.earlier` on the bounded snapshot
   (`dataclasses.replace`), so `daily_day_available` (which already validates `earlier` for
   scope and not-in-the-future, T1) covers it.
2. Trigger (so the happy path adds NO query): in `_daily_references`, a first pass over the
   bounded days that mirrors the existing `seen_members` walk (keys in order, newest day
   first). A key needs the earlier read only when `day.authoritative is not None` and, for a
   station that is in scope and NOT yet seen, its member in that authoritative attempt is
   PRESENT and not `PRODUCED`. Do NOT trigger on an absent member (with several groups a
   station is simply absent from the other groups' summaries; rosters are frozen per attempt),
   on a failed member on an older day for a station already supplied by a newer day, or when
   `authoritative` is `None` (an operator head with no summary stays unavailable). If no key
   triggers, `read_earlier_attempts` is not called.
3. Substitution: for a triggered station use T1's `retained_source`; when an earlier attempt
   PRODUCED it, put that EARLIER attempt's summary and references into `result` (the existing
   `(summary, ref)` shape, so `_headers` and `daily_candidate_from_record(summary=<earlier
   summary>, ...)` run unchanged with the earlier attempt's own claim, and the publication
   gate, superseded status, `daily_ids`, `qc_status` and the 168-hour `issued_at` filters
   apply unchanged) and remember the `RetainedResult` per `_OutputKey`. The walk stops at the
   first earlier `PRODUCED` member: a rejected, superseded or out-of-window result stays
   absent (`withheld_or_ineligible` as today), never an older attempt. `seen_members`: a
   present failed member is marked seen even when no retained result exists (today's
   behaviour); a station supplied from that day, current or retained, is marked seen; an
   absent member without retention stays unseen. The network overview has NO `valid_at`
   coverage check, only the 168-hour window and the freshness labelling: a retained result
   gets the same freshness labelling as any forecast; add no coverage rule.
   Fail-closed is accepted: the first header query raises `DailyAuthorityIntegrityError`
   when a referenced `daily_id` row is missing (~:535), so a missing earlier forecast row
   fails the request like a missing authoritative one.
4. API: `OverviewForecastResponse` (`api/network_overview_schemas.py`; `OverviewSchema` is
   `extra="forbid"`, fine for an additive producer field) gains an optional `retained_result`,
   importing `RetainedResultOut` from `api/swiss_overview_schemas.py` (do not duplicate; its
   `superseding_reason` is an enum string, no free text). The label travels as a dict keyed
   by `_OutputKey` into `_forecast` (or a `model_copy`) and is attached only AFTER
   `daily_candidate_from_record` succeeds, so it never appears on an absence. Additive: bump
   `MAP_CONTRACT_VERSION` 3.11 -> 3.12 (`api/map_contract.py:~30`), regenerate
   `docs/spec/api-v1-map.openapi.json`, update `docs/spec/api-v1-review.md` and examples, run
   `tools/check_map_contract_version.py`. The ETag is a body hash, so it invalidates itself.
5. No change to the authority rule, claiming, retry flows, QC, the Swiss overview, the
   evaluation selection, or the frontend.

## Tests (red first)

Real store on the PostGIS testcontainer, in the style of
`tests/integration/api/test_network_overview.py` and T1's
`tests/integration/store/test_daily_retained_forecast.py`:
- the incident: attempt 2 (all failed) over attempt 1 (produced): before, the station has no
  output; after, attempt 1's forecast with `retained_result` naming attempt 2.
- partial retry: stations the retry produced come from the retry (no label); failed ones come
  from attempt 1 with the label.
- trigger, three cases asserting `read_earlier_attempts` is NOT called (counting wrapper):
  the healthy multi-group case (a station absent from the other group's summary); a healthy
  day 0 with a failed member on an older day (`seen_members` blocks it); an operator head with
  no summary. And the real incident asserts it IS called exactly once for the failed keys.
- seen/precedence: a present failed member with no retained source is marked seen (older
  days do not supply it, as today); an absent member without retention stays unseen.
- no fall-through: a superseded record, and a QC-rejected record without the plausibility
  exception, in the earlier attempt stay absent, not an older attempt (assert the key is
  absent in the response).
- withholding: a gated station whose retained forecast has NO publication selection row stays
  withheld, no values.
- 168-hour filter: tested independently with a header-query fixture (daily references only
  reach back six days, so the retained path alone cannot exercise it); freshness labelling of
  a retained result equals that of a normal one.
- operator chain: an operator `INCOMPLETE` leaf over a produced attempt permits retention.
- admission limits of `read_earlier_attempts` (key cap, payload cap) raise as specified.
- the extracted ordering helper leaves `read_day` results unchanged (existing tests) and gives
  the same order to the new method.
Mutations, each must fail a test: skip the PRODUCED check on the earlier member (fixture: an
INTEGRITY_REJECTED intermediate attempt that carries references, followed by an older valid
produced attempt; failed members cannot carry references); fall through past a rejected
earlier member; drop the label; bypass the publication gate; use the authoritative summary
for the retained output (fixture: the two attempts have DIFFERENT frozen `artifact_id` /
output contract, or assert the `(summary, ref)` identity in `daily_refs`, because accepted
records are validated against the artifact, not the attempt id, so shared artifacts hide
the mutation); trigger on an absent member; issue the earlier read when nothing triggers.

## Performance

The network overview has a recorded latency budget (about 1 s at 200 stations, missed at
160/200 earlier). Report before/after timings at the representative scale with and without a
failed retry in the PR body; the happy path adds no query (proved by the trigger tests).

## Out of scope

Evaluation selection (T3), Swiss overview (T1, merged), retry scheduling, the frontend, #547.

## Review and merge

Independent Claude and Codex plan reviews before READY (round 1 done 2026-11-26; this
revision folds both); independent patch reviews; ruff, changed-module types, focused tests,
map-contract and docs checks; full CI. Owner merges; deployment is the host operators'
decision.
