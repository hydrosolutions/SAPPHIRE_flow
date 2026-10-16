from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this field at runtime

from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.adapters.dhm_files import (
    DailyFlowValue,
    RatingTableBlock,
    parse_daily_flow,
    parse_rating_tables,
)
from sapphire_flow.services.nepal21_delivery_authority import (
    NEPAL21_STATION_CODES,
    ShapeState,
    WitnessBytes,
    canonical_json_bytes,
    classify_rating_block,
    declaration_digest,
    function_payload,
    rating_raw_payload,
    validate_witness_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class _ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_code: str
    declaration_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: UUID
    approval_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CanonicalizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codec: Literal["dhm21-canonicalization-policy-v1"]
    policy_scope: Literal["nepal21"]
    station_codes: tuple[str, ...]
    approvals: tuple[_ApprovalInput, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class CanonicalizationApproval:
    station_code: str
    declaration_digest: str
    approval_id: UUID
    approval_sha256: str


@dataclass(frozen=True, kw_only=True, slots=True)
class CanonicalizationPolicy:
    witness: WitnessBytes
    approvals: tuple[CanonicalizationApproval, ...]


def parse_canonicalization_policy(
    witness: WitnessBytes, *, expected_sha256: str
) -> CanonicalizationPolicy:
    if witness.kind != "canonicalization_policy":
        raise ValueError("canonicalization policy requires the correct witness kind")
    validate_witness_bytes(witness, expected_sha256=expected_sha256)
    parsed = _CanonicalizationInput.model_validate_json(witness.content)
    if parsed.station_codes != NEPAL21_STATION_CODES:
        raise ValueError("canonicalization policy must bind the exact Nepal21 roster")
    keys = {
        (item.station_code, item.declaration_content_sha256)
        for item in parsed.approvals
    }
    if len(keys) != len(parsed.approvals) or any(
        code not in NEPAL21_STATION_CODES for code, _ in keys
    ):
        raise ValueError(
            "canonicalization approvals have duplicate or foreign declaration keys"
        )
    return CanonicalizationPolicy(
        witness=witness,
        approvals=tuple(
            CanonicalizationApproval(
                station_code=item.station_code,
                declaration_digest=item.declaration_content_sha256,
                approval_id=item.approval_id,
                approval_sha256=item.approval_sha256,
            )
            for item in parsed.approvals
        ),
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class DhmSourceFile:
    kind: Literal["DFL", "RT", "GHT_HASH_ONLY"]
    source_root_label: Literal["old", "new", "synthetic_test"]
    relative_path: str
    content: bytes
    expected_sha256: str
    station_code: str | None = None

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.relative_path
            or str(path) != self.relative_path
        ):
            raise ValueError("source path must be a normalized relative POSIX path")
        if hashlib.sha256(self.content).hexdigest() != self.expected_sha256:
            raise ValueError("source bytes do not match pinned SHA-256")
        if self.kind not in (
            "DFL",
            "RT",
            "GHT_HASH_ONLY",
        ) or self.source_root_label not in (
            "old",
            "new",
            "synthetic_test",
        ):
            raise ValueError("unsupported source identity")
        if (self.kind == "GHT_HASH_ONLY") != (self.station_code is not None) or (
            self.station_code is not None
            and self.station_code not in NEPAL21_STATION_CODES
        ):
            raise ValueError(
                "only hash-only GHT sources require an explicit Nepal21 station code"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceOccurrence:
    source: DhmSourceFile
    ordinal: int | None

    def __post_init__(self) -> None:
        if (self.source.kind == "GHT_HASH_ONLY") != (self.ordinal is None) or (
            self.ordinal is not None and self.ordinal < 0
        ):
            raise ValueError("source ordinal is incompatible with source kind")


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceDeclaration:
    station_code: str
    block: RatingTableBlock
    digest: str
    raw_content: bytes
    effective_content: bytes | None
    shape: ShapeState
    occurrences: tuple[SourceOccurrence, ...]
    approval: CanonicalizationApproval | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceDailyRecord:
    station_code: str
    value: DailyFlowValue
    occurrences: tuple[SourceOccurrence, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceCatalog:
    manifest: WitnessBytes
    declarations: tuple[SourceDeclaration, ...]
    daily_records: tuple[SourceDailyRecord, ...]
    canonicalization_policy: CanonicalizationPolicy | None = None
    hash_only_sources: tuple[DhmSourceFile, ...] = ()


def build_nepal21_source_catalog(
    files: Sequence[DhmSourceFile],
    *,
    policy: CanonicalizationPolicy | None = None,
) -> SourceCatalog:
    """Parse pinned sources; unapproved duplicate-point curves remain held."""
    file_keys = [(item.source_root_label, item.relative_path) for item in files]
    if len(file_keys) != len(set(file_keys)):
        raise ValueError("duplicate physical source identity")
    declarations: dict[
        tuple[str, str], tuple[RatingTableBlock, list[SourceOccurrence]]
    ] = {}
    daily: dict[tuple[str, str], tuple[DailyFlowValue, list[SourceOccurrence]]] = {}
    station_sources: dict[str, set[str]] = defaultdict(set)
    hash_only: list[DhmSourceFile] = []
    file_station_codes: dict[tuple[str, str], str] = {}
    for source in sorted(
        files, key=lambda item: (item.source_root_label, item.relative_path)
    ):
        if source.kind == "GHT_HASH_ONLY":
            assert source.station_code is not None
            file_station_codes[(source.source_root_label, source.relative_path)] = (
                source.station_code
            )
            hash_only.append(source)
            continue
        try:
            text = source.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("source must use UTF-8") from exc
        if source.kind == "RT":
            parsed_rt = parse_rating_tables(text)
            code = parsed_rt.station_code
            file_station_codes[(source.source_root_label, source.relative_path)] = code
            station_sources["RT"].add(code)
            for ordinal, block in enumerate(parsed_rt.blocks):
                digest = declaration_digest(code, block)
                _, occurrences = declarations.setdefault((code, digest), (block, []))
                occurrences.append(SourceOccurrence(source=source, ordinal=ordinal))
        else:
            parsed_dfl = parse_daily_flow(text)
            code = parsed_dfl.station_code
            file_station_codes[(source.source_root_label, source.relative_path)] = code
            station_sources["DFL"].add(code)
            for ordinal, value in enumerate(parsed_dfl.values):
                existing, occurrences = daily.setdefault(
                    (code, value.day.isoformat()), (value, [])
                )
                if existing.discharge_m3s != value.discharge_m3s:
                    raise ValueError("conflicting daily values across source files")
                occurrences.append(SourceOccurrence(source=source, ordinal=ordinal))
    if any(
        station_sources[kind] != set(NEPAL21_STATION_CODES) for kind in ("RT", "DFL")
    ):
        raise ValueError(
            "RT and DFL sources must each cover exactly the Nepal21 roster"
        )
    approvals = (
        {
            (item.station_code, item.declaration_digest): item
            for item in policy.approvals
        }
        if policy is not None
        else {}
    )
    if not approvals.keys() <= declarations.keys():
        raise ValueError("canonicalization policy contains absent source declarations")
    normalized = tuple(
        _normalize_declaration(
            code, digest, block, occurrences, approvals.get((code, digest))
        )
        for (code, digest), (block, occurrences) in sorted(declarations.items())
    )
    records = tuple(
        SourceDailyRecord(
            station_code=code, value=value, occurrences=tuple(occurrences)
        )
        for (code, _), (value, occurrences) in sorted(daily.items())
    )
    source_records = [
        _manifest_record(
            occurrence, item.station_code, item.digest, json.loads(item.raw_content)
        )
        for item in normalized
        for occurrence in item.occurrences
    ]
    source_records.extend(
        _manifest_record(
            occurrence,
            record.station_code,
            hashlib.sha256(
                canonical_json_bytes(_daily_record_payload(record))
            ).hexdigest(),
            _daily_record_payload(record),
        )
        for record in records
        for occurrence in record.occurrences
    )
    source_records.extend(
        _manifest_record(
            SourceOccurrence(source=source, ordinal=None),
            file_station_codes[(source.source_root_label, source.relative_path)],
            source.expected_sha256,
            None,
        )
        for source in hash_only
    )
    manifest = WitnessBytes(
        kind="manifest_catalog",
        content=canonical_json_bytes(
            {
                "policy_scope": "nepal21",
                "station_codes": list(NEPAL21_STATION_CODES),
                "codec": "dhm21-source-catalog-v1",
                "source_records": sorted(source_records, key=canonical_json_bytes),
                "rating_declarations": [
                    _manifest_declaration(item) for item in normalized
                ],
                "source_files": [
                    {
                        "kind": item.kind,
                        "source_root_label": item.source_root_label,
                        "relative_path": item.relative_path,
                        "file_sha256": item.expected_sha256,
                        "station_code": file_station_codes[
                            (item.source_root_label, item.relative_path)
                        ],
                    }
                    for item in sorted(
                        files,
                        key=lambda item: (item.source_root_label, item.relative_path),
                    )
                ],
            }
        ),
    )
    return SourceCatalog(
        manifest=manifest,
        declarations=normalized,
        daily_records=records,
        canonicalization_policy=policy,
        hash_only_sources=tuple(hash_only),
    )


def _normalize_declaration(
    code: str,
    digest: str,
    block: RatingTableBlock,
    occurrences: list[SourceOccurrence],
    approval: CanonicalizationApproval | None,
) -> SourceDeclaration:
    if approval is not None and (
        classify_rating_block(block) != "duplicate_point_needs_approval"
        or len({point.stage_m for point in block.points}) < 2
    ):
        raise ValueError(
            "canonicalization approval cannot repair this declaration shape"
        )
    raw_content = canonical_json_bytes(rating_raw_payload(code, block))
    effective = function_payload(
        code, block, canonicalization_approved=approval is not None
    )
    return SourceDeclaration(
        station_code=code,
        block=block,
        digest=digest,
        raw_content=raw_content,
        effective_content=canonical_json_bytes(effective)
        if effective is not None
        else None,
        shape=classify_rating_block(
            block, canonicalization_approved=approval is not None
        ),
        occurrences=tuple(occurrences),
        approval=approval,
    )


def _manifest_declaration(item: SourceDeclaration) -> dict[str, object]:
    return {
        "station_code": item.station_code,
        "declaration_content_sha256": item.digest,
        "raw_declaration_payload_json": json.loads(item.raw_content),
        "from_local_date": item.block.from_date.isoformat(),
        "rating_type_label": item.block.rating_type_label,
        "shape_state": item.shape,
    }


def _manifest_record(
    occurrence: SourceOccurrence,
    station_code: str,
    payload_sha256: str,
    record_payload: dict[str, object] | None,
) -> dict[str, object]:
    source = occurrence.source
    return {
        "origin_kind": source.kind,
        "source_root_label": source.source_root_label,
        "relative_path": source.relative_path,
        "file_sha256": source.expected_sha256,
        "parser_version": "hash-only-v1"
        if source.kind == "GHT_HASH_ONLY"
        else "dhm-files-v1",
        "row_or_block_ordinal": occurrence.ordinal,
        "origin_payload_sha256": payload_sha256,
        "station_code": station_code,
        "record_payload": record_payload,
    }


def _daily_record_payload(record: SourceDailyRecord) -> dict[str, object]:
    return {
        "station_code": record.station_code,
        "local_date": record.value.day.isoformat(),
        "reported_q": 0.0
        if record.value.discharge_m3s == 0
        else record.value.discharge_m3s,
    }
