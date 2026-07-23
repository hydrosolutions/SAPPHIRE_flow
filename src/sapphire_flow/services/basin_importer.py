"""Plan 120 Task 3A — top-level basin/static package import orchestration.

Wraps the Phase-1 loader (``services/basin_package_loader.py``:
``load_basin_package`` / ``evaluate_basin_acceptance``) and the Phase-2
writer (``store/basin_importer.py::import_basin_package``) into ONE call per
package, running the canonical write pipeline inside a single
non-AUTOCOMMIT transaction, and returns a structured, operator-facing
:class:`~sapphire_flow.types.basin_package.BasinPackageImportReport` —
accepted / onboarding-held / rejected, per-basin warnings, material-change
flags, and (for a correction) the affected-artifact set (plan "Task 3A").

Two entrypoints:

- :func:`import_loaded_basin_package` — the reusable CORE. Takes an
  already-loaded package and an already-open, non-AUTOCOMMIT
  ``sa.Connection`` (matching ``store.basin_importer.import_basin_package``'s
  own transaction contract). Runs the whole write pipeline inside a
  SAVEPOINT (``conn.begin_nested()``): a Task 2A/2C write-boundary
  :class:`~sapphire_flow.exceptions.BasinPackageRejectedError` is caught,
  the savepoint is rolled back (so the caller's outer transaction is never
  left in Postgres's "aborted" state), and the rejection is folded into the
  returned report instead of propagating.
- :func:`import_basin_package_from_directory` — the CLI-facing wrapper. Reads
  a package directory (``load_basin_package``) and opens ONE transaction on
  ``engine`` (``engine.begin()``) for the whole run, delegating to
  :func:`import_loaded_basin_package`.

Both entrypoints take an OPTIONAL ``assigned_model_features`` seam (default
``None`` — see ``basin_package_loader.evaluate_basin_acceptance``). A caller
that skips it silently treats every basin as unassigned to any model, so a
null required-static-feature never rises above a warning. The CLI (``cli/
import_basin_package.py``) MUST NOT rely on the default — it builds a real
resolver via :func:`build_assigned_model_features_resolver`.

Neither entrypoint raises for an ANTICIPATED whole-package rejection (a Task
1A schema/whole-package rule, a Task 1B gauge_id-join failure, or a Task
2A/2C write-boundary invariant) — each returns ``outcome="rejected"`` with
the reason instead, so a CLI or onboarding workflow can present the failure
without a bare traceback (contract 04:670-672: never silently complete on a
problem the importer cannot resolve). An UNANTICIPATED failure (a
programming bug, a transaction-contract violation, a DB connectivity error)
still propagates — it is not a business rejection to hide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.exceptions import BasinPackageRejectedError, ConfigurationError
from sapphire_flow.services.basin_package_loader import (
    evaluate_basin_acceptance,
    load_basin_package,
)
from sapphire_flow.store.basin_importer import import_basin_package
from sapphire_flow.types.basin_package import BasinPackageImportReport
from sapphire_flow.types.enums import ModelAssignmentStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    import sqlalchemy as sa

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import StationGroupStore, StationStore
    from sapphire_flow.types.basin_package import (
        BasinAcceptanceDecision,
        BasinRecord,
        LoadedBasinPackage,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId

log = structlog.get_logger(__name__)


def import_loaded_basin_package(
    conn: sa.Connection,
    loaded: LoadedBasinPackage,
    *,
    resolve_station: Callable[[str, str], StationId | None],
    assigned_model_features: Callable[[BasinRecord], frozenset[str]] | None = None,
    clock: Callable[[], UtcDatetime],
) -> BasinPackageImportReport:
    """The Task 3A core: Task 1B acceptance + Task 2A/2C persistence for one
    ALREADY-LOADED package, on an already-open transaction. See module
    docstring for the savepoint/rejection contract.
    """
    try:
        acceptance_report = evaluate_basin_acceptance(
            loaded,
            resolve_station=resolve_station,
            assigned_model_features=assigned_model_features,
        )
    except BasinPackageRejectedError as exc:
        log.warning(
            "basin_importer.package_rejected_at_acceptance",
            package_id=loaded.manifest.package_id,
            reason=str(exc),
        )
        return _rejected_report(loaded.manifest.package_id, exc)

    savepoint = conn.begin_nested()
    try:
        result = import_basin_package(conn, loaded, acceptance_report, clock=clock)
    except BasinPackageRejectedError as exc:
        savepoint.rollback()
        log.warning(
            "basin_importer.package_rejected_at_write",
            package_id=loaded.manifest.package_id,
            reason=str(exc),
        )
        return _rejected_report(
            loaded.manifest.package_id,
            exc,
            accepted=acceptance_report.accepted,
            onboarding_held=acceptance_report.onboarding_held,
        )
    savepoint.commit()

    outcome = "already_imported" if result.already_imported else "imported"
    log.info(
        "basin_importer.report",
        package_id=loaded.manifest.package_id,
        outcome=outcome,
        accepted=len(acceptance_report.accepted),
        onboarding_held=len(acceptance_report.onboarding_held),
        imported_basins=len(result.imported_basins),
    )
    return BasinPackageImportReport(
        package_id=loaded.manifest.package_id,
        outcome=outcome,
        accepted=acceptance_report.accepted,
        onboarding_held=acceptance_report.onboarding_held,
        imported_basins=result.imported_basins,
    )


def import_basin_package_from_directory(
    package_dir: Path,
    engine: sa.Engine,
    *,
    resolve_station: Callable[[str, str], StationId | None],
    assigned_model_features: Callable[[BasinRecord], frozenset[str]] | None = None,
    clock: Callable[[], UtcDatetime],
) -> BasinPackageImportReport:
    """The Task 3A CLI-facing entrypoint: load ``package_dir`` from disk, then
    run :func:`import_loaded_basin_package` inside ONE transaction opened on
    ``engine``. A Task 1A whole-package load rejection short-circuits before
    ``engine`` is ever touched (no connection opened, nothing to roll back).
    """
    try:
        loaded = load_basin_package(package_dir)
    except BasinPackageRejectedError as exc:
        log.warning(
            "basin_importer.package_rejected_at_load",
            package_dir=str(package_dir),
            reason=str(exc),
        )
        return _rejected_report(None, exc)

    with engine.begin() as conn:
        return import_loaded_basin_package(
            conn,
            loaded,
            resolve_station=resolve_station,
            assigned_model_features=assigned_model_features,
            clock=clock,
        )


def build_assigned_model_features_resolver(
    station_store: StationStore,
    group_store: StationGroupStore,
    resolve_station: Callable[[str, str], StationId | None],
    models: Mapping[ModelId, ForecastModel],
) -> Callable[[BasinRecord], frozenset[str]]:
    """The PRODUCTION ``assigned_model_features`` seam (fixer round, major
    finding — the CLI must not silently default this to ``None``, which
    ``evaluate_basin_acceptance`` treats as "no basin is verifiably
    assigned", downgrading every null required-static-feature to a
    warning instead of an onboarding hold).

    For a basin, resolves its station via ``resolve_station`` (same
    ``(code, network)`` pair Task 1B uses) and unions the declared
    ``data_requirements.static_features`` of every model with an ACTIVE
    assignment — direct (``station_store.fetch_model_assignments``) or via
    an active group membership (``group_store.fetch_groups_for_station`` +
    ``fetch_group_model_assignments``) — covering that station.

    ``models`` is the discovered model set (``services.model_registry.
    discover_models``) keyed by ``ModelId``. An ACTIVE assignment naming a
    ``model_id`` absent from ``models`` is a configuration bug (an assigned
    model that failed to install/register), not a basin-package problem —
    it raises :class:`~sapphire_flow.exceptions.ConfigurationError` rather
    than silently resolving an incomplete (or empty) feature set, which
    would let a real requirement slip through as a warning instead of a
    hold.
    """

    def resolver(basin: BasinRecord) -> frozenset[str]:
        station_id = resolve_station(basin.station_code, basin.network)
        if station_id is None:
            # Unmatched station is already its own onboarding hold (§9) —
            # no assignment can exist for a station that isn't in SAP3 yet.
            return frozenset()

        model_ids: set[ModelId] = {
            a.model_id
            for a in station_store.fetch_model_assignments(station_id)
            if a.status == ModelAssignmentStatus.ACTIVE
        }
        for group in group_store.fetch_groups_for_station(station_id):
            model_ids.update(
                a.model_id
                for a in group_store.fetch_group_model_assignments(group.id)
                if a.status == ModelAssignmentStatus.ACTIVE
            )

        features: set[str] = set()
        for model_id in model_ids:
            model = models.get(model_id)
            if model is None:
                raise ConfigurationError(
                    f"station {station_id} (network={basin.network!r}, "
                    f"station_code={basin.station_code!r}) has an ACTIVE "
                    f"model assignment for {model_id!r}, but that model was "
                    "not discovered (services.model_registry.discover_models) "
                    "— cannot resolve its static-feature requirements; "
                    "refusing to silently treat this basin as unassigned"
                )
            features.update(model.data_requirements.static_features)
        return frozenset(features)

    return resolver


def _rejected_report(
    package_id: str | None,
    exc: BasinPackageRejectedError,
    *,
    accepted: tuple[BasinAcceptanceDecision, ...] = (),
    onboarding_held: tuple[BasinAcceptanceDecision, ...] = (),
) -> BasinPackageImportReport:
    return BasinPackageImportReport(
        package_id=package_id,
        outcome="rejected",
        accepted=accepted,
        onboarding_held=onboarding_held,
        rejection_reason=str(exc),
    )
