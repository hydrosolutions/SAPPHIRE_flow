# DHM operational river data: meeting brief

21 September 2026. Companion to the three-slide discussion draft.

The question is whether each forecast station has a scientifically compatible observation feed available before the forecast run. Having historical discharge and seeing a live water level on a map does not establish this.

## What the evidence establishes

- The supplied published-station PDF lists 112 sites. Its internal date is July 2025, despite “2023” in its filename. It lists published record periods, not automatic/manual classifications or current rating status.
- BIPAD's DHM-attributed River Watch catalogue includes matching river/location names for all six historical-delivery sites. This supports possible overlap, not a verified station-ID or datum match. The map shows 283 of 284 returned metadata records. One record (Roshi Khola at Kavre, API ID 270) has apparently reversed coordinates and is omitted. Catalogue presence does not prove a station currently reports.
- The six September delivery files are DFL text files containing comma-separated daily discharge records, accompanied by historical rating tables. A separate Dudh Koshi CSV contains `dateTime (NPT)` and `value (m)`. Its filename spans 2014-01-01 to 2025-11-21; completeness was not established here. Measurement values are not reproduced.
- The project records provider confirmation of the DHM API request/response contract and an implemented offline observation adapter. Direct-DHM access, activation and operational service characteristics still need agreement. There is no need to request the same example API contract again.
- Plan 268 D8 records the owner's answer that current rating tables do not exist for these six sites. Treat this as the recorded position to check for changes, rather than assuming DHM can simply send the missing files.

Do not classify all River Watch stations as “automatic, no ratings, warnings only,” or all PDF stations as “manual, rated.” The available evidence does not establish those categories. Automatic stage sensing, manual stage reading, discharge gauging, publication of historical records and warning use are separate attributes that can coexist at one site.

## Questions that decide operational feasibility

| Priority | Question for DHM | Concrete answer to capture |
|---|---|---|
| 1 | For each of the six sites, which automatic and manual series correspond to the historical discharge record? | Official station code, API station/series ID, location, gauge zero, instrument and relocation history. |
| 2 | What observation can we receive operationally: water level, provisional discharge, manual stage, or several? | Variable, units, datum, cadence, source and quality status per station. |
| 3 | Where do manual staff readings and discharge gaugings go? | Paper/app/phone reporting, regional office, entry system, central database, archive and responsible team. Distinguish staff readings from gauging visits. |
| 4 | When can headquarters and our forecasting software see those observations? | Measurement time, reporting time, entry time, QC release and API visibility. Typical and worst lag, including weekends and monsoon disruption. |
| 5 | Does the operational API include manual observations? Is the archive a separate database? | Actual fields/endpoints or an agreed scheduled file export. Ask DHM to demonstrate one automatic and one manual record end to end. |
| 6 | Without current rating tables, is there an approved provisional discharge product? | Provider, method, validity, uncertainty and update route. Otherwise, timetable for new gaugings/ratings and agreed interim forecast scope. |
| 7 | What access and service can DHM support? | Direct base URL, authentication, network permissions, polling limits, retention/backfill, outage contact and activation date. |
| 8 | How will operational and historical values agree? | Daily definition in NPT, aggregation method, QC flags, provisional/final priority, revisions/deletions, datum changes and an overlap period for comparison. |

## The forecasting decision

For models that require recent observed discharge, a usable live level feed is insufficient without a valid conversion or a provider-approved discharge product. Expired ratings must not silently become current ratings. Manual data can support operations if it arrives before the required cutoff at a suitable cadence; “manual” alone is not a reason to exclude it.

If only timely stage is available, discuss a separately validated level-forecasting approach or models that do not require recent observed discharge. This is a modelling decision, not a drop-in substitution of metres for cubic metres per second. Even models without recent Q still need timely weather inputs and an agreed validation strategy. Weather data is outside these three river-data slides.

End the meeting with one row per pilot station specifying the forecast issue time in NPT, required observation interval, latest acceptable observation, source, delivery deadline, fallback, responsible person and date for resolving each gap. A single successful demonstration at one station does not prove coverage at all six.

## Source trail

- DHM, `Updated_Published_Station_List_2023.pdf`, all three pages, local BARHKH runoff delivery.
- [DHM River Watch](https://www.dhm.gov.np/hydrology/river-watch).
- [BIPAD river-station metadata](https://bipadportal.gov.np/api/v1/river-stations/?limit=500), read on 21 September 2026. DHM attribution is in the metadata. Public access is not an operational service agreement.
- [DHM real-time streamflow page](https://dhm.gov.np/hydrology/realtime-stream) exposes a discharge column in its public page structure; availability, provenance and usefulness of values were not verified. Do not equate this with BIPAD's separate ICIMOD streamflow layer.
- [USGS: how streamflow is measured](https://www.usgs.gov/water-science-school/science/how-streamflow-measured), for the generic measurement schematic, not DHM's internal workflows.
- Repository: `docs/requirements/dhm-api-examples/README.md`, `docs/requirements/dhm-data-formats-questions.md`, `docs/plans/archive/300-dhm-observation-adapter.md`, and `docs/plans/268-dhm-barkhk-runoff-delivery.md` (especially D4 and D8).

The diagrams are discussion models. They deliberately leave DHM's internal database arrangement unresolved. Historical ingestion into the forecast tools follows the workflow described in the meeting request; completion of the six-file SAPPHIRE Flow import was not established.
