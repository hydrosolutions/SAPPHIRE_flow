# SnowMapper → Data Gateway → forecast tools

Draft v0.5 · 13 November 2026 · Maintainer: Bea Marti

This agreement defines the snow files, the gateway output and the handover needed
to use them. **Next milestone: representative 00Z and 12Z forecast files and one
reanalysis day in the agreed format.**

**Status:** confirmed points below are carried forward from the source discussions;
other delivery requirements are proposed for Joel and Vatro to confirm. This draft
does not replace the [existing Nepal gateway agreement](01-data-gateway-requirements.md)
or authorize a live switch. Confirmations are Bea's record of discussions, not
new supplier sign-off on this draft.

## 1. Data contract

| Item | Required delivery | Status |
| --- | --- | --- |
| Source | One merged NetCDF mosaic at `s3://snow-model-data-source/joel-snow-model-mosaic/` | Confirmed: Joel, 6/11 Nov |
| Forecast | Control run, 00Z and 12Z cycles, uniformly 3-hour output through a 240-hour horizon | Control confirmed; Joel confirms cycles, step and horizon |
| Reanalysis | Daily files; history from 2025 on S3; longer training history offline | History route confirmed: Joel, 11 Nov; update lag to confirm |
| Variables | `swe`: snow water equivalent in mm at the timestamp. `rof`: mm over the following interval, not a running accumulation | Proposed; Joel states whether `rof` includes rainfall as well as snowmelt |
| Time | UTC, start stamps: `rof` at 00:00 covers [00:00, 03:00); `swe` at 00:00 is the state at 00:00 | Start stamps confirmed: Joel and Vatro, 11 Nov; example assumes 3-hour output |
| Gateway output | One requested variable, UTC index, one column per registered polygon, and row-level `source` / `source_run`; retain source units and time step | Existing output structure; Vatro confirms new-file handling |
| Gateway processing | No further time shift or differencing. Same spatial statistic and missing-cell policy for history and forecasts; missing is never zero | Required by forecast tools; Vatro documents the existing method or any necessary change |
| Mosaic handling | Crop for each deployment through one data flow; stitch from two daily cycles | Cropping and two-cycle feasibility confirmed: Vatro, 9/11 Nov |

A gateway **HRU** is a registered geometry file and may contain several polygons,
per the [existing agreement](01-data-gateway-requirements.md#1-context--the-agreed-division).
The agreed exclusion of a 00:00 value belonging to the previous day must not exclude
a valid start-stamped interval [00:00, 03:00).

## 2. Responsibilities on our side

- **Bea maintains this agreement and integrates the forecast tools.** We request
  exact forecast cycles: the gateway must return that cycle or report it unavailable.
  We do not silently substitute, fill missing values or extend an exhausted tail.
- **Forecast tools own daily aggregation.** The recorded provisional Nepal operating
  day is **18:00–18:00 UTC**, accepting a 15-minute displacement from civil midnight
  without shifting source timestamps. Bea clarifies the convention and SWE aggregation
  with Sandro; only after that discussion, reconciles the plans and checks training and
  forecast implementation. The 18:00 boundary is not implemented in the current
  shared resampler; this draft does not claim otherwise.
- **Bea handles scope and migration.** We supply the required deployment areas,
  check existing consumers before dropping `hs`, and set forecast-run times from
  confirmed delivery times. No new regional coverage is assumed.
- **Bea owns acceptance and cutover**, including the integration checks and parallel
  run in both deployments, with Sandro's model acceptance before switching.
  Proposed supplier condition: old streams remain available until Bea approves.

## 3. Only three handovers to arrange

Each owner supplies a delivery date with their response. Preparatory integration
can proceed in parallel; operational arrangements need not delay the first samples.

| Owner | What we need |
| --- | --- |
| **Joel — files and samples** | Supply the forecast and reanalysis samples with the short file specification below; confirm forecast coverage, runoff meaning, reanalysis update lag and whether the uploaded history matches. State when each cycle is complete on S3, in hours after 00Z/12Z. |
| **Vatro — gateway delivery** | Verify the samples, state when each cycle is served, and document extraction and operational-series behavior: statistic/no-data policy, joins, tail and stale-data limit. With Joel, specify complete-upload detection and recovery of missed ingests or revised history. |
| **Sandro — model inputs** | Confirm runoff suitability, daily boundary/SWE aggregation, and the training period, variables and resolution needed from Joel. Accept the retrained models against the current models before cutover. |

For an operational stitched series, an older **complete** run may be used only
within the documented age and coverage limits, with its actual cycle in
`source_run`; otherwise report unavailable data. This does not permit substitution
on an exact-cycle request. Retention must support ingestion recovery and the
reanalysis gap; the reported decision against a long-term cloud forecast archive
does not remove that requirement. Joel's “latest run only” proposal needs to fit it.

Joel and Vatro remain the contacts for their services; Bea monitors the forecast
side. Delivery deadlines define lateness. Planned outages are proposed to be
announced in `#data-gateway`. Changes to the agreed interface require updated
samples before rollout.

## File specification accompanying the samples

Joel documents the format; Vatro checks that it is ingestible. The accepted samples
and accompanying specification define the file layout, including forecast object
keys. Use these to settle the details rather than separate meetings for each:

- Keys distinguish run date **and hour**; identify initialization, valid times and
  lead times. Reanalysis currently uses `reanalysis/param=<var>/run_date=YYYYMMDD/`.
- Record grid/CRS, extent, coordinates, units, CF metadata, missing-value encoding
  and packing. Include explicit interval bounds or an unambiguous equivalent.
- State first/last intervals and states, including whether SWE includes +240 h.
  A 240-hour forecast has 3-hour runoff interval starts +0 through +237 h.
- Explain output beyond +144 h, where IFS inputs become 6-hourly; preserve totals
  if redistributing intervals. Identify the source/model version and reanalysis revisions.

**Sources:** Bea's v0.3 draft (13 Nov), reporting Joel/Vatro discussions from
7 Sep–13 Nov; [gateway README](https://github.com/hydrosolutions/recap-dg-client/blob/main/README.md);
[ECMWF input steps](https://www.ecmwf.int/en/forecasts/datasets/open-data).
The [internal notes](05-snowmapper-gateway-follow-ups.md) retain the time-decision
references and separate weather work. Discussion confirmations have not been
independently reconfirmed.
