from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from dataclasses import asdict, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "transfer_test_module", ROOT / "tools/staging_release_transfer.py"
)
assert spec is not None and spec.loader is not None
transfer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = transfer
spec.loader.exec_module(transfer)


def binding() -> transfer.ReleaseBinding:
    return transfer.ReleaseBinding(
        version="1.2.3",
        source_sha="a" * 40,
        source_tree="b" * 40,
        tag_object="c" * 40,
        release_state="d" * 40,
        workflow_sha="a" * 40,
        workflow_ref=transfer.REPOSITORY
        + "/.github/workflows/private-verification.yml@refs/tags/v1.2.3",
        run_id="123",
        run_attempt="1",
        actor="owner",
        delivery_mode=transfer.DeliveryMode.DELIVER_ONLY,
    )


def add_member(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(content))


def payload(tmp_path: Path, approved: transfer.ReleaseBinding | None = None) -> tuple:
    approved = approved or binding()
    directory = tmp_path / "payload"
    directory.mkdir()
    manifest, images, receipt_images = [], [], []
    raw = directory / "export.tar"
    with tarfile.open(raw, "w") as archive:
        for variant in ("default", "aquacast"):
            layer = ("synthetic layer " + variant).encode()
            labels = {
                "org.opencontainers.image.version": approved.version,
                "org.opencontainers.image.revision": approved.source_sha,
            }
            config = transfer.json_bytes(
                {
                    "architecture": "amd64",
                    "os": "linux",
                    "config": {"Labels": labels},
                    "rootfs": {
                        "type": "layers",
                        "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
                    },
                }
            )
            config_hash = hashlib.sha256(config).hexdigest()
            layer_name = variant + "/layer.tar"
            config_name = config_hash + ".json"
            add_member(archive, config_name, config)
            add_member(archive, layer_name, layer)
            manifest.append(
                {"Config": config_name, "RepoTags": None, "Layers": [layer_name]}
            )
            raw_id = "sha256:" + config_hash
            images.append(
                transfer.ImageBinding(
                    variant=variant,
                    raw_id=raw_id,
                    config_digest=raw_id,
                    id_kind="config",
                    platform="linux/amd64",
                    store_mode="classic",
                )
            )
            receipt_images.append(
                {
                    "variant": variant,
                    "image_id": raw_id,
                    "labels": labels,
                    "runtime_import_version": approved.version,
                    "mutable_tag": (
                        "sapphire-flow-aquacast:"
                        if variant == "aquacast"
                        else "sapphire-flow:"
                    )
                    + approved.version,
                }
            )
        add_member(archive, "manifest.json", transfer.json_bytes(manifest))
    raw_bytes = raw.read_bytes()
    (directory / "images.tar.gz").write_bytes(gzip.compress(raw_bytes, mtime=0))
    raw.unlink()
    receipt = {
        "package_version": approved.version,
        "source_revision": approved.source_sha,
        "git_tree": approved.source_tree,
        "remote_tag": "v" + approved.version,
        "remote_tag_object": approved.tag_object,
        "remote_tag_target": approved.source_sha,
        "release_state": approved.release_state,
        "images": receipt_images,
    }
    (directory / "receipt.json").write_bytes(transfer.json_bytes(receipt))
    (directory / "report.bin").write_bytes(b"private synthetic report")
    with tarfile.open(directory / "runtime.tar", "w") as archive:
        for name in transfer.RUNTIME_FILES:
            add_member(archive, name, b"synthetic public config")
    files = {
        name: transfer.FileDigest.of(directory / name)
        for name in ("images.tar.gz", "receipt.json", "runtime.tar", "report.bin")
    }
    envelope = transfer.Envelope(
        binding=approved,
        kind=transfer.PayloadKind.RELEASE,
        files=files,
        gates=dict.fromkeys(transfer.GATE_NAMES, "passed"),
        images=tuple(images),
        expanded_archive_bytes=len(raw_bytes),
        expanded_archive_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        unpacked_layer_bytes=sum(
            len(("synthetic layer " + v).encode()) for v in ("default", "aquacast")
        ),
    )
    return envelope, directory


def config(approved: transfer.ReleaseBinding | None = None) -> transfer.ReceiverConfig:
    return transfer.ReceiverConfig(
        binding=approved or binding(),
        max_archive_bytes=1024 * 1024,
        max_expanded_bytes=1024 * 1024,
        max_bundle_bytes=1024 * 1024,
        timeout_seconds=30,
        reserve_bytes=1,
    )


def wire(envelope: transfer.Envelope, directory: Path) -> bytes:
    metadata = envelope.encode()
    return (
        len(metadata).to_bytes(8, "big")
        + metadata
        + b"".join((directory / n).read_bytes() for n in sorted(envelope.files))
    )


def receive_bytes(
    root: Path, approved: transfer.ReceiverConfig, data: bytes, **kwargs: object
) -> dict:
    import socket
    from concurrent.futures import ThreadPoolExecutor

    first, second = socket.socketpair()
    first.settimeout(5)

    def write() -> None:
        try:
            first.sendall(data)
            first.shutdown(socket.SHUT_WR)
        except OSError:
            pass  # Early rejection closes the receiving socket.

    with ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(write)
        try:
            return transfer.receive_stream(approved, root, second.fileno(), **kwargs)
        finally:
            second.close()
            sent.result(timeout=6)
            first.close()


class TestReleaseBinding:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("version", "01.2.3"),
            ("target", "other@host"),
            ("source_sha", "e" * 40),
            ("workflow_ref", "unapproved"),
            ("run_attempt", "0"),
            ("actor", "bot[bot]"),
        ],
    )
    def test_rejects_unapproved_identity(self, field: str, value: str) -> None:
        with pytest.raises(
            (transfer.TransferError, ValueError), match="invalid|unapproved|differ"
        ):
            replace(binding(), **{field: value})

    def test_binding_does_not_depend_on_a_future_receipt(self) -> None:
        assert transfer.ReleaseBinding.parse(asdict(binding())) == binding()
        assert "receipt" not in asdict(binding())


class TestEnvelope:
    def test_release_requires_all_gates(self, tmp_path: Path) -> None:
        envelope, _ = payload(tmp_path)
        with pytest.raises(transfer.TransferError, match="gates failed"):
            replace(envelope, gates={**envelope.gates, "lint": "failed"})

    def test_duplicate_or_unknown_fields_fail(self, tmp_path: Path) -> None:
        envelope, _ = payload(tmp_path)
        with pytest.raises(transfer.TransferError, match="duplicate"):
            transfer.Envelope.parse(
                envelope.encode().rstrip()[:-1] + b',"schema_version":1}'
            )
        with pytest.raises(transfer.TransferError, match="incomplete envelope"):
            transfer.Envelope.parse(
                transfer.json_bytes({**asdict(envelope), "extra": True})
            )

    def test_owner_receipt_is_digest_only(self, tmp_path: Path) -> None:
        envelope, _ = payload(tmp_path)
        result = envelope.owner_receipt("delivered")
        assert set(result) == {
            "schema_version",
            "status",
            "source",
            "tree",
            "tag_object",
            "release_state",
            "run_id",
            "run_attempt",
            "envelope_sha256",
            "receipt_sha256",
            "runtime_bundle_sha256",
            "archive_sha256",
            "archive_bytes",
            "images",
        }
        assert "labels" not in json.dumps(result)
        with pytest.raises(transfer.TransferError, match="status"):
            envelope.owner_receipt("loaded")


class TestReceiveStream:
    def test_real_archive_verified_and_sealed_without_loading(
        self, tmp_path: Path
    ) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir(mode=0o700)

        def forbid_load(*args: object) -> None:
            pytest.fail("deliver-only must not load")

        result = receive_bytes(
            store, config(), wire(envelope, directory), load=forbid_load
        )
        assert result == envelope.owner_receipt("delivered")
        assert (store / "v1.2.3/images.tar.gz").read_bytes() == (
            directory / "images.tar.gz"
        ).read_bytes()
        assert (store / "v1.2.3/delivered.json").exists()
        assert not list(store.glob("*.partial"))

    @pytest.mark.parametrize("mutation", ["truncated", "trailing", "corrupt"])
    def test_bad_wire_cannot_seal(self, tmp_path: Path, mutation: str) -> None:
        envelope, directory = payload(tmp_path)
        data = wire(envelope, directory)
        data = (
            data[:-1]
            if mutation == "truncated"
            else data + b"x"
            if mutation == "trailing"
            else data[:-1] + bytes([data[-1] ^ 1])
        )
        store = tmp_path / "store"
        store.mkdir()
        with pytest.raises(
            (transfer.TransferError, ValueError), match="truncated|trailing|digest"
        ):
            receive_bytes(store, config(), data)
        assert not (store / "v1.2.3").exists()

    def test_exact_retry_is_idempotent(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        data = wire(envelope, directory)
        first = receive_bytes(store, config(), data)
        second = receive_bytes(store, config(), data)
        assert first == second

    def test_prior_partial_is_never_replaced(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        partial = store / ".123-1.partial"
        partial.mkdir()
        (partial / "original").write_text("retained")
        with pytest.raises(FileExistsError, match="exists"):
            receive_bytes(store, config(), wire(envelope, directory))
        assert (partial / "original").read_text() == "retained"

    def test_changed_accepted_release_is_not_replaced(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        receive_bytes(store, config(), wire(envelope, directory))
        before = (store / "v1.2.3/envelope.json").read_bytes()
        changed = replace(envelope, binding=replace(envelope.binding, run_attempt="2"))
        with pytest.raises(transfer.TransferError, match="conflict"):
            receive_bytes(
                store,
                replace(config(), binding=changed.binding),
                wire(changed, directory),
            )
        assert (store / "v1.2.3/envelope.json").read_bytes() == before

    def test_load_requires_separate_server_approval(self, tmp_path: Path) -> None:
        approved = replace(binding(), delivery_mode=transfer.DeliveryMode.LOAD_IMAGES)
        envelope, directory = payload(tmp_path, approved)
        store = tmp_path / "store"
        store.mkdir()
        with pytest.raises(transfer.TransferError, match="load approval"):
            receive_bytes(store, config(approved), wire(envelope, directory))
        assert list(store.iterdir()) == []

    def test_load_occurs_only_after_verified_seal(self, tmp_path: Path) -> None:
        approved = replace(binding(), delivery_mode=transfer.DeliveryMode.LOAD_IMAGES)
        envelope, directory = payload(tmp_path, approved)
        store = tmp_path / "store"
        store.mkdir()
        calls = []

        def load(item: transfer.Envelope, path: Path) -> dict:
            assert (path / "envelope.json").read_bytes() == item.encode()
            assert (path / "images.tar").exists()
            calls.append(item.binding.sha256)
            return {"native_image_proofs": "synthetic-pass"}

        result = receive_bytes(
            store,
            replace(config(approved), load_approval_binding_sha256=approved.sha256),
            wire(envelope, directory),
            load=load,
        )
        assert result["status"] == "loaded"
        assert calls == [approved.sha256]

    def test_size_rejected_before_creating_partial(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        with pytest.raises(transfer.TransferError, match="capacity"):
            receive_bytes(
                store, replace(config(), max_archive_bytes=1), wire(envelope, directory)
            )
        assert not list(store.iterdir())

    def test_failed_diagnostic_never_loads(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        diagnostic = replace(
            envelope,
            kind=transfer.PayloadKind.DIAGNOSTIC,
            files={"report.bin": envelope.files["report.bin"]},
            images=(),
            expanded_archive_bytes=0,
            expanded_archive_sha256=None,
            unpacked_layer_bytes=0,
            gates={**envelope.gates, "lint": "failed"},
        )
        store = tmp_path / "store"
        store.mkdir()

        def forbid(*args: object) -> None:
            pytest.fail("diagnostic cannot load")

        result = receive_bytes(
            store, config(), wire(diagnostic, directory), load=forbid
        )
        assert result["status"] == "diagnostic-received"
        assert not (store / "v1.2.3").exists()


class TestPayloadValidation:
    def test_receipt_source_mismatch_is_rejected(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        receipt = json.loads((directory / "receipt.json").read_bytes())
        receipt["source_revision"] = "e" * 40
        (directory / "receipt.json").write_bytes(transfer.json_bytes(receipt))
        envelope = replace(
            envelope,
            files={
                **envelope.files,
                "receipt.json": transfer.FileDigest.of(directory / "receipt.json"),
            },
        )
        with pytest.raises(transfer.TransferError, match="receipt binding"):
            transfer.verify_payload(envelope, directory)

    def test_runtime_bundle_rejects_extra_paths(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "runtime.tar"
        with tarfile.open(archive_path, "w") as archive:
            add_member(archive, "../unapproved", b"x")
        with pytest.raises(transfer.TransferError, match="allowlist"):
            transfer.validate_runtime_bundle(archive_path)

    def test_expansion_is_bounded(self, tmp_path: Path) -> None:
        source = tmp_path / "compressed"
        source.write_bytes(gzip.compress(b"abc"))
        with pytest.raises(transfer.TransferError, match="exceeds bound"):
            transfer.expand_archive(source, tmp_path / "expanded", 2)


class TestSend:
    def test_existing_key_file_is_preserved(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        (work / "staging-key").write_text("original")
        with pytest.raises(FileExistsError, match="exists"):
            transfer.send(
                envelope,
                directory,
                work,
                {
                    "STAGING_RESERVE_BYTES": "1",
                    "STAGING_ADMIN_SSH_KEY": "synthetic",
                    "STAGING_KNOWN_HOSTS": "synthetic-host",
                },
                timeout_seconds=1500,
            )
        assert (work / "staging-key").read_text() == "original"

    def test_fixed_ssh_has_only_late_credential_and_exact_ack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        envelope, directory = payload(tmp_path)
        work = tmp_path / "work"
        work.mkdir()

        def run(args: list[str], **kwargs: object) -> object:
            assert args[-2:] == [transfer.TARGET, transfer.REMOTE_COMMAND]
            assert "-oIdentityAgent=none" in args
            assert "-oStrictHostKeyChecking=yes" in args
            assert "RECAP_DG_CLIENT_TOKEN" not in kwargs["env"]
            assert kwargs["stdin"].read() == wire(envelope, directory)
            return SimpleNamespace(
                returncode=0,
                stdout=transfer.json_bytes(envelope.owner_receipt("delivered")),
                stderr=b"synthetic private SSH diagnostic",
            )

        monkeypatch.setattr(transfer.subprocess, "run", run)
        result = transfer.send(
            envelope,
            directory,
            work,
            {
                "STAGING_RESERVE_BYTES": "1",
                "STAGING_ADMIN_SSH_KEY": "synthetic",
                "STAGING_KNOWN_HOSTS": "synthetic-host",
                "RECAP_DG_CLIENT_TOKEN": "not-for-ssh",
            },
            timeout_seconds=1500,
        )
        assert result["status"] == "delivered"
        assert not (work / "staging-key").exists()
        assert not (work / "staging-known-hosts").exists()


class TestSyntheticReleasePipeline:
    def test_sole_producer_export_delivers_same_tested_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from contextlib import nullcontext
        from types import SimpleNamespace

        spec = importlib.util.spec_from_file_location(
            "release_pipeline_test", ROOT / "tools/staging_release.py"
        )
        assert spec is not None and spec.loader is not None
        collector = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = collector
        spec.loader.exec_module(collector)
        template, source_files = payload(tmp_path)
        approved = collector.transfer.ReleaseBinding.parse(asdict(template.binding))
        root = tmp_path / "checkout"
        root.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.setattr(
            collector, "require_release", lambda *args, **kwargs: approved
        )
        monkeypatch.setattr(collector, "require_local_release", lambda *args: approved)
        monkeypatch.setattr(collector.release, "validate_destination", lambda: None)
        monkeypatch.setattr(collector.release, "validate_state", lambda *args: None)
        monkeypatch.setattr(
            collector.release,
            "ensure_clean_context",
            lambda source: approved.source_tree,
        )
        monkeypatch.setattr(
            collector.release,
            "create_git_tree_context",
            lambda source: nullcontext(str(root)),
        )
        monkeypatch.setattr(
            collector.release,
            "fetch_remote_state",
            lambda: (
                approved.source_sha,
                approved.release_state,
                [
                    SimpleNamespace(
                        name="v1.2.3",
                        metadata=object(),
                        object_oid=approved.tag_object,
                        peeled_oid=approved.source_sha,
                    )
                ],
            ),
        )
        receipt_template = json.loads((source_files / "receipt.json").read_bytes())
        image_records = {i["variant"]: i for i in receipt_template["images"]}
        builds = []

        def gate(command: list[str], **kwargs: object) -> object:
            gate_name = command[-1]
            for variant, image_gate in collector.IMAGE_GATES.items():
                if gate_name == image_gate:
                    builds.append(variant)
                    (work / "evidence" / (variant + "-image-id")).write_text(
                        image_records[variant]["image_id"]
                    )
                    for component in ("scan", "sbom", "proof"):
                        (
                            work
                            / "evidence"
                            / (variant + "-" + component + "-result.json")
                        ).write_text('{"outcome":"passed"}')
            return collector.verification.Outcome.PASSED

        monkeypatch.setattr(collector.verification, "run_private", gate)

        def docker(args: list[str], **kwargs: object) -> str:
            if args[:2] == ["image", "inspect"]:
                return image_records[
                    "aquacast" if "aquacast" in args[-1] else "default"
                ]["image_id"]
            return "overlay2" if args[-1] == "{{.Driver}}" else "[]"

        monkeypatch.setattr(collector.release, "run_docker", docker)
        monkeypatch.setattr(
            collector.release,
            "inspect_image",
            lambda raw, version, source, *, variant: collector.release.ReceiptImage(
                **{**image_records[variant], "mutable_tag": raw}
            ),
        )
        exports = []

        def save(ids: list[str], output: Path, limit: int, errors_path: Path) -> None:
            exports.append(ids)
            output.write_bytes(
                gzip.decompress((source_files / "images.tar.gz").read_bytes())
            )

        monkeypatch.setattr(collector, "archive_images", save)
        monkeypatch.setattr(
            collector,
            "runtime_bundle",
            lambda root, item, out: out.write_bytes(
                (source_files / "runtime.tar").read_bytes()
            ),
        )
        env = {
            "PRIVATE_PREPARATION": json.dumps(
                dict.fromkeys(
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
            ),
            "RECAP_DG_CLIENT_TOKEN": "synthetic",
            "AQUACAST_TOKEN": "synthetic",
            "STAGING_MAX_ARCHIVE_BYTES": "1000000",
            "STAGING_MAX_EXPANDED_BYTES": "1000000",
            "STAGING_RESERVE_BYTES": "1",
            "STAGING_TRANSFER_TIMEOUT": "1500",
        }
        markers = tmp_path / "markers"
        with markers.open("wb") as stream:
            progress = collector.Progress(
                phase=collector.Phase.VERIFY, fd=stream.fileno()
            )
            collector.verify_release(ROOT, work, env, progress=progress)
        assert markers.read_text().splitlines() == [
            "STAGING_RELEASE_CHECKPOINT VERIFY PAYLOAD_PREPARATION ENTER",
            "STAGING_RELEASE_CHECKPOINT VERIFY PAYLOAD_PREPARATION PASS",
        ]
        envelope = transfer.Envelope.parse((work / "envelope.json").read_bytes())
        assert builds == ["default", "aquacast"]
        assert exports == [
            [image_records[v]["image_id"] for v in ("default", "aquacast")]
        ]
        assert envelope.kind == transfer.PayloadKind.RELEASE
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        result = receive_bytes(store, config(), wire(envelope, work / "payload"))
        assert result["archive_sha256"] == envelope.files["images.tar.gz"].sha256
        assert result["status"] == "delivered"
        assert (store / "v1.2.3/images.tar.gz").read_bytes() == (
            work / "payload/images.tar.gz"
        ).read_bytes()


class TestApprovedImageLoad:
    @pytest.mark.parametrize(
        "condition", ["success", "tag-conflict", "wrong-architecture"]
    )
    def test_load_has_no_pull_build_or_activation(
        self, tmp_path: Path, condition: str
    ) -> None:
        approved = replace(binding(), delivery_mode=transfer.DeliveryMode.LOAD_IMAGES)
        envelope, directory = payload(tmp_path, approved)
        transfer.verify_payload(envelope, directory)
        records = json.loads((directory / "receipt.json").read_bytes())["images"]
        by_id = {record["image_id"]: record for record in records}
        tags = {record["mutable_tag"]: record["image_id"] for record in records}
        calls = []

        def run(args: list[str], *, stdin: str | None = None) -> str:
            calls.append(args)
            if args == ["info", "--format", "{{.Architecture}}"]:
                return "aarch64" if condition == "wrong-architecture" else "x86_64"
            if args == ["info", "--format", "{{.DockerRootDir}}"]:
                return str(tmp_path)
            if args == ["info", "--format", "{{.Driver}}"]:
                return "overlay2"
            if args == ["info", "--format", "{{json .DriverStatus}}"]:
                return "[]"
            if args[:2] == ["container", "ls"]:
                return ""
            if args[:2] == ["image", "ls"]:
                return next(iter(tags)) if condition == "tag-conflict" else ""
            if args[:3] == ["image", "inspect", "--format"]:
                return (
                    "sha256:" + "f" * 64
                    if condition == "tag-conflict"
                    else tags[args[-1]]
                )
            if args[:2] == ["image", "inspect"]:
                record = by_id[args[-1]]
                return json.dumps(
                    [
                        {
                            "Id": record["image_id"],
                            "Os": "linux",
                            "Architecture": "amd64",
                            "Config": {"Labels": record["labels"]},
                        }
                    ]
                )
            if args[0] == "run":
                assert "--network" in args and "none" in args
                assert "--pull" in args and "never" in args
                assert "--mount" not in args and "-v" not in args
                return approved.version if stdin is not None else ""
            if args[:2] in (["image", "load"], ["image", "tag"]):
                return ""
            pytest.fail("unexpected target command")

        if condition == "success":
            transfer.load_images(
                envelope,
                directory,
                approval=replace(
                    config(approved), load_approval_binding_sha256=approved.sha256
                ),
                run=run,
            )
            assert sum(args[:2] == ["image", "load"] for args in calls) == 1
            assert sum(args[0] == "run" for args in calls) == 4
        else:
            with pytest.raises(transfer.TransferError, match="conflict|native amd64"):
                transfer.load_images(
                    envelope,
                    directory,
                    approval=replace(
                        config(approved), load_approval_binding_sha256=approved.sha256
                    ),
                    run=run,
                )
            assert not any(args[:2] == ["image", "load"] for args in calls)
        assert not any(
            args[0] in ("build", "compose", "pull", "volume") for args in calls
        )

    def test_changed_expanded_bytes_hold_before_load(self, tmp_path: Path) -> None:
        approved = replace(binding(), delivery_mode=transfer.DeliveryMode.LOAD_IMAGES)
        envelope, directory = payload(tmp_path, approved)
        transfer.verify_payload(envelope, directory)
        (directory / "images.tar").write_bytes(b"changed")

        def run(args: list[str], **kwargs: object) -> str:
            assert args[0] == "info"
            return "x86_64" if args[-1] == "{{.Architecture}}" else str(tmp_path)

        with pytest.raises(transfer.TransferError, match="expanded archive changed"):
            transfer.load_images(
                envelope,
                directory,
                approval=replace(
                    config(approved), load_approval_binding_sha256=approved.sha256
                ),
                run=run,
            )


class TestLoadAccepted:
    @pytest.mark.parametrize(
        "approval_kind", ["missing", "wrong-receipt", "wrong-binding", "approved"]
    )
    def test_original_sealed_bytes_and_receipt_control_later_load(
        self, tmp_path: Path, approval_kind: str
    ) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        receive_bytes(store, config(), wire(envelope, directory))
        accepted = store / "v1.2.3"
        before = {p.name: p.read_bytes() for p in accepted.iterdir()}
        approved = replace(
            config(),
            load_approval_binding_sha256=envelope.binding.sha256,
            load_approval_receipt_sha256=envelope.files["receipt.json"].sha256,
        )
        if approval_kind == "missing":
            approved = config()
        elif approval_kind == "wrong-receipt":
            approved = replace(approved, load_approval_receipt_sha256="f" * 64)
        elif approval_kind == "wrong-binding":
            approved = replace(
                approved, binding=replace(envelope.binding, run_attempt="2")
            )
        calls = []

        def load(
            item: transfer.Envelope, path: Path, *, approval: transfer.ReceiverConfig
        ) -> dict:
            assert item == envelope
            assert path == accepted
            assert (
                approval.load_approval_receipt_sha256
                == envelope.files["receipt.json"].sha256
            )
            calls.append("validated-load")
            return {"native_image_proofs": "synthetic-pass"}

        if approval_kind == "approved":
            result = transfer.load_accepted(approved, store, load=load)
            assert result["status"] == "accepted-images-loaded"
            assert calls == ["validated-load"]
            assert (store / "load-v1.2.3.json").exists()
        else:
            with pytest.raises(transfer.TransferError, match="approval|binding"):
                transfer.load_accepted(approved, store, load=load)
            assert calls == []
        assert before == {p.name: p.read_bytes() for p in accepted.iterdir()}

    @pytest.mark.parametrize(
        "name", ["receipt.json", "images.tar.gz", "images.tar", "envelope.json"]
    )
    def test_changed_accepted_identity_or_bytes_never_reach_load(
        self, tmp_path: Path, name: str
    ) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        receive_bytes(store, config(), wire(envelope, directory))
        target = store / "v1.2.3" / name
        target.write_bytes(target.read_bytes() + b"changed")
        approved = replace(
            config(),
            load_approval_binding_sha256=envelope.binding.sha256,
            load_approval_receipt_sha256=envelope.files["receipt.json"].sha256,
        )

        def forbid(*args: object, **kwargs: object) -> dict:
            pytest.fail("changed accepted bytes cannot load")

        with pytest.raises(
            (ValueError, transfer.TransferError), match="digest|Extra data"
        ):
            transfer.load_accepted(approved, store, load=forbid)

    def test_no_receipt_bound_approval_is_not_enough(self, tmp_path: Path) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        receive_bytes(store, config(), wire(envelope, directory))
        approved = replace(
            config(), load_approval_binding_sha256=envelope.binding.sha256
        )
        with pytest.raises(transfer.TransferError, match="receipt requires"):
            transfer.load_accepted(
                approved, store, load=lambda *args, **kwargs: pytest.fail("no approval")
            )

    def test_failed_initial_load_keeps_original_delivery_for_later_approved_load(
        self, tmp_path: Path
    ) -> None:
        original = replace(binding(), delivery_mode=transfer.DeliveryMode.LOAD_IMAGES)
        envelope, directory = payload(tmp_path, original)
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        approved = replace(
            config(original), load_approval_binding_sha256=original.sha256
        )

        def fail(*args: object) -> dict:
            raise transfer.TransferError("synthetic load failure")

        with pytest.raises(transfer.TransferError, match="synthetic load failure"):
            receive_bytes(store, approved, wire(envelope, directory), load=fail)
        accepted = store / "v1.2.3"
        assert (accepted / "delivered.json").exists()
        assert not (accepted / "loaded.json").exists()
        before = {p.name: p.read_bytes() for p in accepted.iterdir()}
        later = replace(
            approved, load_approval_receipt_sha256=envelope.files["receipt.json"].sha256
        )
        result = transfer.load_accepted(
            later,
            store,
            load=lambda *args, **kwargs: {"native_image_proofs": "synthetic-pass"},
        )
        assert result["status"] == "accepted-images-loaded"
        assert before == {p.name: p.read_bytes() for p in accepted.iterdir()}


class TestTransferBudget:
    @pytest.mark.parametrize("requested", [1, 1200, 1260, 1501, 1800])
    def test_rejects_non_nested_sender_configuration(self, requested: int) -> None:
        with pytest.raises(transfer.TransferError, match="timeout|invalid"):
            transfer.sender_timeout(requested)

    def test_pre_ssh_cost_reduces_budget_and_preserves_cleanup(self) -> None:
        budget = transfer.remaining_ssh_budget(1500, 1740, 400)
        assert budget == 1280
        assert transfer.MAX_RECEIVER_SECONDS < budget < 1740 - 400
        with pytest.raises(transfer.TransferError, match="before SSH"):
            transfer.remaining_ssh_budget(1500, 1740, 421)

    def test_receiver_cannot_equal_sender_maximum(self) -> None:
        with pytest.raises(transfer.TransferError, match="invalid"):
            replace(config(), timeout_seconds=1500)

    def test_wire_cost_holds_ssh_and_cleans_created_key_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        envelope, directory = payload(tmp_path)
        work = tmp_path / "work"
        work.mkdir()

        def forbid(*args: object, **kwargs: object) -> None:
            pytest.fail("SSH must not start without enough remaining time")

        monkeypatch.setattr(transfer.subprocess, "run", forbid)
        with pytest.raises(transfer.TransferError, match="before SSH"):
            transfer.send(
                envelope,
                directory,
                work,
                {
                    "STAGING_RESERVE_BYTES": "1",
                    "STAGING_ADMIN_SSH_KEY": "synthetic",
                    "STAGING_KNOWN_HOSTS": "synthetic",
                },
                timeout_seconds=1500,
                deadline=1740,
                clock=lambda: 500,
            )
        assert (work / "transfer.wire").exists()
        assert not (work / "staging-key").exists()
        assert not (work / "staging-known-hosts").exists()

    def test_receiver_cleanup_docker_calls_share_the_remaining_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(transfer.time, "monotonic", lambda: 1199.0)

        def run(args: list[str], **kwargs: object) -> object:
            assert kwargs["timeout"] == 1.0
            return SimpleNamespace(returncode=0, stdout="")

        monkeypatch.setattr(transfer.subprocess, "run", run)
        transfer.docker(["container", "ls"], deadline=1200)
        with pytest.raises(transfer.TransferError, match="deadline"):
            transfer.docker(["container", "ls"], deadline=1199)


class TestEarlyAcceptedConflict:
    def test_conflict_needs_no_body_and_allocates_no_partial(
        self, tmp_path: Path
    ) -> None:
        envelope, directory = payload(tmp_path)
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        receive_bytes(store, config(), wire(envelope, directory))
        other_binding = replace(envelope.binding, run_attempt="2")
        other = replace(envelope, binding=other_binding)
        metadata = other.encode()
        with pytest.raises(transfer.TransferError, match="accepted release conflict"):
            receive_bytes(
                store,
                config(other_binding),
                len(metadata).to_bytes(8, "big") + metadata,
            )
        assert not (store / ".123-2.partial").exists()
        assert (store / "v1.2.3/envelope.json").read_bytes() == envelope.encode()
