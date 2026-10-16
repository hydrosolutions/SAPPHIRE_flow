from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.adapters.nepal_local_day import nepal_day_start
from sapphire_flow.services.nepal21_delivery_authority import (
    NEPAL21_STATION_CODES,
    AttributionState,
    GenerationBuildInput,
    RestrictedReplacementPayload,
    canonical_json_bytes,
    origin_identity_set_sha256,
    sha256_hex_bytes,
    station_set_sha256,
)
from sapphire_flow.services.nepal21_source_catalog import SourceOccurrence
from sapphire_flow.services.rating_conversion import RatingConverter
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.ids import RatingCurveId, StationId, TenantId
from sapphire_flow.types.rating_curve import RatingCurve

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from uuid import UUID

    from sapphire_flow.services.nepal21_source_catalog import (
        SourceCatalog,
        SourceDailyRecord,
        SourceDeclaration,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceStationBinding:
    station_code: str
    station_id: StationId
    tenant_id: TenantId
    highest_surviving_version: int

    def __post_init__(self) -> None:
        if self.station_code not in NEPAL21_STATION_CODES:
            raise ValueError("station binding is outside Nepal21 roster")
        if self.highest_surviving_version < 0:
            raise ValueError("highest surviving version must be nonnegative")


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceAttribution:
    state: AttributionState
    declaration: SourceDeclaration | None

    def __post_init__(self) -> None:
        if (self.state == "selected") != (self.declaration is not None):
            raise ValueError("only selected attribution may bind a declaration")


def select_source_declaration(
    record: SourceDailyRecord, declarations: Sequence[SourceDeclaration]
) -> SourceAttribution:
    station = [
        item for item in declarations if item.station_code == record.station_code
    ]
    covering = [
        item
        for item in station
        if item.block.from_date <= record.value.day <= item.block.to_date
    ]
    if not covering:
        return SourceAttribution(state="held_missing_curve", declaration=None)
    latest_start = max(item.block.from_date for item in covering)
    latest = [item for item in station if item.block.from_date == latest_start]
    if len({item.effective_content for item in latest}) > 1:
        return SourceAttribution(state="held_equal_start_tie", declaration=None)
    usable = [
        item
        for item in latest
        if item.effective_content is not None and item.block.to_date >= record.value.day
    ]
    if not usable:
        return SourceAttribution(state="held_invalid_latest", declaration=None)
    return SourceAttribution(
        state="selected", declaration=min(usable, key=lambda item: item.digest)
    )


def _native_curve(
    declaration: SourceDeclaration,
    binding: SourceStationBinding,
    build: GenerationBuildInput,
    version: int,
    curve_id: RatingCurveId,
) -> tuple[RatingCurve, list[dict[str, float]]]:
    points: list[dict[str, float]] = [
        {
            "water_level": 0.0 if p.stage_m == 0 else p.stage_m,
            "discharge": 0.0 if p.discharge_m3s == 0 else p.discharge_m3s,
        }
        for p in sorted(set(declaration.block.points), key=lambda item: item.stage_m)
    ]
    curve = RatingCurve(
        id=curve_id,
        station_id=binding.station_id,
        version=version,
        valid_from=nepal_day_start(declaration.block.from_date),
        valid_to=nepal_day_start(declaration.block.to_date + timedelta(days=1)),
        points=points,
        interpolation=InterpolationMethod.LINEAR,
        uploaded_by=None,
        created_at=build.created_at,
        delivery_id=build.delivery_id,
        rating_type_label=declaration.block.rating_type_label,
    )
    RatingConverter.from_curve(curve)
    return curve, points


def _origin_set(
    occurrences: tuple[SourceOccurrence, ...],
    payload_digest: str,
    build: GenerationBuildInput,
    id_factory: Callable[[], UUID],
    origins: list[dict[str, object]],
    sets: list[dict[str, object]],
    members: list[dict[str, object]],
) -> str:
    identities: list[dict[str, object]] = []
    common: dict[str, object] = {
        "generation_id": build.id,
        "tenant_id": build.tenant_id,
    }
    for ordinal, occurrence in enumerate(occurrences):
        source = occurrence.source
        identity: dict[str, object] = {
            "origin_id": id_factory(),
            "origin_kind": source.kind,
            "source_root_label": source.source_root_label,
            "relative_path": source.relative_path,
            "file_sha256": source.expected_sha256,
            "parser_version": "hash-only-v1"
            if source.kind == "GHT_HASH_ONLY"
            else "dhm-files-v1",
            "row_or_block_ordinal": occurrence.ordinal,
            "origin_payload_sha256": payload_digest,
            "member_ordinal": ordinal,
        }
        identities.append(identity)
        origins.append(
            {
                **common,
                "cohort": "nepal21",
                "id": identity["origin_id"],
                **{
                    key: value
                    for key, value in identity.items()
                    if key not in {"origin_id", "member_ordinal"}
                },
            }
        )
    kind = {
        "RT": "RT_DECLARATION",
        "DFL": "DFL_RECORD",
        "GHT_HASH_ONLY": "GHT_HASH_ONLY",
    }[occurrences[0].source.kind]
    digest = origin_identity_set_sha256(identities, set_kind=kind)
    sets.append(
        {
            **common,
            "origin_set_sha256": digest,
            "member_count": len(identities),
            "set_kind": kind,
        }
    )
    members.extend(
        {
            **common,
            "origin_set_sha256": digest,
            "origin_id": item["origin_id"],
            "origin_kind": item["origin_kind"],
            "member_ordinal": item["member_ordinal"],
        }
        for item in identities
    )
    return digest


def build_nepal21_replacement_payload(
    catalog: SourceCatalog,
    build: GenerationBuildInput,
    bindings: Sequence[SourceStationBinding],
    *,
    preimage_dir: Path,
    id_factory: Callable[[], UUID],
) -> RestrictedReplacementPayload:
    if (
        len(bindings) != 21
        or {item.station_code for item in bindings} != set(NEPAL21_STATION_CODES)
        or len({item.station_id for item in bindings}) != 21
        or any(item.tenant_id != build.tenant_id for item in bindings)
    ):
        raise ValueError(
            "payload requires exact21 unique station bindings in generation tenant"
        )
    if (
        build.manifest_sha256 != catalog.manifest.sha256
        or build.delivery_id != f"dhm-nepal21-{catalog.manifest.sha256[:16]}"
        or build.station_set_sha256 != station_set_sha256()
    ):
        raise ValueError("generation pins differ from parsed source catalog")
    policy_digest = (
        catalog.canonicalization_policy.witness.sha256
        if catalog.canonicalization_policy is not None
        else None
    )
    if build.canonicalization_policy_sha256 != policy_digest:
        raise ValueError("generation canonicalization pin differs from source catalog")
    by_code = {item.station_code: item for item in bindings}
    versions = {item.station_code: item.highest_surviving_version for item in bindings}
    curves: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    origins: list[dict[str, object]] = []
    sets: list[dict[str, object]] = []
    members: list[dict[str, object]] = []
    declarations: list[dict[str, object]] = []
    attributions: list[dict[str, object]] = []
    declaration_ids: dict[tuple[str, str], UUID] = {}
    curve_ids: dict[tuple[str, str], RatingCurveId] = {}
    common: dict[str, object] = {
        "generation_id": build.id,
        "tenant_id": build.tenant_id,
        "cohort": "nepal21",
    }
    for declaration in sorted(
        catalog.declarations,
        key=lambda item: (item.station_code, item.block.from_date, item.digest),
    ):
        binding = by_code[declaration.station_code]
        key = (declaration.station_code, declaration.digest)
        declaration_ids[key] = id_factory()
        native: dict[str, object] | None = None
        if declaration.effective_content is not None:
            versions[declaration.station_code] += 1
            curve_id = RatingCurveId(id_factory())
            curve_ids[key] = curve_id
            curve, points = _native_curve(
                declaration,
                binding,
                build,
                versions[declaration.station_code],
                curve_id,
            )
            assert curve.valid_to is not None
            native = {
                "points": points,
                "interpolation": "linear",
                "rating_type_label": curve.rating_type_label,
                "valid_from": curve.valid_from.isoformat(),
                "valid_to": curve.valid_to.isoformat(),
            }
            curves.append(
                {
                    "id": curve.id,
                    "station_id": curve.station_id,
                    "version": curve.version,
                    "valid_from": curve.valid_from,
                    "valid_to": curve.valid_to,
                    "points": points,
                    "interpolation": "linear",
                    "uploaded_by": None,
                    "created_at": build.created_at,
                    "delivery_id": build.delivery_id,
                    "rating_type_label": curve.rating_type_label,
                }
            )
        origin_set = _origin_set(
            declaration.occurrences,
            declaration.digest,
            build,
            id_factory,
            origins,
            sets,
            members,
        )
        declarations.append(
            {
                **common,
                "id": declaration_ids[key],
                "station_id": binding.station_id,
                "station_code": declaration.station_code,
                "from_local_date": declaration.block.from_date,
                "to_local_date_inclusive": declaration.block.to_date,
                "rating_type_label": declaration.block.rating_type_label,
                "from_stage_m": declaration.block.from_stage_m,
                "raw_declaration_payload_json": json.loads(declaration.raw_content),
                "declaration_content_sha256": declaration.digest,
                "function_payload_json": json.loads(declaration.effective_content)
                if declaration.effective_content is not None
                else None,
                "function_equivalence_sha256": sha256_hex_bytes(
                    declaration.effective_content
                )
                if declaration.effective_content is not None
                else None,
                "native_curve_payload_json": native,
                "shape_state": declaration.shape,
                "canonicalization_approval_id": declaration.approval.approval_id
                if declaration.approval
                else None,
                "canonicalization_approval_sha256": declaration.approval.approval_sha256
                if declaration.approval
                else None,
                "usable_rating_curve_id": curve_ids.get(key),
                "origin_set_sha256": origin_set,
            }
        )
    for record in catalog.daily_records:
        station_id = by_code[record.station_code].station_id
        selection = select_source_declaration(record, catalog.declarations)
        selected_key = (
            (selection.declaration.station_code, selection.declaration.digest)
            if selection.declaration
            else None
        )
        curve_id = curve_ids[selected_key] if selected_key is not None else None
        correction_version = (
            selection.declaration.block.rating_type_label
            if selection.declaration
            else None
        )
        observation_id = id_factory()
        q = 0.0 if record.value.discharge_m3s == 0 else record.value.discharge_m3s
        digest = sha256_hex_bytes(
            canonical_json_bytes(
                {
                    "station_code": record.station_code,
                    "local_date": record.value.day.isoformat(),
                    "reported_q": q,
                }
            )
        )
        origin_set = _origin_set(
            record.occurrences, digest, build, id_factory, origins, sets, members
        )
        observations.append(
            {
                "id": observation_id,
                "station_id": station_id,
                "timestamp": nepal_day_start(record.value.day),
                "parameter": "discharge",
                "value": q,
                "source": "manual_import",
                "delivery_id": build.delivery_id,
                "rating_curve_id": curve_id,
                "rating_curve_correction_version": correction_version,
                "qc_status": "raw",
                "qc_flags": [],
                "qc_rule_version": None,
                "created_at": build.created_at,
            }
        )
        attributions.append(
            {
                **common,
                "observation_id": observation_id,
                "station_id": station_id,
                "station_code": record.station_code,
                "origin_set_sha256": origin_set,
                "rating_declaration_id": declaration_ids[selected_key]
                if selected_key is not None
                else None,
                "rating_curve_id": curve_id,
                "attribution_state": selection.state,
                "local_date": record.value.day,
                "reported_q": q,
            }
        )
    for source in catalog.hash_only_sources:
        _origin_set(
            (SourceOccurrence(source=source, ordinal=None),),
            source.expected_sha256,
            build,
            id_factory,
            origins,
            sets,
            members,
        )
    return RestrictedReplacementPayload(
        preimage_dir=preimage_dir,
        rating_curve_rows=curves,
        observation_rows=observations,
        origin_rows=origins,
        origin_set_rows=sets,
        origin_member_rows=members,
        declaration_rows=declarations,
        attribution_rows=attributions,
    )
