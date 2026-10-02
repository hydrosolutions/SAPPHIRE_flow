from __future__ import annotations

import builtins
import importlib.machinery
import importlib.metadata as md
import importlib.resources as resources
import importlib.util
import json
import sys
from pathlib import Path

import pytest

import sapphire_flow

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "image_identity_probe.py"
spec = importlib.util.spec_from_file_location("image_identity_probe", _TOOL_PATH)
assert spec is not None and spec.loader is not None
image_identity_probe = importlib.util.module_from_spec(spec)
sys.modules["image_identity_probe"] = image_identity_probe
spec.loader.exec_module(image_identity_probe)


class FakeDocker:
    def __init__(
        self,
        *,
        labels: dict[str, str] | None = None,
        image_id: str = "sha256:" + "a" * 64,
        run_output: str = "0.dev0+g" + "a" * 40,
        fail_run: bool = False,
    ) -> None:
        self.labels = labels or {
            "org.opencontainers.image.version": "0.dev0+g" + "a" * 40,
            "org.opencontainers.image.revision": "a" * 40,
        }
        self.image_id = image_id
        self.run_output = run_output
        self.fail_run = fail_run
        self.calls: list[tuple[list[str], str | None]] = []

    def run(self, args: list[str], *, stdin: str | None = None) -> str:
        self.calls.append((args, stdin))
        if args[:2] == ["image", "inspect"]:
            return json.dumps(
                [{"Id": self.image_id, "Config": {"Labels": self.labels}}]
            )
        if args[:1] == ["run"]:
            if self.fail_run:
                raise image_identity_probe.ImageIdentityProbeError("payload failed")
            if stdin is None:
                raise image_identity_probe.ImageIdentityProbeError("missing stdin")
            if "importlib.resources" not in stdin:
                raise image_identity_probe.ImageIdentityProbeError(
                    "resource check absent"
                )
            return self.run_output
        raise AssertionError(args)


def test_probe_uses_immutable_image_id_for_runtime_check() -> None:
    expected = "0.dev0+g" + "a" * 40
    fake = FakeDocker(run_output=expected)

    image = image_identity_probe.probe_image(
        "sapphire-flow:ci",
        expected,
        "a" * 40,
        variant="default",
        runner=fake,
    )

    assert (
        image.image_id
        == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    run_args = fake.calls[1][0]
    assert (
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        in run_args
    )
    assert "sapphire-flow:ci" not in run_args
    assert run_args[:7] == [
        "run",
        "--rm",
        "-i",
        "--user",
        "app",
        "--entrypoint",
        "/app/.venv/bin/python",
    ]
    assert run_args[-1] == "-"


@pytest.mark.parametrize(
    "labels",
    [
        {"org.opencontainers.image.revision": "a" * 40},
        {
            "org.opencontainers.image.version": "wrong",
            "org.opencontainers.image.revision": "a" * 40,
        },
        {"org.opencontainers.image.version": "0.dev0+g" + "a" * 40},
        {
            "org.opencontainers.image.version": "0.dev0+g" + "a" * 40,
            "org.opencontainers.image.revision": "b" * 40,
        },
    ],
)
def test_probe_rejects_wrong_or_missing_labels(labels: dict[str, str]) -> None:
    with pytest.raises(image_identity_probe.ImageIdentityProbeError, match="label"):
        image_identity_probe.probe_image(
            "sapphire-flow:ci",
            "0.dev0+g" + "a" * 40,
            "a" * 40,
            variant="default",
            runner=FakeDocker(labels=labels),
        )


@pytest.mark.parametrize("image_id", ["", "sha256:" + "A" * 64, "abc" + "a" * 64])
def test_probe_rejects_invalid_image_id(image_id: str) -> None:
    with pytest.raises(image_identity_probe.ImageIdentityProbeError, match="image ID"):
        image_identity_probe.probe_image(
            "sapphire-flow:ci",
            "0.dev0+g" + "a" * 40,
            "a" * 40,
            variant="default",
            runner=FakeDocker(image_id=image_id),
        )


def test_probe_rejects_runtime_version_mismatch() -> None:
    with pytest.raises(image_identity_probe.ImageIdentityProbeError, match="runtime"):
        image_identity_probe.probe_image(
            "sapphire-flow:ci",
            "0.dev0+g" + "a" * 40,
            "a" * 40,
            variant="default",
            runner=FakeDocker(run_output="wrong"),
        )


def test_probe_propagates_payload_failure() -> None:
    with pytest.raises(
        image_identity_probe.ImageIdentityProbeError, match="payload failed"
    ):
        image_identity_probe.probe_image(
            "sapphire-flow:ci",
            "0.dev0+g" + "a" * 40,
            "a" * 40,
            variant="aquacast",
            runner=FakeDocker(fail_run=True),
        )


def test_default_payload_checks_resources_and_forbidden_specs() -> None:
    payload = image_identity_probe._default_payload()
    assert 'resources.files("sapphire_flow")' in payload
    assert 'root / "py.typed"' in payload
    assert 'root / "models" / "aquacast" / "configs" / "cmal_pool_pt.yaml"' in payload
    assert 'root / "models" / "aquacast" / "configs" / "cmal_small.yaml"' in payload
    assert "find_spec(module_name) is not None" in payload
    assert "import sapphire_flow.models.aquacast" not in payload


def test_aquacast_payload_checks_real_capability_imports() -> None:
    payload = image_identity_probe._aquacast_payload()
    assert "import aquacast" in payload
    assert "import torch" in payload
    assert "from sapphire_flow.models.aquacast import AquacastShim" in payload
    assert "CmalPoolPT" in payload
    assert "CmalSmall" in payload
    assert "issubclass" in payload
    assert "discovery" not in payload
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text()
    assert "CmalPoolPT" in dockerfile
    assert "CmalSmall" in dockerfile
    assert "import aquacast, torch" in dockerfile
    assert "discovery" not in dockerfile


def test_aquacast_payload_executes_supported_registration_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_required_resources(tmp_path)
    monkeypatch.setenv("EXPECTED_VERSION", sapphire_flow.__version__)
    monkeypatch.setattr(resources, "files", lambda package: tmp_path)
    monkeypatch.setitem(sys.modules, "aquacast", object())
    monkeypatch.setitem(sys.modules, "torch", object())

    exec(image_identity_probe._aquacast_payload(), {"__name__": "__main__"})


def test_shared_payload_executes_real_resource_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sapphire_flow

    monkeypatch.setenv("EXPECTED_VERSION", sapphire_flow.__version__)

    exec(image_identity_probe._shared_identity_payload(), {"__name__": "__main__"})


def test_default_payload_rejects_forbidden_dependency_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sapphire_flow

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str) -> object:
        if name == "aquacast":
            return importlib.machinery.ModuleSpec(name, loader=None)
        return original_find_spec(name)

    monkeypatch.setenv("EXPECTED_VERSION", sapphire_flow.__version__)
    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(SystemExit, match="forbidden optional dependency"):
        exec(image_identity_probe._default_payload(), {"__name__": "__main__"})


def _write_required_resources(root: Path) -> None:
    (root / "models" / "aquacast" / "configs").mkdir(parents=True)
    (root / "py.typed").write_text("")
    (root / "models" / "aquacast" / "configs" / "cmal_pool_pt.yaml").write_text(
        "x: 1\n"
    )
    (root / "models" / "aquacast" / "configs" / "cmal_small.yaml").write_text("x: 1\n")


def test_default_payload_rejects_missing_package_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "py.typed").write_text("")
    monkeypatch.setenv("EXPECTED_VERSION", sapphire_flow.__version__)
    monkeypatch.setattr(resources, "files", lambda package: tmp_path)

    with pytest.raises(SystemExit, match="missing package resources"):
        exec(image_identity_probe._default_payload(), {"__name__": "__main__"})


def test_default_payload_rejects_metadata_version_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_required_resources(tmp_path)
    monkeypatch.setenv("EXPECTED_VERSION", sapphire_flow.__version__)
    monkeypatch.setattr(resources, "files", lambda package: tmp_path)
    monkeypatch.setattr(md, "version", lambda distribution: "wrong")

    with pytest.raises(SystemExit, match="version mismatch"):
        exec(image_identity_probe._default_payload(), {"__name__": "__main__"})


def test_aquacast_payload_propagates_capability_import_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_required_resources(tmp_path)
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals_: object = None,
        locals_: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "aquacast":
            raise ModuleNotFoundError("No module named 'aquacast'")
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setenv("EXPECTED_VERSION", sapphire_flow.__version__)
    monkeypatch.setattr(resources, "files", lambda package: tmp_path)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ModuleNotFoundError, match="aquacast"):
        exec(image_identity_probe._aquacast_payload(), {"__name__": "__main__"})


def test_subprocess_docker_runner_passes_stdin_and_returns_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fake_run(args: list[str], **kwargs: object) -> object:
        calls.append((args, kwargs))
        return type("Proc", (), {"returncode": 0, "stdout": " ok ", "stderr": ""})()

    monkeypatch.setattr(image_identity_probe.subprocess, "run", fake_run)

    output = image_identity_probe.SubprocessDockerRunner().run(["run"], stdin="payload")

    assert output == "ok"
    assert calls == [
        (
            ["docker", "run"],
            {"input": "payload", "text": True, "capture_output": True, "check": False},
        )
    ]


def test_subprocess_docker_runner_reports_stderr_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> object:
        return type(
            "Proc", (), {"returncode": 7, "stdout": "stdout", "stderr": "stderr"}
        )()

    monkeypatch.setattr(image_identity_probe.subprocess, "run", fake_run)

    with pytest.raises(image_identity_probe.ImageIdentityProbeError, match="stderr"):
        image_identity_probe.SubprocessDockerRunner().run(["run"], stdin="payload")
