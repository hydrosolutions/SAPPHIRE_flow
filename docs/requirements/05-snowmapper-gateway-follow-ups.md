# SnowMapper integration: follow-ups

13 November 2026 · Maintainer: Bea Marti

Supporting tracker for the [draft agreement](05-snowmapper-gateway-contract.md).
These items preserve unresolved work from Bea's v0.3 draft. They are not accepted
commitments and do not all block sample delivery.

| Follow-up | Owner | Needed by / next action |
| --- | --- | --- |
| Current Nepal timestamp check | Vatro | Now: compare a source interval with the served value; establish whether the suspected one-hour offset exists |
| Training package | Sandro specifies; Joel delivers | Before retraining: agree period, variables, grid, model version, transfer route and revision identity |
| Central Asia migration | Bea | Before cutover: check daily-table and older-client consumers, including `hs` dependencies |
| Nepal local storage | Bea with NHS | Before deployment: agree which data are kept locally and avoid unnecessary copies |
| Gateway capacity | Vatro | Before rollout: revisit the 9 Nov estimate after the domain and extraction scope are fixed |
| Storage payment and contacts | Bea coordinates | Before operational acceptance: name the bucket payer, delivery monitor and response contact for each service |
| Old-stream retirement | Bea coordinates with Joel and Vatro | After sign-off only: agree archive/offline transfer and deletion responsibility separately |
| Agreement maintenance | Bea proposes to maintain this agreement; Joel and Vatro maintain their technical details | Confirm this repository location and link it from the gateway README; record owner acceptance and dates |

## Separate weather enhancement

Vatro reportedly agreed on 13 Nov to allow callers to choose the weather forecast
tail and to keep stitching in the gateway, after the snow work. This does not yet
establish selectable **snow** tails or permit fallback on an exact-cycle request.

| Weather cycle | Requested horizon | Status from v0.3 |
| --- | --- | --- |
| 00Z and 12Z | 11 days | Bea's request; Vatro to confirm |
| 06Z and 18Z | 4 days | Reported confirmed by Vatro, 13 Nov |

Vatro should reconcile the reported 00Z horizon (10 days observed versus about
15 days documented), confirm 12Z availability, and define behavior before today's
cycle is ready. The reported 00Z gateway availability is about 10:00 UTC; the same
delay for 12Z has not been confirmed. Acceptance: an export is available at any
hour **when an eligible complete cycle covers the requested period**, with its actual
cycle visible; otherwise the caller receives a clear unavailable response.

## Background retained from v0.3

- Old Central Asia stream: `joel-snow-model/`, daily SWE/HS/ROF/HS24, used by Tajik
  and Kyrgyz long-term forecasts. Nepal stream: `joel-snow-model-nepal/`, reported
  hourly, four cycles daily, ten-day forecasts with end stamps.
- New mosaic: two reanalysis years reported uploaded by 13 Nov; the 6 Nov upload
  covered Sep 2025–Aug 2026. Verify actual coverage and timestamp convention.
- Joel's indicative large-domain estimate was 54 GB initially and 169 GB after
  four further years, with the small domain about a quarter of that. These are
  background estimates, not retention commitments or an agreed budget.
- Source discussions: SAPPHIRE `#data-gateway` (11 Nov); Bea–Vatro messages
  (9/13 Nov); Bea–Joel messages (3/6/9 Nov, 20 Oct and 7 Sep). No private message
  transcripts are included here.
