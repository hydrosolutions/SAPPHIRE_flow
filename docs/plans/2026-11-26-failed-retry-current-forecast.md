---
status: READY
---
# Current forecast when a later retry fails (#581)

HIGH RISK under `AGENTS.md` (user-visible behaviour, external API contract, shared
authority/selection model). MVP, no production: smallest change that fixes the incident;
no frontend edit. Branch from current `main`.

## Problem

For each (group, issue day) the store marks one attempt "authoritative". When every model
fails, the attempt still finishes with a summary (state `INCOMPLETE`, outcomes with
`MODEL_FAILED`/`INPUT_EXCLUDED` members, `types/daily_forecast_outcome.py:~134`), and the
authority rule picks the newest attempt WITH a summary: scheduled authority is the highest
ordinal with a summary; operator authority is only the operator-chain leaf with a summary
(otherwise `None`, which stays "unavailable") (`store/daily_forecast_store.py:~908-915`,
`:~990-1010`). On 2026-11-20 the Swiss midnight CMAL attempt produced 132 stations; its 06Z
retry finished with 166 model failures and 7 input exclusions, so the retry became
authoritative. `select_current_member` (`services/evaluation_forecasts.py:~356`) reads only
the authoritative attempt and only members in state `PRODUCED`, so every station had no
candidate and the Swiss overview returned `no_current_candidate` for all 134 stations while
the morning's good forecasts stayed stored and unused.

The Swiss overview reads each group with `PgDailyForecastStore.read_day` (one call per group,
`services/swiss_overview.py:~196-205`), which already loads and orders every attempt of the
day and then discards all but `latest` and `authoritative`. Only `api/network_overview.py`
(~:474) uses the bounded `read_days_bounded` (7 days x groups), which loads no history.

## Decision (owner, 2026-11-26): option A

When a later attempt fails for a station, an earlier valid result of the same issue day MAY
remain visible, with an explicit backend label. It must never be relabelled as the newest
attempt, bypass QC or withholding, or rewrite history. The frontend invents no fallback.

## Scope

T1 (this plan's first PR): store exposure of earlier attempts via `read_day`, shared retained
selection, Swiss overview (the incident), map contract. T2 (separate small PR, same shared
selection): `api/network_overview.py` (needs a bounded earlier-attempts query because it uses
`read_days_bounded`) and the evaluation current selection (`services/evaluation_forecasts.py:
~240`). Until T2 the network overview keeps the incident gap: state that in the PR body; a
retained result must never appear in a consumer without the label.

## T1 change

1. Store: extract the attempt ordering that is inline in `read_day` (~:870-905; scheduled
   attempts by `claim.ordinal`, otherwise the operator parent chain from the unique leaf)
   into one helper used by `read_day` and nothing new. Add an optional field to
   `DailyDaySnapshot`: `earlier: tuple[DailyAttemptSnapshot, ...] = ()`, the OLDER attempts: in
   that newest-first order (`daily_forecast_store.py:~874`) they are the entries that FOLLOW
   the authoritative attempt (the suffix after it), keeping only those with outcomes, newest
   first. (Wording matters: taking the prefix would leave `earlier` empty in the incident.)
   `read_day` fills it from the attempts it already loaded (no new query, no new Protocol
   method: `DailyEvaluationReader` stays `read_day`-only, so existing fakes keep working with
   the default `()`). `read_days_bounded` leaves it `()` (documented; T2 handles it). An
   in-flight operator head (authority `None`) stays unavailable: no fallback.
   Scope: earlier attempts pass the same scope validation as `daily_day_available`
   (tenant, deployment, group, model, data use, `context.issue_time`) AND the same
   not-in-the-future check (`started_at`, `finished_at`, `recovery.confirmed_at` not after
   `as_of`), because `daily_day_available` validates only `latest` and `authoritative`: do it
   inside `daily_day_available` or a sibling called from the same two sites in
   `read_current_headlines`. Same-day is enforced by the key's
   `issue_time`; do not compare the whole `DailyClaimKey` (contexts can differ within one
   issue day).
2. Selection (`services/evaluation_forecasts.py`): a SEPARATE retained-aware selector, so
   evaluation and network consumers are unchanged until T2. For a station whose member in the
   authoritative attempt is not `PRODUCED` (including absent), look at `day.earlier` newest
   first and STOP at the first earlier attempt whose member for that station is `PRODUCED`:
   if its candidate (built by `daily_candidate_from_record`) is present and covers
   `valid_at`, return it with a `RetainedResult`; if the candidate is `None` (QC-rejected,
   superseded, ownership or integrity failure) or does not cover `valid_at`, the headline is
   unavailable (or expired); NEVER fall through to an older attempt, which would mask a
   rejection. Retention preserves the EXISTING candidate eligibility unchanged, whatever
   `daily_candidate_from_record` decides for the earlier record (including its
   `PLAUSIBILITY_FLAGGED` handling of rejected records for configured models,
   `evaluation_forecasts.py:~775`); the plan adds no new eligibility rule. A newest attempt
   whose member is `INTEGRITY_REJECTED` or any other non-`PRODUCED` state also triggers the
   walk (option A: the earlier result is shown with the superseding state in the label);
   a test pins this. Across groups the walk runs inside each group's selection, so a station
   absent from group A's authoritative attempt looks through A's `earlier` before group B is
   considered; real station overlap across groups is rare and this is accepted. A station whose
   authoritative member is `PRODUCED` but expired keeps
   `EXPIRED_HORIZON` (the walk never runs). The retained walk lives inside the existing
   per-group selection, before `select_current_daily` returns the first group whose member
   exists. `RetainedResult` carries: earlier attempt id and issue time, superseding attempt id
   and state, superseding member state and reason, both nullable (a member absent from the
   authoritative attempt has no state or reason).
3. Candidate cache: `read_current_headlines` (`services/swiss_overview.py:~219-237`) builds
   candidates only from `day.authoritative` references. Add the earlier attempts' references,
   for the stations that need the fallback only, to the batched `fetch_forecasts_by_ids` and
   `fetch_rejected_forecasts_by_ids`. No extra fetch when no station needs a fallback.
4. What each layer enforces: `daily_candidate_from_record` decides record eligibility
   (QC status with its configured-model plausibility exception, superseded status, ownership
   (artifact, issued_at, attempt), integrity and units); retention reuses it unchanged.
   Publication withholding is applied in the route (`api/routes/api_swiss_overview.py`
   `_cmal`, `withheld=gated and role is REVIEWER`). There is no separate revocation check:
   "revoked" means SUPERSEDED status or a QC reject the builder does not admit, both yielding
   `candidate is None`. The
   retained label is added to BOTH the available variant and the withheld identity variant
   (`CmalWithheld`: its `output_id`/`issued_at` are those of the retained earlier output);
   withholding behaviour is unchanged.
5. API: `CurrentHeadline` gains `retained: RetainedResult | None`; `_cmal` and the response
   models expose an optional `retained_result` object, for example `{kind:
   "earlier_attempt_after_failed_retry", original_issued_at, original_attempt_id,
   superseding_attempt_id, superseding_state, superseding_reason}` (the last two nullable).
   Additive: bump `MAP_CONTRACT_VERSION` 3.10 -> 3.11 (`api/map_contract.py:~30`), regenerate
   `docs/spec/api-v1-map.openapi.json`, update `docs/spec/api-v1-review.md` and the examples,
   run `tools/check_map_contract_version.py` (it only checks the version increased), and check
   consumer-side response models are not `extra=forbid`.
6. No change to attempt claiming, the authority rule, retry/recovery flows, QC, or the
   frontend.

## Tests (red first)

Real store on the PostGIS testcontainer plus unit tests on the selector and `_cmal`:
- the incident: scheduled attempt 2 (all failed, `INCOMPLETE` with summary) over attempt 1
  (produced): before, `no_current_candidate`; after, attempt 1's forecast with the retained
  label naming attempt 2 (capture the API payload before/after for one representative
  station in the PR body).
- partial retry: stations the retry produced use the retry (no label); stations it failed use
  the earlier attempt with the label.
- operator chain: an operator `INCOMPLETE` leaf over a produced attempt permits retention; an
  operator head WITHOUT a summary stays unavailable (authority `None`).
- expiry: earlier result no longer covers `valid_at` -> unavailable, not retained;
  authoritative-`PRODUCED`-but-expired stays `expired_horizon`.
- no fall-through: an earlier `PRODUCED` member whose record the builder does not admit
  (use a SUPERSEDED record, and a QC-rejected record WITHOUT the plausibility exception)
  gives unavailable, not an even older attempt.
- a newest attempt whose member is `INTEGRITY_REJECTED` over an earlier produced attempt shows
  the earlier result with `superseding_state` integrity_rejected (pins option A).
- a unit test with `earlier` built directly (no Postgres) exercising the selector.
- withholding: a retained result for a withheld station returns the withheld variant with the
  retained label, no values (test and mutation aim at `_cmal`).
- differing issue dates: yesterday's attempt is never used.
- no failed retry: `retained` empty, behaviour unchanged, no extra store call, and
  `fetch_forecasts_by_ids` / `fetch_rejected_forecasts_by_ids` receive exactly the same ids as
  before this change (assert via a counting fake).
Mutations, each must fail a test: ignore the `PRODUCED` check on the earlier member (fixture:
an earlier `INTEGRITY_REJECTED` member that carries references, since failed/excluded members
cannot carry references); skip `valid_at` coverage; use a previous day's attempt; drop the
label; bypass withholding in `_cmal`; run the walk when the authoritative member is
`PRODUCED` but expired; fall through past a rejected earlier member.

## Out of scope

Station-local batch failure isolation (#547), retry scheduling, FI/model changes, frontend,
changing which attempt is authoritative, T2 consumers.

## Review and merge

Independent Claude and Codex plan reviews before READY (round 1 done 2026-11-26; this
revision folds both); independent patch reviews; ruff, changed-module types, focused tests,
map-contract and docs checks; full CI. Owner merges; deployment is the host operators'
decision.
