---
status: DRAFT
created: 2026-07-03
plan: 099
title: Dashboard display timezone — P1 shipped; P2 (the toggle) needs three decisions it never took
scope: DISPLAY ONLY. Axis timezone labelling (P1, SHIPPED) and an optional UTC/local toggle (P2). Explicitly NOT storage, NOT the API's timestamps, and NOT the operational day boundary — that is Plan 252 (a grid is a step and a phase) and Plan 254 (execution). Display timezone and grid phase are independent; conflating them is how a one-hour error hides behind a 45-minute one.
depends_on: [252]
blocks: []
source: 2026-07-03 — an unlabeled UTC axis caused a real UTC-vs-CEST misread during a Mac-mini observation investigation
---

# Plan 099 — dashboard display timezone

## Status

**DRAFT — and its problem statement was STALE. Re-measured 2026-09-09.**

⚠️ **P1 HAS SHIPPED** (`docs/plans/README.md:491`, PR #59). Every claim in the original problem
statement about unlabeled axes is now refuted:

| Original claim | Measured 2026-09-09 |
|---|---|
| Observations axis is labelled only "Date" (`stations/detail.html:272`) | **REFUTED** — it reads `"Date (UTC)"` at `:282` |
| Forecast axis reads "Valid time (UTC)" at `forecasts/detail.html:88` | **STALE** — correct text, at `:95` |
| `default_display_timezone` is `"Europe/Zurich"` | **PARTLY REFUTED** — the model default is `"UTC"` (`config/deployment.py:217`); the active Swiss `config.toml:60` overrides it to `"Europe/Zurich"` |
| `default_display_timezone` is never applied | **CONFIRMED** — still a dangling config, read nowhere |

**What is left is P2 only**, and P2's scope was also wrong:

- ⛔ **The climatological baseline chart must NOT get a timezone toggle.** Its x-axis is
  `"Day of year"` (`stations/detail.html:304`) — not a time axis at all. The original P2 named it.
- ⭐ **Two timestamp-bearing charts were MISSED**: forcing (`:339`) and hindcasts-vs-observed
  (`:383`), both on `"Date (UTC)"` axes. They belong in P2 and were never listed.

## Goal

## Goal

1. **Every dashboard time axis is unambiguously labeled with its timezone.**
2. **A control toggles the displayed timezone**, DST-correct. ⚠️ **What "local" means is Q1 and is
   NOT `Europe/Zurich`** — an earlier revision hard-coded it in this goal while leaving the question
   open below. Owner direction 2026-09-09: default to **the viewer's own browser locale**, always
   showing the zone on the axis.

## Phases (proposed)

- **P1 — label axes (minimal, ship first).** Add the timezone to the obs +
  baseline chart axis titles in `stations/detail.html` (and any other unlabeled
  time axis), matching the forecast chart's "(UTC)". Cheap, removes the foot-gun
  immediately. No data change.
- **P2 — timezone toggle (the nice-to-have).** A client-side control
  (dropdown/toggle: **UTC** / **Europe/Zurich**) that re-renders the Plotly
  charts in the chosen zone. Persist the choice (localStorage). Applies to the
  **observation, forecast, forcing and hindcast** charts. ⛔ **NOT the baseline chart** — its x-axis is
  `Day of year` (`stations/detail.html:304`), so a timezone on it is meaningless. An earlier revision
  listed it here while excluding it above.

## Open design questions (grill-me before READY)

⛔ **Three are blocking. None has been answered.**

**Q1 — what does "local" MEAN?** Hard-coded `Europe/Zurich`, the deployment's
`default_display_timezone`, or the station's own IANA zone? ⚠️ **Plan 252 OD-12 makes this sharper
than it was in 2026-07:** the operational day boundary is now declared per deployment with an optional
**per-station** override, so a deployment-wide display zone can disagree with the grid a given
station's data actually sits on. Recommend: the toggle offers UTC and the deployment's
`default_display_timezone`, which also un-dangles that config — and the axis label always names the
zone, so the two can never be confused silently.

**Q2 — DST correctness (the sharp one).** `Europe/Zurich` is UTC+1 in winter and UTC+2 in summer, so a
naive fixed offset is wrong half the year. The toggle must convert with a real mechanism (browser
`Intl` / `toLocaleString(..., {timeZone})`), never a constant. ⭐ **Note the asymmetry with Plan 252:**
display converts with a DST-aware zone; the data grid uses a fixed offset precisely because DST has no
uniform day. Both are correct, for opposite reasons, and the plan must say so or someone will
"harmonise" them.

**Q3 — how does a picked local date become a UTC query bound?** The station dashboard builds naive
date bounds (`stations/detail.html:244-245`) while the API parses naive timestamps as UTC
(`api/routes/api_stations.py:132-141`). Today those agree because the display is UTC. **Under a
toggle they stop agreeing**, and a user picking "yesterday" in local time silently queries a
UTC-shifted window — worst across a DST transition. This is the finding that makes P2 more than a
cosmetic change, and it was not in the original plan at all.

**Q4 — scope.** Charts confirmed in scope: observations, forecast, forcing, hindcasts. Out: the
baseline chart (not a time axis). **Undecided:** the observations table and any raw timestamp text.

**Q5 — default view.** Recommend **UTC** — an ops dashboard benefits from an unambiguous default —
with the toggle opting into local, persisted in `localStorage`.

## ⛔ The part that IS ours — the API, not the dashboard

**Owner, 2026-09-09:** *"We don't implement the dashboard though so that should not be our problem. We
provide the api to fetch data."* That reframes this plan: the toggle is a consumer concern, but the
**API contract is ours** and it is where the real defect sits.

Measured: the station dashboard builds naive date bounds (`stations/detail.html:244-245`) while the
API parses a naive timestamp as UTC (`api/routes/api_stations.py:132-141`). Today they agree only
because the display is UTC. **Any consumer sending a local-time range silently queries a shifted
window** — worst across a DST transition.

**Requirement:** the API must accept an explicit timezone offset on a date range and must not silently
reinterpret a naive one. Whether that means requiring an offset or documenting and enforcing a single
rule is Q3. ⚠️ **This survives even if we never build the toggle**, and it should probably move to an
API plan rather than a dashboard one.

## Non-goals

- Full i18n / per-user timezone preferences (v2).
- Changing storage or API timestamps away from UTC (UTC stays the source of truth).

## Process

DRAFT until a grill-me settles DST handling + default view, then phases → READY.
P1 is a trivial template label change (could ship on its own as a quick code PR);
P2 is a small client-side Plotly + a persisted toggle. Tests: template smoke that
the axis carries a tz label; a light check that the toggle re-renders (or unit
the conversion helper if one is added).
