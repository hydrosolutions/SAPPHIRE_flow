from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "exporter_test", ROOT / "tools/qualify_image_exporter.py"
)
assert _spec is not None and _spec.loader is not None
helper = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = helper
_spec.loader.exec_module(helper)


def host() -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
    }


def public_archive(path: Path, nonce: str, *, omitted: int | None = None) -> list[str]:
    blobs: dict[str, bytes] = {}

    def blob(raw: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        blobs["blobs/sha256/" + digest[7:]] = raw
        return digest

    layer = b"shared public layer"
    layer_id = blob(gzip.compress(layer, mtime=0))
    entries, roots, ids = [], [], []
    for index, variant in enumerate(("default", "aquacast")):
        config = blob(
            json.dumps(
                {
                    "architecture": "amd64",
                    "os": "linux",
                    "config": {
                        "Labels": {"exporter.nonce": nonce, "exporter.variant": variant}
                    },
                    "rootfs": {
                        "type": "layers",
                        "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
                    },
                }
            ).encode()
        )
        manifest = blob(
            json.dumps(
                {"config": {"digest": config}, "layers": [{"digest": layer_id}]}
            ).encode()
        )
        raw_id = blob(json.dumps({"manifests": [{"digest": manifest}]}).encode())
        ids.append(raw_id)
        if index != omitted:
            roots.append({"digest": raw_id})
        entries.append(
            {
                "Config": "blobs/sha256/" + config[7:],
                "RepoTags": None,
                "Layers": ["blobs/sha256/" + layer_id[7:]],
            }
        )
    blobs["index.json"] = json.dumps({"manifests": roots}).encode()
    blobs["manifest.json"] = json.dumps(entries).encode()
    with tarfile.open(path, "w") as archive:
        for name, raw in blobs.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return ids


class TestRoute:
    @pytest.mark.parametrize(
        "selected", ["deliver-only", "load-images", "exporter-smoke-only"]
    )
    def test_exact_routes(self, selected: str) -> None:
        assert (
            helper.parse_route({"inputs": {"delivery_mode": selected}}, selected).value
            == selected
        )

    @pytest.mark.parametrize(
        "selected",
        [
            "DELIVER-ONLY",
            "Deliver-only",
            " deliver-only",
            "deliver-only ",
            "dеliver-only",
            "",
            "EXPORTER-SMOKE-ONLY",
            "other",
        ],
    )
    def test_case_whitespace_and_confusables_fail(self, selected: str) -> None:
        with pytest.raises(helper.BootstrapError, match="exact execution route"):
            helper.parse_route({"inputs": {"delivery_mode": selected}}, selected)

    @pytest.mark.parametrize(
        "inputs",
        [
            {},
            {"delivery_mode": "load-images"},
            {"delivery_mode": "deliver-only", "new": False},
            {"delivery_mode": True},
        ],
    )
    def test_event_input_schema_must_match(self, inputs: dict) -> None:
        with pytest.raises(helper.BootstrapError, match="inputs differ"):
            helper.parse_route({"inputs": inputs}, "deliver-only")

    def test_public_helper_rejects_package_secret(self, tmp_path: Path) -> None:
        with pytest.raises(helper.BootstrapError, match="private credential"):
            helper.route_from_env({"RECAP_DG_CLIENT_TOKEN": "synthetic"})

    @pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}'])
    def test_strict_json(self, raw: str) -> None:
        with pytest.raises(helper.BootstrapError, match="duplicate|nonfinite"):
            helper.strict_json(raw)


class TestDaemonConfiguration:
    def test_merge_preserves_unrelated_keys_and_features(self) -> None:
        before = {"log-driver": "json-file", "features": {"other": False}}
        assert helper.containerd_config(before) == {
            "log-driver": "json-file",
            "features": {"other": False, "containerd-snapshotter": True},
        }
        assert before["features"] == {"other": False}

    @pytest.mark.parametrize(
        "value",
        [
            [],
            {"debug": True},
            {"storage-driver": "overlay2"},
            {"data-root": "/other"},
            {"containerd": "/other.sock"},
            {"hosts": ["tcp://x"]},
            {"features": []},
            {"features": {"containerd-snapshotter": "true"}},
        ],
    )
    def test_conflicting_configuration_is_not_rewritten(self, value: object) -> None:
        with pytest.raises(helper.BootstrapError, match="configuration"):
            helper.containerd_config(value)

    def test_atomic_merge_and_backup_use_disposable_files(self, tmp_path: Path) -> None:
        directory = tmp_path / "docker"
        directory.mkdir(mode=0o700)
        path = directory / "daemon.json"
        original = b'{"log-driver":"json-file"}\n'
        path.write_bytes(original)
        path.chmod(0o640)
        helper.configure_file(path, owner=os.getuid())
        assert (
            json.loads(path.read_text())["features"]["containerd-snapshotter"] is True
        )
        assert path.stat().st_mode & 0o777 == 0o640
        assert (
            next(directory.glob(".sapphire-exporter-backup-*")).read_bytes() == original
        )
        helper.configure_file(path, owner=os.getuid())
        assert len(list(directory.glob(".sapphire-exporter-backup-*"))) == 1

    def test_symlink_cannot_replace_external_file(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.json"
        target = tmp_path / "untouched"
        target.write_text("{}")
        path.symlink_to(target)
        with pytest.raises(
            helper.BootstrapError, match="unsafe daemon configuration file"
        ):
            helper.configure_file(path, owner=os.getuid())
        assert target.read_text() == "{}"

    @pytest.mark.parametrize(
        "flags",
        [
            "-H fd:// --containerd=/run/containerd/containerd.sock",
            "--host=fd:// --containerd /run/containerd/containerd.sock",
            "-H fd://",
        ],
    )
    def test_supported_stock_containerd_socket_is_not_a_custom_override(
        self, flags: str
    ) -> None:
        helper.check_service(
            "{ path=/usr/bin/dockerd ; argv[]=/usr/bin/dockerd "
            + flags
            + " ; ignore_errors=no ; pid=1 ; status=0/0 }",
            "",
            "",
            "journal",
            "inherit",
        )

    @pytest.mark.parametrize(
        "flags",
        [
            "-H tcp://0.0.0.0:2375",
            "-H fd:// --containerd=/tmp/custom.sock",
            "-H fd:// --data-root=/tmp",
            "-H fd:// --storage-driver=overlay2",
        ],
    )
    def test_custom_service_flags_stop(self, flags: str) -> None:
        with pytest.raises(helper.BootstrapError, match="unsupported Docker service"):
            helper.check_service(
                "{ path=/usr/bin/dockerd ; argv[]=/usr/bin/dockerd "
                + flags
                + " ; ignore_errors=no }",
                "",
                "",
                "journal",
                "inherit",
            )


class TestPreparationSchema:
    @pytest.mark.parametrize("key", ["route", "engine", "exporter-smoke"])
    @pytest.mark.parametrize("outcome", [None, "skipped", "failure", "cancelled"])
    def test_each_new_key_is_unconditionally_required(
        self, key: str, outcome: str | None
    ) -> None:
        values = dict.fromkeys(
            (
                "checkout",
                "uv",
                "buildx",
                "trivy",
                "syft",
                "route",
                "engine",
                "exporter-smoke",
            ),
            "success",
        )
        if outcome is None:
            del values[key]
        else:
            values[key] = outcome
        assert not helper.stage.gate_environment_ready(
            {
                "PRIVATE_PREPARATION": json.dumps(values),
                "RECAP_DG_CLIENT_TOKEN": "synthetic",
                "AQUACAST_TOKEN": "synthetic",
            }
        )

    def test_only_complete_success_record_passes(self) -> None:
        values = dict.fromkeys(
            (
                "checkout",
                "uv",
                "buildx",
                "trivy",
                "syft",
                "route",
                "engine",
                "exporter-smoke",
            ),
            "success",
        )
        env = {
            "PRIVATE_PREPARATION": json.dumps(values),
            "RECAP_DG_CLIENT_TOKEN": "synthetic",
            "AQUACAST_TOKEN": "synthetic",
        }
        assert helper.stage.gate_environment_ready(env)
        values["extra"] = "success"
        env["PRIVATE_PREPARATION"] = json.dumps(values)
        assert not helper.stage.gate_environment_ready(env)


class TestPublicArchive:
    def test_real_validator_accepts_both_index_ids_and_shared_layer(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "images.tar"
        ids = public_archive(path, "synthetic")
        helper.check_public_archive(path)
        helper.stage.require_untagged_pair(path)
        archive = helper.transfer.tool("image_archive")
        descriptions = [archive.describe_image_archive(path, raw_id) for raw_id in ids]
        assert len({item.config_digest for item in descriptions}) == 2
        assert archive.unpacked_layer_bytes(path, helper.ARCHIVE_LIMIT) == len(
            b"shared public layer"
        )
        assert all(
            item.config_digest != raw_id
            for item, raw_id in zip(descriptions, ids, strict=True)
        )

    def test_shared_gzip_layer_expansion_respects_limit(self, tmp_path: Path) -> None:
        path = tmp_path / "images.tar"
        public_archive(path, "synthetic")
        with pytest.raises(ValueError, match="unpacked image layers exceed capacity"):
            helper.transfer.tool("image_archive").unpacked_layer_bytes(
                path, len(b"shared public layer") - 1
            )

    @pytest.mark.parametrize("omitted", [0, 1])
    def test_classic_multi_id_omission_remains_rejected(
        self, tmp_path: Path, omitted: int
    ) -> None:
        path = tmp_path / "images.tar"
        ids = public_archive(path, "synthetic", omitted=omitted)
        for raw_id in ids:
            with pytest.raises(
                ValueError, match="OCI and Docker manifest configs differ"
            ):
                helper.transfer.tool("image_archive").describe_image_archive(
                    path, raw_id
                )

    @pytest.mark.parametrize(
        "entries",
        [None, {}, [], [{}], [{}, {}, {}], [{"RepoTags": ["tag"]}, {}], [1, {}]],
    )
    def test_shared_untagged_predicate_rejects_same_invalid_shapes(
        self, tmp_path: Path, entries: object
    ) -> None:
        path = tmp_path / "image.tar"
        with tarfile.open(path, "w") as archive:
            if entries is not None:
                raw = json.dumps(entries).encode()
                item = tarfile.TarInfo("manifest.json")
                item.size = len(raw)
                archive.addfile(item, io.BytesIO(raw))
        with pytest.raises(
            KeyError if entries is None else helper.transfer.TransferError,
            match="manifest.json" if entries is None else "exactly two untagged",
        ):
            helper.stage.require_untagged_pair(path)

    @pytest.mark.parametrize("entry", [{}, {"RepoTags": None}, {"RepoTags": []}])
    def test_shared_untagged_predicate_preserves_accepted_shapes(
        self, tmp_path: Path, entry: dict
    ) -> None:
        path = tmp_path / "image.tar"
        with tarfile.open(path, "w") as archive:
            raw = json.dumps([entry, entry]).encode()
            item = tarfile.TarInfo("manifest.json")
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
        helper.stage.require_untagged_pair(path)


class TestWorkflowContract:
    def test_single_input_and_private_positive_route_guard(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        event = workflow.get("on", workflow.get(True))["workflow_dispatch"]
        assert set(event["inputs"]) == {"delivery_mode"}
        assert event["inputs"]["delivery_mode"]["options"] == [
            "deliver-only",
            "load-images",
            "exporter-smoke-only",
        ]
        assert event["inputs"]["delivery_mode"]["default"] == "deliver-only"
        steps = workflow["jobs"]["verify"]["steps"]
        private_route = (
            "steps.route.outcome == 'success' && "
            "(inputs.delivery_mode == 'deliver-only' || "
            "inputs.delivery_mode == 'load-images')"
        )
        expected = {
            "verify": "${{ always() && !cancelled() && "
            "steps.checkout.outcome == 'success' && " + private_route + " }}",
            "preflight": "${{ always() && !cancelled() && "
            "steps.checkout.outcome == 'success' && " + private_route + " }}",
            "transfer": "${{ always() && !cancelled() && "
            + private_route
            + " && steps.transfer_admission.outcome == 'success' }}",
        }
        actual = private_steps()
        assert set(actual) == set(expected)
        for phase, step in actual.items():
            assert step["if"] == expected[phase]
        assert_secret_scope(workflow["jobs"]["verify"], actual)
        assert not mentions_secret_context(workflow.get("env"))
        indexed = {step["id"]: step for step in steps if "id" in step}
        assert steps.index(indexed["uv"]) < steps.index(indexed["route"])
        assert indexed["route"]["timeout-minutes"] == 15
        assert indexed["engine"]["timeout-minutes"] == 3
        assert indexed["exporter-smoke"]["timeout-minutes"] == 5
        assert indexed["buildx"]["with"]["cleanup"] is False
        assert (
            indexed["buildx"]["with"]["driver-opts"] == "image=" + helper.BUILDKIT_IMAGE
        )

    def test_public_flags_and_capture_match_production_not_iidfile(self) -> None:
        source = (ROOT / "tools/private_verification_gates.sh").read_text()
        fragment = source[
            source.index("  docker buildx build") : source.index(
                "  local image", source.index("  docker buildx build")
            )
        ]
        production = shlex.split(fragment)
        smoke = helper.build_args(Path("context"), "exporter-smoke-test:default")
        for flag in ("--platform", "--builder"):
            assert (
                smoke[smoke.index(flag) + 1] == production[production.index(flag) + 1]
            )
        for flag in ("--load", "--pull", "--provenance=false", "--sbom=false"):
            assert flag in smoke and flag in production
        assert "--iidfile" not in source
        assert "image=$(docker image inspect --format '{{.Id}}' \"$tag\")" in source


class TestHostAndAccounting:
    @pytest.mark.parametrize("key", helper.OVERRIDES)
    def test_all_context_overrides_rejected(self, key: str) -> None:
        with pytest.raises(helper.BootstrapError, match="override"):
            helper.require_host({**host(), key: "override"})

    def test_monitor_observes_root_even_without_containerd_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        points = [
            {
                "/": helper.DiskPoint(device=1, free=10 * 1024**3),
                "/var/lib/containerd": None,
            },
            {
                "/": helper.DiskPoint(device=1, free=7 * 1024**3),
                "/var/lib/containerd": None,
            },
        ]
        monkeypatch.setattr(
            helper.GrowthMonitor, "snapshot", lambda self: points.pop(0)
        )
        monitor = helper.GrowthMonitor(tmp_path, tmp_path)
        with pytest.raises(helper.BootstrapError, match="growth STOP"):
            monitor.check()

    def test_same_device_is_not_double_counted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        points = [
            {
                "/": helper.DiskPoint(device=1, free=10 * 1024**3),
                "/docker": helper.DiskPoint(device=1, free=10 * 1024**3),
            },
            {
                "/": helper.DiskPoint(device=1, free=9 * 1024**3),
                "/docker": helper.DiskPoint(device=1, free=9 * 1024**3),
            },
        ]
        monkeypatch.setattr(
            helper.GrowthMonitor, "snapshot", lambda self: points.pop(0)
        )
        helper.GrowthMonitor(tmp_path, tmp_path).check()


class FakeDocker:
    def __init__(
        self, directory: Path, *, version: str = "28.0.4", containers: str = "0"
    ) -> None:
        self.directory = directory
        self.version = version
        self.containers = containers
        self.mode = "classic"
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:3] == ["docker", "context", "show"]:
            return "default"
        if args[:3] == ["docker", "context", "inspect"]:
            return json.dumps(helper.SOCKET)
        if args[:2] == ["docker", "version"]:
            return self.version
        if args[:2] == ["docker", "info"]:
            return {
                "{{.Containers}}": self.containers,
                "{{.Swarm.LocalNodeState}}": "inactive",
                "{{.Driver}}": "overlayfs" if self.mode == "containerd" else "overlay2",
                "{{json .DriverStatus}}": (
                    '[["driver-type","io.containerd.snapshotter.v1"]]'
                )
                if self.mode == "containerd"
                else "[]",
            }[args[-1]]
        if args[0] == "systemctl":
            key = args[-2].split("=", 1)[1]
            return {
                "ExecStart": (
                    "{ path=/usr/bin/dockerd ; argv[]=/usr/bin/dockerd -H fd:// "
                    "--containerd=/run/containerd/containerd.sock ; "
                    "ignore_errors=no ; pid=1 }"
                ),
                "Environment": "",
                "EnvironmentFiles": "",
                "StandardOutput": "journal",
                "StandardError": "inherit",
            }[key]
        if args[:3] == ["sudo", "--non-interactive", sys.executable]:
            return ""
        if args[:4] == ["sudo", "--non-interactive", "systemctl", "restart"]:
            self.mode = "containerd"
            return ""
        raise AssertionError(args)


class TestEnginePreparation:
    def test_only_the_supported_stock_service_is_restarted(
        self, tmp_path: Path
    ) -> None:
        fake = FakeDocker(tmp_path)
        helper.prepare_engine(ROOT, host(), fake.run)
        assert fake.mode == "containerd"
        assert [call for call in fake.calls if "restart" in call] == [
            ["sudo", "--non-interactive", "systemctl", "restart", "docker.service"]
        ]

    def test_already_containerd_is_not_restarted(self, tmp_path: Path) -> None:
        fake = FakeDocker(tmp_path)
        fake.mode = "containerd"
        helper.prepare_engine(ROOT, host(), fake.run)
        assert not any("restart" in call for call in fake.calls)

    @pytest.mark.parametrize("version,containers", [("29.8.2", "0"), ("28.0.4", "1")])
    def test_unapproved_version_or_any_workload_prevents_mutation(
        self, tmp_path: Path, version: str, containers: str
    ) -> None:
        fake = FakeDocker(tmp_path, version=version, containers=containers)
        with pytest.raises(helper.BootstrapError, match="version|workloads"):
            helper.prepare_engine(ROOT, host(), fake.run)
        assert not any(call[0] == "sudo" for call in fake.calls)

    def test_failed_restart_is_not_retried_or_accepted(self, tmp_path: Path) -> None:
        fake = FakeDocker(tmp_path)

        def run(args: list[str]) -> str:
            if "restart" in args:
                raise helper.BootstrapError("synthetic restart failure")
            return fake.run(args)

        with pytest.raises(helper.BootstrapError, match="restart failure"):
            helper.prepare_engine(ROOT, host(), run)
        assert fake.mode == "classic"


class TestBuilderIdentity:
    @pytest.mark.parametrize(
        "identity",
        [helper.BUILDKIT_INDEX, helper.BUILDKIT_MANIFEST, helper.BUILDKIT_CONFIG],
    )
    def test_correct_digest_kind_in_pinned_amd64_graph(self, identity: str) -> None:
        def run(args: list[str]) -> str:
            if args == ["docker", "buildx", "version"]:
                return "github.com/docker/buildx v0.38.0 synthetic"
            if args == ["docker", "buildx", "inspect", helper.BUILDER]:
                return (
                    "Driver: docker-container\nStatus: running\n"
                    "BuildKit version: v0.33.1\n"
                    "Endpoint: unix:///var/run/docker.sock"
                )
            return (
                helper.BUILDKIT_IMAGE if args[-2] == "{{.Config.Image}}" else identity
            )

        helper.require_builder(run)
        with pytest.raises(helper.BootstrapError, match="image differs"):
            helper.require_builder(
                lambda args: (
                    "sha256:" + "f" * 64 if args[-2] == "{{.Image}}" else run(args)
                )
            )


class TestPublicSource:
    def test_smoke_checks_canonical_source_without_borrowing_delivery_approval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        event = tmp_path / "event.json"
        event.write_text(
            json.dumps({"inputs": {"delivery_mode": "exporter-smoke-only"}})
        )
        source = "a" * 40
        env = {
            "GITHUB_EVENT_PATH": str(event),
            "EXPORTER_ROUTE": "exporter-smoke-only",
            "GITHUB_SHA": source,
            "GITHUB_REF": "refs/tags/v1.2.3",
            "GITHUB_ACTOR": "owner",
            "GITHUB_TRIGGERING_ACTOR": "owner",
            "GITHUB_REPOSITORY": helper.transfer.REPOSITORY,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_JOB": "verify",
            "GITHUB_WORKFLOW_SHA": source,
            "GITHUB_WORKFLOW_REF": helper.transfer.REPOSITORY
            + "/.github/workflows/private-verification.yml@refs/tags/v1.2.3",
        }
        calls: list[str] = []
        monkeypatch.setattr(
            helper.stage,
            "require_release",
            lambda *args: pytest.fail("smoke borrowed private admission"),
        )
        monkeypatch.setattr(
            helper.release, "validate_destination", lambda: calls.append("origin")
        )
        monkeypatch.setattr(
            helper.release, "ensure_clean_context", lambda value: calls.append("clean")
        )
        monkeypatch.setattr(
            helper.release,
            "fetch_remote_state",
            lambda: (
                source,
                "b" * 40,
                [SimpleNamespace(name="v1.2.3", metadata=object(), peeled_oid=source)],
            ),
        )
        monkeypatch.setattr(
            helper.release, "validate_state", lambda *args: calls.append("state")
        )
        monkeypatch.setattr(
            helper.release, "ensure_source_on_main", lambda *args: calls.append("main")
        )
        monkeypatch.setattr(
            helper.release,
            "read_release_target",
            lambda value: SimpleNamespace(text="1.2.3"),
        )
        assert helper.require_source(tmp_path, env) == helper.Route.SMOKE
        assert calls == ["origin", "clean", "state", "main"]
        env["GITHUB_WORKFLOW_SHA"] = "c" * 40
        with pytest.raises(helper.BootstrapError, match="noncanonical"):
            helper.require_source(tmp_path, env)

    def test_smoke_is_not_a_release_binding_mode(self) -> None:
        with pytest.raises(ValueError, match="not a valid DeliveryMode"):
            helper.transfer.DeliveryMode("exporter-smoke-only")


class TestSmokeWithFakeCommands:
    @pytest.mark.parametrize(
        "change_tag,reject_expansion", [(False, False), (True, False), (False, True)]
    )
    def test_same_raw_ids_shared_production_save_and_exact_cleanup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        change_tag: bool,
        reject_expansion: bool,
    ) -> None:
        from types import SimpleNamespace

        root, runner = tmp_path / "checkout", tmp_path / "runner"
        root.mkdir()
        runner.mkdir()
        nonce = "c" * 32
        monkeypatch.setattr(helper.uuid, "uuid4", lambda: SimpleNamespace(hex=nonce))
        template = tmp_path / "template.tar"
        ids = public_archive(template, nonce)
        if reject_expansion:
            archive_tool = helper.transfer.tool("image_archive")
            unpacked = archive_tool.unpacked_layer_bytes

            def smaller_limit(path: Path, limit: int) -> int:
                assert limit == helper.ARCHIVE_LIMIT
                return unpacked(path, len(b"shared public layer") - 1)

            monkeypatch.setattr(archive_tool, "unpacked_layer_bytes", smaller_limit)
        monkeypatch.setattr(helper, "engine_state", lambda run: "containerd")
        monkeypatch.setattr(helper, "require_builder", lambda run: None)
        calls: list[list[str]] = []
        tags: dict[str, str] = {}
        saves: list[list[str]] = []
        inspections = 0

        def run(args: list[str]) -> str:
            nonlocal inspections
            calls.append(args)
            if args[:2] == ["docker", "info"]:
                return str(tmp_path)
            if args[:3] == ["docker", "image", "ls"]:
                return ""
            if args[:3] == ["docker", "buildx", "build"]:
                tag = args[args.index("--tag") + 1]
                tags[tag] = ids[0 if tag.endswith(":default") else 1]
                dockerfile = (Path(args[-1]) / "Dockerfile").read_text()
                assert "RUN " not in dockerfile and dockerfile.startswith(
                    "FROM scratch\nCOPY "
                )
                return ""
            if args[:3] == ["docker", "image", "inspect"]:
                if args[-2] == "{{.Os}}/{{.Architecture}}":
                    return "linux/amd64"
                inspections += 1
                if change_tag and inspections == 2:
                    return "sha256:" + "f" * 64
                return tags[args[-1]]
            if args[:3] == ["docker", "image", "rm"]:
                del tags[args[-1]]
                return ""
            raise AssertionError(args)

        def save(
            raw_ids: list[str],
            output: Path,
            limit: int,
            errors_path: Path,
            *,
            timeout_seconds: float,
            observer: object,
        ) -> None:
            assert callable(observer)
            observer()
            assert len(tags) == 2
            assert raw_ids == ids
            assert limit == helper.ARCHIVE_LIMIT
            assert 0 < timeout_seconds <= 240
            saves.append(raw_ids)
            output.write_bytes(template.read_bytes())

        monkeypatch.setattr(helper.stage, "archive_images", save)
        commands = helper.Commands(root, helper.time.monotonic() + 270)
        monkeypatch.setattr(commands, "run", run)
        env = {**host(), "RUNNER_TEMP": str(runner)}
        if change_tag:
            with pytest.raises(
                helper.BootstrapError, match="mutable image tag changed"
            ):
                helper.smoke(root, env, commands)
            assert saves == []
            assert not any(call[:3] == ["docker", "image", "rm"] for call in calls)
        elif reject_expansion:
            with pytest.raises(
                ValueError, match="unpacked image layers exceed capacity"
            ):
                helper.smoke(root, env, commands)
            assert saves == [ids]
            assert len(tags) == 2
            assert not any(call[:3] == ["docker", "image", "rm"] for call in calls)
            result = json.loads(next(runner.glob("*/result.json")).read_text())
            assert result["status"] == "HOLD"
        else:
            helper.smoke(root, env, commands)
            assert saves == [ids]
            assert tags == {}
            result = json.loads(next(runner.glob("*/result.json")).read_text())
            assert result["status"] == "PASS"
            assert result["unpacked_layer_bytes"] == len(b"shared public layer")
            assert all(
                item["id_kind"] == "oci-descriptor"
                and item["store_mode"] == "containerd"
                for item in result["images"]
            )
            assert not list(runner.glob("*/images.tar"))
            assert (
                len(
                    [
                        call
                        for call in calls
                        if call[:3] == ["docker", "buildx", "build"]
                    ]
                )
                == 2
            )


class TestFixedPublicFailures:
    @pytest.mark.parametrize(
        "error,code",
        [
            (helper.BootstrapError("unexpected Docker version"), "ENGINE_VERSION"),
            (helper.BootstrapError("UNTRUSTED_ERROR_CANARY"), "PUBLIC_PREPARATION"),
            (RuntimeError("UNTRUSTED_ERROR_CANARY"), "PUBLIC_PREPARATION"),
        ],
    )
    def test_cli_reports_only_closed_public_codes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
        error: Exception,
        code: str,
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["qualify_image_exporter.py", "route"])

        def fail(*args: object) -> None:
            raise error

        monkeypatch.setattr(helper, "require_source", fail)
        old_mask = os.umask(0o077)
        try:
            assert helper.main() == 1
        finally:
            os.umask(old_mask)
        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == f"EXPORTER_PREPARATION_FAILED ROUTE {code}\n"


# This evaluator covers only the syntax used by the pinned private-step predicates.
# Full-expression assertions above reject changes to that subset or extra disjuncts.
def private_steps() -> dict[str, dict]:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/private-verification.yml").read_text()
    )
    prefix = "python3 -I tools/staging_release.py "
    return {
        step["run"].removeprefix(prefix): step
        for step in workflow["jobs"]["verify"]["steps"]
        if step.get("run", "").startswith(prefix)
    }


def mentions_secret_context(value: object) -> bool:
    return re.search(r"\bsecrets\b", json.dumps(value), re.IGNORECASE) is not None


def assert_secret_scope(job: dict, private: dict[str, dict]) -> None:
    secret_steps = [step for step in job["steps"] if mentions_secret_context(step)]
    assert secret_steps == [private["verify"], private["transfer"]], (
        "unexpected secret-step set"
    )
    assert not mentions_secret_context(
        {key: value for key, value in job.items() if key != "steps"}
    ), "secret context outside private steps"


def evaluate_private_if(
    expression: str, values: dict[str, str], *, cancelled: bool
) -> bool:
    assert expression.startswith("${{ ") and expression.endswith(" }}")
    source = (
        expression[4:-3]
        .replace("&&", " and ")
        .replace("||", " or ")
        .replace("!", "not ")
    )
    tree = ast.parse(source.strip(), mode="eval")

    def visit(node: ast.AST) -> object:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Attribute):
            return values[ast.unparse(node)]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert not node.args and not node.keywords
            assert node.func.id in {"always", "cancelled"}
            return True if node.func.id == "always" else cancelled
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            items = [bool(visit(item)) for item in node.values]
            return all(items) if isinstance(node.op, ast.And) else any(items)
        if isinstance(node, ast.Compare):
            assert len(node.ops) == len(node.comparators) == 1
            assert isinstance(node.ops[0], ast.Eq)
            left, right = visit(node.left), visit(node.comparators[0])
            assert isinstance(left, str) and isinstance(right, str)
            return left.casefold() == right.casefold()
        raise AssertionError(
            f"unsupported workflow expression node: {type(node).__name__}"
        )

    return bool(visit(tree.body))


def workflow_state(
    selected: str, event_inputs: dict, changes: dict[str, str]
) -> dict[str, str]:
    values = {
        "steps." + step + ".outcome": "success"
        for step in (
            "checkout",
            "uv",
            "buildx",
            "trivy",
            "syft",
            "route",
            "engine",
            "exporter-smoke",
            "verification",
            "transfer_admission",
        )
    }
    values["inputs.delivery_mode"] = selected
    try:
        helper.parse_route({"inputs": event_inputs}, selected)
    except helper.BootstrapError:
        values["steps.route.outcome"] = "failure"
    for step, outcome in changes.items():
        values["steps." + step + ".outcome"] = outcome
    return values


def ready_from_actual_yaml(steps: dict[str, dict], values: dict[str, str]) -> bool:
    preparation = json.loads(steps["verify"]["env"]["PRIVATE_PREPARATION"])
    for key, expression in preparation.items():
        match = re.fullmatch(r"\$\{\{ (steps\.[a-z-]+\.outcome) \}\}", expression)
        assert match is not None
        preparation[key] = values[match[1]]
    return helper.stage.gate_environment_ready(
        {
            "PRIVATE_PREPARATION": json.dumps(preparation),
            "RECAP_DG_CLIENT_TOKEN": "synthetic",
            "AQUACAST_TOKEN": "synthetic",
        }
    )


class TestActualWorkflowReachability:
    @pytest.mark.parametrize(
        "selected,event_inputs",
        [
            ("exporter-smoke-only", {"delivery_mode": "exporter-smoke-only"}),
            ("unknown", {"delivery_mode": "unknown"}),
            ("DELIVER-ONLY", {"delivery_mode": "DELIVER-ONLY"}),
            ("LOAD-IMAGES", {"delivery_mode": "LOAD-IMAGES"}),
            (" deliver-only", {"delivery_mode": " deliver-only"}),
            ("load-images ", {"delivery_mode": "load-images "}),
            ("dеliver-only", {"delivery_mode": "dеliver-only"}),
            ("deliver-only", {}),
            ("deliver-only", {"delivery_mode": "deliver-only", "extra": "x"}),
            ("deliver-only", {"delivery_mode": "load-images"}),
            ("deliver-only", {"delivery_mode": True}),
        ],
    )
    @pytest.mark.parametrize(
        "outcome,cancelled",
        [
            ("success", False),
            ("failure", False),
            ("skipped", False),
            ("cancelled", True),
        ],
    )
    def test_public_or_invalid_event_cannot_reach_any_private_step(
        self,
        selected: str,
        event_inputs: dict,
        outcome: str,
        cancelled: bool,
    ) -> None:
        steps = private_steps()
        changes = dict.fromkeys(
            (
                "checkout",
                "uv",
                "engine",
                "exporter-smoke",
                "verification",
                "transfer_admission",
            ),
            outcome,
        )
        values = workflow_state(selected, event_inputs, changes)
        assert {
            phase: evaluate_private_if(step["if"], values, cancelled=cancelled)
            for phase, step in steps.items()
        } == {
            "verify": False,
            "preflight": False,
            "transfer": False,
        }

    @pytest.mark.parametrize("selected", ["deliver-only", "load-images"])
    @pytest.mark.parametrize(
        "step,outcome,cancelled,expected",
        [
            ("route", "success", False, (True, True, True)),
            ("route", "failure", False, (False, False, False)),
            ("route", "skipped", False, (False, False, False)),
            ("route", "cancelled", False, (False, False, False)),
            ("checkout", "failure", False, (False, False, False)),
            ("checkout", "skipped", False, (False, False, False)),
            ("route", "success", True, (False, False, False)),
            ("verification", "failure", False, (True, True, True)),
            ("transfer_admission", "failure", False, (True, True, False)),
            ("transfer_admission", "skipped", False, (True, True, False)),
            ("transfer_admission", "cancelled", False, (True, True, False)),
        ],
    )
    def test_private_status_and_transfer_admission_truth_table(
        self,
        selected: str,
        step: str,
        outcome: str,
        cancelled: bool,
        expected: tuple[bool, bool, bool],
    ) -> None:
        changes = {step: outcome}
        if step == "checkout" and outcome != "success":
            # A failed checkout cannot have produced successful transfer admission.
            changes["transfer_admission"] = "skipped"
        values = workflow_state(selected, {"delivery_mode": selected}, changes)
        steps = private_steps()
        assert (
            tuple(
                evaluate_private_if(steps[phase]["if"], values, cancelled=cancelled)
                for phase in ("verify", "preflight", "transfer")
            )
            == expected
        )

    @pytest.mark.parametrize("selected", ["deliver-only", "load-images"])
    @pytest.mark.parametrize(
        "preparation_step",
        ["buildx", "trivy", "syft", "engine", "exporter-smoke"],
    )
    @pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled"])
    def test_failed_preparation_blocks_gates_but_preserves_approved_diagnostic_path(
        self,
        selected: str,
        preparation_step: str,
        outcome: str,
    ) -> None:
        steps = private_steps()
        values = workflow_state(
            selected,
            {"delivery_mode": selected},
            {preparation_step: outcome, "verification": "failure"},
        )
        assert not ready_from_actual_yaml(steps, values)
        # always() intentionally allows diagnostic preparation despite earlier failure.
        assert evaluate_private_if(steps["verify"]["if"], values, cancelled=False)
        assert evaluate_private_if(steps["preflight"]["if"], values, cancelled=False)
        for admission, expected in (
            ("success", True),
            ("failure", False),
            ("skipped", False),
            ("cancelled", False),
        ):
            values["steps.transfer_admission.outcome"] = admission
            assert (
                evaluate_private_if(steps["transfer"]["if"], values, cancelled=False)
                is expected
            )
        assert not any(
            evaluate_private_if(step["if"], values, cancelled=True)
            for step in steps.values()
        )

    @pytest.mark.parametrize("selected", ["deliver-only", "load-images"])
    @pytest.mark.parametrize("early_step", ["checkout", "uv"])
    @pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled"])
    def test_pre_route_failure_cannot_start_private_diagnostics(
        self,
        selected: str,
        early_step: str,
        outcome: str,
    ) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        ordered = workflow["jobs"]["verify"]["steps"]
        indexed = {step["id"]: step for step in ordered if "id" in step}
        assert ordered.index(indexed[early_step]) < ordered.index(indexed["route"])
        assert "if" not in indexed["route"]  # Actions' implicit success() condition.
        values = workflow_state(
            selected,
            {"delivery_mode": selected},
            {
                early_step: outcome,
                "route": "skipped",
                "transfer_admission": "skipped",
            },
        )
        assert not ready_from_actual_yaml(private_steps(), values)
        assert not any(
            evaluate_private_if(step["if"], values, cancelled=False)
            for step in private_steps().values()
        )

    def test_yaml_complete_success_record_still_admits_gates(self) -> None:
        values = workflow_state("deliver-only", {"delivery_mode": "deliver-only"}, {})
        assert ready_from_actual_yaml(private_steps(), values)

    def test_case_insensitive_actions_match_requires_actual_strict_route_guard(
        self,
    ) -> None:
        values = workflow_state("DELIVER-ONLY", {"delivery_mode": "DELIVER-ONLY"}, {})
        expression = private_steps()["verify"]["if"]
        assert not evaluate_private_if(expression, values, cancelled=False)
        values["steps.route.outcome"] = "success"
        assert evaluate_private_if(expression, values, cancelled=False)

    def test_extra_always_disjunct_is_detectably_unsafe(self) -> None:
        values = workflow_state(
            "exporter-smoke-only", {"delivery_mode": "exporter-smoke-only"}, {}
        )
        expression = private_steps()["verify"]["if"]
        assert not evaluate_private_if(expression, values, cancelled=False)
        unsafe = expression[:-3] + " || always() }}"
        assert evaluate_private_if(unsafe, values, cancelled=False)


class TestWholeSecretContextLock:
    @pytest.mark.parametrize(
        "expression",
        [
            "${{ secrets['STAGING_ADMIN_SSH_KEY'] }}",
            "${{ toJSON(secrets) }}",
        ],
    )
    @pytest.mark.parametrize("field", ["env", "with", "run", "if"])
    def test_new_bracket_or_whole_context_step_fails_actual_scope_lock(
        self,
        expression: str,
        field: str,
    ) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        job = workflow["jobs"]["verify"]
        injected = {
            "name": "Unexpected context access",
            field: {"value": expression} if field in ("env", "with") else expression,
        }
        job["steps"].append(injected)
        with pytest.raises(AssertionError, match="unexpected secret-step set"):
            assert_secret_scope(job, private_steps())

    @pytest.mark.parametrize(
        "expression",
        [
            "${{ secrets['STAGING_ADMIN_SSH_KEY'] }}",
            "${{ toJSON(secrets) }}",
        ],
    )
    def test_job_environment_context_access_fails_actual_scope_lock(
        self,
        expression: str,
    ) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        job = workflow["jobs"]["verify"]
        job["env"]["UNEXPECTED_CONTEXT"] = expression
        with pytest.raises(
            AssertionError, match="secret context outside private steps"
        ):
            assert_secret_scope(job, private_steps())
