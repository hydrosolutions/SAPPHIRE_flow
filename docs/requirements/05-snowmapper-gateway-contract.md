# SnowMapper → Data Gateway → forecast tools

Draft v0.4 · 13 November 2026 · Proposed maintainer: Bea Marti

This agreement defines the snow data delivered by SnowMapper, the series served
by the Data Gateway, and each owner's responsibilities. The next milestone is an
agreed file specification and representative 00Z and 12Z sample files. Proposed
cutover rule: existing streams remain available until replacement data pass the
agreed checks and Bea approves the switch.

**Status:** a proposal for owner confirmation, not authorization to change live
services. **Confirmed** below means reported as accepted in the source discussions;
**Proposed** still needs the responsible owners' acceptance. Undecided details are
listed once, in the appendix or [follow-up tracker](05-snowmapper-gateway-follow-ups.md).
This draft does not supersede the [existing Nepal gateway agreement](01-data-gateway-requirements.md).

## 1. Owners

| Owner | Responsible for |
| --- | --- |
| Joel Fiddes | Snow model, file specification, export and delivery to S3 |
| Vatro | Gateway ingestion, extraction per polygon, API and client documentation |
| Bea Marti | Forecast-tool integration, local-day aggregation, rollout and this agreement |
| Sandro | Model input requirements, training-data specification and model acceptance |

Each owner confirms their commitments and dates below. Bea proposes to maintain
the shared agreement; each owner maintains their own technical details.

## 2. Data exchanged

| Item | Agreement or proposal | Status |
| --- | --- | --- |
| Delivery | One merged NetCDF mosaic at `s3://snow-model-data-source/joel-snow-model-mosaic/` | Confirmed: Joel, 6/11 Nov |
| Forecast | Control run only; proposed cycles 00Z and 12Z, 3-hour output, 240-hour horizon | Control confirmed; cycles, step and horizon need Joel and Vatro |
| Reanalysis | Daily files; history from 2025 on S3; longer training history delivered offline | History route confirmed: Joel, 11 Nov; update lag to confirm |
| Variables | `swe`: snow water equivalent in mm, a state at the timestamp. `rof`: mm accumulated over the following step, not a running accumulation | Proposed; Joel defines what water `rof` includes and Sandro confirms suitability |
| Time | UTC. A `rof` value stamped 00:00 covers [00:00, 03:00); `swe` at 00:00 is the state at 00:00 | Start stamps confirmed: Joel and Vatro, 11 Nov; 3-hour example assumes proposed step |
| Gateway output | One requested variable per DataFrame, UTC index, one value column per polygon, plus row-level `source` and `source_run` | Existing interface; Vatro confirms for new files |
| Gateway processing | Preserve the delivered step, units and start stamps; no additional time shift or differencing of new snow files | Proposed: Vatro to accept |
| Gateway adaptation | Two-cycle stitching is feasible; crop the mosaic for each deployment using one data flow | Confirmed: Vatro, 11/9 Nov; exact stitching rules still open |
| Spatial extraction | Same documented statistic and missing-cell policy for training and operational data; distinguish missing data from zero | Proposed: Vatro and Sandro to confirm details |

A gateway **HRU** identifies a registered geometry file, which can contain several
catchment or elevation-band polygons, per the [existing gateway agreement](01-data-gateway-requirements.md#1-context--the-agreed-division).
It is not necessarily one output series. Vatro confirmed excluding a 00:00 value
that belongs to the previous day (11 Nov); this must not exclude a valid new
start-stamped interval [00:00, 03:00).
Forecast tools own local-day aggregation. Bea and Sandro must agree how intervals
crossing local midnight are split and how SWE is summarized; Nepal midnight is
18:15 UTC, inside a proposed 3-hour interval.

## 3. Delivery and failure rules

**All rules in this section are proposed and require the named owners' acceptance.**

- **Complete delivery — Joel and Vatro:** write a completion marker after all files
  for a cycle are uploaded. The gateway ingests only complete cycles. Agree the
  marker contents and retry procedure in the appendix.
- **Exact-cycle requests — Vatro:** return the requested cycle or a clear
  unavailable response. Never silently substitute another cycle.
- **Operational series — Vatro:** join reanalysis, forecast gap-fill and a selected
  forecast tail without duplicate intervals. Return the actual producing cycle
  in `source_run`. Agree the join boundaries, tail selection, maximum fallback
  age and response to insufficient coverage before rollout.
- **Missing cycles — Vatro and Bea:** operational fallback may use an older complete
  cycle only within those agreed limits. Missing values remain missing, never zero.
  Forecast tools do not invent values or extend an exhausted forecast tail.
- **Recovery and revisions — Joel and Vatro:** retain enough source runs to recover
  missed ingestion and enough gateway history to bridge reanalysis latency.
  Agree how revised reanalysis replaces earlier data. A long-term forecast archive
  is distinct from this recovery retention: v0.3 reports no long-term cloud
  forecast archive as settled, but Joel's "latest run only" proposal still needs
  reconciliation with ingestion recovery.
- **Changes and cutover — all owners:** changes to variables, timing, grid or layout
  come with updated documentation and sample files before rollout. Old streams
  remain available until Bea approves cutover after model and integration acceptance.

Enter deadlines as elapsed hours after the cycle's UTC initialization. Snow depends
on IFS input availability; snow upload and gateway weather availability are separate
delivery paths, so neither service deadline is inferred from the other.

| Commitment | Owner | Hours after 00Z initialization | Hours after 12Z initialization |
| --- | --- | --- | --- |
| Snow cycle complete on S3 | Joel | To agree | To agree |
| Snow cycle available through gateway | Vatro | To agree | To agree |
| Forecast-tool run / latest acceptable input | Bea | To agree | To agree |

Before operational acceptance, Bea coordinates agreement on who detects late
delivery, who responds for each service, and when a cycle counts as late. Planned
outages are proposed to be announced in `#data-gateway`.

## 4. Next deliveries and acceptance

Owners supply calendar dates when confirming this draft; none are committed yet.

| Delivery | Owner | Depends on | Done when | Due |
| --- | --- | --- | --- | --- |
| File specification | Joel, coordinated by Bea with Vatro and Sandro | Domain and variable decisions | Appendix fields agreed; runoff meaning accepted | To agree |
| Representative samples | Joel | File specification | 00Z, 12Z and one reanalysis day on S3; Vatro verifies metadata and values | To agree |
| Gateway integration | Vatro | Samples | Three registered geometries match independently computed values within agreed tolerances; joins and a missing-cycle case pass | To agree |
| Operational delivery | Joel and Vatro | Integration and deadlines | Seven consecutive days complete and on time | To agree |
| Training data and models | Sandro; Joel supplies grids | Sandro's data specification | Sandro accepts coverage, input compatibility and skill against current models | To agree |
| Forecast tools and parallel run | Bea | Gateway integration; accepted models for cutover | Both deployments compared with old streams; local-day aggregation checked; Bea approves switch | To agree |

Gateway and forecast-tool preparation can start while samples are being produced.
Operational deadlines and retention decisions do not block the first samples, but
must be settled before operational acceptance. Vatro checks the current Nepal
timestamp handling now; a possible one-hour offset is not yet a confirmed defect.

## Appendix: file and extraction details to settle

Keep this appendix short: record the chosen values and link the accepted sample
files and expected gateway output here. Do not duplicate implementation manuals.

| Detail | Decision needed | Owner |
| --- | --- | --- |
| Domain and variables | Large domain including Kazakhstan or smaller Central Asia + Nepal domain; whether all consumers can drop `hs`; precise physical meaning of `rof` | Bea coordinates domain / `hs`; Joel defines `rof`; Sandro accepts inputs |
| File layout | Keys include run date **and hour**; filenames, dimensions, forecast initialization, valid times and lead times. Reanalysis currently uses `reanalysis/param=<var>/run_date=YYYYMMDD/` | Joel, with Vatro |
| Grid and encoding | CRS, extent, coordinates, resolution, no-data mask, CF metadata, units, int16 fill value, scale and offset | Joel |
| Time coverage | Exact first/last state and interval; whether to include `time_bnds` (Joel proposed, Vatro to confirm); whether SWE includes +240 h. For 240 h of 3-hour runoff, interval starts are +0 through +237 h | Joel, with Vatro |
| Longer leads | Confirm output after IFS input changes to 6-hour steps beyond +144 h; document any temporal redistribution and preserve runoff totals | Joel, with Sandro |
| Existing reanalysis | Check timestamps, step, packing and actual uploaded period; re-export incompatible files. Confirm update lag and how revisions are identified | Joel |
| Completeness and recovery | Marker name and contents, ingest trigger, retry behavior, source/gateway retention periods and deletion owner | Joel and Vatro |
| Extraction and stitching | Polygon statistic, weighting, partial/no-coverage behavior, units; exact joins, tail selection and permitted fallback age | Vatro, with Sandro and Bea |
| Acceptance examples | Three geometries including partial/no coverage; numeric tolerances, midnight/cycle joins, missing cycle and source-run checks | Vatro and Bea |

**Sources:** Bea's v0.3 draft (13 Nov 2026), drawing on Joel/Vatro discussions
dated 7 Sep, 20 Oct and 3–13 Nov; [gateway client README](https://github.com/hydrosolutions/recap-dg-client/blob/main/README.md);
[ECMWF open-data steps](https://www.ecmwf.int/en/forecasts/datasets/open-data).
Discussion confirmations are carried forward from that draft, not independently
reconfirmed. The accepted gateway documentation should be linked at a fixed revision
when owners sign off.
