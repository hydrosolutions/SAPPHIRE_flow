"""Render an in-memory synthetic assessment, without operational state claims."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.station_preparation import PreparationAssessment

MAX_REPORT_BYTES = 128 * 1024


def render_station_preparation_report(assessment: PreparationAssessment) -> str:
    request = assessment.request
    identity = request.station.identity
    package = request.package
    facts = (
        ("Requested tenant", str(identity.tenant_id)),
        ("Requested network", identity.network),
        ("Requested station code", identity.code),
        ("Requested station name", request.station.name),
        ("Requested station kind", request.station.kind.value),
        ("Requested longitude", str(request.station.location.lon)),
        ("Requested latitude", str(request.station.location.lat)),
        ("Requested altitude", str(request.station.location.altitude_masl)),
        ("Requested package_id", package.package_id),
        ("Requested fingerprint (canonical package)", package.fingerprint),
        ("Requested source_fingerprint", package.source_fingerprint),
        ("Requested derived_fingerprint (derived/review)", package.derived_fingerprint),
    )
    details = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in facts
    )
    bindings = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(value)}</td>"
            for value in (
                binding.basin_code,
                binding.gauge_id,
                binding.gateway_hru_name,
                binding.polygon_name,
            )
        )
        + "</tr>"
        for binding in request.bindings
    )
    holds = "".join(f"<li>{escape(hold.value)}</li>" for hold in assessment.holds)
    report = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Synthetic station preparation</title><body>"
        f"<h1>{assessment.outcome.value}</h1>"
        "<p>CALLER-SUPPLIED SYNTHETIC NO-OBSERVATION SCENARIO.</p>"
        "<p>Actual observation, conversion, assignment and forecast state: "
        "NOT ASSESSED. "
        "No such state was inspected or changed.</p>"
        "<p>Not production admission. No activation, admission, approval, readiness "
        "or authentication permission is granted.</p>"
        "<h2>Requested synthetic facts (not authenticated)</h2>"
        f"<dl>{details}</dl><h2>Requested bindings</h2>"
        "<table><thead><tr><th>Basin</th><th>Gauge</th><th>HRU</th><th>Polygon</th>"
        f"</tr></thead><tbody>{bindings}</tbody></table>"
        f"<h2>Holds</h2><ul>{holds}</ul></body></html>"
    )
    if len(report.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds 128 KiB; evidence was not truncated")
    return report
