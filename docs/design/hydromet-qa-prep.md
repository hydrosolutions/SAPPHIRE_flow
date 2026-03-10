# Hydromet Q&A — Nepal DHM Session

1 hour meeting. Expect late arrivals — keep it tight.

## Before the meeting

- Prepare a simple system diagram: ingest → forecast → alert → (API)
- Prepare a 1-page summary of v1.0 scope (water level forecasts, no bulletins, no discharge)
- Have a mock flood threshold table to walk through units/datums
- Have the station metadata format ready (what fields we need per station)

## Agenda

### 1. System overview (10 min)

Walk through the system diagram. Key points to communicate:
- SAPPHIRE ingests weather data (ECMWF) and station observations
- Produces probabilistic water level forecasts
- Exposes forecasts via REST API
- Includes flood alerting when thresholds are exceeded
- v1.0 targets water level; discharge conversion planned for v2.0
- Bulletin production deferred (see item 7)

### 2. Observation corrections from source (5 min)

**We need to know**: Does DHM ever retroactively correct observation values
in their source database after initial transmission?

- If yes: how common? (Daily? Weekly? Rarely?)
- If yes: should SAPPHIRE pick up corrections automatically, or should all
  corrections go through the manual editing UI in SAPPHIRE?
- If no: we use a simpler ingest strategy that skips duplicate values

**Why we're asking**: This determines a core database design decision that's
hard to change later.

### 3. Flood threshold definitions (5 min)

**We need to know**: How are flood thresholds defined?

- Units: water level (m) or discharge (m³/s)?
- Reference datum: meters above sea level, meters above gauge zero, or other?
- Are thresholds constant year-round, or do they vary by season?
- How many stations currently have defined flood thresholds?
- Available via API or provided as spreadsheet?

**Why we're asking**: Thresholds must match the forecast unit. Since v1.0
forecasts water level, thresholds need to be in compatible units. If
thresholds are in discharge, we'd need rating curves from day one.

### 4. Water level first — confirm plan (3 min)

**Inform**: Our plan is to forecast water level in v1.0 and add discharge
conversion (via rating curves) in v2.0. Water level is more directly
observable and avoids compounding rating curve errors.

**Confirm**: Is water-level-only forecasting acceptable for DHM's operations
in the first phase? Or does their workflow depend on discharge values?

### 5. Date formats (5 min)

**We need to know**:

- What date format is the data in that comes from DHM's API? (Gregorian?
  Bikram Sambat? Both?)
- What date formats should we make available via the SAPPHIRE API?
- When we discuss and visualize results together, which calendar/date format
  should we use?

We plan to store everything internally as UTC/Gregorian and convert for
display. Need to know what display formats matter.

### 6. API vs CSV data availability (3 min)

**We need to know**: Once the collaboration agreement is formalized, what
station and observation data will be available via API vs. what will need
to be shared as CSV/Excel?

Specifically:
- Station metadata (location, basin, parameters)
- Real-time observations
- Historical observations
- Flood thresholds

### 7. Bulletin production scope (5 min)

**Context**: We know DHM has a forecast dashboard in development. We need
to agree on where SAPPHIRE's scope ends.

**Our recommendation**: v1.0 provides a forecast API that DHM's dashboard
can consume. We do NOT build bulletin generation into v1.0 — defer to v3
if needed.

**Question**: Does DHM need automated bulletin production from SAPPHIRE, or
does their existing dashboard handle that? Where do we draw the boundary?

### 8. Forecast frequency during flood events (5 min)

**Context**: ECMWF forecasts are available every 6 hours. Our regular
schedule runs forecasts after each ECMWF update. v1.0 will implement this
regular schedule.

**Forward-looking question**: During active flood events, does DHM want
more frequent forecast updates? If so:
- How often? (Every 1-2 hours?)
- Do they use real-time rainfall data to update forecasts between ECMWF
  cycles?

We can't promise this for v1.0, but if DHM wants it, we should plan the
architecture to support it.

### 9. Flood alert timing — before or after forecaster review (3 min)

**Context**: The system can check flood thresholds and raise alerts at two
points in the pipeline:
- **On raw forecasts** — immediately after models run, before any human review.
  Gives earlier warning but alerts may be based on uncorrected values.
- **On published forecasts** — after a forecaster has reviewed and potentially
  edited the forecast. Alerts reflect human-vetted values but are delayed by
  review time.
- **Both** — initial alert on raw, re-check after publication.

**We need to know**: In DHM's operational workflow, should flood alerts go out
as soon as the model produces a forecast, or only after a forecaster has
reviewed and approved it? Or both (preliminary + confirmed)?

**Why we're asking**: This affects pipeline sequencing and whether the alerting
module needs to run twice per cycle. We need to design for this early.

### 10. Data latency (2 min, low priority — skip if short on time)

**Quick question**: What's the typical delay between a measurement at a
station and the data appearing in DHM's API? (Seconds? Minutes? Hours?)

This affects how we schedule our ingest cycles.

### 11. Data retention and regulatory requirements (5 min)

**Context**: We're designing data retention policies for the system. Some data
(NWP archive, observations, forecasts, alerts) could be kept permanently or
on a rolling window. The choice affects storage costs and infrastructure
complexity.

**We need to know**:

- Does DHM have regulatory or legal requirements for how long forecast and
  alert records must be retained? (e.g. 5 years? 10 years? Permanent?)
- Same question for observation data — is there a mandated retention period?
- Is there value in keeping the NWP weather forecast archive long-term, or
  is a rolling window (e.g. 2–3 years) sufficient for DHM's needs?
- After a flood event, are there post-event investigations that require
  access to historical forecasts and alerts? How far back?

**Why we're asking**: Permanent retention is the safest default but adds
storage and maintenance cost. If DHM has specific regulatory requirements,
we design to those. If not, we can use rolling windows for some data
categories and reduce operational burden.

### 12. Stale forecast policy (3 min)

**Context**: If the forecast pipeline fails or is delayed, the API continues
serving the last successful forecast. At some point, a stale forecast becomes
misleading rather than useful.

**We need to know**: In DHM's operational workflow, how old can a forecast be
before it should be flagged as unreliable or withdrawn entirely? (e.g. 12h?
24h? Depends on season?)

### 13. Alert escalation (3 min, low priority)

**Context**: When SAPPHIRE raises a flood alert, someone needs to act on it.
If no one acknowledges the alert within a certain time, should the system
escalate (e.g. notify a supervisor)?

**We need to know**: Does DHM have an existing escalation protocol for flood
alerts? If so, should SAPPHIRE integrate with it, or does DHM handle
escalation outside the system?

## Not discussed — handled separately

| Topic | Reason | When |
|-------|--------|------|
| Rating curve sample data | Data request, not a discussion | After agreement signed |
| Data sharing with DRRMA | Already known — we plan scoped API tokens | Built into design |
| Historical data availability | Already known — daily + 2-5yr sub-daily | Built into design |
| DHM tech stack & existing systems | Separate technical deep-dive | After agreement signed |
| Station inventory details | Depends on data access | After agreement signed |

## After the meeting

- [ ] Document answers to each question
- [ ] Update design docs with confirmed decisions
- [ ] Request rating curve sample data (post-agreement)
- [ ] Schedule technical deep-dive on DHM's existing systems (post-agreement)
- [ ] Update 00-overview.md open questions with resolutions
