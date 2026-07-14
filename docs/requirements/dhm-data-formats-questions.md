# DHM ↔ SAPPHIRE Flow — data-format confirmation checklist

**To:** DHM data team &nbsp; **From:** hydrosolutions (SAPPHIRE Flow) &nbsp; **Date:** 2026-07-13

Thank you for the sample files (**daily flow**, **staff-gauge readings**, **rating table**) and the **QC document**. To build the automatic link that reads DHM data into the SAPPHIRE forecasting system, we need to confirm a few practical details about *how* the real data will reach us and *exactly* what each field means.

**How to use this list:** please fill in the **"Your answer"** column. Short answers are perfect — a "Yes/No", a number, or one line is enough. **"Not sure — will check with senior staff"** is a completely fine answer; just mark it so we know to follow up. Items marked **⭐** are the ones we need first in order to start.

This list covers **two things**: (a) the **format of the data** you will send us, and (b) the **station metadata / GIS** we need from you to set each station up (§7).

---

## Our current understanding — please confirm or correct

We think the setup is as follows. **If any line is wrong, please correct it** — that alone answers many of our questions.

- **Operational (real-time) data reaches us via an API** from your database.
- The API gives us **water level (gauge height, in metres)**; **we convert level → discharge ourselves** using the rating table.
- **We produce forecasts only for _automatic_ (telemetric) stations**, which send water level roughly **every 10–15 minutes**.
- **Historical data** (daily discharge, past manual staff-gauge readings, and rating tables) reaches us as **files**.
- **Rating tables are uploaded periodically**; each has a **validity period** (From/To date), and we apply the table valid for each reading's date.

> **Is the above correct?** &nbsp; Yes / No — corrections: ____________________

---

### The samples, as we received them (for reference)

**Daily flow (historical) — `DFL_…txt`**
```
Daily Flow of Station: A in m3/s
 2011
01/Jan/2011,365
02/Jan/2011,364
```

**Staff-gauge readings (historical manual level) — `GHT_…txt`**
```
Staff Gauge Readings of Station: A in m
 2011
01/Jan/2011,08:00,2.86
01/Jan/2011,12:00,2.80
01/Jan/2011,16:00,2.80
```

**Rating table — `RT_…txt`**
```
Station No: A
Data Type: Rating Table
Left Column= Stage in m
Right Column= Discharge im m3/s
Rating Type No =  8
From Date = 21-Jul-2011
To Date = 15-Jul-2012
From Stage = 1.0
1,59.6
1.1,70.2
```

---

## 1. Station types & scope

*Why we ask: this decides which stations we forecast, which data channel each one uses, and how it reaches our software.*

| # | What we need to know | Example of the answer we're hoping for | Your answer |
|---|---|---|---|
| 1.1 ⭐ | Confirm we produce forecasts **only for automatic (telemetric) stations**, not manual staff-gauge stations. | Yes / No (if no: ____) | |
| 1.2 ⭐ | **Automatic** stations send water level every **how many minutes** (10? 15? varies by station?), and does the **API serve exactly this**? | e.g. "10 min for all" / "10–15, varies" | |
| 1.3 ⭐ | Are **manual** (staff-gauge) stations **also available operationally via the API**, or do they only ever come as **historical files**? If we ever needed manual data operationally, **how would it reach our software**? | e.g. "manual = files only, not on API" | |
| 1.4 | For the stations in scope, which are **automatic** vs **manual**? (a list, or a pointer to where we can see it) | e.g. "Stns 439, 447 automatic; 601 manual" | |

## 2. The operational API (automatic stations)

*Why we ask: this is our main live input; we need the access details, exact meaning of each value, and time reference.*

| # | What we need to know | Example answer | Your answer |
|---|---|---|---|
| 2.1 ⭐ | Confirm the API returns **water level in metres** (not discharge). | Yes / No | |
| 2.2 ⭐ | **API access + a dummy example.** The web address (URL), how we **authenticate** (key / token / login), and how we **request a station + time range** (or "latest"). We understand real access can't be granted yet — even a **sample API command for a dummy / test station (no real access code needed)** plus an example response would let us start building. A link to API docs is ideal. | e.g. `GET https://…/level?station=DUMMY&from=…&to=…` + a sample response | |
| 2.3 ⭐ | Can the API be queried for **past data** (a historical date range), or **only the latest** reading(s)? (This decides whether we can backfill history via the API or need files — see 3.1.) | e.g. "any date range" / "latest 24 h only" | |
| 2.4 | **Response format:** JSON / CSV / plain text? Does it include any header/metadata (station id, unit, timezone)? | e.g. "JSON: `[{time, value}]`" | |
| 2.5 ⭐ | **Time zone** of the API timestamps. | Nepal local time (UTC+5:45) / UTC / other | |
| 2.6 ⭐ | **Datum:** is the level measured from the **staff-gauge zero** at the station, or **metres above mean sea level (amsl)**? Does it differ by station? (please list amsl stations) | e.g. "gauge-zero for all" / "Stn 601 = amsl" | |
| 2.7 | **Freshness:** how soon after a measurement is it retrievable, and how often should we poll? | e.g. "available ~2 min later; poll every 10 min" | |
| 2.8 | **Missing / removed data:** see §5 — please confirm flagged values are simply absent from the API response. | (answer in 5.1) | |

## 3. Historical files

*Why we ask: we need history to train the models, at the same resolution we will forecast.*

| # | What we need to know | Example answer | Your answer |
|---|---|---|---|
| 3.1 ⭐ | For training an **automatic** station we need its **historical high-frequency (10–15 min) water level**. Can we obtain this by querying **past data from the API** (2.3), or is it available **only as files**? If files, in what **format and resolution** (10-min like the API, or the 3×/day manual `GHT` layout)? | e.g. "API serves history from 2019" / "files, 10-min" | |
| 3.2 ⭐ | **Daily flow file (`DFL`):** unit always **m³/s**? Values **integer or decimal**? How is a **missing day** shown (blank / `-999` / row absent)? | e.g. "m³/s, integer, row absent" | |
| 3.3 | **How is daily flow defined?** Daily **mean / instantaneous / max**? From **mean stage → rating** or **mean of instantaneous Q**? Day boundary in **NPT**? Is it **provisional or final**, and what **tolerance** should we expect vs our own level→Q conversion? | e.g. "daily-mean stage → rating, final, ±2%" | |
| 3.4 ⭐ | **Delivery — our proposal:** we set up a **drop location on our server** (e.g. an SFTP folder or shared drive) where you **deposit new files** (daily flow, rating tables) as they become available — we assume roughly **once a year**. Does that work, what **cadence**, and how will we know a file is **complete** (a done-marker / checksum) and the rule when a file **replaces** an earlier one? | e.g. "annual; a `.done` marker; new file replaces old" | |
| 3.5 | **File grammar & naming:** filename convention (do prefixes **`DFL`/`GHT`/`RT`** identify type?), **one file per year** or multi-year, text encoding, and any **missing-value marker** inside a file. | e.g. "`<PREFIX>_<code>_<year>.txt`, one year, UTF-8, `-999`" | |
| 3.6 ⭐ | **Coverage manifest:** for each station in scope, the **earliest & latest** data, the **cadence**, and any **known gaps** — so we know what history we actually have to train on. | e.g. "Stn 439: 2015–now, 10-min, gap 2018-06" | |

## 4. Rating tables (level → discharge conversion)

*Why we ask: we convert every live level reading to discharge using these, so we need the exact table to use, how updates arrive, and the conversion rules.*

| # | What we need to know | Example answer | Your answer |
|---|---|---|---|
| 4.1 ⭐ | The **currently-active** rating table we should use for **live conversion** — what **format** is it, **how is it delivered** (a file like the `RT` sample?), and does its **`To Date` stay open / in the future** until a newer table replaces it? | e.g. "same `RT` file format; latest has To Date far in the future" | |
| 4.2 ⭐ | New tables are **uploaded periodically** — how do we **receive** them (same folder/format), how do we **recognise** a new/updated one, and do we correctly **select by `From Date`/`To Date`**? | e.g. "new file in same folder; pick by date window" | |
| 4.3 ⭐ | Do validity periods ever **overlap** or leave **gaps**? If two tables overlap, **which wins** (highest `Rating Type No`? most recent upload?)? If there's a gap, what should we do? | e.g. "no overlaps/gaps by design" | |
| 4.4 ⭐ | **Out-of-range levels** — below `From Stage` (1.0 m → 59.6 m³/s) or above the top row (14 m): what should we do? In particular, what discharge applies for a level **below 1.0 m**? | e.g. "clamp to ends" / "below 1.0 m ≈ 0 flow" / "flag" | |
| 4.5 | **Interpolation** between the 0.1 m steps — is **straight-line (linear)** acceptable, or is there an **exact equation** we should use? | e.g. "linear is fine" / "Q = c(h−a)^b, params: ___" | |
| 4.6 | **Corrections beyond a simple shift:** are there ever **temporary shifts**, **segmented / compound** rating curves, or an **underlying equation** (e.g. `Q = c(h−a)^b`) behind the table — and how/when are these applied to the level? | e.g. "occasional temporary shift, provided per period" | |
| 4.7 | Confirm rating units: **stage in m**, **discharge in m³/s**. | Yes / No | |
| 4.8 ⭐ | **Versioning:** is **`Rating Type No` a stable, unique** per-station version? Can a table be **revised in place** (same number, changed values), **backdated**, or **reissued** for a period already covered? (We need this to detect and re-apply updates correctly.) | e.g. "unique & immutable; new number for any change" | |
| 4.9 ⭐ | **Boundary timing:** is the rating **`To Date` inclusive**? When a rating changes on a date, does the new table take effect at the **start of that day (NPT)**? (Decides which table converts a reading right at the boundary.) | e.g. "To Date inclusive; new table from 00:00 NPT" | |

## 5. Quality control (QC) & corrections

*Why we ask: the QC document says flagged data is not sent, which changes how we read missing values; and corrections may arrive later.*

| # | What we need to know | Example answer | Your answer |
|---|---|---|---|
| 5.1 ⭐ | Confirm **flagged data is not transferred via the API** — so a flagged value simply **arrives as a gap**, with no marker (we can't tell it from a sensor outage). | Yes / No | |
| 5.2 | Do the **automatic-station levels** get QC-checked (Threshold / Step) in DMS? Are thresholds defined **per station**, **per variable** (one rule for all water-level stations), or **per station *and* variable**? Can you share the **actual threshold and step limits** for water level at our stations? | e.g. "per variable; WL 0–15 m; step ±2 per 10 min" | |
| 5.3 ⭐ | **How do we learn about later corrections _and deletions_?** If a value is later accepted/modified/**deleted** in DMS, does the change show up via the API, and **how do we detect it** — a **"modified-since" query**, a per-value **last-updated / version**, a **deletion signal** (or does the value just vanish)? And **how far back** should we re-check for changes? | e.g. "modified-since endpoint; deletes leave a tombstone; re-check 30 days" | |
| 5.4 | Which **QC flag names** exist? (the dialog showed "Erroneous") | e.g. "Erroneous, Suspect, Manual" | |

## 6. Station identifiers & other parameters

| # | What we need to know | Example answer | Your answer |
|---|---|---|---|
| 6.1 ⭐ | We understand the **real station IDs may not be assigned/selected yet** — can you confirm their **format** (likely numeric codes?), and that the IDs, **names**, and any official **WIGOS ids** will be provided once the stations are chosen? | e.g. "numeric codes, provided at station selection" | |
| 6.2 | Besides water level / discharge, will DHM also provide **precipitation/rainfall** (mentioned in the QC document) or other parameters? For which stations, at what frequency, via API or file? | e.g. "rainfall at Stn 439, 10-min, API" | |
| 6.3 | Are all dates **Gregorian** (as in the samples), or is **Bikram Sambat** used anywhere in the data we'd receive? | e.g. "all Gregorian" | |
| 6.4 | If convenient, **one station list/spreadsheet** covering: id ↔ API code, automatic/manual, coordinates, datum, river/basin, status, **measured parameters**, and any **upstream flow regulation** (dams/diversions) that affects the gauge. | e.g. "one CSV with these columns" | |

## 7. Station metadata & GIS we need from you (for station setup)

*Why we ask: to set up each forecast station we need its location, its vertical reference (datum), and its catchment. Where something isn't available, we can derive or defer it — we just need to know which case applies.*

| # | What we need you to provide (per station) | Example / expected form | Your answer |
|---|---|---|---|
| 7.1 ⭐ | **Gauge coordinates** — latitude & longitude (WGS84 decimal degrees). **Please flag any station whose coordinates are unknown or may be incorrect** — we will investigate those separately and treat them as **lower priority**, so they don't hold up the rest. | e.g. "27.8768, 87.1520" ; "Stn 601 — coordinates uncertain, flag" | |
| 7.2 ⭐ | **Datum (gauge-zero elevation) in metres above mean sea level (amsl)** — the elevation of the gauge's zero point. **Is this available as station metadata?** We need it to relate gauge-height readings to amsl (and for any station that reports level in amsl — see 2.5). | e.g. "yes, per-station amsl datum available" / "not available" | |
| 7.3 | **Catchment / basin outline** (drainage-area polygon) for each gauge — available as **shapefile / GeoJSON**, in which **coordinate system (CRS/EPSG)**, keyed by the **station code**? **If not available, we will derive it from a public DEM** — just confirm none exists so we plan for that. | e.g. "shapefiles, EPSG:4326, keyed by code" / "none — derive" | |
| 7.4 | **Catchment area (km²)**, if known — helps us **validate** any outline we derive. | e.g. "≈ 3,700 km²" | |
| 7.5 | Any other station metadata you hold: **station elevation**, **river name**, **sensor type** (radar level sensor / staff gauge), **operational status** (active/inactive). | e.g. "radar sensor, active, Sapt Koshi" | |
| 7.6 ⭐ | **Station lifecycle history:** for each station, any **relocations**, **gauge-zero (datum) changes**, **sensor replacements**, or **station-ID reuse** — and whether any of these **reset the rating curve**. (A datum change or relocation silently invalidates older level→discharge, so we must know the dates.) | e.g. "Stn 439 datum revised 2016-05; rating reset then" | |

## 8. For your API & database team (more technical)

*Why we ask: you mentioned there is no API documentation yet, so we need your developers to spell out how the API behaves — this is what lets us build a reliable, correct connection. The contact person can pass this whole section to the API / database team.*

| # | What we need to know | Example answer | Your answer |
|---|---|---|---|
| 8.1 ⭐ | **API contract** (since no docs exist, please spell it out): endpoint(s), query parameters, the **response schema**, whether results are **paged** (and how), the **maximum time window** per request, result **ordering**, and how **duplicate timestamps** are handled. | e.g. "1 endpoint; JSON; paged 1000/req; max 31 days; sorted ascending" | |
| 8.2 | **Errors & limits:** HTTP status/error codes, any **rate limits**, request **timeout**, and **retry** guidance; can we request **several stations** in one call? | e.g. "429 above 60/min; retry with backoff; one station per call" | |
| 8.3 ⭐ | **Time precision:** exact **timestamp format**, the **UTC offset** (NPT = +05:45), **seconds precision**, and the definition of a **local day** (needed for daily aggregation). | e.g. "`YYYY-MM-DD HH:MM:SS+05:45`, minute precision" | |
| 8.4 | **Bootstrap & handoff:** how far back the **first API backfill** can go, the **date from which** we use API vs files, how a **partial current day** is served, and **which source wins** if a timestamp appears in both a file and the API. | e.g. "API from 2019; API wins on overlap" | |
| 8.5 | **Environments & auth operations:** is there a **test/sandbox** environment; **IP allow-listing**; **TLS/cert** requirements; **token expiry & rotation** (who owns it); any planned **maintenance windows**. | e.g. "sandbox available; IP allowlist; 90-day token" | |
| 8.6 | **Edge-case examples (very helpful):** could you share small **sample files / API responses** showing the tricky cases — a **missing** value, a **duplicate**, a **revised** and a **deleted** value, an **out-of-range** level, and a **replaced / overlapping** rating table? | e.g. "attached: 6 small examples" | |

---

*Thank you! Even partial answers help us move forward. Please send back this table with the "Your answer" column filled in, and flag anything that needs a senior colleague so we can arrange a short call if useful. Sections 1–7 can be answered by one person; **section 8 is for your API/database team.***
