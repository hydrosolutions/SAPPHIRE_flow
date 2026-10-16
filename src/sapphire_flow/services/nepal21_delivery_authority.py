from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog

from sapphire_flow.db.metadata import (
    dhm_delivery_observation_attributions,
    dhm_delivery_origins,
    dhm_origin_set_members,
    dhm_origin_sets,
    dhm_rating_declarations,
    expired_rating_test_permissions,
    rating_curves,
    restricted_delivery_generations,
    restricted_delivery_release_witnesses,
    restricted_delivery_releases,
    stations,
)
from sapphire_flow.db.metadata import (
    observations as observations_table,
)
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.store._helpers import require_real_transaction
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.domain import (
    QcFlag,
    QcRuleSet,
    StationQcOverride,
    aggregate_qc_status,
)
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId
from sapphire_flow.types.observation import Observation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from sapphire_flow.adapters.dhm_files import RatingTableBlock
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ClimBaseline
    from sapphire_flow.types.ids import TenantId

log = structlog.get_logger(__name__)

NEPAL21_COHORT = "nepal21"
NEPAL21_NETWORK = "dhm"
NEPAL21_STATION_CODES: tuple[str, ...] = (
    "225",
    "280",
    "350",
    "404.7",
    "439.7",
    "440",
    "445",
    "447",
    "450",
    "550.05",
    "602",
    "602.5",
    "604.5",
    "610",
    "620",
    "647",
    "650",
    "652",
    "670",
    "684",
    "695",
)
OLD6_STATION_CODES = frozenset({"447", "450", "604.5", "647", "670", "684"})

_REQUIRED_PREDECESSOR_FIELDS: dict[str, frozenset[str]] = {
    "observation": frozenset(
        {
            "kind",
            "id",
            "station_id",
            "timestamp",
            "parameter",
            "source",
            "value",
            "delivery_id",
            "created_at",
            "qc_status",
            "qc_flags",
            "qc_rule_version",
            "rating_curve_id",
            "rating_curve_correction_version",
        }
    ),
    "rating_curve": frozenset(
        {
            "kind",
            "id",
            "station_id",
            "version",
            "valid_from",
            "valid_to",
            "points",
            "interpolation",
            "uploaded_by",
            "created_at",
            "delivery_id",
            "rating_type_label",
        }
    ),
}
MAX_WITNESS_BYTES = 10 * 1024 * 1024
CANONICAL_JSON_VERSION = "dhm21-canonical-json-v1"

ShapeState = Literal[
    "raw_usable",
    "duplicate_point_needs_approval",
    "canonicalized_usable",
    "decreasing_invalid",
]
AttributionState = Literal[
    "selected",
    "held_missing_curve",
    "held_invalid_latest",
    "held_equal_start_tie",
    "held_native_mismatch",
    "not_applicable",
]
WitnessKind = Literal["manifest_catalog", "canonicalization_policy"]


class RestrictedDeliveryState(Enum):
    BUILDING = "building"
    ACTIVE_PENDING_QC = "active_pending_qc"
    ACTIVE_QC_COMPLETE = "active_qc_complete"
    ABORTED = "aborted"


@dataclass(frozen=True, kw_only=True, slots=True)
class WitnessBytes:
    kind: WitnessKind
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, kw_only=True, slots=True)
class RestrictedReleaseInput:
    id: UUID
    tenant_id: TenantId
    manifest_catalog: WitnessBytes
    station_set_sha256: str
    expected_predecessor_semantic_sha256: str
    canonicalization_policy: WitnessBytes | None
    created_at: UtcDatetime
    created_by_run_id: UUID | None = None

    @property
    def allowed_new_delivery_id(self) -> str:
        return f"dhm-nepal21-{self.manifest_catalog.sha256[:16]}"


@dataclass(frozen=True, kw_only=True, slots=True)
class GenerationBuildInput:
    id: UUID
    release_id: UUID
    tenant_id: TenantId
    delivery_id: str
    predecessor_semantic_sha256: str
    preimage_sha256: str
    manifest_sha256: str
    station_set_sha256: str
    canonicalization_policy_sha256: str | None
    created_at: UtcDatetime
    created_by_run_id: UUID | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ExpectedQcPolicyPin:
    policy_sha256: str
    rule_set: QcRuleSet
    station_networks: dict[StationId, str]
    overrides: list[StationQcOverride]
    baselines: list[ClimBaseline]

    @property
    def content_bytes(self) -> bytes:
        return canonical_json_bytes(
            _semantic_value(
                {
                    "codec": "dhm21-qc-policy-v1",
                    "rule_set": asdict(self.rule_set),
                    "station_networks": self.station_networks,
                    "overrides": [asdict(item) for item in self.overrides],
                    "baselines": [asdict(item) for item in self.baselines],
                }
            )
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RestrictedReplacementPayload:
    preimage_dir: Path
    rating_curve_rows: list[dict[str, object]]
    observation_rows: list[dict[str, object]]
    origin_rows: list[dict[str, object]]
    origin_set_rows: list[dict[str, object]]
    origin_member_rows: list[dict[str, object]]
    declaration_rows: list[dict[str, object]]
    attribution_rows: list[dict[str, object]]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_sha256(value: str, label: str) -> None:
    if not __import__("re").fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _finite_number(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if value == 0:
        return 0.0
    return value


def station_set_sha256(codes: tuple[str, ...] = NEPAL21_STATION_CODES) -> str:
    return sha256_hex_bytes(canonical_json_bytes({"station_codes": sorted(codes)}))


def origin_set_sha256(origin_payload_hashes: list[str], *, set_kind: str) -> str:
    for digest in origin_payload_hashes:
        _require_sha256(digest, "origin payload hash")
    return sha256_hex_bytes(
        canonical_json_bytes(
            {"kind": set_kind, "members": sorted(origin_payload_hashes)}
        )
    )


def origin_identity_set_sha256(
    origins: Sequence[Mapping[str, object]], *, set_kind: str
) -> str:
    parts: list[str] = []
    for origin in sorted(origins, key=lambda item: int(str(item["member_ordinal"]))):
        payload_hash = str(origin["origin_payload_sha256"])
        file_hash = str(origin["file_sha256"])
        _require_sha256(payload_hash, "origin payload hash")
        _require_sha256(file_hash, "origin file hash")
        parts.append(
            ":".join(
                [
                    str(origin["member_ordinal"]),
                    str(origin["origin_id"]),
                    str(origin["origin_kind"]),
                    str(origin["source_root_label"]),
                    str(origin["relative_path"]),
                    file_hash,
                    str(origin["parser_version"]),
                    ""
                    if origin.get("row_or_block_ordinal") is None
                    else str(origin["row_or_block_ordinal"]),
                    payload_hash,
                ]
            )
        )
    return sha256_hex_bytes((set_kind + "|" + "|".join(parts)).encode("utf-8"))


def rating_raw_payload(station_code: str, block: RatingTableBlock) -> dict[str, object]:
    return {
        "codec": CANONICAL_JSON_VERSION,
        "station_code": station_code,
        "rating_type_label": block.rating_type_label,
        "from_local_date": block.from_date.isoformat(),
        "to_local_date_inclusive": block.to_date.isoformat(),
        "from_stage_m": _finite_number(block.from_stage_m, "from_stage_m"),
        "points": [
            {
                "stage_m": _finite_number(point.stage_m, "stage_m"),
                "discharge_m3s": _finite_number(point.discharge_m3s, "discharge_m3s"),
            }
            for point in block.points
        ],
    }


def declaration_digest(station_code: str, block: RatingTableBlock) -> str:
    return sha256_hex_bytes(
        canonical_json_bytes(rating_raw_payload(station_code, block))
    )


def classify_rating_block(
    block: RatingTableBlock, *, canonicalization_approved: bool = False
) -> ShapeState:
    if any(
        not math.isfinite(point.stage_m) or not math.isfinite(point.discharge_m3s)
        for point in block.points
    ):
        raise ValueError("rating points must be finite")
    by_stage = sorted(
        ((point.stage_m, point.discharge_m3s) for point in block.points),
        key=lambda item: item[0],
    )
    grouped: dict[float, set[float]] = {}
    for stage, discharge in by_stage:
        grouped.setdefault(stage, set()).add(discharge)
    if any(len(values) > 1 for values in grouped.values()):
        return "decreasing_invalid"
    sorted_discharge = [next(iter(grouped[stage])) for stage in sorted(grouped)]
    if any(b < a for a, b in zip(sorted_discharge, sorted_discharge[1:], strict=False)):
        return "decreasing_invalid"
    if len(grouped) != len(block.points):
        return (
            "canonicalized_usable"
            if canonicalization_approved
            else "duplicate_point_needs_approval"
        )
    return "raw_usable"


def function_payload(
    station_code: str,
    block: RatingTableBlock,
    *,
    canonicalization_approved: bool = False,
) -> dict[str, object] | None:
    shape = classify_rating_block(
        block, canonicalization_approved=canonicalization_approved
    )
    if shape in {"decreasing_invalid", "duplicate_point_needs_approval"}:
        return None
    by_stage: dict[float, float] = {}
    for point in block.points:
        prior = by_stage.get(point.stage_m)
        if prior is not None and prior != point.discharge_m3s:
            return None
        by_stage[point.stage_m] = point.discharge_m3s
    return {
        "codec": CANONICAL_JSON_VERSION,
        "station_code": station_code,
        "interpolation": "linear",
        "stage_unit": "m",
        "discharge_unit": "m3/s",
        "reference": "delivered_stage",
        "points": [
            {
                "stage_m": _finite_number(stage, "stage_m"),
                "discharge_m3s": _finite_number(by_stage[stage], "discharge_m3s"),
            }
            for stage in sorted(by_stage)
        ],
    }


def _semantic_value(value: object) -> object:
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, float):
        return _finite_number(value, "predecessor numeric value")
    if isinstance(value, list):
        sequence = cast("list[object]", value)
        return [_semantic_value(item) for item in sequence]
    if isinstance(value, tuple):
        sequence_tuple = cast("tuple[object, ...]", value)
        return [_semantic_value(item) for item in sequence_tuple]
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        return {
            str(key): _semantic_value(item) for key, item in sorted(mapping.items())
        }
    return value


def _normalize_predecessor_row(row: Mapping[str, object]) -> dict[str, object]:
    kind = row.get("kind")
    if kind not in _REQUIRED_PREDECESSOR_FIELDS:
        raise ValueError("predecessor row kind must be observation or rating_curve")
    required = _REQUIRED_PREDECESSOR_FIELDS[str(kind)]
    missing = sorted(required - set(row))
    if missing:
        raise ValueError(f"predecessor {kind} row missing required fields: {missing}")
    return {key: _semantic_value(row[key]) for key in sorted(required)}


def predecessor_semantic_digest(rows: Sequence[Mapping[str, object]]) -> str:
    normalized = [_normalize_predecessor_row(row) for row in rows]
    return sha256_hex_bytes(
        canonical_json_bytes(
            {
                "codec": "dhm21-predecessor-semantic-v3",
                "rows": sorted(normalized, key=lambda item: canonical_json_bytes(item)),
            }
        )
    )


def validate_witness_bytes(witness: WitnessBytes, *, expected_sha256: str) -> None:
    _require_sha256(expected_sha256, "expected witness hash")
    if not witness.content:
        raise ValueError("witness bytes must not be empty")
    if len(witness.content) > MAX_WITNESS_BYTES:
        raise ValueError("witness bytes exceed finite size cap")
    if witness.sha256 != expected_sha256:
        raise ValueError("witness bytes do not match expected hash")
    raw = json.loads(witness.content.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("witness must be a JSON object")
    _reject_duplicate_json_keys(witness.content)


def _reject_duplicate_json_keys(content: bytes) -> None:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"witness contains duplicate JSON key {key!r}")
            seen.add(key)
            out[key] = value
        return out

    json.loads(content.decode("utf-8"), object_pairs_hook=no_duplicates)


def verify_manifest_catalog(manifest_catalog: WitnessBytes) -> dict[str, Any]:
    validate_witness_bytes(manifest_catalog, expected_sha256=manifest_catalog.sha256)
    catalog_raw = json.loads(manifest_catalog.content.decode("utf-8"))
    if not isinstance(catalog_raw, dict):
        raise ValueError("manifest catalog must be an object")
    catalog = cast("dict[str, object]", catalog_raw)
    codes = catalog.get("station_codes")
    if codes != list(NEPAL21_STATION_CODES):
        raise ValueError("manifest catalog station scope must equal Nepal21 roster")
    if "rating_declarations" not in catalog:
        raise ValueError("manifest catalog declarations must be explicit")
    declarations_raw = catalog["rating_declarations"]
    if not isinstance(declarations_raw, list):
        raise ValueError("manifest catalog declarations must be a list")
    declarations = cast("list[object]", declarations_raw)
    keys: set[tuple[str, str]] = set()
    for item_raw in declarations:
        if not isinstance(item_raw, dict):
            raise ValueError("manifest declaration must be an object")
        item = cast("dict[str, object]", item_raw)
        station_code = item.get("station_code")
        digest = item.get("declaration_content_sha256")
        if (
            not isinstance(station_code, str)
            or station_code not in NEPAL21_STATION_CODES
        ):
            raise ValueError("manifest declaration station is outside roster")
        if not isinstance(digest, str):
            raise ValueError("manifest declaration digest is missing")
        _require_sha256(digest, "manifest declaration digest")
        payload = item.get("raw_declaration_payload_json")
        if payload is None:
            raise ValueError("manifest declaration raw payload binding is missing")
        if sha256_hex_bytes(canonical_json_bytes(payload)) != digest:
            raise ValueError("manifest declaration raw payload digest mismatch")
        key = (station_code, digest)
        if key in keys:
            raise ValueError("manifest catalog contains duplicate declaration identity")
        keys.add(key)
    return catalog


def insert_restricted_release(
    conn: sa.Connection, release: RestrictedReleaseInput
) -> None:
    validate_witness_bytes(
        release.manifest_catalog, expected_sha256=release.manifest_catalog.sha256
    )
    if release.canonicalization_policy is not None:
        validate_witness_bytes(
            release.canonicalization_policy,
            expected_sha256=release.canonicalization_policy.sha256,
        )
    conn.execute(
        sa.insert(restricted_delivery_releases).values(
            id=release.id,
            tenant_id=release.tenant_id,
            network=NEPAL21_NETWORK,
            cohort=NEPAL21_COHORT,
            manifest_sha256=release.manifest_catalog.sha256,
            station_set_sha256=release.station_set_sha256,
            canonicalization_policy_sha256=(
                release.canonicalization_policy.sha256
                if release.canonicalization_policy
                else None
            ),
            expected_predecessor_delivery_id=DELIVERY_ID,
            expected_predecessor_semantic_sha256=release.expected_predecessor_semantic_sha256,
            allowed_new_delivery_id=release.allowed_new_delivery_id,
            status="authorized",
            created_at=release.created_at,
            created_by_run_id=release.created_by_run_id,
        )
    )
    _insert_witness(
        conn, release.id, release.manifest_catalog, created_at=release.created_at
    )
    if release.canonicalization_policy is not None:
        _insert_witness(
            conn,
            release.id,
            release.canonicalization_policy,
            created_at=release.created_at,
        )


def _insert_witness(
    conn: sa.Connection,
    release_id: UUID,
    witness: WitnessBytes,
    *,
    created_at: UtcDatetime,
) -> None:
    conn.execute(
        sa.insert(restricted_delivery_release_witnesses).values(
            release_id=release_id,
            witness_kind=witness.kind,
            content_sha256=witness.sha256,
            content_bytes=witness.content,
            created_at=created_at,
        )
    )


def fetch_release_witness(
    conn: sa.Connection, release_id: UUID, kind: WitnessKind
) -> WitnessBytes:
    row = (
        conn.execute(
            sa.select(restricted_delivery_release_witnesses).where(
                restricted_delivery_release_witnesses.c.release_id == release_id,
                restricted_delivery_release_witnesses.c.witness_kind == kind,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"missing {kind} witness")
    content = bytes(row["content_bytes"])
    witness = WitnessBytes(kind=kind, content=content)
    if witness.sha256 != row["content_sha256"]:
        raise ValueError(f"stored {kind} witness hash mismatch")
    return witness


_PREDECESSOR_OBSERVATION_COLUMNS = tuple(
    sorted(_REQUIRED_PREDECESSOR_FIELDS["observation"] - {"kind"})
)
_PREDECESSOR_CURVE_COLUMNS = tuple(
    sorted(_REQUIRED_PREDECESSOR_FIELDS["rating_curve"] - {"kind"})
)


def fetch_predecessor_semantic_rows(
    conn: sa.Connection, tenant_id: TenantId
) -> list[dict[str, object]]:
    station_scope = (
        sa.select(stations.c.id)
        .where(stations.c.tenant_id == tenant_id)
        .where(stations.c.network == NEPAL21_NETWORK)
        .where(stations.c.code.in_(sorted(OLD6_STATION_CODES)))
    )
    observation_rows: list[dict[str, object]] = [
        {"kind": "observation", **cast("dict[str, object]", dict(row))}
        for row in conn.execute(
            sa.select(
                *(
                    getattr(observations_table.c, name)
                    for name in _PREDECESSOR_OBSERVATION_COLUMNS
                )
            )
            .where(observations_table.c.delivery_id == DELIVERY_ID)
            .where(observations_table.c.station_id.in_(station_scope))
        ).mappings()
    ]
    curve_rows: list[dict[str, object]] = [
        {"kind": "rating_curve", **cast("dict[str, object]", dict(row))}
        for row in conn.execute(
            sa.select(
                *(getattr(rating_curves.c, name) for name in _PREDECESSOR_CURVE_COLUMNS)
            )
            .where(rating_curves.c.delivery_id == DELIVERY_ID)
            .where(rating_curves.c.station_id.in_(station_scope))
        ).mappings()
    ]
    return [*observation_rows, *curve_rows]


def write_predecessor_preimage(
    conn: sa.Connection, *, tenant_id: TenantId, generation_id: UUID, preimage_dir: Path
) -> str:
    rows = fetch_predecessor_semantic_rows(conn, tenant_id)
    content = canonical_json_bytes(
        {
            "codec": "dhm21-preimage-v1",
            "generation_id": str(generation_id),
            "predecessor_delivery_id": DELIVERY_ID,
            "rows": [_normalize_predecessor_row(row) for row in rows],
        }
    )
    digest = sha256_hex_bytes(content)
    preimage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = preimage_dir / f"{generation_id}-{digest}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with suppress(FileNotFoundError):
            path.unlink()
        raise
    dir_fd = os.open(preimage_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    if path.read_bytes() != content:
        raise ValueError("preimage read-back differs from written bytes")
    return digest


def _insert_batch(
    conn: sa.Connection,
    table: sa.Table,
    rows: list[dict[str, object]],
    *,
    batch_size: int,
) -> None:
    for offset in range(0, len(rows), batch_size):
        conn.execute(sa.insert(table), rows[offset : offset + batch_size])


def _delete_predecessor_rows(conn: sa.Connection, tenant_id: TenantId) -> None:
    station_scope = (
        sa.select(stations.c.id)
        .where(stations.c.tenant_id == tenant_id)
        .where(stations.c.network == NEPAL21_NETWORK)
        .where(stations.c.code.in_(sorted(OLD6_STATION_CODES)))
    )
    conn.execute(
        sa.delete(observations_table)
        .where(observations_table.c.delivery_id == DELIVERY_ID)
        .where(observations_table.c.station_id.in_(station_scope))
    )
    conn.execute(
        sa.delete(rating_curves)
        .where(rating_curves.c.delivery_id == DELIVERY_ID)
        .where(rating_curves.c.station_id.in_(station_scope))
    )


def validate_generation_manifest_catalog(
    conn: sa.Connection, generation_id: UUID
) -> None:
    generation = (
        conn.execute(
            sa.select(restricted_delivery_generations).where(
                restricted_delivery_generations.c.id == generation_id
            )
        )
        .mappings()
        .one()
    )
    manifest = verify_manifest_catalog(
        fetch_release_witness(conn, generation["release_id"], "manifest_catalog")
    )
    expected = {
        (item["station_code"], item["declaration_content_sha256"])
        for item in manifest["rating_declarations"]
    }
    actual = set(
        conn.execute(
            sa.select(
                dhm_rating_declarations.c.station_code,
                dhm_rating_declarations.c.declaration_content_sha256,
            ).where(dhm_rating_declarations.c.generation_id == generation_id)
        ).all()
    )
    if actual != expected:
        raise ValueError("persisted rating declarations differ from manifest witness")


def populate_restricted_generation_payload(
    conn: sa.Connection,
    *,
    generation_id: UUID,
    tenant_id: TenantId,
    payload: RestrictedReplacementPayload,
) -> str:
    require_real_transaction(conn, caller="populate_restricted_generation_payload")
    preimage_sha256 = write_predecessor_preimage(
        conn,
        tenant_id=tenant_id,
        generation_id=generation_id,
        preimage_dir=payload.preimage_dir,
    )
    _delete_predecessor_rows(conn, tenant_id)
    _insert_batch(conn, rating_curves, payload.rating_curve_rows, batch_size=500)
    _insert_batch(conn, observations_table, payload.observation_rows, batch_size=5000)
    _insert_batch(conn, dhm_delivery_origins, payload.origin_rows, batch_size=500)
    _insert_batch(conn, dhm_origin_sets, payload.origin_set_rows, batch_size=500)
    _insert_batch(
        conn, dhm_origin_set_members, payload.origin_member_rows, batch_size=5000
    )
    _insert_batch(
        conn, dhm_rating_declarations, payload.declaration_rows, batch_size=500
    )
    _insert_batch(
        conn,
        dhm_delivery_observation_attributions,
        payload.attribution_rows,
        batch_size=5000,
    )
    validate_generation_manifest_catalog(conn, generation_id)
    return preimage_sha256


def _assert_replay_rows_match(
    conn: sa.Connection,
    *,
    table: sa.Table,
    key_columns: Sequence[str],
    expected_rows: Sequence[Mapping[str, object]],
    where: sa.ColumnElement[bool],
    label: str,
    ignored_columns: frozenset[str] = frozenset(),
) -> None:
    actual = conn.execute(sa.select(table).where(where)).mappings().all()
    actual_by_key = {
        tuple(row[column] for column in key_columns): row for row in actual
    }
    expected_by_key = {
        tuple(row[column] for column in key_columns): row for row in expected_rows
    }
    if set(actual_by_key) != set(expected_by_key):
        raise ValueError(f"existing restricted generation {label} replay mismatch")
    for key, expected in expected_by_key.items():
        actual_row = actual_by_key[key]
        mismatches = [
            column
            for column, value in expected.items()
            if column not in ignored_columns and actual_row[column] != value
        ]
        if mismatches:
            raise ValueError(
                f"existing restricted generation {label} replay mismatch: {mismatches}"
            )


def _return_if_existing_generation_replay_matches(
    conn: sa.Connection,
    build: GenerationBuildInput,
    payload: RestrictedReplacementPayload | None,
) -> bool:
    existing = (
        conn.execute(
            sa.select(restricted_delivery_generations).where(
                restricted_delivery_generations.c.id == build.id
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        return False
    allowed_states = {
        RestrictedDeliveryState.ACTIVE_PENDING_QC.value,
        RestrictedDeliveryState.ACTIVE_QC_COMPLETE.value,
    }
    if existing["state"] not in allowed_states:
        raise ValueError("existing restricted generation is not replayable")
    expected = {
        "release_id": build.release_id,
        "tenant_id": build.tenant_id,
        "delivery_id": build.delivery_id,
        "predecessor_delivery_id": DELIVERY_ID,
        "predecessor_semantic_sha256": build.predecessor_semantic_sha256,
        "preimage_sha256": build.preimage_sha256,
        "manifest_sha256": build.manifest_sha256,
        "station_set_sha256": build.station_set_sha256,
        "canonicalization_policy_sha256": build.canonicalization_policy_sha256,
    }
    mismatches = [key for key, value in expected.items() if existing[key] != value]
    if mismatches:
        raise ValueError(
            f"existing restricted generation replay mismatch: {mismatches}"
        )
    conn.execute(
        sa.text("SELECT public.sf_lock_dhm21_delivery_write(:tenant, :release)"),
        {"tenant": build.tenant_id, "release": build.release_id},
    )
    existing = (
        conn.execute(
            sa.select(restricted_delivery_generations).where(
                restricted_delivery_generations.c.id == build.id
            )
        )
        .mappings()
        .one()
    )
    validate_generation_manifest_catalog(conn, build.id)
    if (
        readback_generation_content_digest(conn, build.id)
        != existing["immutable_content_sha256"]
    ):
        raise ValueError("existing generation immutable content readback mismatch")
    if (
        existing["state"] == RestrictedDeliveryState.ACTIVE_QC_COMPLETE.value
        and readback_qc_completion_digest(
            conn,
            generation_id=build.id,
            policy_sha256=existing["qc_policy_sha256"],
            completed_at=existing["qc_completed_at"],
            run_id=existing["qc_run_id"],
        )
        != existing["qc_completion_sha256"]
    ):
        raise ValueError("existing generation QC completion readback mismatch")
    if payload is not None:
        _assert_replay_rows_match(
            conn,
            table=observations_table,
            key_columns=("id",),
            expected_rows=payload.observation_rows,
            where=observations_table.c.delivery_id == build.delivery_id,
            label="observation",
            ignored_columns=frozenset({"qc_status", "qc_flags", "qc_rule_version"}),
        )
        _assert_replay_rows_match(
            conn,
            table=rating_curves,
            key_columns=("id",),
            expected_rows=payload.rating_curve_rows,
            where=rating_curves.c.delivery_id == build.delivery_id,
            label="curve",
        )
        _assert_replay_rows_match(
            conn,
            table=dhm_delivery_origins,
            key_columns=("id",),
            expected_rows=payload.origin_rows,
            where=dhm_delivery_origins.c.generation_id == build.id,
            label="origin",
        )
        _assert_replay_rows_match(
            conn,
            table=dhm_origin_sets,
            key_columns=("origin_set_sha256",),
            expected_rows=payload.origin_set_rows,
            where=dhm_origin_sets.c.generation_id == build.id,
            label="origin set",
        )
        _assert_replay_rows_match(
            conn,
            table=dhm_origin_set_members,
            key_columns=("origin_set_sha256", "origin_id"),
            expected_rows=payload.origin_member_rows,
            where=dhm_origin_set_members.c.generation_id == build.id,
            label="origin member",
        )
        _assert_replay_rows_match(
            conn,
            table=dhm_rating_declarations,
            key_columns=("id",),
            expected_rows=payload.declaration_rows,
            where=dhm_rating_declarations.c.generation_id == build.id,
            label="rating declaration",
        )
        _assert_replay_rows_match(
            conn,
            table=dhm_delivery_observation_attributions,
            key_columns=("observation_id",),
            expected_rows=payload.attribution_rows,
            where=dhm_delivery_observation_attributions.c.generation_id == build.id,
            label="observation attribution",
        )
    return True


def run_dhm21_delivery_replacement_transaction(
    conn: sa.Connection,
    build: GenerationBuildInput,
    payload: RestrictedReplacementPayload | None = None,
) -> None:
    require_real_transaction(conn, caller="run_dhm21_delivery_replacement_transaction")
    if conn.get_isolation_level().upper() != "READ COMMITTED":
        raise ValueError("protected DHM21 writes require READ COMMITTED")
    if _return_if_existing_generation_replay_matches(conn, build, payload):
        return
    release = (
        conn.execute(
            sa.select(restricted_delivery_releases).where(
                restricted_delivery_releases.c.id == build.release_id,
                restricted_delivery_releases.c.tenant_id == build.tenant_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if release is None or release["status"] != "authorized":
        raise ValueError("authorized restricted release is absent")
    if release["allowed_new_delivery_id"] != build.delivery_id:
        raise ValueError("release/delivery id mismatch")
    if release["manifest_sha256"] != build.manifest_sha256:
        raise ValueError("release manifest hash mismatch")
    manifest = fetch_release_witness(conn, build.release_id, "manifest_catalog")
    verify_manifest_catalog(manifest)
    conn.execute(
        sa.text("SELECT public.sf_lock_dhm21_delivery_write(:tenant_id, :release_id)"),
        {"tenant_id": build.tenant_id, "release_id": build.release_id},
    )
    locked_release = (
        conn.execute(
            sa.select(restricted_delivery_releases).where(
                restricted_delivery_releases.c.id == build.release_id,
                restricted_delivery_releases.c.tenant_id == build.tenant_id,
            )
        )
        .mappings()
        .one()
    )
    if locked_release["status"] != "authorized":
        raise ValueError("authorized restricted release is absent")
    locked_predecessor_sha256 = predecessor_semantic_digest(
        fetch_predecessor_semantic_rows(conn, build.tenant_id)
    )
    if locked_predecessor_sha256 != build.predecessor_semantic_sha256:
        raise ValueError("locked predecessor semantic digest differs from build input")
    if (
        locked_predecessor_sha256
        != locked_release["expected_predecessor_semantic_sha256"]
    ):
        raise ValueError(
            "locked predecessor semantic digest differs from authorized release"
        )
    conn.execute(
        sa.insert(restricted_delivery_generations).values(
            id=build.id,
            release_id=build.release_id,
            tenant_id=build.tenant_id,
            network=NEPAL21_NETWORK,
            cohort=NEPAL21_COHORT,
            delivery_id=build.delivery_id,
            predecessor_delivery_id=DELIVERY_ID,
            predecessor_semantic_sha256=build.predecessor_semantic_sha256,
            preimage_sha256=build.preimage_sha256,
            manifest_sha256=build.manifest_sha256,
            station_set_sha256=build.station_set_sha256,
            canonicalization_policy_sha256=build.canonicalization_policy_sha256,
            state=RestrictedDeliveryState.BUILDING.value,
            created_at=build.created_at,
            created_by_run_id=build.created_by_run_id,
        )
    )
    if payload is not None:
        preimage_sha256 = populate_restricted_generation_payload(
            conn, generation_id=build.id, tenant_id=build.tenant_id, payload=payload
        )
        if preimage_sha256 != build.preimage_sha256:
            raise ValueError("preimage digest differs from release-bound build input")


def run_dhm21_delivery_replacement_dry_run(
    conn: sa.Connection,
    build: GenerationBuildInput,
    payload: RestrictedReplacementPayload,
) -> str:
    require_real_transaction(conn, caller="run_dhm21_delivery_replacement_dry_run")
    nested = conn.begin_nested()
    try:
        run_dhm21_delivery_replacement_transaction(conn, build, payload)
        row_count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(observations_table)
            .where(observations_table.c.delivery_id == build.delivery_id)
        )
        if row_count != len(payload.observation_rows):
            raise ValueError("dry-run persisted observation count differs from payload")
        nested.rollback()
        return build.preimage_sha256
    except Exception:
        nested.rollback()
        raise


def readback_generation_content_digest(conn: sa.Connection, generation_id: UUID) -> str:
    digest = conn.execute(
        sa.text("SELECT public.sf_dhm21_content_digest(:generation)"),
        {"generation": generation_id},
    ).scalar_one()
    if not isinstance(digest, str):
        raise ValueError("content readback requires a persisted generation")
    return digest


def publish_generation(
    conn: sa.Connection,
    *,
    generation_id: UUID,
    immutable_content_sha256: str,
    published_at: UtcDatetime,
    run_id: UUID | None = None,
) -> None:
    require_real_transaction(conn, caller="publish_generation")
    _require_sha256(immutable_content_sha256, "immutable content hash")
    row = (
        conn.execute(
            sa.select(restricted_delivery_generations).where(
                restricted_delivery_generations.c.id == generation_id
            )
        )
        .mappings()
        .one()
    )
    manifest = fetch_release_witness(conn, row["release_id"], "manifest_catalog")
    verify_manifest_catalog(manifest)
    if row["preimage_sha256"] is None:
        raise ValueError("publication requires predecessor preimage digest")
    delivery_observations = conn.scalar(
        sa.select(sa.func.count())
        .select_from(observations_table)
        .where(observations_table.c.delivery_id == row["delivery_id"])
    )
    if delivery_observations == 0:
        raise ValueError("publication requires inserted delivery observations")
    attribution_count = conn.scalar(
        sa.select(sa.func.count())
        .select_from(dhm_delivery_observation_attributions)
        .where(dhm_delivery_observation_attributions.c.generation_id == generation_id)
    )
    if attribution_count == 0:
        raise ValueError("publication requires observation attributions")
    conn.execute(
        sa.update(restricted_delivery_generations)
        .where(restricted_delivery_generations.c.id == generation_id)
        .values(
            state=RestrictedDeliveryState.ACTIVE_PENDING_QC.value,
            immutable_content_sha256=immutable_content_sha256,
            published_at=published_at,
            published_by_run_id=run_id,
        )
    )


def _deserialize_qc_flags(raw: object) -> list[QcFlag]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("qc_flags must be a list")
    raw_items = cast("list[object]", raw)
    flags: list[QcFlag] = []
    for item_raw in raw_items:
        if not isinstance(item_raw, dict):
            raise ValueError("qc_flags entries must be objects")
        item = cast("dict[str, object]", item_raw)
        detail = item.get("detail")
        flags.append(
            QcFlag(
                rule_id=str(item["rule_id"]),
                rule_version=str(item["rule_version"]),
                status=QcStatus(str(item["status"])),
                detail=str(detail) if detail is not None else None,
            )
        )
    return flags


def _serialize_qc_flags(flags: list[QcFlag]) -> list[dict[str, str | None]]:
    return [
        {
            "rule_id": flag.rule_id,
            "rule_version": flag.rule_version,
            "status": flag.status.value,
            "detail": flag.detail,
        }
        for flag in flags
    ]


def _observation_from_row(row: sa.engine.row.RowMapping) -> Observation:
    return Observation(
        id=ObservationId(row["id"]),
        station_id=StationId(row["station_id"]),
        timestamp=row["timestamp"],
        parameter=row["parameter"],
        value=row["value"],
        source=ObservationSource(row["source"]),
        rating_curve_id=(
            RatingCurveId(row["rating_curve_id"])
            if row["rating_curve_id"] is not None
            else None
        ),
        rating_curve_correction_version=row["rating_curve_correction_version"],
        qc_status=QcStatus(row["qc_status"]),
        qc_flags=_deserialize_qc_flags(row["qc_flags"]),
        qc_rule_version=row["qc_rule_version"],
        created_at=row["created_at"],
        delivery_id=row["delivery_id"],
    )


def readback_qc_completion_digest(
    conn: sa.Connection,
    *,
    generation_id: UUID,
    policy_sha256: str,
    completed_at: UtcDatetime,
    run_id: UUID | None = None,
) -> str:
    digest = conn.execute(
        sa.text(
            "SELECT public.sf_dhm21_qc_completion_digest("
            ":generation, :policy, :completed, :run)"
        ),
        {
            "generation": generation_id,
            "policy": policy_sha256,
            "completed": completed_at,
            "run": run_id,
        },
    ).scalar_one()
    if not isinstance(digest, str):
        raise ValueError("QC completion readback requires a persisted generation")
    return digest


def run_dhm21_qc_transaction(
    conn: sa.Connection,
    *,
    tenant_id: TenantId,
    generation_id: UUID,
    expected_policy: ExpectedQcPolicyPin,
    completed_at: UtcDatetime,
    run_id: UUID | None = None,
) -> str:
    require_real_transaction(conn, caller="run_dhm21_qc_transaction")
    _require_sha256(expected_policy.policy_sha256, "QC policy hash")
    if sha256_hex_bytes(expected_policy.content_bytes) != expected_policy.policy_sha256:
        raise ValueError("QC policy bundle hash mismatch")
    generation = (
        conn.execute(
            sa.select(restricted_delivery_generations).where(
                restricted_delivery_generations.c.id == generation_id,
                restricted_delivery_generations.c.tenant_id == tenant_id,
                restricted_delivery_generations.c.state
                == RestrictedDeliveryState.ACTIVE_PENDING_QC.value,
            )
        )
        .mappings()
        .one_or_none()
    )
    if generation is None:
        raise ValueError("generation is not active pending QC")
    if generation["qc_policy_sha256"] is not None:
        raise ValueError("generation already carries QC evidence")
    conn.execute(
        sa.text("SELECT public.sf_lock_dhm21_delivery_write(:tenant_id, :release_id)"),
        {"tenant_id": tenant_id, "release_id": generation["release_id"]},
    )
    rows = (
        conn.execute(
            sa.select(observations_table)
            .join(
                dhm_delivery_observation_attributions,
                dhm_delivery_observation_attributions.c.observation_id
                == observations_table.c.id,
            )
            .where(
                dhm_delivery_observation_attributions.c.generation_id == generation_id,
                dhm_delivery_observation_attributions.c.tenant_id == tenant_id,
                observations_table.c.delivery_id == generation["delivery_id"],
            )
            .order_by(
                observations_table.c.station_id,
                observations_table.c.timestamp,
                observations_table.c.id,
            )
            .with_for_update(of=observations_table)
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ValueError("QC requires attributed observations")
    observations = [_observation_from_row(row) for row in rows]
    if any(obs.qc_status is not QcStatus.RAW for obs in observations):
        raise ValueError("QC runner only processes RAW attributed observations")
    result = Stage1QualityChecker().check_with_coverage(
        observations,
        expected_policy.rule_set,
        expected_policy.overrides,
        expected_policy.baselines,
        station_networks=expected_policy.station_networks,
    )
    missing = [obs.id for obs in observations if obs.id not in result.judged]
    if missing:
        raise ValueError(f"QC policy did not evaluate every attributed row: {missing}")
    for obs in observations:
        flags = result.flags.get(obs.id, [])
        status = aggregate_qc_status(flags)
        serialized_flags = _serialize_qc_flags(flags)
        conn.execute(
            sa.update(observations_table)
            .where(observations_table.c.id == obs.id)
            .values(
                qc_status=status.value,
                qc_flags=serialized_flags,
                qc_rule_version=expected_policy.policy_sha256,
            )
        )
    completion_sha256 = readback_qc_completion_digest(
        conn,
        generation_id=generation_id,
        policy_sha256=expected_policy.policy_sha256,
        completed_at=completed_at,
        run_id=run_id,
    )
    complete_generation_qc(
        conn,
        generation_id=generation_id,
        qc_policy_sha256=expected_policy.policy_sha256,
        qc_completion_sha256=completion_sha256,
        completed_at=completed_at,
        run_id=run_id,
    )
    return completion_sha256


def complete_generation_qc(
    conn: sa.Connection,
    *,
    generation_id: UUID,
    qc_policy_sha256: str,
    qc_completion_sha256: str,
    completed_at: UtcDatetime,
    run_id: UUID | None = None,
) -> None:
    require_real_transaction(conn, caller="complete_generation_qc")
    _require_sha256(qc_policy_sha256, "QC policy hash")
    _require_sha256(qc_completion_sha256, "QC completion hash")
    generation = (
        conn.execute(
            sa.select(restricted_delivery_generations).where(
                restricted_delivery_generations.c.id == generation_id,
                restricted_delivery_generations.c.state
                == RestrictedDeliveryState.ACTIVE_PENDING_QC.value,
            )
        )
        .mappings()
        .one_or_none()
    )
    if generation is None:
        raise ValueError("generation is not active pending QC")
    attribution_count = conn.scalar(
        sa.select(sa.func.count())
        .select_from(dhm_delivery_observation_attributions)
        .where(dhm_delivery_observation_attributions.c.generation_id == generation_id)
    )
    if attribution_count == 0:
        raise ValueError("QC completion requires published attribution rows")
    unqcd = conn.scalar(
        sa.select(sa.func.count())
        .select_from(dhm_delivery_observation_attributions)
        .join(
            observations_table,
            observations_table.c.id
            == dhm_delivery_observation_attributions.c.observation_id,
        )
        .where(dhm_delivery_observation_attributions.c.generation_id == generation_id)
        .where(observations_table.c.qc_rule_version.is_(None))
    )
    if unqcd != 0:
        raise ValueError(
            "QC completion requires every attributed observation to be QCed"
        )
    conn.execute(
        sa.update(restricted_delivery_generations)
        .where(restricted_delivery_generations.c.id == generation_id)
        .values(
            state=RestrictedDeliveryState.ACTIVE_QC_COMPLETE.value,
            qc_policy_sha256=qc_policy_sha256,
            qc_completion_sha256=qc_completion_sha256,
            qc_completed_at=completed_at,
            qc_run_id=run_id,
        )
    )


def enable_expired_rating_test_permission(
    conn: sa.Connection,
    *,
    tenant_id: TenantId,
    generation_id: UUID,
    owner_authorization_sha256: str,
    precedence_sha256: str,
    selection_manifest_sha256: str,
    enabled_at: UtcDatetime,
    run_id: UUID | None = None,
) -> UUID:
    require_real_transaction(conn, caller="enable_expired_rating_test_permission")
    for label, digest in (
        ("owner authorization", owner_authorization_sha256),
        ("precedence", precedence_sha256),
        ("selection manifest", selection_manifest_sha256),
    ):
        _require_sha256(digest, label)
    conn.execute(
        sa.text(
            "SELECT public.sf_lock_dhm21_permission_change(:tenant_id, :generation_id)"
        ),
        {"tenant_id": tenant_id, "generation_id": generation_id},
    )
    version = conn.scalar(
        sa.select(
            sa.func.coalesce(
                sa.func.max(expired_rating_test_permissions.c.permission_version), 0
            )
            + 1
        ).where(
            expired_rating_test_permissions.c.tenant_id == tenant_id,
            expired_rating_test_permissions.c.generation_id == generation_id,
        )
    )
    permission_id = uuid4()
    conn.execute(
        sa.insert(expired_rating_test_permissions).values(
            id=permission_id,
            tenant_id=tenant_id,
            generation_id=generation_id,
            cohort=NEPAL21_COHORT,
            permission_version=version,
            state="enabled",
            owner_authorization_sha256=owner_authorization_sha256,
            precedence_sha256=precedence_sha256,
            selection_manifest_sha256=selection_manifest_sha256,
            enabled_at=enabled_at,
            enabled_by_run_id=run_id,
        )
    )
    return permission_id


def disable_expired_rating_test_permission(
    conn: sa.Connection,
    *,
    permission_id: UUID,
    disabled_at: UtcDatetime,
    run_id: UUID | None = None,
) -> None:
    require_real_transaction(conn, caller="disable_expired_rating_test_permission")
    row = (
        conn.execute(
            sa.select(expired_rating_test_permissions).where(
                expired_rating_test_permissions.c.id == permission_id
            )
        )
        .mappings()
        .one()
    )
    conn.execute(
        sa.text(
            "SELECT public.sf_lock_dhm21_permission_change(:tenant_id, :generation_id)"
        ),
        {"tenant_id": row["tenant_id"], "generation_id": row["generation_id"]},
    )
    conn.execute(
        sa.update(expired_rating_test_permissions)
        .where(expired_rating_test_permissions.c.id == permission_id)
        .values(state="disabled", disabled_at=disabled_at, disabled_by_run_id=run_id)
    )


def active_accepted_generation_predicate(
    observation_table: sa.Table = observations_table,
) -> sa.ColumnElement[bool]:
    return sa.func.sf_active_accepted_delivery(
        observation_table.c.station_id,
        observation_table.c.delivery_id,
    )
