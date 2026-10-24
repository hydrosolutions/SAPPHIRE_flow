from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "release_identity.py"

spec = importlib.util.spec_from_file_location("release_identity", _TOOL_PATH)
assert spec is not None and spec.loader is not None
release_identity = importlib.util.module_from_spec(spec)
sys.modules["release_identity"] = release_identity
spec.loader.exec_module(release_identity)


class TestCanonicalVersionParsing:
    def test_numeric_ordering_is_not_string_ordering(self) -> None:
        assert release_identity.Version.parse_tag(
            "v0.1.1000"
        ) > release_identity.Version.parse_tag("v0.1.999")

    @pytest.mark.parametrize(
        "value", ["v01.2.3", "v1.02.3", "v1.2.03", "v1.2", "1.2.3", "v1.2.3-review"]
    )
    def test_noncanonical_tags_fail(self, value: str) -> None:
        with pytest.raises(release_identity.ReleaseIdentityError):
            release_identity.Version.parse_tag(value)


class TestMetadata:
    def test_message_round_trips(self) -> None:
        message = release_identity.tag_message(
            "v1.2.3",
            "a" * 40,
            "v1.0.0",
            "b" * 40,
        )
        metadata = release_identity.parse_metadata(message)
        assert metadata.tag == "v1.2.3"
        assert metadata.source == "a" * 40
        assert metadata.ceiling_tag == "v1.0.0"
        assert metadata.ceiling_source == "b" * 40

    def test_omitted_ceiling_fails(self) -> None:
        message = "\n".join(
            [
                release_identity.HELPER_MARKER,
                "tag=v1.2.3",
                f"source={'a' * 40}",
            ]
        )
        with pytest.raises(release_identity.ReleaseIdentityError):
            release_identity.parse_metadata(message)


class TestOldPublisherQuiescenceEvidence:
    def test_complete_paginated_terminal_query_is_accepted(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "runs.json"
        path.write_text(
            '{"complete": true, "paginated": true, "runs": [{"status": "completed"}]}'
        )
        release_identity.validate_old_workflow_runs(path)

    @pytest.mark.parametrize(
        "payload",
        [
            '{"complete": false, "paginated": true, "runs": []}',
            '{"complete": true, "paginated": true, "query_error": "boom", "runs": []}',
            '{"complete": true, "paginated": true, "runs": [{"status": "queued"}]}',
            '{"complete": true, "paginated": true, "runs": [{"status": "mystery"}]}',
        ],
    )
    def test_incomplete_nonterminal_unknown_or_error_query_fails(
        self, tmp_path: Path, payload: str
    ) -> None:
        path = tmp_path / "runs.json"
        path.write_text(payload)
        with pytest.raises(release_identity.ReleaseIdentityError):
            release_identity.validate_old_workflow_runs(path)


def test_uppercase_source_sha_is_rejected() -> None:
    with pytest.raises(release_identity.ReleaseIdentityError):
        release_identity.require_full_sha("A" * 40)


def test_production_cli_has_no_local_remote_or_skip_docker_escape() -> None:
    help_text = _TOOL_PATH.read_text()
    assert "--allow-local-remote" not in help_text
    assert "--skip-docker" not in help_text


def _isolated_git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if (
            key == "GIT_CONFIG_PARAMETERS"
            or key == "GIT_CONFIG_COUNT"
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(key)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _git(repo: Path, *args: str) -> None:
    import subprocess

    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_isolated_git_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)


def test_validate_destination_accepts_canonical_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "remote", "add", "origin", release_identity.CANONICAL_HTTPS_URL)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)

    release_identity.validate_destination()


@pytest.mark.parametrize(
    ("config_args", "message"),
    [
        (
            ("remote", "add", "origin", "https://example.com/other.git"),
            "unexpected origin",
        ),
        (("remote", "add", "origin", release_identity.CANONICAL_HTTPS_URL), "pushurl"),
    ],
)
def test_validate_destination_rejects_wrong_origin_or_pushurl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_args: tuple[str, ...],
    message: str,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, *config_args)
    if message == "pushurl":
        _git(
            tmp_path,
            "remote",
            "set-url",
            "--push",
            "origin",
            "https://example.com/push.git",
        )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(release_identity.ReleaseIdentityError, match=message):
        release_identity.validate_destination()


def test_validate_destination_rejects_push_default_and_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "remote", "add", "origin", release_identity.CANONICAL_HTTPS_URL)
    _git(tmp_path, "config", "remote.pushDefault", "origin")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        release_identity.ReleaseIdentityError, match="remote.pushDefault"
    ):
        release_identity.validate_destination()

    _git(tmp_path, "config", "--unset", "remote.pushDefault")
    _git(
        tmp_path, "config", "url.https://evil.example/.insteadOf", "https://github.com/"
    )
    with pytest.raises(release_identity.ReleaseIdentityError, match="insteadOf"):
        release_identity.validate_destination()


def test_validate_destination_rejects_push_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "remote", "add", "origin", release_identity.CANONICAL_HTTPS_URL)
    _git(
        tmp_path,
        "config",
        "url.https://evil.example/.pushInsteadOf",
        "https://github.com/",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(release_identity.ReleaseIdentityError, match="pushInsteadOf"):
        release_identity.validate_destination()


def test_validate_destination_redacts_credentials_in_wrong_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init")
    _git(
        tmp_path,
        "remote",
        "add",
        "origin",
        "https://user:secret@example.com/repo.git",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(release_identity.ReleaseIdentityError) as excinfo:
        release_identity.validate_destination()

    message = str(excinfo.value)
    assert "secret" not in message
    assert "https://<redacted>@example.com/repo.git" in message


def test_query_old_workflow_runs_uses_get_and_checks_complete_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        page = next(
            part.removeprefix("page=") for part in args if part.startswith("page=")
        )
        stdout = (
            '{"total_count": 101, "workflow_runs": ['
            + ",".join(
                '{"status":"completed","workflow_id":339307243}'
                for _ in range(100 if page == "1" else 1)
            )
            + "]}"
        )
        return type("Proc", (), {"returncode": 0, "stdout": stdout})()

    monkeypatch.setattr(release_identity.subprocess, "run", fake_run)

    runs = release_identity.query_old_workflow_runs()

    assert len(runs) == 101
    assert calls[0][0:5] == ["gh", "api", "--hostname", "github.com", "--method"]
    assert calls[0][5] == "GET"
    assert any("workflows/339307243/runs" in part for part in calls[0])
    assert all("tag-main.yml" not in " ".join(call) for call in calls)
    assert any("page=2" in call for call in calls)


def test_query_old_workflow_runs_rejects_wrong_workflow_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args, **kwargs):
        stdout = (
            '{"total_count": 1, "workflow_runs": '
            '[{"status":"completed","workflow_id":123}]}'
        )
        return type("Proc", (), {"returncode": 0, "stdout": stdout})()

    monkeypatch.setattr(release_identity.subprocess, "run", fake_run)

    with pytest.raises(release_identity.ReleaseIdentityError, match="workflow_id"):
        release_identity.query_old_workflow_runs()


def test_query_old_workflow_runs_rejects_truncated_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args, **kwargs):
        stdout = (
            '{"total_count": 101, "workflow_runs": '
            '[{"status":"completed","workflow_id":339307243}]}'
        )
        return type("Proc", (), {"returncode": 0, "stdout": stdout})()

    monkeypatch.setattr(release_identity.subprocess, "run", fake_run)

    with pytest.raises(release_identity.ReleaseIdentityError, match="incomplete"):
        release_identity.query_old_workflow_runs()


class TestReleaseTargetBytes:
    def test_canonical_bytes(self) -> None:
        assert release_identity.parse_release_target(b"1.2.3\n").text == "1.2.3"

    @pytest.mark.parametrize(
        "raw",
        [
            b"1.2.3",
            b"1.2.3\r\n",
            b"1.2.3\n\n",
            b"01.2.3\n",
            b" 1.2.3\n",
            b"v1.2.3\n",
            b"1.2.3.dev1\n",
            b"\xef\xbb\xbf1.2.3\n",
        ],
    )
    def test_noncanonical_bytes_refuse(self, raw: bytes) -> None:
        with pytest.raises(release_identity.ReleaseIdentityError, match="target"):
            release_identity.parse_release_target(raw)


class TestApprovedReceiptBinding:
    def test_state_change_during_build_cannot_write_receipt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from argparse import Namespace
        from contextlib import nullcontext
        from types import SimpleNamespace

        tag = SimpleNamespace(
            name="v1.2.3", metadata=object(), peeled_oid="a" * 40, object_oid="b" * 40
        )
        states = iter([("a" * 40, "c" * 40, [tag]), ("a" * 40, "d" * 40, [tag])])
        monkeypatch.setattr(
            release_identity, "fetch_remote_state", lambda: next(states)
        )
        monkeypatch.setattr(release_identity, "validate_destination", lambda: None)
        monkeypatch.setattr(release_identity, "validate_state", lambda *args: None)
        monkeypatch.setattr(
            release_identity, "ensure_clean_context", lambda source: "e" * 40
        )
        monkeypatch.setattr(
            release_identity,
            "create_git_tree_context",
            lambda source: nullcontext(str(tmp_path)),
        )

        class Builder:
            def build(self, context: Path, version: str, source: str) -> list:
                return [
                    SimpleNamespace(variant="default"),
                    SimpleNamespace(variant="aquacast"),
                ]

        receipt = tmp_path / "receipt.json"
        with pytest.raises(
            release_identity.ReleaseIdentityError, match="state changed"
        ):
            release_identity.build_receipt(
                Namespace(version="1.2.3", source="a" * 40, receipt=str(receipt)),
                docker_builder=Builder(),
                approved_tag_object="b" * 40,
                approved_release_state="c" * 40,
            )
        assert not receipt.exists()

    def test_explicit_variant_for_immutable_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        def probe(image: str, version: str, source: str, *, variant: str) -> object:
            assert variant == "aquacast"
            return SimpleNamespace(
                variant=variant,
                mutable_tag=image,
                image_id=image,
                labels={},
                runtime_import_version=version,
            )

        monkeypatch.setattr(
            release_identity,
            "_load_image_probe",
            lambda: SimpleNamespace(probe_image=probe),
        )
        result = release_identity.inspect_image(
            "sha256:" + "a" * 64, "1.2.3", "b" * 40, variant="aquacast"
        )
        assert result.variant == "aquacast"
