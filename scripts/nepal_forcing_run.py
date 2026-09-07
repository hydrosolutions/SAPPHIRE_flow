"""Plan 192 Stage B (light shape) — one unattended gateway-forcing run for HRU 12300.

Fetches the IFS forcing for basin 12300 from the recap Data Gateway at an
EXPLICIT 00Z cycle and stores it, then appends one JSONL record describing what
the Gateway actually served. Single-shot by design (like the watchdog and the
recap probe); the host timer owns the cadence.

Why an explicit cycle rather than the adapter's own walk-back: the legacy
resolver takes the first candidate whose probe does not raise, with no horizon
check (``adapters/recap_gateway.py``), and 06/12/18Z runs carry ~2 days against
00Z's ~15. Naming the cycle sidesteps that entirely and needs no ``src/`` change
(Plan 192 D5/D8).

Why the JSONL record: this feed is UNATTENDED and unmonitored — an explicit
``cycle_time`` also suppresses the ``FORECAST_FRESHNESS`` heartbeat — so silence
would otherwise be indistinguishable from success. The record is also the
evidence used to report Gateway issues upstream, capturing what the forecast
path discards (resolved cycle, member/step counts, horizon, error codes).

Runs inside a container that already has ``recap_client`` and ``sapphire_flow``
baked in; ``scripts/`` is never in the image, so the wrapper feeds this file via
stdin (same mechanism as ``scripts/launchd/run-recap-probe.sh``).

Config (env):
  DATABASE_URL          standing Nepal store (built by docker/entrypoint.sh)
  RECAP_API_KEY         gateway key (never logged)
  NEPAL_FORCING_LOG     JSONL sink (default /dev/stderr, captured by the wrapper)
  NEPAL_FORCING_STATION station UUID to force (default: the seeded 12300 station)
  NEPAL_MAX_CYCLE_AGE_HOURS  walk-back bound; < 6 keeps it to one candidate

Exit codes: 0 = forcing stored; 1 = the run failed (gateway/store); 2 = config.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Callable

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CONFIG = 2

_DEFAULT_STATION = "22222222-2222-2222-2222-222222222222"

# Plan 219 Q3: all three, matching the standing probe's snow set.
_SNOW_VARIABLES = frozenset({"snow_depth", "snowmelt", "swe"})
_MIN_EXPECTED_HORIZON_DAYS = 14.0


def resolve_cycle(now: datetime) -> datetime:
    """The 00Z cycle of ``now``'s UTC day — the only cycle worth requesting.

    00Z runs carry ~15 days; 06/12/18Z carry ~2. Flooring to the day rather
    than to the 6 h publication cadence is the whole point.
    """
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_failure(outcome: object | None) -> str | None:
    """Why this run's NWP fetch failed, or None if it delivered.

    THREE distinct signals must all count as failure, and `summarize_stored`
    cannot tell them apart: it QUERIES the cycle's existing rows, so a
    same-cycle re-run after an earlier success would classify STALE rows as a
    fresh success and leave the dead-man green through a real outage.

      * ``outcome is None``      -- flow-fatal.
      * ``fetch_failure_reason`` -- the generic exception boundary (Plan 223 D5).
      * ``nwp_unavailable``      -- no adequate cycle, i.e. `source_data_missing`.
        `run_forecast_cycle` sets this WITHOUT `fetch_failure_reason`, so the
        Plan 223 check alone does not catch it.

    The third is the one that actually occurred: the Gateway published nothing
    for 2026-08-29/30. For the production forecast cycle `nwp_unavailable` only
    means "fall back to runoff-only" and is NOT fatal — but this feed exists
    solely to fetch NWP, so for it the condition is terminal.

    Reads both flags with `getattr` defaults deliberately: the host script and
    the container image have repeatedly been at different versions here, and an
    absent attribute must degrade to "that signal is unavailable" rather than
    raise AttributeError mid-run. `nwp_unavailable` predates
    `fetch_failure_reason`, so the older signal keeps working on an older image.
    """
    if outcome is None:
        return "outcome_none"
    reason = getattr(outcome, "fetch_failure_reason", None)
    if reason is not None:
        return str(reason)
    if getattr(outcome, "nwp_unavailable", False):
        return "nwp_unavailable"
    return None


def classify(stored: dict[str, Any]) -> tuple[bool, str | None]:
    """Decide whether a completed run actually delivered usable forcing.

    A run that stores rows but only a stub horizon is NOT a success: it means
    the Gateway served a short cycle, which is exactly the condition this feed
    exists to notice and report.
    """
    if not stored.get("rows"):
        return False, "no_rows_stored"
    horizon = stored.get("horizon_days")
    if horizon is None:
        return False, "no_horizon"
    if horizon < _MIN_EXPECTED_HORIZON_DAYS:
        return False, f"short_horizon_{horizon}d"
    return True, None


def build_record(
    *,
    run_ts: str,
    cycle: datetime,
    stored: dict[str, Any] | None,
    duration_s: float,
    error: BaseException | None,
) -> dict[str, Any]:
    """One JSONL line: what was asked for, what came back, and what went wrong."""
    record: dict[str, Any] = {
        "run_ts": run_ts,
        "hru": "12300",
        "cycle_requested": cycle.isoformat(),
        "duration_s": round(duration_s, 1),
    }
    if error is not None:
        record["ok"] = False
        record["error_type"] = type(error).__name__
        record["error_code"] = getattr(error, "code", None)
        record["error_msg"] = str(error)[:300]
        return record
    stored = stored or {}
    ok, reason = classify(stored)
    record["ok"] = ok
    if reason is not None:
        record["degraded_reason"] = reason
    record.update(stored)
    return record


def summarize_stored(conn: Any, cycle: datetime) -> dict[str, Any]:
    """Read back what actually landed — the run's own claim is not evidence."""
    import sqlalchemy as sa

    row = conn.execute(
        sa.text(
            "SELECT count(*) AS rows, count(DISTINCT member_id) AS members, "
            "count(DISTINCT valid_time) AS steps, "
            "count(DISTINCT parameter) AS parameters, "
            "min(valid_time) AS first_step, max(valid_time) AS last_step "
            "FROM weather_forecasts WHERE cycle_time = :cycle"
        ),
        {"cycle": cycle},
    ).one()
    out: dict[str, Any] = {
        "rows": int(row.rows),
        "members": int(row.members),
        "steps": int(row.steps),
        "parameters": int(row.parameters),
    }
    if row.first_step is not None and row.last_step is not None:
        out["first_step"] = row.first_step.isoformat()
        out["last_step"] = row.last_step.isoformat()
        out["horizon_days"] = round(
            (row.last_step - cycle.replace(tzinfo=row.last_step.tzinfo)).total_seconds()
            / 86400.0,
            2,
        )
    return out


def emit(record: dict[str, Any], sink: str) -> None:
    """Append the record to ``sink``; stream names are written, never reopened.

    The wrapper points ``NEPAL_FORCING_LOG`` at ``/dev/stderr`` and redirects
    the container's stderr to a host file. Under ``docker run`` the app process
    is non-root while that file belongs to the host user, so *reopening*
    ``/dev/stderr`` raises ``PermissionError`` — write to the inherited stream
    instead. (The recap probe gets away with ``open()`` because ``docker exec``
    hands it a pipe.)
    """
    line = json.dumps(record, default=str) + "\n"
    if sink in {"/dev/stderr", "-"}:
        sys.stderr.write(line)
    elif sink == "/dev/stdout":
        sys.stdout.write(line)
    else:
        with open(sink, "a", encoding="utf-8") as fh:  # noqa: PTH123
            fh.write(line)
    tail = record.get("error_code") or record.get("degraded_reason") or ""
    sys.stdout.write(
        f"{record['run_ts']} nepal-forcing ok={record['ok']!s:5s} "
        f"rows={record.get('rows', 0)} {tail}\n"
    )


def run(clock: Callable[[], datetime]) -> int:
    import time

    import sqlalchemy as sa
    from recap_client import RecapClient
    from recap_client.config import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import (
        RecapClientLike,
        RecapGatewayForecastAdapter,
        StoreBackedGatewayPolygonResolver,
    )
    from sapphire_flow.flows._db import make_pg_stores

    # Deliberate coupling to a private flow task: it is the seam that fetches
    # AND persists, and Plan 192 D8 chose it over standing up Prefect. Tracked
    # in the runbook as the thing to re-check when run_forecast_cycle changes.
    from sapphire_flow.flows.run_forecast_cycle import (  # noqa: PLC2701
        _fetch_nwp_task,  # pyright: ignore[reportPrivateUsage]
    )
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.ids import StationId

    sink = os.environ.get("NEPAL_FORCING_LOG", "/dev/stderr")
    try:
        database_url = os.environ["DATABASE_URL"]
        api_key = os.environ["RECAP_API_KEY"]
    except KeyError as exc:
        sys.stderr.write(f"missing required env var: {exc}\n")
        return EXIT_CONFIG

    station_id = StationId(
        UUID(os.environ.get("NEPAL_FORCING_STATION", _DEFAULT_STATION))
    )
    max_age = float(os.environ.get("NEPAL_MAX_CYCLE_AGE_HOURS", "5.0"))
    now = ensure_utc(clock())
    cycle = ensure_utc(resolve_cycle(now))
    run_ts = now.isoformat()

    started = time.perf_counter()
    stored: dict[str, Any] | None = None
    error: BaseException | None = None
    snow_unavailable = False
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as conn:
            stores = make_pg_stores(conn)
            station_store = stores["station_store"]
            binding = station_store.fetch_forecast_binding(station_id)  # type: ignore[attr-defined]
            adapter = RecapGatewayForecastAdapter(
                client=cast(
                    "RecapClientLike",
                    RecapClient(
                        ApiClientConfig(
                            base_url=os.environ.get(
                                "RECAP_BASE_URL", "https://recap.ieasyhydro.org/sdk"
                            ),
                            api_key=api_key,
                        )
                    ),
                ),
                resolver=StoreBackedGatewayPolygonResolver(
                    stores["gateway_polygon_store"]  # type: ignore[arg-type]
                ),
                max_cycle_age_hours=max_age,
            )
            # Plan 219: turn the snow channel on. `_fetch_nwp_task` ALREADY
            # fetches, converts, stores and contains snow — it simply does
            # nothing unless given a requirements map. In production that map
            # is derived from each station's assigned models
            # (`_compute_required_snow`); this feed deliberately has no model
            # (Plan 192), so it supplies a fixed one. That is a documented
            # divergence from the production opt-in gate, not evidence the gate
            # works — see the runbook.
            outcome = _fetch_nwp_task.fn(
                adapter=adapter,
                station_configs=[binding],
                cycle_time=cycle,
                weather_forecast_store=stores["weather_forecast_store"],
                clock=lambda: ensure_utc(clock()),
                pipeline_health_store=stores["pipeline_health_store"],
                required_snow={station_id: _SNOW_VARIABLES},
            )
            snow_unavailable = bool(getattr(outcome, "snow_unavailable", False))
            # See `fetch_failure`: a non-None outcome can still be a failure,
            # and `summarize_stored` below would otherwise report the cycle's
            # PRE-EXISTING rows as a fresh success.
            failure = fetch_failure(outcome)
            if failure is not None:
                raise RuntimeError(
                    f"NWP fetch failed (flow-fatal condition): {failure}"
                )
            stored = summarize_stored(conn, cycle)
    except Exception as exc:  # noqa: BLE001 - every failure mode becomes a record
        error = exc

    record = build_record(
        run_ts=run_ts,
        cycle=cycle,
        stored=stored,
        duration_s=time.perf_counter() - started,
        error=error,
    )
    # Plan 219 D5: additive only. Every legacy key keeps its meaning —
    # `rows`/`members`/`parameters` stay IFS-shaped — so the 12+ historical
    # records stay comparable and the analysis does not need a version check.
    record["snow_requested"] = sorted(_SNOW_VARIABLES)
    record["snow_unavailable"] = snow_unavailable
    if snow_unavailable:
        # D4: a snow-only gap is RECORDED, not PAGED. This feed exists for IFS
        # operating evidence; snow is an additional observation. Production
        # already treats `snow_unavailable` as degradation affecting only
        # snow-fed models. `ok` is deliberately NOT cleared here.
        record["snow_degraded_reason"] = "snow_unavailable"
    emit(record, sink)
    return EXIT_OK if record["ok"] else EXIT_RUN_FAILED


def main() -> int:
    return run(clock=lambda: datetime.now(UTC))


if __name__ == "__main__":
    raise SystemExit(main())
