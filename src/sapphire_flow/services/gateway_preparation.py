"""Geometry-free approved handover admission and single-host evidence custody."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pandas as pd
import pyogrio
import pyogrio.errors
from pydantic import BaseModel, ConfigDict, StrictStr, TypeAdapter, ValidationError

from sapphire_flow.adapters.recap_gateway import RECAP_VARIABLES, RecapClientLike
from sapphire_flow.adapters.recap_registration import (
    GatewayAccessError,
    RecapRegistration,
)
from sapphire_flow.basin_operator.inventory import restore_inventory
from sapphire_flow.basin_operator.review import compute_review_fingerprints, file_hash
from sapphire_flow.exceptions import BasinPackageRejectedError
from sapphire_flow.services.basin_package_loader import load_package_identity
from sapphire_flow.services.gateway_data_coverage import (
    CoverageScope,
    assess_coverage,
    private_gateway_fetch,
)
from sapphire_flow.types.basin_package import compute_manifest_fingerprint
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.gateway_preparation import (
    ApprovedFeature,
    ApprovedHandover,
    ChunkEvidence,
    CoverageKey,
    DataProposal,
    DataStream,
    DateEndConvention,
    ForecastScope,
    HistoryScope,
    PreparationState,
    ProductContract,
    RegistrationAttempt,
    RegistrationStatus,
    RequestedVariable,
    RequirementSnapshot,
    SourceRunContract,
    SourceRunSemantics,
    StreamSetting,
    Subscription,
    SubscriptionAction,
    SubscriptionChange,
    SubscriptionPlan,
    parse_gateway_hru_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.model import ModelDataRequirements


class _FeatureRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: StrictStr
    station_code: StrictStr
    name: StrictStr
    display_name: StrictStr
    gauge_id: StrictStr
    gateway_hru_name: StrictStr


class _ApprovalDecision(BaseModel):
    station_code: StrictStr
    status: StrictStr
    reviewer: StrictStr
    fingerprint: StrictStr
    reviewed_at: StrictStr


class _Approval(BaseModel):
    source_fingerprint: StrictStr
    derived_fingerprint: StrictStr
    decisions: tuple[_ApprovalDecision, ...]


class _Derivation(BaseModel):
    source_package_id: StrictStr
    source_fingerprint: StrictStr
    approved_station_codes: tuple[StrictStr, ...]


def _handover_hashes(directory: Path) -> dict[str, str]:
    sidecar = directory / "checksums.sha256"
    if not sidecar.is_file():
        raise ValueError("approved handover checksums missing")
    for path in (directory, *directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("handover symbolic links are forbidden")
    hashes: dict[str, str] = {}
    for line in sidecar.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError("invalid handover checksum entry")
        digest, name = match.groups()
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or name == "checksums.sha256"
            or name in hashes
        ):
            raise ValueError("unsafe or duplicate handover checksum path")
        path = directory / relative
        if not path.is_file() or file_hash(path) != "sha256:" + digest:
            raise ValueError("handover checksum mismatch")
        hashes[name] = "sha256:" + digest
    actual = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p != sidecar
    }
    if set(hashes) != actual:
        raise ValueError("handover checksum omissions")
    return hashes


def admit_handover(directory: Path) -> ApprovedHandover:
    """Verify exported approval applicability, never geometry acceptance."""
    hashes = _handover_hashes(directory)
    try:
        approval = _Approval.model_validate_json(
            (directory / "approval.json").read_text()
        )
        package = directory / "package"
        derivation = _Derivation.model_validate_json(
            (package / "derivation.json").read_text()
        )
        rows = TypeAdapter(list[dict[str, Any]]).validate_json(
            (directory / "coordinates-and-snap.json").read_text()
        )
        stations = restore_inventory(rows)
        manifest, computed = load_package_identity(package)
        package_hashes = {
            name.removeprefix("package/"): digest
            for name, digest in hashes.items()
            if name.startswith("package/")
        }
        _, fingerprint = compute_review_fingerprints(
            compute_manifest_fingerprint(manifest, computed), package_hashes, stations
        )
        if (
            fingerprint != approval.derived_fingerprint
            or approval.source_fingerprint != derivation.source_fingerprint
            or manifest.package_id == derivation.source_package_id
        ):
            raise ValueError("handover approval identity mismatch")
        codes = {s.code for s in stations}
        if (
            set(derivation.approved_station_codes) != codes
            or len(derivation.approved_station_codes) != len(codes)
            or {d.station_code for d in approval.decisions} != codes
            or len(approval.decisions) != len(codes)
            or any(
                d.status != "approved"
                or d.fingerprint != approval.source_fingerprint
                or not d.reviewer.strip()
                or not d.reviewed_at.strip()
                for d in approval.decisions
            )
        ):
            raise ValueError("handover decisions are not applicable approvals")
        # Attribute-only GDAL read: no geometry objects, validity or CRS checks.
        reader: Any = cast("Any", pyogrio).read_dataframe
        frame: Any = reader(
            package / "basins.gpkg",
            columns=list(_FeatureRow.model_fields),
            read_geometry=False,
        )
        features = tuple(
            _FeatureRow.model_validate(row) for row in frame.to_dict("records")
        )
        if (
            {f.station_code for f in features} != codes
            or len(features) != len(codes)
            or len({f.name for f in features}) != len(codes)
            or len({f.gauge_id for f in features}) != len(codes)
            or len({f.gateway_hru_name for f in features}) != 1
        ):
            raise ValueError("handover feature association mismatch")
        by_code = {s.code: s for s in stations}
        if any(
            f.network != manifest.network
            or f.display_name != by_code[f.station_code].display_name
            or f.gateway_hru_name not in manifest.gateway_hru_names
            for f in features
        ):
            raise ValueError("handover feature identity mismatch")
        admitted = ApprovedHandover(
            directory=directory.resolve(),
            package_id=manifest.package_id,
            source_fingerprint=approval.source_fingerprint,
            derived_fingerprint=fingerprint,
            checksums=hashes,
            features=tuple(
                ApprovedFeature(
                    network=f.network,
                    station=by_code[f.station_code],
                    polygon_name=f.name,
                    gauge_id=f.gauge_id,
                    proposed_hru=f.gateway_hru_name,
                )
                for f in features
            ),
        )
        if _handover_hashes(directory) != hashes:
            raise ValueError("handover changed during admission")
        return admitted
    except (
        OSError,
        KeyError,
        TypeError,
        ValidationError,
        BasinPackageRejectedError,
        pyogrio.errors.DataSourceError,
        pyogrio.errors.DataLayerError,
    ):
        raise ValueError("invalid approved handover") from None


_STATE = TypeAdapter(PreparationState)


@contextmanager
def preparation_workspace(directory: Path, *, handover: Path) -> Iterator[Path]:
    """Exclusive single-process custody; never use copied concurrent workspaces."""
    directory = directory.absolute()
    source = handover.resolve()
    repository = Path(__file__).resolve().parents[3]
    if (
        directory.is_symlink()
        or directory.resolve().is_relative_to(source)
        or directory.resolve().is_relative_to(repository)
        or source.is_relative_to(directory.resolve())
        or any(
            (parent / ".git").exists()
            for parent in (directory.resolve(), *directory.resolve().parents)
        )
    ):
        raise ValueError("evidence workspace must be outside repository and handover")
    if any(p.is_symlink() for p in directory.parents):
        raise ValueError("workspace symbolic links are forbidden")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError("evidence workspace must have private 0700 permissions")
    lock_path = directory / ".preparation.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "another process owns this preparation workspace"
            ) from None
        yield directory
    finally:
        os.close(fd)


def save_preparation_state(directory: Path, state: PreparationState) -> None:
    """Caller holds workspace lock. Flush intent before external side effects."""
    payload = _STATE.dump_json(state, indent=2)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "state.json")
        parent_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        Path(name).unlink(missing_ok=True)


def _unique_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_preparation_state(directory: Path) -> PreparationState:
    path = directory / "state.json"
    if path.is_symlink():
        raise ValueError("state symbolic links are forbidden")
    try:
        payload = path.read_bytes()
        raw: dict[str, Any] = json.loads(payload, object_pairs_hook=_unique_json_keys)
        state = _STATE.validate_json(payload)
        # Schema-2 records predate this optional observation; unknown is not zero.
        if isinstance(raw.get("coverage"), list):
            for chunk in cast("list[Any]", raw["coverage"]):
                if isinstance(chunk, dict):
                    cast("dict[str, Any]", chunk).setdefault("observed_count", None)
        if isinstance(raw.get("subscription_actions"), list):
            for action in cast("list[Any]", raw["subscription_actions"]):
                if isinstance(action, dict):
                    cast("dict[str, Any]", action).setdefault("failure_reason", None)
        if json.dumps(raw, sort_keys=True) != json.dumps(
            json.loads(_STATE.dump_json(state)), sort_keys=True
        ):
            raise ValueError("state fields omitted, unknown or noncanonical")
    except (OSError, ValueError):
        raise ValueError(
            "invalid or unsupported preparation state; do not reset"
        ) from None
    current = admit_handover(state.handover.directory)
    if current != state.handover:
        raise ValueError("source handover changed; return upstream")
    return state


def register_handover(
    directory: Path,
    state: PreparationState,
    adapter: RecapRegistration,
    *,
    requested_hru: str,
    attempt_id: str,
    now: str,
) -> PreparationState:
    """Persist an uncertain intent before exactly one upload. Never auto-retry."""
    requested_hru = parse_gateway_hru_name(requested_hru)
    if (
        not state.proposals
        or state.confirmed_proposal != proposal_revision(state.proposals[-1])
        or state.proposals[-1].holds
    ):
        raise ValueError("confirm a complete proposal before registration")
    if not state.gateway_review_reference.strip():
        raise ValueError("Vatro integration review reference required before live use")
    if state.attempts:
        previous = state.attempts[-1]
        if previous.requested_hru != requested_hru:
            raise ValueError("registration name cannot change to bypass reconciliation")
        if previous.status == RegistrationStatus.CONFIRMED:
            return state
        if previous.status != RegistrationStatus.NOT_CREATED:
            raise ValueError("registration reconciliation required; do not retry")
    current = admit_handover(state.handover.directory)
    if current != state.handover:
        raise ValueError("handover changed; return upstream")
    content = (current.directory / "package/basins.gpkg").read_bytes()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if digest != current.checksums["package/basins.gpkg"]:
        raise ValueError("upload bytes changed")
    attempt = RegistrationAttempt(
        attempt_id=attempt_id,
        recorded_at=now,
        requested_hru=requested_hru,
        upload_sha256=digest,
        status=RegistrationStatus.RECONCILIATION_REQUIRED,
    )
    state = replace(state, attempts=(*state.attempts, attempt))
    save_preparation_state(directory, state)
    try:
        receipt = adapter.upload(hru_code=requested_hru, content=content)
    except GatewayAccessError as exc:
        completed = replace(attempt, failure_reason=exc.reason.value)
    else:
        # HTTP success does not prove registration-to-feature ownership.
        completed = replace(
            attempt,
            message=receipt.message,
            detail=receipt.detail,
            warnings=receipt.warnings or "",
        )
    state = replace(state, attempts=(*state.attempts, completed))
    save_preparation_state(directory, state)
    return state


def reconcile_registration(
    directory: Path,
    state: PreparationState,
    *,
    status: RegistrationStatus,
    actor: str,
    evidence_reference: str,
    now: str,
) -> PreparationState:
    if not state.attempts or status not in (
        RegistrationStatus.CONFIRMED,
        RegistrationStatus.NOT_CREATED,
    ):
        raise ValueError("reconcile an existing intent with supported evidence")
    previous = state.attempts[-1]
    if previous.status == RegistrationStatus.CONFIRMED:
        raise ValueError("confirmed registration cannot be reset")
    reconciled = replace(
        previous,
        status=status,
        actor=actor,
        evidence_reference=evidence_reference,
        recorded_at=now,
    )
    state = replace(state, attempts=(*state.attempts, reconciled))
    save_preparation_state(directory, state)
    return state


def propose_data(
    *,
    model_id: str,
    requirements: ModelDataRequirements | None,
    time_step: timedelta,
    history: HistoryScope | None,
    configuration_hash: str | None = None,
    forecast: ForecastScope | None = None,
    contracts: tuple[ProductContract, ...] = (),
) -> DataProposal:
    holds: list[str] = []
    requested: list[RequestedVariable] = []
    prerequisites: list[str] = []
    if history is None:
        holds.append("history_period_and_purpose_required")
    elif any(
        t.hour or t.minute or t.second or t.microsecond
        for t in (history.start, history.end)
    ):
        holds.append("history_scope_requires_utc_date_boundaries")
    if requirements is None:
        holds.append("installed_model_declaration_unavailable")
    else:
        if requirements.spatial_input_type != SpatialRepresentation.BASIN_AVERAGE:
            holds.append("unsupported_spatial_requirements")
        if time_step not in requirements.supported_time_steps:
            holds.append("unsupported_time_step")
        prerequisites.extend(
            "static:" + name for name in sorted(requirements.static_features)
        )
        past_targets = requirements.required_past_targets
        if past_targets is None:
            holds.append("past_target_declaration_unresolved")
        else:
            prerequisites.extend("past_target:" + name for name in sorted(past_targets))
        for names, weather_stream, snow_stream in (
            (
                requirements.past_dynamic_features,
                DataStream.ERA5_HISTORY,
                DataStream.SNOW_HISTORY,
            ),
            (
                requirements.future_dynamic_features,
                DataStream.IFS_FORECAST,
                DataStream.SNOW_FORECAST,
            ),
        ):
            for name in sorted(names):
                variable = RECAP_VARIABLES.get(name)
                if variable is None:
                    holds.append("unsupported_required_variable:" + name)
                    continue
                stream = snow_stream if variable.snow_name else weather_stream
                parameter = variable.snow_name or (
                    variable.era5_name
                    if stream == DataStream.ERA5_HISTORY
                    else variable.ifs_name
                )
                if parameter is None:
                    holds.append("unsupported_required_stream:" + name)
                    continue
                requested.append(
                    RequestedVariable(
                        canonical=name,
                        stream=stream,
                        parameter=parameter,
                        source_unit=variable.source_unit,
                    )
                )
    streams = {v.stream for v in requested}
    if len({c.stream for c in contracts}) != len(contracts):
        raise ValueError("duplicate product contracts")
    for stream in sorted(streams - {c.stream for c in contracts}):
        holds.append("unconfirmed_product_contract:" + stream.value)
    for variable in requested:
        contract = next((c for c in contracts if c.stream == variable.stream), None)
        if contract is None:
            continue
        if variable.stream in (DataStream.ERA5_HISTORY, DataStream.SNOW_HISTORY):
            if (
                contract.historical_source_run.semantics
                == SourceRunSemantics.UNRESOLVED
            ):
                holds.append(
                    "historical_source_run_unconfirmed:" + variable.stream.value
                )
            if timedelta(days=1) % contract.cadence:
                holds.append("history_cadence_not_supported_by_date_chunks")
        elif forecast is not None:
            try:
                contract.forecast_leads(forecast.first_lead, forecast.end_lead)
            except ValueError:
                holds.append("forecast_interval_not_aligned_with_source_cadence")
        modes = (
            {"fc"}
            if variable.stream == DataStream.IFS_FORECAST and forecast
            else {None}
        )
        for mode in modes:
            matches = [
                b
                for b in contract.subscription_parameters
                if b.canonical == variable.canonical and b.forecast_type == mode
            ]
            if len(matches) != 1:
                holds.append(
                    "subscription_parameter_identity_unconfirmed:" + variable.canonical
                )

    if streams & {DataStream.IFS_FORECAST, DataStream.SNOW_FORECAST}:
        if forecast is not None:
            runs = dict(forecast.source_runs)
            for cycle in forecast.cycles:
                run = runs.get(cycle.isoformat())
                if run is None or run.semantics == SourceRunSemantics.UNRESOLVED:
                    holds.append(
                        "forecast_source_run_contract_unconfirmed:" + cycle.isoformat()
                    )
        if forecast is None:
            holds.append("explicit_forecast_scope_required")
        elif (
            DataStream.IFS_FORECAST in streams
            and not forecast.product_grid_reference.strip()
        ):
            holds.append("fixed_product_grid_unconfirmed")
        elif (
            requirements is not None
            and forecast.end_lead < requirements.forecast_horizon_steps * time_step
        ):
            holds.append("forecast_scope_shorter_than_declared_horizon")
    if _conflicting_subscription_identity(tuple(requested), contracts):
        holds.append("conflicting_subscription_identity")
    return DataProposal(
        model_id=model_id,
        requirements=_snapshot(requirements),
        time_step=time_step,
        history=history,
        variables=tuple(requested),
        non_gateway_prerequisites=tuple(prerequisites),
        holds=tuple(holds),
        configuration_hash=configuration_hash,
        forecast=forecast,
        contracts=contracts,
    )


def _conflicting_subscription_identity(
    variables: tuple[RequestedVariable, ...], contracts: tuple[ProductContract, ...]
) -> bool:
    by_stream = {contract.stream: contract for contract in contracts}
    identities: dict[tuple[str, str], tuple[str, str]] = {}
    for variable in variables:
        contract = by_stream.get(variable.stream)
        if contract is None:
            continue
        mode = "fc" if variable.stream == DataStream.IFS_FORECAST else None
        for binding in contract.subscription_parameters:
            if binding.canonical != variable.canonical or binding.forecast_type != mode:
                continue
            identity = (contract.subscription_source, binding.parameter_name)
            # The same snow variable may share history/forecast flags.
            product = (
                "snow"
                if variable.stream
                in (DataStream.SNOW_HISTORY, DataStream.SNOW_FORECAST)
                else variable.stream.value
            )
            meaning = (variable.canonical, product)
            if identity in identities and identities[identity] != meaning:
                return True
            identities[identity] = meaning
    return False


def proposal_revision(proposal: DataProposal) -> str:
    payload = TypeAdapter(DataProposal).dump_python(proposal, mode="json")

    return (
        "sha256:"
        + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    )


def retain_proposal(
    directory: Path, state: PreparationState, proposal: DataProposal
) -> PreparationState:
    if state.proposals and proposal_revision(state.proposals[-1]) == proposal_revision(
        proposal
    ):
        return state
    state = replace(
        state,
        proposals=(*state.proposals, proposal),
        confirmed_proposal=None,
        confirmed_subscription_plan=None,
    )
    save_preparation_state(directory, state)
    return state


def confirm_proposal(
    directory: Path, state: PreparationState, revision: str
) -> PreparationState:
    if not state.proposals or proposal_revision(state.proposals[-1]) != revision:
        raise ValueError("proposal changed; inspect and confirm its exact revision")
    if state.proposals[-1].holds:
        raise ValueError("proposal contains unresolved required inputs")
    state = replace(state, confirmed_proposal=revision)
    save_preparation_state(directory, state)
    return state


def _confirmed_scope(state: PreparationState) -> tuple[str, DataProposal]:
    if not state.attempts or state.attempts[-1].status != RegistrationStatus.CONFIRMED:
        raise ValueError("registration identity requires attributed reconciliation")
    if not state.proposals or state.confirmed_proposal != proposal_revision(
        state.proposals[-1]
    ):
        raise ValueError("confirm the exact proposal revision first")
    if state.proposals[-1].holds:
        raise ValueError("required proposal inputs remain held")
    return state.attempts[-1].requested_hru, state.proposals[-1]


def preview_subscriptions(
    directory: Path,
    state: PreparationState,
    adapter: RecapRegistration,
) -> PreparationState:
    hru, proposal = _confirmed_scope(state)
    if _conflicting_subscription_identity(proposal.variables, proposal.contracts):
        raise ValueError("conflicting required subscription identities")
    contracts = {c.stream: c for c in proposal.contracts}
    desired: dict[tuple[str, str], set[DataStream]] = {}
    for variable in proposal.variables:
        contract = contracts[variable.stream]
        modes = (
            {"fc"}
            if variable.stream == DataStream.IFS_FORECAST and proposal.forecast
            else {None}
        )
        for mode in modes:
            bindings = [
                b
                for b in contract.subscription_parameters
                if b.canonical == variable.canonical and b.forecast_type == mode
            ]
            if len(bindings) != 1:
                raise ValueError("exact subscription parameter identity required")
            key = (contract.subscription_source, bindings[0].parameter_name)
            desired.setdefault(key, set()).add(variable.stream)
        catalog = (
            "era5"
            if variable.stream == DataStream.ERA5_HISTORY
            else "ifs"
            if variable.stream == DataStream.IFS_FORECAST
            else "jsnow"
        )
        entries = [
            e for e in adapter.parameters(catalog) if e.name == variable.parameter
        ]
        if not entries:
            raise ValueError("required parameter unavailable; ask owner and Vatro")
        if variable.stream == DataStream.IFS_FORECAST:
            forecast = proposal.forecast
            assert forecast is not None
            matching = [
                e
                for e in entries
                if e.forecast_type == "fc"
                and e.level_type == forecast.level_type
                and e.level == forecast.level
                and e.grid_resolution == forecast.control_grid
            ]
            if len(matching) != 1:
                raise ValueError(
                    "required IFS control product unavailable or ambiguous"
                )
        elif variable.stream in (DataStream.SNOW_HISTORY, DataStream.SNOW_FORECAST):
            if len(entries) != 1:
                raise ValueError("snow catalog parameter is ambiguous")
            flag = (
                entries[0].forecast_enabled
                if variable.stream == DataStream.SNOW_FORECAST
                else entries[0].reanalysis_enabled
            )
            if flag != StreamSetting.ENABLED:
                raise ValueError("required snow stream unavailable")
    changes: list[SubscriptionChange] = []
    for (source, parameter), streams in sorted(desired.items()):
        rows = adapter.subscriptions(source=source, hru_code=hru)
        before = next((row for row in rows if row.parameter_name == parameter), None)
        snow = bool(streams & {DataStream.SNOW_FORECAST, DataStream.SNOW_HISTORY})
        after = before or Subscription(
            source=source,
            hru_code=hru,
            parameter_name=parameter,
            enabled=StreamSetting.ENABLED,
            forecast_enabled=StreamSetting.DISABLED
            if snow
            else StreamSetting.UNSPECIFIED,
            reanalysis_enabled=StreamSetting.DISABLED
            if snow
            else StreamSetting.UNSPECIFIED,
        )
        if (
            before is not None
            and before.enabled != StreamSetting.ENABLED
            and snow
            and (
                (
                    DataStream.SNOW_FORECAST not in streams
                    and before.forecast_enabled == StreamSetting.ENABLED
                )
                or (
                    DataStream.SNOW_HISTORY not in streams
                    and before.reanalysis_enabled == StreamSetting.ENABLED
                )
            )
        ):
            raise ValueError(
                "enabling this row would enable an unrequested snow stream"
            )
        after = replace(
            after,
            enabled=StreamSetting.ENABLED,
            forecast_enabled=StreamSetting.ENABLED
            if DataStream.SNOW_FORECAST in streams
            else after.forecast_enabled,
            reanalysis_enabled=StreamSetting.ENABLED
            if DataStream.SNOW_HISTORY in streams
            else after.reanalysis_enabled,
        )
        changes.append(SubscriptionChange(before=before, after=after))
    plan = SubscriptionPlan(
        proposal_revision=proposal_revision(proposal), changes=tuple(changes)
    )
    if state.subscription_plans and state.subscription_plans[-1] == plan:
        return state
    state = replace(
        state,
        subscription_plans=(*state.subscription_plans, plan),
        confirmed_subscription_plan=None,
    )
    save_preparation_state(directory, state)
    return state


def subscription_revision(plan: SubscriptionPlan) -> str:
    return (
        "sha256:"
        + hashlib.sha256(TypeAdapter(SubscriptionPlan).dump_json(plan)).hexdigest()
    )


def confirm_subscriptions(
    directory: Path, state: PreparationState, revision: str
) -> PreparationState:
    _confirmed_scope(state)
    if (
        not state.subscription_plans
        or subscription_revision(state.subscription_plans[-1]) != revision
    ):
        raise ValueError("subscription delta changed; inspect and confirm again")
    state = replace(state, confirmed_subscription_plan=revision)
    save_preparation_state(directory, state)
    return state


def _subscription_verified(state: PreparationState, change: SubscriptionChange) -> bool:
    latest = next(
        (
            action
            for action in reversed(state.subscription_actions)
            if action.change == change
        ),
        None,
    )
    return latest is not None and latest.outcome == "verified"


def apply_subscriptions(
    directory: Path,
    state: PreparationState,
    adapter: RecapRegistration,
    *,
    now: str,
) -> PreparationState:
    _, proposal = _confirmed_scope(state)
    if not state.subscription_plans:
        raise ValueError("preview and confirm subscription delta first")
    plan = state.subscription_plans[-1]
    if plan.proposal_revision != proposal_revision(
        proposal
    ) or state.confirmed_subscription_plan != subscription_revision(plan):
        raise ValueError("subscription confirmation is stale")
    for change in plan.changes:
        target = change.after
        rows = adapter.subscriptions(source=target.source, hru_code=target.hru_code)
        current = next(
            (r for r in rows if r.parameter_name == target.parameter_name), None
        )
        prior = [a for a in state.subscription_actions if a.change == change]
        if current != target:
            if current != change.before or prior:
                action = SubscriptionAction(
                    change=change, recorded_at=now, outcome="uncertain"
                )
                state = replace(
                    state, subscription_actions=(*state.subscription_actions, action)
                )
                save_preparation_state(directory, state)
                raise ValueError(
                    "subscription outcome or concurrent change requires reconciliation"
                )
            intent = SubscriptionAction(
                change=change, recorded_at=now, outcome="intent"
            )
            state = replace(
                state, subscription_actions=(*state.subscription_actions, intent)
            )
            save_preparation_state(directory, state)
            try:
                if current is None:
                    adapter.create_subscription(target)
                else:
                    adapter.update_subscription(current, target)
            except GatewayAccessError as exc:
                failure = SubscriptionAction(
                    change=change,
                    recorded_at=now,
                    outcome="uncertain",
                    failure_reason=exc.reason,
                )
                state = replace(
                    state, subscription_actions=(*state.subscription_actions, failure)
                )
                save_preparation_state(directory, state)
                # Persist before read-back, which may itself fail or be interrupted.
            rows = adapter.subscriptions(source=target.source, hru_code=target.hru_code)
            current = next(
                (r for r in rows if r.parameter_name == target.parameter_name), None
            )
        action = SubscriptionAction(
            change=change,
            recorded_at=now,
            outcome="verified" if current == target else "uncertain",
        )
        state = replace(
            state, subscription_actions=(*state.subscription_actions, action)
        )
        save_preparation_state(directory, state)
        if current != target:
            raise ValueError("subscription outcome uncertain; inspect before retry")
    return state


def history_instructions(state: PreparationState) -> tuple[str, ...]:
    hru, proposal = _confirmed_scope(state)
    history = proposal.history
    if history is None:
        raise ValueError("history period is unresolved")
    lines: list[str] = []
    era5 = sorted(
        v.parameter for v in proposal.variables if v.stream == DataStream.ERA5_HISTORY
    )
    if era5:
        first, last = history.job_years
        lines.append(
            "1. In the Gateway DAGs dashboard use era5_land_backfill__targeted; "
            f"shapefile_names=[{hru}]; year_start={first}; year_end={last}; "
            f"explicitly select variables={','.join(era5)}."
        )
        lines.append(
            f"2. Verify the agreed half-open interval {history.start.isoformat()} "
            f"to {history.end.isoformat()}; job years are expanded, "
            "not a change to this interval."
        )
    snow = sorted(
        v.parameter for v in proposal.variables if v.stream == DataStream.SNOW_HISTORY
    )
    if snow:
        contract = next(
            c for c in proposal.contracts if c.stream == DataStream.SNOW_HISTORY
        )
        if not contract.snow_history_procedure.strip():
            lines.append(
                "HOLD: obtain the supported snow history procedure from Vatro; "
                "ERA5 job success is not snow evidence."
            )
        else:
            lines.append(
                f"Snow: HRU={hru}; variables={','.join(snow)}; "
                f"interval={history.start.isoformat()} to {history.end.isoformat()}; "
                f"procedure={contract.snow_history_procedure}; "
                f"basis={contract.reference}."
            )
    lines.append(
        "3. Record actual job references, then resume full-scope retrieval. "
        "Do not run shared ifs__subdaily or any automatic refresh."
    )
    return tuple(lines)


def record_history_job(
    directory: Path,
    state: PreparationState,
    *,
    stream: DataStream,
    actor: str,
    reference: str,
) -> PreparationState:
    _, proposal = _confirmed_scope(state)
    if stream not in {v.stream for v in proposal.variables} or stream not in (
        DataStream.ERA5_HISTORY,
        DataStream.SNOW_HISTORY,
    ):
        raise ValueError("job reference must match a requested historical stream")
    if not actor.strip() or not reference.strip():
        raise ValueError("job attribution and reference required")
    state = replace(
        state, history_jobs=(*state.history_jobs, (stream.value, actor, reference))
    )
    save_preparation_state(directory, state)
    return state


@dataclass(frozen=True, kw_only=True, slots=True)
class _CoverageRequest:
    variable: RequestedVariable
    contract: ProductContract
    start: datetime
    end: datetime
    member: int | None
    cycle: datetime | None
    times: tuple[datetime, ...]
    run: SourceRunContract

    def key(self, station: str) -> CoverageKey:
        return (
            station,
            self.variable.stream,
            self.variable.parameter,
            self.start,
            self.end,
            self.member,
            self.cycle,
        )

    def coverage_scope(self, polygon: str) -> CoverageScope:
        return CoverageScope(
            polygon=polygon,
            start=self.start,
            end=self.end,
            cadence=self.contract.cadence,
            source=self.contract.provenance,
            contract_reference=self.contract.reference,
            source_run=self.run,
            expected_times=self.times,
        )

    def run_identities(self) -> tuple[str, ...]:
        if self.run.semantics == SourceRunSemantics.EXACT:
            assert self.run.value is not None
            return (self.run.value,)
        if self.run.semantics == SourceRunSemantics.UTC_INSTANT:
            assert self.run.timestamp is not None
            return (self.run.timestamp.isoformat(),)
        if self.run.semantics == SourceRunSemantics.UTC_DATE_MIDNIGHT:
            return tuple(
                sorted(
                    {
                        time.replace(
                            hour=0, minute=0, second=0, microsecond=0
                        ).isoformat()
                        for time in self.times
                    }
                )
            )
        return ()


def _coverage_requests(proposal: DataProposal) -> tuple[_CoverageRequest, ...]:
    if proposal.holds:
        raise ValueError("coverage scope remains unresolved")
    contracts = {contract.stream: contract for contract in proposal.contracts}
    requests: list[_CoverageRequest] = []
    for variable in proposal.variables:
        contract = contracts.get(variable.stream)
        if contract is None:
            raise ValueError("coverage contract missing")
        if variable.stream in (DataStream.ERA5_HISTORY, DataStream.SNOW_HISTORY):
            if proposal.history is None:
                raise ValueError("coverage history missing")
            start = proposal.history.start
            while start < proposal.history.end:
                end = min(start + timedelta(days=31), proposal.history.end)
                if (end - start) % contract.cadence:
                    raise ValueError("history support is not aligned")
                times = tuple(
                    start + n * contract.cadence
                    for n in range(int((end - start) / contract.cadence))
                )
                requests.append(
                    _CoverageRequest(
                        variable=variable,
                        contract=contract,
                        start=start,
                        end=end,
                        cycle=None,
                        member=None,
                        times=times,
                        run=contract.historical_source_run,
                    )
                )
                start = end
        else:
            forecast = proposal.forecast
            if forecast is None:
                raise ValueError("coverage forecast missing")
            runs = dict(forecast.source_runs)
            for cycle in forecast.cycles:
                run = runs.get(cycle.isoformat(), SourceRunContract())
                for member in (
                    forecast.members
                    if variable.stream == DataStream.IFS_FORECAST
                    else (None,)
                ):
                    requests.append(
                        _CoverageRequest(
                            variable=variable,
                            contract=contract,
                            start=cycle + forecast.first_lead,
                            end=cycle + forecast.end_lead,
                            cycle=cycle,
                            member=member,
                            times=tuple(
                                cycle + lead
                                for lead in contract.forecast_leads(
                                    forecast.first_lead, forecast.end_lead
                                )
                            ),
                            run=run,
                        )
                    )
    if any(
        not request.times or request.run.semantics == SourceRunSemantics.UNRESOLVED
        for request in requests
    ):
        raise ValueError("coverage time or run support unresolved")
    return tuple(requests)


def _coverage_issues(
    evidence: ChunkEvidence, request: _CoverageRequest
) -> tuple[str, ...]:
    issues = set(evidence.holds)
    count = len(request.times)
    if evidence.expected_count != count:
        issues.add("evidence_expected_count_mismatch")
    if evidence.usable_count != count or evidence.missing_intervals:
        issues.add("missing_requested_support")
    if evidence.observed_count is not None and evidence.observed_count != count:
        issues.add("evidence_observed_count_mismatch")
    expected_runs = request.run_identities()
    if evidence.usable_count and (
        evidence.validated_source != request.contract.provenance
        or evidence.validated_source_run != request.run
        or not set(evidence.validated_run_identities) <= set(expected_runs)
        or (
            evidence.usable_count == count
            and evidence.validated_run_identities != expected_runs
        )
    ):
        issues.add("evidence_provenance_mismatch")
    return tuple(sorted(issues))


def _latest_coverage(
    state: PreparationState, revision: str
) -> dict[CoverageKey, ChunkEvidence]:
    return {
        evidence.scope_key: evidence
        for evidence in state.coverage
        if evidence.scope_revision == revision
    }


def resume_coverage(
    directory: Path,
    state: PreparationState,
    client: RecapClientLike,
    *,
    now: str,
) -> PreparationState:
    hru, proposal = _confirmed_scope(state)
    if (
        not state.subscription_plans
        or state.confirmed_subscription_plan
        != subscription_revision(state.subscription_plans[-1])
    ):
        raise ValueError("confirmed required subscriptions missing")
    plan = state.subscription_plans[-1]
    if plan.proposal_revision != proposal_revision(proposal):
        raise ValueError("subscription scope is stale")
    if not all(_subscription_verified(state, change) for change in plan.changes):
        raise ValueError("subscription read-back remains held")
    revision = proposal_revision(proposal)
    for request in _coverage_requests(proposal):
        latest = _latest_coverage(state, revision)
        pending = [
            feature
            for feature in state.handover.features
            if request.cycle is not None
            or (existing := latest.get(request.key(feature.station.code))) is None
            or _coverage_issues(existing, request)
        ]
        if not pending:
            continue
        failure: str | None = None
        try:
            raw = private_gateway_fetch(
                directory,
                partial(
                    _fetch_scope,
                    client,
                    hru=hru,
                    variable=request.variable,
                    contract=request.contract,
                    start=request.start,
                    end=request.end,
                    cycle=request.cycle,
                    member=request.member,
                    forecast=proposal.forecast,
                ),
            )
            if not isinstance(raw, pd.DataFrame):
                failure = "unsupported_response_frame"
                raw = pd.DataFrame()
        except GatewayAccessError as exc:
            failure = exc.reason.value
            raw = pd.DataFrame()
        for feature in pending:
            result = assess_coverage(raw, request.coverage_scope(feature.polygon_name))
            evidence = ChunkEvidence(
                scope_revision=revision,
                station_code=feature.station.code,
                stream=request.variable.stream,
                variable=request.variable.parameter,
                start=request.start.isoformat(),
                end=request.end.isoformat(),
                member=request.member,
                cycle=request.cycle.isoformat() if request.cycle else None,
                fetched_at=now,
                expected_count=result.expected_count,
                usable_count=result.usable_count,
                observed_count=result.observed_count,
                missing_intervals=result.missing_intervals,
                holds=(*result.holds, failure) if failure else result.holds,
                response_sha256=result.response_sha256,
                validated_source=result.validated_source,
                validated_source_run=result.validated_source_run,
                validated_run_identities=result.validated_run_identities,
            )
            state = replace(state, coverage=(*state.coverage, evidence))
            save_preparation_state(directory, state)
    return state


def _fetch_scope(
    client: RecapClientLike,
    *,
    hru: str,
    variable: RequestedVariable,
    contract: ProductContract,
    start: datetime,
    end: datetime,
    cycle: datetime | None,
    member: int | None,
    forecast: ForecastScope | None,
) -> object:
    if cycle is None:
        # Request whole provider dates, then retain only agreed half-open support.
        last_date = (
            (end - timedelta(microseconds=1)).date()
            if contract.date_end == DateEndConvention.INCLUSIVE
            else end.date()
        )
        if variable.stream == DataStream.ERA5_HISTORY:
            raw = client.ecmwf.era5_land_reanalysis(
                variable=variable.parameter,
                start_date=start.date(),
                end_date=last_date,
                hru_code=hru,
                include_provenance=True,
            )
        else:
            raw = client.snow.reanalysis(
                variable=variable.parameter,
                start_date=start.date(),
                end_date=last_date,
                hru_code=hru,
                include_provenance=True,
            )
    elif variable.stream == DataStream.IFS_FORECAST:
        assert forecast is not None and member == 0
        raw = client.ecmwf.ifs_forecast(
            variable=variable.parameter,
            run_date=cycle.date(),
            run_hour=cycle.hour,
            hru_code=hru,
            ifs_type="fc",
            member=None,
            level_type=forecast.level_type,
            level=forecast.level,
            horizon_days=math.ceil(forecast.end_lead / timedelta(days=1)),
            include_provenance=True,
        )
    else:
        raw = client.snow.forecast(
            variable=variable.parameter,
            run_date=cycle.date(),
            run_hour=cycle.hour,
            hru_code=hru,
            include_provenance=True,
        )
    if (
        cycle is None
        and isinstance(raw, pd.DataFrame)
        and isinstance(raw.index, pd.DatetimeIndex)
        and raw.index.tz is not None
    ):
        return cast("Any", raw).loc[(raw.index >= start) & (raw.index < end)]
    return raw


def _coverage_label(value: str | None) -> str | None:
    if value is None:
        return None
    return (
        value
        if re.fullmatch(r"[A-Za-z0-9_.:+-]{1,200}", value)
        else "[redacted metadata]"
    )


def _coverage_summary(
    request: _CoverageRequest | None,
    evidence: ChunkEvidence | None,
    issues: tuple[str, ...],
) -> dict[str, Any]:
    run = evidence.validated_source_run if evidence else None
    return {
        "applicability": "requested" if request else "unexpected",
        "station_code": _coverage_label(evidence.station_code) if evidence else None,
        "scope_revision": evidence.scope_revision if evidence else None,
        "stream": request.variable.stream
        if request
        else evidence.stream
        if evidence
        else None,
        "variable": _coverage_label(
            request.variable.parameter
            if request
            else evidence.variable
            if evidence
            else None
        ),
        "start": request.start.isoformat()
        if request
        else evidence.start
        if evidence
        else None,
        "end": request.end.isoformat()
        if request
        else evidence.end
        if evidence
        else None,
        "cycle": request.cycle.isoformat()
        if request and request.cycle
        else evidence.cycle
        if evidence
        else None,
        "member": request.member if request else evidence.member if evidence else None,
        "requested_count": len(request.times) if request else None,
        "recorded_expected_count": evidence.expected_count if evidence else None,
        "usable_count": evidence.usable_count if evidence else None,
        "observed_count": evidence.observed_count if evidence else None,
        "missing_count": len(request.times) - evidence.usable_count
        if request and evidence and evidence.usable_count <= len(request.times)
        else None,
        "missing_intervals": evidence.missing_intervals if evidence else (),
        "validated_source": _coverage_label(evidence.validated_source)
        if evidence
        else None,
        "validated_source_run": {
            "semantics": run.semantics,
            "value": _coverage_label(run.value),
            "timestamp": run.timestamp.isoformat() if run.timestamp else None,
        }
        if run
        else None,
        "validated_run_identities": tuple(
            _coverage_label(value) for value in evidence.validated_run_identities
        )
        if evidence
        else (),
        "fetched_at": evidence.fetched_at if evidence else None,
        "response_sha256": evidence.response_sha256 if evidence else None,
        "holds": tuple(_coverage_label(value) for value in issues),
        "publication_lag_seconds": request.contract.publication_lag.total_seconds()
        if request
        else None,
        "limitations": (
            "Declared publication lag is not a freshness guarantee; "
            "coverage is not scientific or model readiness. "
            "Legacy observed counts may be unknown."
        ),
    }


def _subscription_diagnostics(state: PreparationState) -> list[dict[str, Any]]:
    if not state.subscription_plans:
        return []
    rows: list[dict[str, Any]] = []
    for change in state.subscription_plans[-1].changes:
        actions = [
            action for action in state.subscription_actions if action.change == change
        ]
        rows.append(
            {
                "source": change.after.source,
                "hru_code": change.after.hru_code,
                "parameter_name": change.after.parameter_name,
                "current_outcome": actions[-1].outcome if actions else "unrecorded",
                "failure_reason": actions[-1].failure_reason if actions else None,
                "failure_history": [
                    {
                        "recorded_at": _coverage_label(action.recorded_at),
                        "reason": action.failure_reason,
                    }
                    for action in actions
                    if action.failure_reason is not None
                ],
            }
        )
    return rows


def preparation_report(state: PreparationState) -> dict[str, Any]:
    proposal = state.proposals[-1] if state.proposals else None
    revision = proposal_revision(proposal) if proposal else None
    requests: tuple[_CoverageRequest, ...] = ()
    if proposal is not None:
        # Unresolved proposals stay held; do not invent expected support.
        with suppress(ValueError):
            requests = _coverage_requests(proposal)
    latest = _latest_coverage(state, revision) if revision else {}
    expected = {
        request.key(feature.station.code): request
        for request in requests
        for feature in state.handover.features
    }
    unexpected = set(latest) - set(expected)
    stations = {feature.station.code for feature in state.handover.features}
    outcomes: list[dict[str, Any]] = []
    for feature in state.handover.features:
        holds = list(proposal.holds) if proposal else ["proposal_missing"]
        if (
            not state.attempts
            or state.attempts[-1].status != RegistrationStatus.CONFIRMED
        ):
            holds.append("registration_identity_unconfirmed")
        if state.confirmed_proposal != revision or revision is None:
            holds.append("proposal_unconfirmed")
        if not state.subscription_plans or (
            state.subscription_plans[-1].proposal_revision != revision
            or state.confirmed_subscription_plan
            != subscription_revision(state.subscription_plans[-1])
        ):
            holds.append("subscriptions_unconfirmed")
        elif any(
            not _subscription_verified(state, change)
            for change in state.subscription_plans[-1].changes
        ):
            holds.append("subscription_readback_missing")
        station_expected = {
            key: request
            for key, request in expected.items()
            if key[0] == feature.station.code
        }
        station_latest = {
            key: evidence
            for key, evidence in latest.items()
            if key[0] == feature.station.code
        }
        summaries: list[dict[str, Any]] = []
        verified = 0
        if not station_expected or not set(station_expected) <= set(station_latest):
            holds.append("requested_scope_incomplete")
        station_unexpected = {
            key
            for key in unexpected
            if key[0] == feature.station.code or key[0] not in stations
        }
        if station_unexpected:
            holds.append("unexpected_coverage_evidence")
        for key, request in station_expected.items():
            evidence = station_latest.get(key)
            issues = (
                _coverage_issues(evidence, request)
                if evidence
                else ("coverage_not_verified",)
            )
            holds.extend(issues)
            verified += not issues
            summaries.append(_coverage_summary(request, evidence, issues))
        for key in latest:
            if key in station_unexpected:
                summaries.append(
                    _coverage_summary(
                        None, latest[key], ("unexpected_coverage_evidence",)
                    )
                )
        outcomes.append(
            {
                "network": feature.network,
                "station_code": feature.station.code,
                "polygon": feature.polygon_name,
                "proposed_hru": feature.proposed_hru,
                "gauge_id": feature.gauge_id,
                "original_coordinates": asdict(feature.station.original),
                "outcome": "held" if holds else "eligible_for_preparation",
                "holds": sorted(
                    {_coverage_label(hold) or "unknown_hold" for hold in holds}
                ),
                "verified_chunks": verified,
                "coverage": summaries,
            }
        )
    return {
        "meaning": (
            "Gateway source coverage only; not persistence, "
            "model readiness or activation"
        ),
        "source_fingerprint": state.handover.source_fingerprint,
        "derived_fingerprint": state.handover.derived_fingerprint,
        "proposal_revision": revision,
        "stations": outcomes,
        "subscription_diagnostics": _subscription_diagnostics(state),
    }


def _snapshot(requirements: ModelDataRequirements | None) -> RequirementSnapshot | None:
    if requirements is None:
        return None
    return RequirementSnapshot(
        target_parameters=tuple(sorted(requirements.target_parameters)),
        past_dynamic_features=tuple(sorted(requirements.past_dynamic_features)),
        future_dynamic_features=tuple(sorted(requirements.future_dynamic_features)),
        static_features=tuple(sorted(requirements.static_features)),
        supported_time_steps=tuple(sorted(requirements.supported_time_steps)),
        lookback_steps=requirements.lookback_steps,
        forecast_horizon_steps=requirements.forecast_horizon_steps,
        spatial_input_type=requirements.spatial_input_type.value,
        ensemble_mode=requirements.ensemble_mode.value,
        required_past_targets=None
        if requirements.required_past_targets is None
        else tuple(sorted(requirements.required_past_targets)),
        declared_lookbacks=tuple(sorted(requirements.declared_lookbacks)),
        declared_aggregations=tuple(
            sorted(
                (name, value.value)
                for name, value in requirements.declared_aggregations
            )
        ),
        declared_horizon_semantics=requirements.declared_horizon_semantics,
        declared_min_future_steps=requirements.declared_min_future_steps,
    )
