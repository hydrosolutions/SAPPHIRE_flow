# CMAL horizon negotiation for explicitly eligible group members

status: READY

## Trigger and intended behavior

Release 0.1.1178 is active on Nepal. The 21-member static-encoder group has
17 operational model/input opt-ins and four stations still onboarding. Weather
preparation correctly restricts requests to those explicit opt-ins. Horizon
negotiation incorrectly requires all 21 stored group members, silently retaining
the 15-day request instead of accepting the available 11-day complete prefix.
The later strict fallback reached an unqualified radiation interval and refused.
No static-encoder forecasts were stored. Its new input opt-in is held; existing
CMAL-small and climatology operation, station statuses and evidence are preserved.

Use the same explicit eligible `(station, model)` pairs for preparation and
negotiation. With no eligibility selection, require the full stored group as
before. With a selection, require every selected member of each participating
group. Never infer eligibility from available data, bindings or projections.
Leave the track unchanged, with no candidate or fetch, if a projection is outside
the explicit selection or an eligible group member is missing. Existing strict
resolution then handles the original ceiling. For each participating model, compare
expected `(station, model)` pairs against track assignment pairs, never station IDs
alone. Derive expected pairs from group membership intersected with eligibility,
even when a binding is absent. Groups without track assignments are skipped,
not considered successfully negotiated. Preserve weather-hole rejection, FI floors,
exact receipts, data-purpose labels and all strict/noneligible routes.

## Scope and phases

1. Thread `provisional_weather_pairs` from the existing flow into
   `negotiate_weather_horizons` as an optional typed keyword. Restrict only the
   expected-member set in its full-group guard, and refuse out-of-selection pairs.
   No schema, model, station promotion, source timestamp or FI contract change.
2. Add behavioral regressions: explicit eligible subset accepts 11 days; missing
   eligible member and outside-selection projection do not negotiate; default
   full-group behavior remains strict. Cover caller threading (selection and None), overlapping models with distinct
   eligibility, and unchanged resolver, track identities and refused requests.
3. Update model onboarding and touchpoint docs. Run focused selected-weather and
   group tests, lint and changed-file typing; full suite must pass in CI before
   merge. Obtain one independent patch review; keep reports local.
4. Hold at owner PR merge. After separately authorized compatible release,
   restore the already-prepared static input opt-in and verify normal issuance,
   authenticated API results, memory and storage. Do not claim all21 forecasts:
   four onboarding stations remain excluded until normal promotion is approved.

```json
{"phases":[{"id":"implementation","depends_on":[]},{"id":"verification","depends_on":["implementation"]},{"id":"review_pr","depends_on":["verification"]},{"id":"deployment","depends_on":["review_pr"],"gate":"owner merge and release authority"}]}
```

## Review gate

Independent Claude review completed. Orchestrator resolved its three findings by
requiring model-specific pairs, specifying missing-member handling, and defining
refusal as unchanged strict resolution with no candidate/fetch. READY for this
bounded implementation; review report remains local.
