"""Longitudinal recap Data Gateway availability probe (one cycle per invocation).

Records, per run, what the Gateway actually serves and when — the ERA5-Land
latency edge, IFS forecast availability/cadence, and whether the operational /
gap-fill stitching works — so a launchd/cron timer can build a time series.

Single-shot by design: it runs ONE probe cycle and exits, mirroring the watchdog
(`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`); the timer schedules
the cadence. Results are appended as JSONL (one record per endpoint per run) to
``RECAP_PROBE_LOG`` for later analysis with ``pandas.read_json(..., lines=True)``.

Runs either in a host venv where the ``recap-dg-client`` git-pin is synced, or
inside a container that already has ``recap_client`` baked in (the deployed
mode — see ``scripts/launchd/run-recap-probe.sh``, which ``docker exec``s this
script into the running worker container via stdin).

Config (env):
  RECAP_API_KEY            gateway key (or RECAP_API_KEY_FILE, a 0600 file)
  RECAP_API_KEY_FILE       path to a file holding the key (checked before RECAP_API_KEY)
  RECAP_BASE_URL           default https://recap.ieasyhydro.org/sdk
  RECAP_TEST_HRU           default 12300
  RECAP_PROBE_LOG          default ~/Library/Logs/sapphire-recap-probe.jsonl

The key is read from env/file only and is never written to the log or repo.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    from recap_client import RecapClient
    from recap_client.config import ApiClientConfig
except ImportError as exc:  # pragma: no cover - deploy-host dependency
    sys.stderr.write(
        f"recap_client not importable ({exc}). Run in a venv where the "
        "recap-dg-client git-pin is synced (needs RECAP_DG_CLIENT_TOKEN), or "
        "inside a container/worker image that already has recap_client.\n"
    )
    raise SystemExit(2) from exc

_BASE_URL = os.environ.get("RECAP_BASE_URL", "https://recap.ieasyhydro.org/sdk")
_HRU = os.environ.get("RECAP_TEST_HRU", "12300")
_LOG_PATH = Path(
    os.environ.get(
        "RECAP_PROBE_LOG",
        str(Path.home() / "Library" / "Logs" / "sapphire-recap-probe.jsonl"),
    )
)
_ERA5_VAR = "total_precipitation"
_IFS_VAR = "tp"

# Radiation variables (owner request 2026-09-03) — measured available for HRU
# 12300 on that date. Probed over the SAME windows as the `tp` /
# `total_precipitation` controls, so any divergence is attributable to the
# VARIABLE rather than to the window or the date. Availability is not the
# question here; STABILITY over time is, which is what accumulating these in
# the JSONL answers.
_IFS_RADIATION_VARS = ("ssr", "str")
_ERA5_RADIATION_VARS = ("surface_net_solar_radiation", "surface_net_thermal_radiation")

# Temperature — the OTHER variable the 12300 feed actually ingests. Probing only
# `tp` was a real blind spot: ERA5 gaps on this Gateway are PER-VARIABLE (measured
# 2026-09-03 — `total_precipitation` missing 08-23 while `2m_temperature` was
# missing 08-24), so a temperature outage was invisible behind a green `tp` probe.
_IFS_TEMP_VAR = "2t"
_ERA5_TEMP_VAR = "2m_temperature"

# Ensemble spot-check. The feed needs all 51 members (fc + 50 pf), but the API
# serves ONE member per call, so counting all 50 every cycle is disproportionate.
# Sampling the ends plus the middle detects the failure mode that actually
# occurs: partial publication (measured 2026-08-20 — pf absent 09:27 UTC, all 50
# present by 12:59 UTC). A green `fc` says nothing about any of this.
_PF_SAMPLE_MEMBERS = ("1", "25", "50")

# Snow (JSNOW). Its reanalysis lags much further back than ERA5's, so it gets its
# own, deeper window — the ERA5 behind-edge window returns nothing for it.
_SNOW_VARS = ("hs", "rof", "swe")


def _load_key() -> str:
    key_file = os.environ.get("RECAP_API_KEY_FILE")
    if key_file and Path(key_file).is_file():
        return Path(key_file).read_text(encoding="utf-8").strip()
    env_key = os.environ.get("RECAP_API_KEY")
    if env_key:
        return env_key.strip()
    raise SystemExit("no RECAP_API_KEY / RECAP_API_KEY_FILE set")


def _emit(record: dict[str, Any]) -> None:
    """Append one probe record as a JSONL line and echo a terse stdout summary."""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    tail = record.get("error_code") or record.get("cov_max") or ""
    sys.stdout.write(
        f"{record['run_ts']} {record['endpoint']:22s} ok={record['ok']!s:5s} {tail}\n"
    )


def _frame_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Coverage, resolution histogram, and provenance counts for a returned frame."""
    if df.empty:
        return {"n_rows": 0}
    idx = pd.to_datetime(df.index)
    steps = idx.to_series().diff().dropna().dt.total_seconds() / 3600.0
    out: dict[str, Any] = {
        "n_rows": int(len(df)),
        "cov_min": idx.min().isoformat(),
        "cov_max": idx.max().isoformat(),
        "step_hours_counts": {str(k): int(v) for k, v in steps.value_counts().items()},
    }
    if "source" in df.columns:
        out["source_counts"] = {
            str(k): int(v) for k, v in df["source"].value_counts().items()
        }
    return out


def _probe(
    run_ts: str, endpoint: str, params: dict[str, Any], call: Callable[[], pd.DataFrame]
) -> None:
    record: dict[str, Any] = {"run_ts": run_ts, "endpoint": endpoint, "params": params}
    try:
        df = call()
        record["ok"] = True
        record.update(_frame_stats(df))
    except Exception as exc:  # noqa: BLE001 - probe records every failure mode
        record["ok"] = False
        record["error_type"] = type(exc).__name__
        record["error_code"] = getattr(exc, "code", None)
        record["error_msg"] = str(exc)[:200]
    _emit(record)


def _era5_latency_edge(run_ts: str, client: RecapClient, today: date) -> None:
    """Bounded search for the newest ERA5 date the Gateway serves (the lag edge)."""
    for n in range(2, 15):
        ed = today - timedelta(days=n)
        record: dict[str, Any] = {
            "run_ts": run_ts,
            "endpoint": "era5_latency_edge",
            "params": {"end_date": ed.isoformat()},
        }
        try:
            df = client.ecmwf.era5_land_reanalysis(
                variable=_ERA5_VAR,
                start_date=ed - timedelta(days=1),
                end_date=ed,
                hru_code=_HRU,
                include_provenance=True,
            )
            last = pd.to_datetime(df.index).max()
            record["ok"] = True
            record["last_observed"] = last.isoformat()
            record["lag_days"] = (today - last.date()).days
            _emit(record)
            return
        except Exception as exc:  # noqa: BLE001
            record["ok"] = False
            record["error_code"] = getattr(exc, "code", None)
            record["error_msg"] = str(exc)[:120]
            _emit(record)


def _era5_daily_coverage(
    run_ts: str,
    client: RecapClient,
    variable: str,
    start: date,
    end: date,
) -> None:
    """Per-day sweep emitting ONE summary record that NAMES the missing days.

    A multi-day range request hard-errors on the first absent day rather than
    truncating, so a single window probe collapses "one day of five is missing"
    and "the endpoint is down" into the same red — and stays red forever for any
    window straddling a hole. Measured 2026-09-03: `total_precipitation` is
    missing 08-23 and `2m_temperature` 08-24, each an isolated day inside
    otherwise-good data, so this is the normal case here, not an edge case.

    `ok` therefore means THE SERVICE ANSWERED (at least one day returned data);
    `complete` means no day in the window was missing. Keeping those separate is
    the whole point — an archive hole must not read as an outage.
    """
    days_ok: list[str] = []
    days_missing: list[str] = []
    causes: dict[str, str] = {}
    day = start
    while day <= end:
        iso = day.isoformat()
        try:
            df = client.ecmwf.era5_land_reanalysis(
                variable=variable,
                start_date=day,
                end_date=day,
                hru_code=_HRU,
                include_provenance=True,
            )
            if len(df):
                days_ok.append(iso)
            else:
                days_missing.append(iso)
                causes[iso] = "empty_frame"
        except Exception as exc:  # noqa: BLE001 - probe records every failure mode
            days_missing.append(iso)
            causes[iso] = str(getattr(exc, "code", None) or type(exc).__name__)
        day += timedelta(days=1)

    _emit(
        {
            "run_ts": run_ts,
            "endpoint": f"era5_daily_coverage_{variable}",
            "params": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "variable": variable,
            },
            "ok": bool(days_ok),
            "complete": not days_missing,
            "days_requested": len(days_ok) + len(days_missing),
            "days_ok": len(days_ok),
            "days_missing": days_missing,
            "missing_causes": causes,
        }
    )


def main() -> int:
    client = RecapClient(ApiClientConfig(base_url=_BASE_URL, api_key=_load_key()))
    now = datetime.now(UTC)
    run_ts = now.isoformat()
    today = now.date()

    # 1) ERA5 latency edge (the headline: how stale is the observed series today?)
    _era5_latency_edge(run_ts, client, today)

    # 2) Pure reanalysis over a to-today window — the endpoint OUR adapter calls.
    #    Records whether it hard-errors past the edge (expected) vs truncates.
    _probe(
        run_ts,
        "era5_reanalysis_to_today",
        {"start": (today - timedelta(days=10)).isoformat(), "end": today.isoformat()},
        lambda: client.ecmwf.era5_land_reanalysis(
            variable=_ERA5_VAR,
            start_date=today - timedelta(days=10),
            end_date=today,
            hru_code=_HRU,
            include_provenance=True,
        ),
    )

    # 3) IFS forecast availability + native cadence (fc control), today & yesterday.
    for n in (0, 1):
        rd = today - timedelta(days=n)
        _probe(
            run_ts,
            f"ifs_forecast_run-{n}",
            {"run_date": rd.isoformat(), "ifs_type": "fc", "run_hour": 0},
            lambda rd=rd: client.ecmwf.ifs_forecast(
                variable=_IFS_VAR,
                run_date=rd,
                hru_code=_HRU,
                ifs_type="fc",
                run_hour=0,
                horizon_days=15,
                include_provenance=True,
            ),
        )

    # 4) Operational stitched series (ERA5 + IFS gap-fill) — the past→future bridge.
    _probe(
        run_ts,
        "operational_stitched",
        {"start": (today - timedelta(days=15)).isoformat(), "subdaily_resolution": 6},
        lambda: client.ecmwf.operational(
            hru_code=_HRU,
            start_date=today - timedelta(days=15),
            era5_variable_name=_ERA5_VAR,
            ifs_variable_name=_IFS_VAR,
            subdaily_resolution=6,
            include_provenance=True,
        ),
    )

    # 5) IFS gap-fill for the recent lag window.
    _probe(
        run_ts,
        "ifs_gap_fill",
        {
            "gap_start": (today - timedelta(days=8)).isoformat(),
            "gap_end": today.isoformat(),
            "subdaily_resolution": 6,
        },
        lambda: client.ecmwf.ifs_gap_fill(
            hru_code=_HRU,
            ifs_variable_name=_IFS_VAR,
            gap_start_date=today - timedelta(days=8),
            gap_end_date=today,
            subdaily_resolution=6,
            include_provenance=True,
        ),
    )

    # 6) IFS radiation — mirrors (3)'s shape exactly (fc, run_hour 0, run-0 and
    #    run-1), so (3)'s `tp` records are a same-window control for these.
    for ifs_var in (_IFS_TEMP_VAR, *_IFS_RADIATION_VARS):
        for n in (0, 1):
            rd = today - timedelta(days=n)
            _probe(
                run_ts,
                f"ifs_forecast_{ifs_var}_run-{n}",
                {
                    "run_date": rd.isoformat(),
                    "ifs_type": "fc",
                    "run_hour": 0,
                    "variable": ifs_var,
                },
                lambda v=ifs_var, d=rd: client.ecmwf.ifs_forecast(
                    variable=v,
                    run_date=d,
                    hru_code=_HRU,
                    ifs_type="fc",
                    run_hour=0,
                    level_type="sfc",
                    horizon_days=15,
                    include_provenance=True,
                ),
            )

    # 7) ERA5 per-day coverage BEHIND the lag edge (t-12..t-8), one summary
    #    record per variable naming exactly which days are absent. A window
    #    request would hard-error on the first missing day and report the whole
    #    variable red — see `_era5_daily_coverage` for why that distinction
    #    matters here specifically. (2)'s to-today window stays as-is: it is a
    #    DIFFERENT question (does the endpoint truncate or hard-fail past the
    #    edge?) and its records must remain comparable.
    era5_start = today - timedelta(days=12)
    era5_end = today - timedelta(days=8)
    for era5_var in (_ERA5_VAR, _ERA5_TEMP_VAR, *_ERA5_RADIATION_VARS):
        _era5_daily_coverage(run_ts, client, era5_var, era5_start, era5_end)

    # 8) Ensemble spot-check: the feed needs 51 members, the probe otherwise only
    #    ever sees `fc`. One call per member (the API serves one at a time), so
    #    we sample rather than enumerate — see `_PF_SAMPLE_MEMBERS`. A member that
    #    is absent while `fc` succeeds is the partial-publication signature.
    for member in _PF_SAMPLE_MEMBERS:
        _probe(
            run_ts,
            f"ifs_forecast_pf_member-{member}",
            {
                "run_date": today.isoformat(),
                "ifs_type": "pf",
                "run_hour": 0,
                "member": member,
                "variable": _IFS_VAR,
            },
            lambda m=member: client.ecmwf.ifs_forecast(
                variable=_IFS_VAR,
                run_date=today,
                hru_code=_HRU,
                ifs_type="pf",
                run_hour=0,
                member=m,
                horizon_days=15,
                include_provenance=True,
            ),
        )

    # 9) Snow (JSNOW) forecast — the channel Plan 219 wants to ingest. Same
    #    run-0 shape as the IFS sections so they are directly comparable.
    for snow_var in _SNOW_VARS:
        _probe(
            run_ts,
            f"snow_forecast_{snow_var}",
            {"run_date": today.isoformat(), "run_hour": 0, "variable": snow_var},
            lambda v=snow_var: client.snow.forecast(
                hru_code=_HRU,
                variable=v,
                run_date=today,
                run_hour=0,
                include_provenance=True,
            ),
        )

    # 10) Snow reanalysis over a DEEPER window than (7): JSNOW reanalysis lags
    #     well behind ERA5's, and the t-12..t-8 window returns nothing for it
    #     (measured 2026-08-31). Probing where data plausibly exists is what
    #     makes a failure here meaningful rather than expected.
    snow_start = today - timedelta(days=20)
    snow_end = today - timedelta(days=16)
    for snow_var in _SNOW_VARS:
        _probe(
            run_ts,
            f"snow_reanalysis_{snow_var}",
            {
                "start": snow_start.isoformat(),
                "end": snow_end.isoformat(),
                "variable": snow_var,
            },
            lambda v=snow_var: client.snow.reanalysis(
                hru_code=_HRU,
                variable=v,
                start_date=snow_start,
                end_date=snow_end,
                include_provenance=True,
            ),
        )

    sys.stdout.write(f"# recap probe cycle complete -> {_LOG_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
