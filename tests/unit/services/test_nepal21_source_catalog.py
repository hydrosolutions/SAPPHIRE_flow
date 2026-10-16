from __future__ import annotations

import dataclasses
import hashlib
import json
from uuid import UUID

import pytest

from sapphire_flow.services.nepal21_delivery_authority import (
    NEPAL21_STATION_CODES,
    WitnessBytes,
    canonical_json_bytes,
    verify_manifest_catalog,
)
from sapphire_flow.services.nepal21_source_catalog import (
    CanonicalizationPolicy,
    DhmSourceFile,
    build_nepal21_source_catalog,
    parse_canonicalization_policy,
)


def _sources() -> list[DhmSourceFile]:
    result: list[DhmSourceFile] = []
    for code in NEPAL21_STATION_CODES:
        rt = (
            f"Station No:{code}\nData Type: Rating Table\nLeft Column= Stage in m\n"
            "Right Column= Discharge im m3/s\nRating Type No =1\n"
            "From Date =01-Jan-1990\nTo Date =31-Dec-1991\nFrom Stage =0\n0,1\n1,2\n"
        ).encode()
        dfl = f"Daily Flow of Station: {code} in m3/s\n1991\n02/Jan/1991,2\n".encode()
        result.extend(
            (
                DhmSourceFile(
                    kind="RT",
                    source_root_label="synthetic_test",
                    relative_path=f"RT_{code}.txt",
                    content=rt,
                    expected_sha256=hashlib.sha256(rt).hexdigest(),
                ),
                DhmSourceFile(
                    kind="DFL",
                    source_root_label="synthetic_test",
                    relative_path=f"DFL_{code}.txt",
                    content=dfl,
                    expected_sha256=hashlib.sha256(dfl).hexdigest(),
                ),
            )
        )
    return result


def _replace_content(source: DhmSourceFile, content: bytes) -> DhmSourceFile:
    return dataclasses.replace(
        source, content=content, expected_sha256=hashlib.sha256(content).hexdigest()
    )


def _synthetic_approval(
    sources: list[DhmSourceFile], code: str = "225"
) -> CanonicalizationPolicy:
    declaration = next(
        item
        for item in build_nepal21_source_catalog(sources).declarations
        if item.station_code == code
    )
    witness = WitnessBytes(
        kind="canonicalization_policy",
        content=canonical_json_bytes(
            {
                "codec": "dhm21-canonicalization-policy-v1",
                "policy_scope": "nepal21",
                "station_codes": list(NEPAL21_STATION_CODES),
                "approvals": [
                    {
                        "station_code": code,
                        "declaration_content_sha256": declaration.digest,
                        "approval_id": str(UUID(int=520)),
                        "approval_sha256": hashlib.sha256(
                            b"synthetic owner approval only"
                        ).hexdigest(),
                    }
                ],
            }
        ),
    )
    return parse_canonicalization_policy(witness, expected_sha256=witness.sha256)


class TestBuildNepal21SourceCatalog:
    def test_full_roster_manifest_is_derived_and_input_order_independent(self) -> None:
        sources = _sources()
        catalog = build_nepal21_source_catalog(sources)
        assert len(catalog.declarations) == len(catalog.daily_records) == 21
        assert catalog == build_nepal21_source_catalog(list(reversed(sources)))
        assert (
            len(verify_manifest_catalog(catalog.manifest)["rating_declarations"]) == 21
        )

    def test_duplicate_semantics_retain_all_physical_occurrences(self) -> None:
        sources = _sources()
        duplicate_rt = dataclasses.replace(sources[0], relative_path="duplicate_rt.txt")
        duplicate_dfl = dataclasses.replace(
            sources[1], relative_path="duplicate_dfl.txt"
        )
        catalog = build_nepal21_source_catalog([*sources, duplicate_rt, duplicate_dfl])
        assert len(catalog.declarations) == len(catalog.daily_records) == 21
        assert (
            len(
                next(
                    item for item in catalog.declarations if item.station_code == "225"
                ).occurrences
            )
            == 2
        )
        assert (
            len(
                next(
                    item for item in catalog.daily_records if item.station_code == "225"
                ).occurrences
            )
            == 2
        )

    def test_conflicting_daily_sources_refused(self) -> None:
        sources = _sources()
        changed = dataclasses.replace(sources[1], relative_path="conflicting.txt")
        changed = _replace_content(
            changed, changed.content.replace(b"1991,2", b"1991,3")
        )
        with pytest.raises(ValueError, match="conflicting daily values"):
            build_nepal21_source_catalog([*sources, changed])

    def test_incomplete_roster_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly the Nepal21 roster"):
            build_nepal21_source_catalog(_sources()[:-1])

    def test_duplicate_physical_identity_refused(self) -> None:
        sources = _sources()
        with pytest.raises(ValueError, match="duplicate physical source"):
            build_nepal21_source_catalog([*sources, sources[0]])

    def test_unapproved_duplicate_points_remain_held(self) -> None:
        sources = _sources()
        sources[0] = _replace_content(
            sources[0], sources[0].content.replace(b"0,1\n", b"0,1\n0,1\n")
        )
        declaration = next(
            item
            for item in build_nepal21_source_catalog(sources).declarations
            if item.station_code == "225"
        )
        assert declaration.shape == "duplicate_point_needs_approval"
        assert declaration.effective_content is None

    def test_signed_zero_has_one_effective_identity(self) -> None:
        sources = _sources()
        original = build_nepal21_source_catalog(sources)
        sources[0] = _replace_content(
            sources[0], sources[0].content.replace(b"\n0,1", b"\n-0,1")
        )
        changed = build_nepal21_source_catalog(sources)
        assert (
            original.declarations[0].effective_content
            == changed.declarations[0].effective_content
        )


class TestDhmSourceFile:
    def test_pinned_content_mismatch_refused(self) -> None:
        with pytest.raises(ValueError, match="pinned SHA-256"):
            dataclasses.replace(_sources()[0], content=b"mutated")

    @pytest.mark.parametrize(
        "path", ["../outside", "/absolute", "a/../b", "a\\b", "a//b"]
    )
    def test_noncanonical_path_refused(self, path: str) -> None:
        with pytest.raises(ValueError, match="relative POSIX path"):
            dataclasses.replace(_sources()[0], relative_path=path)


class TestCanonicalizationPolicy:
    def test_explicit_pinned_approval_deduplicates_only_identical_points(self) -> None:
        sources = _sources()
        sources[0] = _replace_content(
            sources[0], sources[0].content.replace(b"0,1\n", b"0,1\n0,1\n")
        )
        policy = _synthetic_approval(sources)
        catalog = build_nepal21_source_catalog(sources, policy=policy)
        declaration = next(
            item for item in catalog.declarations if item.station_code == "225"
        )
        assert declaration.shape == "canonicalized_usable"
        assert declaration.approval == policy.approvals[0]
        assert declaration.effective_content is not None
        assert len(json.loads(declaration.effective_content)["points"]) == 2
        assert len(json.loads(declaration.raw_content)["points"]) == 3

    def test_wrong_pin_refused(self) -> None:
        sources = _sources()
        policy = _synthetic_approval(sources)
        with pytest.raises(ValueError, match="expected hash"):
            parse_canonicalization_policy(policy.witness, expected_sha256="0" * 64)

    def test_approval_cannot_repair_decreasing_function(self) -> None:
        sources = _sources()
        sources[0] = _replace_content(
            sources[0], sources[0].content.replace(b"0,1\n1,2", b"0,5\n1,2")
        )
        policy = _synthetic_approval(sources)
        with pytest.raises(ValueError, match="cannot repair"):
            build_nepal21_source_catalog(sources, policy=policy)


class TestHashOnlySource:
    def test_ght_bytes_are_hashed_not_parsed_or_promoted(self) -> None:
        content = b"\xff\xfe synthetic binary GHT"
        source = DhmSourceFile(
            kind="GHT_HASH_ONLY",
            source_root_label="new",
            relative_path="225.ght",
            content=content,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            station_code="225",
        )
        catalog = build_nepal21_source_catalog([*_sources(), source])
        assert catalog.hash_only_sources == (source,)
        assert len(catalog.daily_records) == 21
        assert len(catalog.declarations) == 21
