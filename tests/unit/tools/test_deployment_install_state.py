from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from tools import deployment_install_state as state

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def deliveries(tmp_path: Path) -> tuple[state.Delivery, ...]:
    tmp_path.chmod(0o700)
    bindings: dict[str, Path] = {}
    for index, name in enumerate(state.ROLES):
        path = tmp_path / name
        path.write_text(str(index) * 64 + "\n")
        path.chmod(0o444)
        bindings[name] = path
    return state.read_deliveries(bindings)


class TestCredentials:
    def test_same_effective_value_can_be_redelivered(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...]
    ) -> None:
        state.save_fingerprints(tmp_path, deliveries, lambda size: b"s" * size)
        for delivery in deliveries:
            replacement = delivery.path.with_suffix(".new")
            replacement.write_bytes(delivery.effective)
            replacement.chmod(0o444)
            replacement.replace(delivery.path)
        checked = state.check_fingerprints(tmp_path)
        assert [item.effective for item in checked] == [
            item.effective for item in deliveries
        ]
        assert [item.metadata for item in checked] != [
            item.metadata for item in deliveries
        ]

    @pytest.mark.parametrize("role", state.ROLES)
    def test_changed_value_is_refused(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...], role: str
    ) -> None:
        state.save_fingerprints(tmp_path, deliveries, lambda size: b"s" * size)
        path = tmp_path / role
        path.chmod(0o600)
        path.write_text("changed_credential")
        with pytest.raises(state.InstallError, match="credential_changed"):
            state.check_fingerprints(tmp_path)

    def test_pepper_whitespace_matches_runtime_reader(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...]
    ) -> None:
        state.save_fingerprints(tmp_path, deliveries, lambda size: b"s" * size)
        path = tmp_path / "access_token_pepper"
        path.chmod(0o600)
        path.write_bytes(b" \n" + deliveries[-1].effective + b"\t\n")
        assert (
            state.check_fingerprints(tmp_path)[-1].effective == deliveries[-1].effective
        )

    @pytest.mark.parametrize(
        "content", [b"", b"unsafe/password", b"password\n\n", b"x" * 4097]
    )
    def test_unsupported_database_content_is_refused(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...], content: bytes
    ) -> None:
        path = tmp_path / "db_password"
        path.chmod(0o600)
        path.write_bytes(content)
        with pytest.raises(state.InstallError, match="credential_format_invalid"):
            state.read_deliveries({item.name: item.path for item in deliveries})

    def test_role_passwords_must_be_distinct(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...]
    ) -> None:
        path = tmp_path / "sapphire_api_db_password"
        path.chmod(0o600)
        path.write_bytes(deliveries[0].effective)
        with pytest.raises(
            state.InstallError, match="database_credentials_not_distinct"
        ):
            state.read_deliveries({item.name: item.path for item in deliveries})

    def test_fingerprints_are_private_and_not_raw_values(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...]
    ) -> None:
        state.save_fingerprints(tmp_path, deliveries, os.urandom)
        path = tmp_path / "credentials.json"
        assert path.stat().st_mode & 0o777 == 0o600
        text = path.read_text()
        assert all(item.effective.decode() not in text for item in deliveries)
        assert len({entry["salt"] for entry in json.loads(text)["credentials"]}) == 5

    @pytest.mark.parametrize("mutation", ["missing", "corrupt", "public", "symlink"])
    def test_unusable_record_never_rebaselines(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...], mutation: str
    ) -> None:
        state.save_fingerprints(tmp_path, deliveries, os.urandom)
        path = tmp_path / "credentials.json"
        if mutation == "missing":
            path.unlink()
        elif mutation == "corrupt":
            path.write_text("invalid json")
        elif mutation == "public":
            path.chmod(0o644)
        else:
            path.rename(tmp_path / "other.json")
            path.symlink_to(tmp_path / "other.json")
        with pytest.raises(state.InstallError, match="record"):
            state.check_fingerprints(tmp_path)

    def test_original_bytes_and_metadata_remain_unchanged(
        self, tmp_path: Path, deliveries: tuple[state.Delivery, ...]
    ) -> None:
        state.save_fingerprints(tmp_path, deliveries, os.urandom)
        state.check_fingerprints(tmp_path)
        state.unchanged(deliveries)


class TestLockAndRecord:
    def test_second_lock_refuses_concurrent_action(self, tmp_path: Path) -> None:
        lock = tmp_path / "lock"
        with (
            state.installation_lock(lock),
            pytest.raises(state.InstallError, match="installation_busy"),
            state.installation_lock(lock),
        ):
            pytest.fail("second lock acquired")

    def test_record_round_trip_preserves_owned_ids(self, tmp_path: Path) -> None:
        tmp_path.chmod(0o700)
        record = state.InstallationRecord(
            installation_id="a" * 32,
            project="synthetic",
            daemon="test-daemon",
            http_port=8080,
            revision="b" * 40,
            identity_kind="ci-development",
            state_directory=str(tmp_path),
        )
        record = replace(record, known=(("container", "c" * 64),))
        state.save_record(tmp_path, record)
        assert state.load_record(tmp_path) == record


class TestDeliveryRedelivery:
    def test_equivalent_atomic_redelivery_during_check_is_allowed(
        self, deliveries: tuple[state.Delivery, ...]
    ) -> None:
        original = deliveries[0].path
        replacement = original.with_name("redelivery")
        replacement.write_bytes(original.read_bytes())
        replacement.chmod(original.stat().st_mode & 0o777)
        replacement.replace(original)
        state.unchanged(deliveries)
