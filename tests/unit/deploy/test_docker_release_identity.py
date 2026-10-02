from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "release_identity.py"
spec = importlib.util.spec_from_file_location("release_identity_receipt", _TOOL_PATH)
assert spec is not None and spec.loader is not None
release_identity = importlib.util.module_from_spec(spec)
sys.modules["release_identity_receipt"] = release_identity
spec.loader.exec_module(release_identity)

_PROBE_TOOL_PATH = _REPO_ROOT / "tools" / "image_identity_probe.py"
probe_spec = importlib.util.spec_from_file_location(
    "image_identity_probe", _PROBE_TOOL_PATH
)
assert probe_spec is not None and probe_spec.loader is not None
image_identity_probe = importlib.util.module_from_spec(probe_spec)
sys.modules["image_identity_probe"] = image_identity_probe
probe_spec.loader.exec_module(image_identity_probe)

_COMPOSE_TOOL_PATH = _REPO_ROOT / "tools" / "compose_release_identity.py"
compose_spec = importlib.util.spec_from_file_location(
    "compose_release_identity", _COMPOSE_TOOL_PATH
)
assert compose_spec is not None and compose_spec.loader is not None
compose_release_identity = importlib.util.module_from_spec(compose_spec)
sys.modules["compose_release_identity"] = compose_release_identity
compose_spec.loader.exec_module(compose_release_identity)

_ENV_TOOL_PATH = _REPO_ROOT / "tools" / "release_env_identity.py"
env_spec = importlib.util.spec_from_file_location(
    "release_env_identity", _ENV_TOOL_PATH
)
assert env_spec is not None and env_spec.loader is not None
release_env_identity = importlib.util.module_from_spec(env_spec)
sys.modules["release_env_identity"] = release_env_identity
env_spec.loader.exec_module(release_env_identity)


def test_dockerfile_declares_release_identity_args_and_labels() -> None:
    text = (_REPO_ROOT / "Dockerfile").read_text()
    assert "ARG SAPPHIRE_RELEASE_VERSION" in text
    assert "ARG SAPPHIRE_SOURCE_REVISION" in text
    assert 'org.opencontainers.image.version="$SAPPHIRE_RELEASE_VERSION"' in text
    assert 'org.opencontainers.image.revision="$SAPPHIRE_SOURCE_REVISION"' in text
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW" in text


def test_dockerfile_uses_dependency_layer_before_root_install() -> None:
    text = (_REPO_ROOT / "Dockerfile").read_text()
    dep_index = text.index("uv sync --frozen --no-dev --no-install-project")
    setup_copy_index = text.index("COPY pyproject.toml setup.py uv.lock README.md ./")
    copy_index = text.index("COPY src/ src/")
    root_index = text.index("uv sync --frozen --no-dev --no-editable")
    extra_root_index = text.index(
        "uv sync --frozen --no-dev --no-editable --extra aquacast"
    )
    assert setup_copy_index < dep_index < copy_index < root_index
    assert setup_copy_index < dep_index < copy_index < extra_root_index


def test_generated_version_metadata_is_dockerignored() -> None:
    assert "src/sapphire_flow/_version.py" in (_REPO_ROOT / ".dockerignore").read_text()


def test_compose_default_and_aquacast_builds_pass_release_identity_args() -> None:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    anchor_args = compose["x-app-build"]["args"]
    assert "SAPPHIRE_RELEASE_VERSION" in anchor_args
    assert "SAPPHIRE_SOURCE_REVISION" in anchor_args
    aquacast_args = compose["services"]["prefect-worker"]["build"]["args"]
    assert aquacast_args["WITH_AQUACAST"] == "1"
    assert "SAPPHIRE_RELEASE_VERSION" in aquacast_args
    assert "SAPPHIRE_SOURCE_REVISION" in aquacast_args


def test_compose_uses_buildkit_secrets_not_credential_build_args() -> None:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    text = (_REPO_ROOT / "docker-compose.yml").read_text()
    assert "recap_dg_client_token" in compose["x-app-build"]["secrets"]
    assert "aquacast_token" in compose["services"]["prefect-worker"]["build"]["secrets"]
    for line in text.splitlines():
        if "args:" in line or "SAPPHIRE_" in line or "WITH_AQUACAST" in line:
            assert "TOKEN" not in line


def test_release_receipt_command_has_atomic_non_overwrite_contract() -> None:
    text = (_REPO_ROOT / "tools/release_identity.py").read_text()
    assert "os.O_EXCL" in text
    assert "receipt path already exists" in text
    assert "runtime_import_version" in text
    assert "image_id" in text


def test_release_env_identity_updates_identity_without_touching_multiline_secret(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    secret = b'# comment\nDB_PASSWORD="first\nSAPPHIRE_RELEASE_VERSION=9.9.9\nlast"\n'
    env_path.write_bytes(secret)
    env_path.chmod(0o640)

    release_env_identity.write_identity_atomic(
        env_path,
        {
            "VERSION": "1.2.3",
            "SAPPHIRE_RELEASE_VERSION": "1.2.3",
            "SAPPHIRE_SOURCE_REVISION": "a" * 40,
        },
    )

    updated = env_path.read_bytes()
    assert secret in updated
    assert updated.endswith(b"SAPPHIRE_SOURCE_REVISION=" + b"a" * 40 + b"\n")
    assert env_path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize(
    "content",
    [
        b"VERSION=1.2.3\nVERSION=1.2.4\n",
        b"VERSION='1.2.3'\n",
        b"export DB_PASSWORD=secret\n",
        b"not a binding\n",
        b"DB_PASSWORD='first\n'\nVERSION=evil\nlast'\n",
        b'DB_PASSWORD="unterminated\nVERSION=1.2.3\n',
    ],
)
def test_release_env_identity_rejects_ambiguous_or_duplicate_input(
    content: bytes,
) -> None:
    with pytest.raises(release_env_identity.DotenvIdentityError):
        release_env_identity.public_identity_snapshot(content)


@pytest.mark.parametrize(
    "content",
    [
        b'export DB_PASSWORD="first\nVERSION=evil-export\nlast"\n',
        b"DB_PASSWORD='first\n\\'\nSAPPHIRE_RELEASE_VERSION=evil-single\nlast'\n",
    ],
)
def test_release_env_identity_rejects_reproduced_canary_forms_before_output_or_write(
    tmp_path: Path, content: bytes
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_bytes(content)

    with pytest.raises(release_env_identity.DotenvIdentityError):
        release_env_identity.public_identity_snapshot(content)
    with pytest.raises(release_env_identity.DotenvIdentityError):
        release_env_identity.write_identity_atomic(
            env_path,
            {
                "VERSION": "1.2.3",
                "SAPPHIRE_RELEASE_VERSION": "1.2.3",
                "SAPPHIRE_SOURCE_REVISION": "a" * 40,
            },
        )

    assert env_path.read_bytes() == content
    assert list(tmp_path.glob(".env.tmp.*")) == []


def test_release_env_identity_temp_is_secure_and_cleaned_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"DB_PASSWORD=super-secret\n")
    created_modes: list[int] = []
    replace_modes: list[int] = []
    real_open = release_env_identity.os.open

    def observing_open(path: str | Path, flags: int, mode: int = 0o777) -> int:
        fd = real_open(path, flags, mode)
        created_modes.append(Path(path).stat().st_mode & 0o777)
        return fd

    def fail_replace(src: str | Path, dst: str | Path) -> None:
        replace_modes.append(Path(src).stat().st_mode & 0o777)
        raise RuntimeError("forced replace failure")

    monkeypatch.setattr(release_env_identity.os, "open", observing_open)
    monkeypatch.setattr(release_env_identity.os, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="forced replace failure"):
        release_env_identity.write_identity_atomic(
            env_path,
            {
                "VERSION": "1.2.3",
                "SAPPHIRE_RELEASE_VERSION": "1.2.3",
                "SAPPHIRE_SOURCE_REVISION": "a" * 40,
            },
        )

    assert created_modes == [0o600]
    assert replace_modes == [0o644]
    assert list(tmp_path.glob(".env.tmp.*")) == []
    assert env_path.read_bytes() == b"DB_PASSWORD=super-secret\n"


def test_native_compose_reads_writer_output_with_multiline_canary_secrets(
    tmp_path: Path,
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI is not installed")
    env_path = tmp_path / ".env"
    canary = (
        b'ORDINARY_SECRET="first\n'
        b"VERSION=evil-ordinary\n"
        b'last"\n'
        b'WHITESPACE_SECRET=   "alpha\n'
        b"SAPPHIRE_RELEASE_VERSION=evil-whitespace\n"
        b"SAPPHIRE_SOURCE_REVISION=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        b'omega"\n'
    )
    env_path.write_bytes(canary)
    env_path.chmod(0o640)

    snapshot = release_env_identity.public_identity_snapshot(env_path.read_bytes())
    assert snapshot == {
        "VERSION": {"present": False},
        "SAPPHIRE_RELEASE_VERSION": {"present": False},
        "SAPPHIRE_SOURCE_REVISION": {"present": False},
    }

    release_env_identity.write_identity_atomic(
        env_path,
        {
            "VERSION": "1.2.3",
            "SAPPHIRE_RELEASE_VERSION": "1.2.3",
            "SAPPHIRE_SOURCE_REVISION": "a" * 40,
        },
    )

    updated = env_path.read_bytes()
    assert canary in updated
    assert b"evil-ordinary" in updated
    assert b"evil-whitespace" in updated
    assert env_path.stat().st_mode & 0o777 == 0o640
    public_evidence = json.dumps(
        release_env_identity.public_identity_snapshot(canary), sort_keys=True
    )
    assert "evil-ordinary" not in public_evidence
    assert "evil-whitespace" not in public_evidence
    (tmp_path / "compose.yml").write_text(
        "services:\n  api:\n    image: sapphire-flow:${VERSION:?missing}\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in release_env_identity.IDENTITY_KEYS
    }

    proc = subprocess.run(
        [docker, "compose", "-f", "compose.yml", "config", "--format", "json"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["services"]["api"]["image"] == "sapphire-flow:1.2.3"


def test_release_env_identity_rejects_malformed_before_write(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    original = b'DB_PASSWORD="unterminated\nVERSION=evil\n'
    env_path.write_bytes(original)

    with pytest.raises(release_env_identity.DotenvIdentityError):
        release_env_identity.write_identity_atomic(
            env_path,
            {
                "VERSION": "1.2.3",
                "SAPPHIRE_RELEASE_VERSION": "1.2.3",
                "SAPPHIRE_SOURCE_REVISION": "a" * 40,
            },
        )

    assert env_path.read_bytes() == original


def test_disposable_checkout_fixture_does_not_depend_on_real_env_presence(
    tmp_path: Path,
) -> None:
    configured_source = tmp_path / "configured-source"
    configured_source.mkdir()
    source_env = configured_source / ".env"
    source_env.write_bytes(b"VERSION=source-only\nDB_PASSWORD=source-secret\n")

    proc, _calls = _run_release_consumption_block(tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (
        source_env.read_bytes() == b"VERSION=source-only\nDB_PASSWORD=source-secret\n"
    )


def test_host_startup_uses_explicit_supported_python_contract() -> None:
    start = (_REPO_ROOT / "scripts" / "launchd" / "start-sapphire.sh").read_text()
    bootstrap = (_REPO_ROOT / "scripts" / "bootstrap-mac-mini.sh").read_text()

    for text in [start, bootstrap]:
        assert (
            'SAPPHIRE_PYTHON="${SAPPHIRE_PYTHON:-${REPO_ROOT}/.venv/bin/python}"'
            in text
        )
        assert "sys.version_info < (3, 12)" in text
        assert "\"${SAPPHIRE_PYTHON}\" - <<'PY'" in text
    assert "python3 - <<'PY'" not in start
    assert "python3 - <<'PY'" not in bootstrap


def _release_consumption_block() -> str:
    text = (
        _REPO_ROOT / "docs" / "operations" / "mac-mini-deploy-runbook.md"
    ).read_text()
    start = text.index(
        "After the receipt passes for an owner-published canonical release"
    )
    fence_start = text.index("```bash", start) + len("```bash")
    fence_end = text.index("```", fence_start)
    return text[fence_start:fence_end].strip()


def _write_release_receipt(
    path: Path,
    *,
    version: str = "1.2.3",
    source: str = "a" * 40,
    default_tag: str | None = None,
    aquacast_tag: str | None = None,
    default_id: str = "sha256:" + "1" * 64,
    aquacast_id: str = "sha256:" + "2" * 64,
) -> None:
    default_ref = default_tag or f"sapphire-flow:{version}"
    aquacast_ref = aquacast_tag or f"sapphire-flow-aquacast:{version}"
    path.write_text(
        json.dumps(
            {
                "package_version": version,
                "source_revision": source,
                "images": [
                    {
                        "variant": "default",
                        "mutable_tag": default_ref,
                        "image_id": default_id,
                    },
                    {
                        "variant": "aquacast",
                        "mutable_tag": aquacast_ref,
                        "image_id": aquacast_id,
                    },
                ],
            }
        )
    )


def _run_release_consumption_block(
    tmp_path: Path,
    *,
    receipt_text: str | None = None,
    receipt_version: str = "1.2.3",
    requested_version: str = "1.2.3",
    receipt_source: str = "a" * 40,
    requested_source: str = "a" * 40,
    compose_images: dict[str, str] | None = None,
    image_ids: dict[str, str] | None = None,
    running_containers: dict[str, str] | None = None,
    prior_image_ids: dict[str, str] | None = None,
    rollback_verify_overrides: dict[str, str] | None = None,
    existing_rollback_tags: dict[str, str] | None = None,
    existing_rollback_anchors: dict[str, object] | None = None,
    existing_prior_receipt_text: str | None = None,
    existing_absence_marker: bool = False,
    prior_receipt_text: str | None = None,
    existing_env_text: bytes | None = b"DB_PASSWORD=secret\n",
    rollback_dir_inside_checkout: bool = False,
    use_default_rollback_dir: bool = False,
    uv_exit: int = 0,
    config_exit: int = 0,
    fresh_config_exit: int = 0,
    init_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    receipt = tmp_path / "receipt.json"
    if receipt_text is None:
        _write_release_receipt(receipt, version=receipt_version, source=receipt_source)
    else:
        receipt.write_text(receipt_text)
    compose_images = compose_images or {
        "api": f"sapphire-flow:{requested_version}",
        "init": f"sapphire-flow:{requested_version}",
        "prefect-worker": f"sapphire-flow-aquacast:{requested_version}",
        "prefect-worker-backup": f"sapphire-flow:{requested_version}",
        "prefect-worker-ingest": f"sapphire-flow:{requested_version}",
    }
    image_ids = image_ids or {
        f"sapphire-flow:{requested_version}": "sha256:" + "1" * 64,
        f"sapphire-flow-aquacast:{requested_version}": "sha256:" + "2" * 64,
    }
    running_containers = running_containers or {
        "api": "container-api",
        "prefect-worker": "container-worker",
    }
    prior_image_ids = prior_image_ids or {
        "container-api": "sha256:" + "3" * 64,
        "container-worker": "sha256:" + "4" * 64,
    }
    rollback_verify_overrides = rollback_verify_overrides or {}
    existing_rollback_tags = existing_rollback_tags or {}
    prior_receipt = tmp_path / "prior-receipt.json"
    if prior_receipt_text is not None:
        prior_receipt.write_text(prior_receipt_text)
    work_dir = tmp_path / "checkout"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir()
    (work_dir / "docs" / "operations").mkdir(parents=True)
    (work_dir / "docs" / "operations" / "mac-mini-deploy-runbook.md").write_text(
        (_REPO_ROOT / "docs" / "operations" / "mac-mini-deploy-runbook.md").read_text()
    )
    shutil.copytree(_REPO_ROOT / "tools", work_dir / "tools")
    (work_dir / "docker-compose.yml").write_text(
        (_REPO_ROOT / "docker-compose.yml").read_text()
    )
    (work_dir / "docker-compose.macmini.yml").write_text(
        (_REPO_ROOT / "docker-compose.macmini.yml").read_text()
    )
    rollback_dir = (
        work_dir / "release" / "rollback-test"
        if rollback_dir_inside_checkout
        else tmp_path / "rollback"
    )
    if existing_rollback_anchors is not None:
        rollback_dir.mkdir(parents=True, exist_ok=True)
        (rollback_dir / "rollback-anchors.json").write_text(
            json.dumps(existing_rollback_anchors, indent=2, sort_keys=True) + "\n"
        )
    if existing_prior_receipt_text is not None:
        rollback_dir.mkdir(parents=True, exist_ok=True)
        (rollback_dir / "prior-release-receipt.json").write_text(
            existing_prior_receipt_text
        )
    if existing_absence_marker:
        rollback_dir.mkdir(parents=True, exist_ok=True)
        (rollback_dir / "prior-release-receipt.absent").write_text(
            "No PRIOR_RELEASE_RECEIPT was provided or present; "
            "this may be the first legacy cutover.\n"
        )
    bin_dir = tmp_path / "bin"
    if bin_dir.exists():
        shutil.rmtree(bin_dir)
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    if calls.exists():
        calls.unlink()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        f"compose_images = {compose_images!r}\n"
        f"image_ids = {image_ids!r}\n"
        f"running_containers = {running_containers!r}\n"
        f"prior_image_ids = {prior_image_ids!r}\n"
        f"rollback_verify_overrides = {rollback_verify_overrides!r}\n"
        f"initial_tags = {existing_rollback_tags!r}\n"
        f"tags_path = pathlib.Path({str(tmp_path / 'rollback-tags.json')!r})\n"
        f"config_exit = {config_exit!r}\n"
        f"fresh_config_exit = {fresh_config_exit!r}\n"
        f"config_count_path = pathlib.Path({str(tmp_path / 'config-count.txt')!r})\n"
        f"init_exit = {init_exit!r}\n"
        "calls.write_text((calls.read_text() if calls.exists() else '') "
        "                 + 'docker ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['image', 'inspect']:\n"
        "    tag = sys.argv[3]\n"
        "    tags = (json.loads(tags_path.read_text()) "
        "        if tags_path.exists() else initial_tags)\n"
        "    if tag in rollback_verify_overrides:\n"
        "        override = rollback_verify_overrides[tag]\n"
        "        if override == '__FAIL__':\n"
        "            raise SystemExit(42)\n"
        "        print(override)\n"
        "    elif tag in image_ids:\n"
        "        print(image_ids[tag])\n"
        "    elif tag in tags:\n"
        "        print(tags[tag])\n"
        "    else:\n"
        "        raise SystemExit(1)\n"
        "elif sys.argv[1:3] == ['image', 'ls']:\n"
        "    tag = sys.argv[-1]\n"
        "    tags = (json.loads(tags_path.read_text()) "
        "        if tags_path.exists() else initial_tags)\n"
        "    if tag in image_ids:\n"
        "        print(image_ids[tag])\n"
        "    elif tag in tags:\n"
        "        print(tags[tag])\n"
        "elif sys.argv[1] == 'inspect':\n"
        "    container = sys.argv[2]\n"
        "    if container not in prior_image_ids:\n"
        "        raise SystemExit(1)\n"
        "    print(prior_image_ids[container])\n"
        "elif sys.argv[1] == 'tag':\n"
        "    source, tag = sys.argv[2], sys.argv[3]\n"
        "    if not source.startswith('sha256:'):\n"
        "        raise SystemExit(1)\n"
        "    tags = (json.loads(tags_path.read_text()) "
        "        if tags_path.exists() else initial_tags)\n"
        "    tags[tag] = source\n"
        "    tags_path.write_text(json.dumps(tags))\n"
        "elif sys.argv[1] == 'compose' and 'ps' in sys.argv:\n"
        "    print(running_containers.get(sys.argv[-1], ''))\n"
        "elif sys.argv[1] == 'compose' and 'config' in sys.argv:\n"
        "    if config_count_path.exists():\n"
        "        config_count = int(config_count_path.read_text())\n"
        "    else:\n"
        "        config_count = 0\n"
        "    config_count_path.write_text(str(config_count + 1))\n"
        "    if config_count == 0 and config_exit:\n"
        "        raise SystemExit(config_exit)\n"
        "    if config_count > 0 and fresh_config_exit:\n"
        "        raise SystemExit(fresh_config_exit)\n"
        "    services = {name: {'image': image} "
        "                for name, image in compose_images.items()}\n"
        "    print(json.dumps({'services': services}))\n"
        "elif sys.argv[1] == 'compose' and 'stop' in sys.argv:\n"
        "    print('STOP')\n"
        "elif sys.argv[1] == 'compose' and 'run' in sys.argv:\n"
        "    if '--no-build' in sys.argv:\n"
        "        raise SystemExit(64)\n"
        "    has_required_pull = '--pull' in sys.argv and 'never' in sys.argv\n"
        "    if '--build=false' not in sys.argv or not has_required_pull:\n"
        "        raise SystemExit(65)\n"
        "    if 'init' not in sys.argv:\n"
        "        raise SystemExit(2)\n"
        "    raise SystemExit(init_exit)\n"
        "elif sys.argv[1] == 'compose' and 'up' in sys.argv:\n"
        "    print('UP')\n"
        "else:\n"
        "    raise SystemExit(2)\n"
    )
    docker.chmod(0o755)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "import pathlib, sys\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        f"uv_exit = {uv_exit!r}\n"
        "calls.write_text((calls.read_text() if calls.exists() else '') "
        "                 + 'uv ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(uv_exit)\n"
    )
    uv.chmod(0o755)
    script = (
        _release_consumption_block()
        .replace("<version>", requested_version)
        .replace("<full-sha>", requested_source)
        .replace("<path>", str(receipt))
        .replace("export PATH=/usr/local/bin:$PATH", f"export PATH={bin_dir}:$PATH")
    )
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "SAPPHIRE_PYTHON": sys.executable,
    }
    if not use_default_rollback_dir:
        env["ROLLBACK_DIR"] = str(rollback_dir)
    if prior_receipt.exists():
        env["PRIOR_RELEASE_RECEIPT"] = str(prior_receipt)
    env_path = work_dir / ".env"
    if existing_env_text is not None:
        env_path.write_bytes(existing_env_text)
        env_path.chmod(0o640)
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=work_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc, calls.read_text() if calls.exists() else ""


def test_runbook_deploys_verified_images_without_rebuilding() -> None:
    block = _release_consumption_block()
    assert "set -euo pipefail" in block
    assert "docker compose build" not in block
    assert "up -d --no-build --pull never" in block
    assert "--pull never" in block
    assert "stop prefect-worker prefect-worker-ingest" in block
    assert "rollback-anchors.json" in block
    assert "PRIOR_RELEASE_RECEIPT" in block
    assert "$HOME/sapphire-release-rollback/$SAPPHIRE_RELEASE_VERSION" in block
    assert ":rollback-{variant}-pre-" in block
    assert "run --rm --build=false --pull never init" in block
    assert "run --rm --no-build" not in block
    assert 'SAPPHIRE_PYTHON="${SAPPHIRE_PYTHON:-$(pwd)/.venv/bin/python}"' in block
    assert "\"${SAPPHIRE_PYTHON}\" - <<'PY'" in block
    assert "python3 - <<'PY'" not in block


def test_native_compose_run_supports_explicit_false_build_without_daemon() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI is not installed")

    proc = subprocess.run(
        [docker, "compose", "run", "--build=false", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "--build" in proc.stdout
    assert "--no-build" not in proc.stdout


def _markdown_section(text: str, heading: str, next_heading_prefix: str = "## ") -> str:
    start = text.index(heading)
    end = text.find(f"\n{next_heading_prefix}", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def test_runbook_primary_procedure_points_to_receipt_not_historical_build() -> None:
    text = (
        _REPO_ROOT / "docs" / "operations" / "mac-mini-deploy-runbook.md"
    ).read_text()
    procedure = _markdown_section(text, "## Procedure")

    assert "Release identity receipt" in procedure
    assert "only current official deploy recipe" in procedure
    assert "--build" not in procedure
    assert "not current procedure" in text


def test_cicd_upgrade_procedure_consumes_verified_receipt_not_live_build() -> None:
    text = (_REPO_ROOT / "docs" / "standards" / "cicd.md").read_text()
    upgrade = _markdown_section(
        text, "### Upgrade procedure", next_heading_prefix="### "
    )

    assert "mac-mini-deploy-runbook.md#release-identity-receipt" in upgrade
    assert "SAPPHIRE_RELEASE_VERSION" in upgrade
    assert "SAPPHIRE_SOURCE_REVISION" in upgrade
    assert "run --rm --build=false --pull never init" in upgrade
    assert "up -d --no-build --pull never" in upgrade
    assert "docker compose build" not in upgrade
    assert "docker compose run --rm --build init" not in upgrade
    assert "run --rm --no-build" not in upgrade


def test_runbook_binds_requested_identity_compose_selection_and_receipt() -> None:
    block = _release_consumption_block()
    assert 'receipt.get("package_version") != version' in block
    assert 'receipt.get("source_revision") != source_revision' in block
    assert "receipt_tags != set(expected)" in block
    assert '"api": f"sapphire-flow:{version}"' in block
    assert '"prefect-worker": f"sapphire-flow-aquacast:{version}"' in block
    assert "does not match expected" in block
    assert 'docker", "image", "inspect", tag' in block
    assert (
        "local Docker daemon"
        in (
            _REPO_ROOT / "docs" / "operations" / "mac-mini-deploy-runbook.md"
        ).read_text()
    )


def test_runbook_recipe_correct_selection_runs_stop_init_then_up(
    tmp_path: Path,
) -> None:
    proc, calls = _run_release_consumption_block(tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    stop = (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml "
        "stop prefect-worker prefect-worker-ingest"
    )
    init = (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml "
        "run --rm --build=false --pull never init"
    )
    up = (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml "
        "up -d --no-build --pull never"
    )
    rollback_default = (
        "docker tag sha256:" + "3" * 64 + " sapphire-flow:rollback-default-pre-1.2.3"
    )
    rollback_aquacast = (
        "docker tag sha256:"
        + "4" * 64
        + " sapphire-flow-aquacast:rollback-aquacast-pre-1.2.3"
    )
    assert stop in calls
    assert init in calls
    assert up in calls
    assert rollback_default in calls
    assert rollback_aquacast in calls
    assert calls.index(
        "uv run python3 tools/compose_release_identity.py"
    ) < calls.index(rollback_default)
    assert calls.index(rollback_default) < calls.index(stop)
    assert calls.index(rollback_aquacast) < calls.index(stop)
    assert calls.index(stop) < calls.index(init) < calls.index(up)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"receipt_version": "1.2.2"}, "package_version"),
        ({"receipt_source": "b" * 40}, "source_revision"),
        (
            {"compose_images": {"api": "postgres:16"}},
            "service api image",
        ),
        (
            {
                "compose_images": {
                    "api": "sapphire-flow:1.2.3",
                    "prefect-worker": "sapphire-flow:1.2.3",
                    "prefect-worker-ingest": "sapphire-flow-aquacast:1.2.3",
                    "prefect-worker-backup": "sapphire-flow:1.2.3",
                    "init": "sapphire-flow:1.2.3",
                }
            },
            "service prefect-worker-ingest image",
        ),
        ({"image_ids": {"sapphire-flow:1.2.3": "sha256:" + "1" * 64}}, "returned"),
        (
            {
                "image_ids": {
                    "sapphire-flow:1.2.3": "sha256:" + "9" * 64,
                    "sapphire-flow-aquacast:1.2.3": "sha256:" + "2" * 64,
                }
            },
            "does not match receipt",
        ),
        (
            {
                "receipt_text": json.dumps(
                    {
                        "package_version": "1.2.3",
                        "source_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "images": [
                            {
                                "variant": "default",
                                "mutable_tag": "sapphire-flow:1.2.3",
                                "image_id": "sha256:" + "1" * 64,
                            },
                            {
                                "variant": "default",
                                "mutable_tag": "sapphire-flow:1.2.3",
                                "image_id": "sha256:" + "1" * 64,
                            },
                        ],
                    }
                )
            },
            "unique default/aquacast",
        ),
        (
            {
                "receipt_text": json.dumps(
                    {
                        "package_version": "1.2.3",
                        "source_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "images": [
                            {
                                "variant": "default",
                                "mutable_tag": "sapphire-flow:1.2.3",
                                "image_id": "sha256:" + "1" * 64,
                            },
                            "not an image object",
                        ],
                    }
                )
            },
            "malformed",
        ),
        ({"receipt_text": "{"}, "json"),
        ({"config_exit": 7}, "returned non-zero"),
        ({"uv_exit": 4}, ""),
    ],
)
def test_runbook_recipe_identity_failures_never_mutate(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    proc, calls = _run_release_consumption_block(tmp_path, **kwargs)

    assert proc.returncode != 0
    assert "docker tag" not in calls
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml ps"
        not in calls
    )
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml stop"
        not in calls
    )
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml run"
        not in calls
    )
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml up"
        not in calls
    )
    if message:
        assert message in proc.stderr or message in proc.stdout


def test_runbook_recipe_init_failure_never_invokes_up(tmp_path: Path) -> None:
    proc, calls = _run_release_consumption_block(tmp_path, init_exit=9)

    assert proc.returncode != 0
    assert " stop prefect-worker prefect-worker-ingest" in calls
    assert " run --rm --build=false --pull never init" in calls
    assert " up " not in calls


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"running_containers": {"prefect-worker": "container-worker"}}, "api"),
        (
            {
                "prior_image_ids": {
                    "container-api": "not-a-sha",
                    "container-worker": "sha256:" + "4" * 64,
                }
            },
            "malformed",
        ),
        (
            {
                "rollback_verify_overrides": {
                    "sapphire-flow:rollback-default-pre-1.2.3": "sha256:" + "9" * 64
                }
            },
            "rollback tag",
        ),
    ],
)
def test_runbook_recipe_rollback_prep_failures_never_mutate(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    proc, calls = _run_release_consumption_block(tmp_path, **kwargs)

    assert proc.returncode != 0
    assert message in proc.stderr or message in proc.stdout
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml stop"
        not in calls
    )
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml run"
        not in calls
    )
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.macmini.yml up"
        not in calls
    )


def test_runbook_recipe_preserves_prior_receipt_when_available(tmp_path: Path) -> None:
    prior_receipt = '{"package_version": "1.2.2"}\n'
    proc, _calls = _run_release_consumption_block(
        tmp_path, prior_receipt_text=prior_receipt
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (
        tmp_path / "rollback" / "prior-release-receipt.json"
    ).read_text() == prior_receipt
    anchors = json.loads((tmp_path / "rollback" / "rollback-anchors.json").read_text())
    assert anchors["default"]["image_id"] == "sha256:" + "3" * 64
    assert anchors["aquacast"]["image_id"] == "sha256:" + "4" * 64


def test_runbook_recipe_records_absent_prior_receipt_for_first_cutover(
    tmp_path: Path,
) -> None:
    proc, _calls = _run_release_consumption_block(tmp_path)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (tmp_path / "rollback" / "prior-release-receipt.absent").is_file()
    prior_identity = json.loads(
        (tmp_path / "rollback" / "prior-public-identity.json").read_text()
    )
    assert prior_identity["VERSION"] == {"present": False}
    assert prior_identity["SAPPHIRE_RELEASE_VERSION"] == {"present": False}
    assert prior_identity["SAPPHIRE_SOURCE_REVISION"] == {"present": False}


def test_runbook_recipe_preserves_prior_public_identity_without_env_copy(
    tmp_path: Path,
) -> None:
    env_text = (
        b"VERSION=1.2.2\n"
        b"SAPPHIRE_RELEASE_VERSION=1.2.2\n"
        b"SAPPHIRE_SOURCE_REVISION=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        b"DB_PASSWORD=super-secret\n"
    )
    proc, _calls = _run_release_consumption_block(tmp_path, existing_env_text=env_text)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    prior_identity = json.loads(
        (tmp_path / "rollback" / "prior-public-identity.json").read_text()
    )
    assert prior_identity["VERSION"] == {"present": True, "value": "1.2.2"}
    assert prior_identity["SAPPHIRE_RELEASE_VERSION"] == {
        "present": True,
        "value": "1.2.2",
    }
    assert prior_identity["SAPPHIRE_SOURCE_REVISION"] == {
        "present": True,
        "value": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }
    assert not (tmp_path / "rollback" / ".env").exists()
    assert (
        "super-secret"
        not in (tmp_path / "rollback" / "prior-public-identity.json").read_text()
    )


def test_runbook_recipe_fresh_env_verify_failure_stops_before_compose_mutation(
    tmp_path: Path,
) -> None:
    proc, calls = _run_release_consumption_block(tmp_path, fresh_config_exit=8)

    assert proc.returncode != 0
    assert "docker tag" in calls
    assert " stop prefect-worker prefect-worker-ingest" not in calls
    assert " run --rm --build=false --pull never init" not in calls
    assert " up -d --no-build --pull never" not in calls
    assert (tmp_path / "rollback" / "prior-public-identity.json").is_file()


def test_runbook_recipe_retry_after_persistence_keeps_original_prior_identity(
    tmp_path: Path,
) -> None:
    old_env = (
        b"VERSION=1.2.2\n"
        b"SAPPHIRE_RELEASE_VERSION=1.2.2\n"
        b"SAPPHIRE_SOURCE_REVISION=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
    )
    first, _first_calls = _run_release_consumption_block(
        tmp_path, existing_env_text=old_env, fresh_config_exit=8
    )
    assert first.returncode != 0
    prior_path = tmp_path / "rollback" / "prior-public-identity.json"
    prior_after_first = json.loads(prior_path.read_text())
    assert prior_after_first["VERSION"] == {"present": True, "value": "1.2.2"}

    incoming_env = (
        b"VERSION=1.2.3\n"
        b"SAPPHIRE_RELEASE_VERSION=1.2.3\n"
        b"SAPPHIRE_SOURCE_REVISION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    )
    second, second_calls = _run_release_consumption_block(
        tmp_path,
        existing_env_text=incoming_env,
        existing_absence_marker=True,
    )

    assert second.returncode == 0, second.stderr + second.stdout
    assert " stop prefect-worker prefect-worker-ingest" in second_calls
    assert json.loads(prior_path.read_text()) == prior_after_first


def test_runbook_recipe_default_rollback_dir_is_outside_checkout(
    tmp_path: Path,
) -> None:
    proc, _calls = _run_release_consumption_block(
        tmp_path, use_default_rollback_dir=True
    )

    rollback_dir = tmp_path / "home" / "sapphire-release-rollback" / "1.2.3"
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert rollback_dir.joinpath("rollback-anchors.json").is_file()
    assert not (_REPO_ROOT / "release" / "rollback").exists()


def test_runbook_recipe_rejects_explicit_in_checkout_rollback_dir(
    tmp_path: Path,
) -> None:
    proc, calls = _run_release_consumption_block(
        tmp_path, rollback_dir_inside_checkout=True
    )

    assert proc.returncode != 0
    assert "outside checkout" in proc.stderr
    assert " stop prefect-worker prefect-worker-ingest" not in calls
    assert not (_REPO_ROOT / "release" / "rollback-test").exists()


def test_runbook_recipe_retry_preserves_original_anchors_without_retagging(
    tmp_path: Path,
) -> None:
    anchors = {
        "default": {
            "service": "api",
            "image_id": "sha256:" + "3" * 64,
            "tag": "sapphire-flow:rollback-default-pre-1.2.3",
        },
        "aquacast": {
            "service": "prefect-worker",
            "image_id": "sha256:" + "4" * 64,
            "tag": "sapphire-flow-aquacast:rollback-aquacast-pre-1.2.3",
        },
    }
    existing_tags = {item["tag"]: item["image_id"] for item in anchors.values()}

    proc, calls = _run_release_consumption_block(
        tmp_path,
        existing_rollback_anchors=anchors,
        existing_rollback_tags=existing_tags,
        existing_absence_marker=True,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "docker tag" not in calls
    written = json.loads((tmp_path / "rollback" / "rollback-anchors.json").read_text())
    assert written == anchors


def test_runbook_recipe_retry_after_restart_fails_before_replacing_original_pair(
    tmp_path: Path,
) -> None:
    anchors = {
        "default": {
            "service": "api",
            "image_id": "sha256:" + "3" * 64,
            "tag": "sapphire-flow:rollback-default-pre-1.2.3",
        },
        "aquacast": {
            "service": "prefect-worker",
            "image_id": "sha256:" + "4" * 64,
            "tag": "sapphire-flow-aquacast:rollback-aquacast-pre-1.2.3",
        },
    }
    existing_tags = {item["tag"]: item["image_id"] for item in anchors.values()}

    proc, calls = _run_release_consumption_block(
        tmp_path,
        existing_rollback_anchors=anchors,
        existing_rollback_tags=existing_tags,
        prior_image_ids={
            "container-api": "sha256:" + "8" * 64,
            "container-worker": "sha256:" + "9" * 64,
        },
        existing_absence_marker=True,
    )

    assert proc.returncode != 0
    assert "existing rollback anchors differ" in proc.stderr
    assert "docker tag" not in calls
    assert " stop prefect-worker prefect-worker-ingest" not in calls


def test_runbook_recipe_existing_anchor_manifest_mismatch_fails_before_mutation(
    tmp_path: Path,
) -> None:
    proc, calls = _run_release_consumption_block(
        tmp_path,
        existing_rollback_anchors={"default": {"image_id": "sha256:" + "0" * 64}},
        existing_rollback_tags={
            "sapphire-flow:rollback-default-pre-1.2.3": "sha256:" + "3" * 64,
            "sapphire-flow-aquacast:rollback-aquacast-pre-1.2.3": "sha256:" + "4" * 64,
        },
        existing_absence_marker=True,
    )

    assert proc.returncode != 0
    assert "existing rollback anchors differ" in proc.stderr
    assert " stop prefect-worker prefect-worker-ingest" not in calls


def test_runbook_recipe_prior_receipt_mismatch_fails_before_mutation(
    tmp_path: Path,
) -> None:
    proc, calls = _run_release_consumption_block(
        tmp_path,
        existing_prior_receipt_text='{"package_version": "1.2.1"}\n',
        prior_receipt_text='{"package_version": "1.2.2"}\n',
    )

    assert proc.returncode != 0
    assert "existing rollback evidence differs" in proc.stderr
    assert " stop prefect-worker prefect-worker-ingest" not in calls


def test_runbook_recipe_inspect_failure_for_existing_tag_never_retags(
    tmp_path: Path,
) -> None:
    tag = "sapphire-flow:rollback-default-pre-1.2.3"
    proc, calls = _run_release_consumption_block(
        tmp_path,
        existing_rollback_tags={
            tag: "sha256:" + "3" * 64,
            "sapphire-flow-aquacast:rollback-aquacast-pre-1.2.3": "sha256:" + "4" * 64,
        },
        rollback_verify_overrides={tag: "__FAIL__"},
    )

    assert proc.returncode != 0
    assert "cannot inspect existing rollback tag" in proc.stderr
    assert "docker tag" not in calls
    assert " stop prefect-worker prefect-worker-ingest" not in calls


def test_inspect_image_rejects_missing_or_wrong_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_probe(
        image_tag: str,
        expected_version: str,
        expected_revision: str,
        *,
        variant: str,
    ) -> object:
        raise image_identity_probe.ImageIdentityProbeError(
            "container label org.opencontainers.image.revision mismatch"
        )

    monkeypatch.setattr(image_identity_probe, "probe_image", fake_probe)
    monkeypatch.setattr(
        release_identity, "_load_image_probe", lambda: image_identity_probe
    )

    with pytest.raises(release_identity.ReleaseIdentityError, match="revision"):
        release_identity.inspect_image("sapphire-flow:1.2.3", "1.2.3", "a" * 40)


def test_atomic_receipt_write_never_publishes_partial_json(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    payload = '{"package_version":"1.2.3","source_revision":"' + "a" * 40 + '"}\n'

    release_identity.atomic_write_receipt(receipt, payload)
    assert receipt.read_text() == payload
    with pytest.raises(release_identity.ReleaseIdentityError, match="already exists"):
        release_identity.atomic_write_receipt(
            receipt, payload.replace("1.2.3", "1.2.4")
        )
    assert receipt.read_text() == payload


class _FakeBuilder:
    def build(self, context: Path, version: str, source: str) -> list[object]:
        labels = {
            "org.opencontainers.image.version": version,
            "org.opencontainers.image.revision": source,
        }
        return [
            release_identity.ReceiptImage(
                variant="default",
                mutable_tag=f"sapphire-flow:{version}",
                image_id="sha256:default",
                labels=labels,
                runtime_import_version=version,
            ),
            release_identity.ReceiptImage(
                variant="aquacast",
                mutable_tag=f"sapphire-flow-aquacast:{version}",
                image_id="sha256:aquacast",
                labels=labels,
                runtime_import_version=version,
            ),
        ]


def test_build_receipt_requires_two_fake_built_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "a" * 40
    tag = release_identity.RemoteTag(
        name="v1.2.3",
        version=release_identity.Version.parse_tag("v1.2.3"),
        object_oid="b" * 40,
        peeled_oid=source,
        annotated=True,
        direct_object_oid=source,
        direct_type="commit",
        embedded_name="v1.2.3",
        metadata=release_identity.ReleaseMetadata(
            marker=release_identity.HELPER_MARKER,
            tag="v1.2.3",
            source=source,
            ceiling_tag="v1.0.0",
            ceiling_source="c" * 40,
        ),
    )
    monkeypatch.setattr(release_identity, "validate_destination", lambda: None)
    monkeypatch.setattr(
        release_identity, "fetch_remote_state", lambda: (source, "d" * 40, [tag])
    )
    monkeypatch.setattr(
        release_identity, "validate_state", lambda state, tags, main: tag.metadata
    )
    monkeypatch.setattr(
        release_identity, "ensure_clean_context", lambda requested: "e" * 40
    )
    context = tmp_path / "context"
    context.mkdir()
    monkeypatch.setattr(
        release_identity,
        "create_git_tree_context",
        lambda requested: __import__("contextlib").nullcontext(str(context)),
    )
    receipt = tmp_path / "receipt.json"

    result = release_identity.build_receipt(
        __import__("argparse").Namespace(
            version="1.2.3", source=source, receipt=str(receipt)
        ),
        docker_builder=_FakeBuilder(),
    )

    assert result == 0
    data = json.loads(receipt.read_text())
    assert {image["variant"] for image in data["images"]} == {"default", "aquacast"}


def test_inspect_image_checks_aquacast_variant_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, str]] = []

    def fake_probe(
        image_tag: str,
        expected_version: str,
        expected_revision: str,
        *,
        variant: str,
    ) -> object:
        calls.append((image_tag, expected_version, expected_revision, variant))
        return image_identity_probe.ProbedImage(
            variant=variant,
            mutable_tag=image_tag,
            image_id="sha256:abc",
            labels={
                "org.opencontainers.image.version": expected_version,
                "org.opencontainers.image.revision": expected_revision,
            },
            runtime_import_version=expected_version,
        )

    monkeypatch.setattr(image_identity_probe, "probe_image", fake_probe)
    monkeypatch.setattr(
        release_identity, "_load_image_probe", lambda: image_identity_probe
    )

    release_identity.inspect_image("sapphire-flow-aquacast:1.2.3", "1.2.3", "a" * 40)

    assert calls == [("sapphire-flow-aquacast:1.2.3", "1.2.3", "a" * 40, "aquacast")]


def test_inspect_image_uses_loader_returned_probe_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProbeModule:
        ImageIdentityProbeError = RuntimeError

        @staticmethod
        def probe_image(
            image_tag: str,
            expected_version: str,
            expected_revision: str,
            *,
            variant: str,
        ) -> object:
            return image_identity_probe.ProbedImage(
                variant=variant,
                mutable_tag=image_tag,
                image_id="sha256:abc",
                labels={
                    "org.opencontainers.image.version": expected_version,
                    "org.opencontainers.image.revision": expected_revision,
                },
                runtime_import_version=expected_version,
            )

    monkeypatch.setattr(release_identity, "_load_image_probe", lambda: FakeProbeModule)

    image = release_identity.inspect_image("sapphire-flow:1.2.3", "1.2.3", "a" * 40)

    assert image.variant == "default"
    assert image.image_id == "sha256:abc"


def test_compose_release_identity_preflight_rejects_mismatched_canonical_tag() -> None:
    env = {
        "SAPPHIRE_RELEASE_VERSION": "1.2.3",
        "SAPPHIRE_SOURCE_REVISION": "a" * 40,
        "VERSION": "1.2.4",
    }

    with pytest.raises(compose_release_identity.ComposeReleaseIdentityError):
        compose_release_identity.validate_env(env)


def test_compose_release_identity_preflight_rejects_malformed_dev_version() -> None:
    env = {
        "SAPPHIRE_RELEASE_VERSION": "not-a-version",
        "SAPPHIRE_SOURCE_REVISION": "a" * 40,
        "VERSION": "dev-a",
    }

    with pytest.raises(compose_release_identity.ComposeReleaseIdentityError):
        compose_release_identity.validate_env(env)


def test_compose_release_identity_preflight_rejects_mismatched_dev_source() -> None:
    env = {
        "SAPPHIRE_RELEASE_VERSION": "0.dev0+g" + "b" * 40,
        "SAPPHIRE_SOURCE_REVISION": "a" * 40,
        "VERSION": "dev-a",
    }

    with pytest.raises(compose_release_identity.ComposeReleaseIdentityError):
        compose_release_identity.validate_env(env)


def test_compose_release_identity_preflight_allows_dev_docker_safe_tag() -> None:
    env = {
        "SAPPHIRE_RELEASE_VERSION": "0.dev0+g" + "a" * 40,
        "SAPPHIRE_SOURCE_REVISION": "a" * 40,
        "VERSION": "dev-a",
    }

    compose_release_identity.validate_env(env)


def _ci_workflow() -> dict[str, object]:
    return yaml.safe_load((_REPO_ROOT / ".github/workflows/ci.yml").read_text())


def _ci_job(name: str) -> dict[str, object]:
    jobs = _ci_workflow()["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def _job_steps(name: str) -> list[dict[str, object]]:
    steps = _ci_job(name)["steps"]
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _named_step(job_name: str, step_name: str) -> dict[str, object]:
    for step in _job_steps(job_name):
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"no step {step_name!r} in {job_name!r}")


def test_ci_default_image_proof_reuses_existing_build() -> None:
    steps = _job_steps("build-image-and-scan")
    build_steps = [step for step in steps if step.get("name") == "Build app image"]
    assert len(build_steps) == 1
    names = [str(step.get("name", step.get("uses", ""))) for step in steps]
    assert names.index("Upload SBOM artifact") < names.index(
        "Prove default image release identity"
    )
    proof = _named_step("build-image-and-scan", "Prove default image release identity")
    assert "tools/image_identity_probe.py" in str(proof["run"])
    assert "--variant default" in str(proof["run"])
    assert "sapphire-flow:ci-${{ github.sha }}" in str(proof["run"])


def test_ci_build_actions_are_load_only_and_disable_build_record_uploads() -> None:
    default_build = _named_step("build-image-and-scan", "Build app image")
    aquacast_build = _named_step(
        "aquacast-release-image-proof", "Build Aquacast app image"
    )
    for step in [default_build, aquacast_build]:
        assert step.get("uses") == (
            "docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc"
        )
        assert step.get("env", {}).get("DOCKER_BUILD_RECORD_UPLOAD") == "false"
        with_block = step["with"]
        assert isinstance(with_block, dict)
        assert with_block["load"] is True
        assert with_block["push"] is False
        assert "cache-to" not in with_block
        assert "outputs" not in with_block


def test_ci_aquacast_sibling_builds_only_aquacast_variant() -> None:
    job = _ci_job("aquacast-release-image-proof")
    assert job.get("permissions") == {"contents": "read", "pull-requests": "read"}
    steps = _job_steps("aquacast-release-image-proof")
    build_steps = [step for step in steps if "Build" in str(step.get("name", ""))]
    assert [step["name"] for step in build_steps] == ["Build Aquacast app image"]
    build = build_steps[0]
    with_block = build["with"]
    assert isinstance(with_block, dict)
    assert with_block["context"] == "."
    assert with_block["file"] == "./Dockerfile"
    assert "WITH_AQUACAST=1" in with_block["build-args"]
    assert (
        "SAPPHIRE_RELEASE_VERSION=0.dev0+g${{ github.sha }}" in with_block["build-args"]
    )
    assert "SAPPHIRE_SOURCE_REVISION=${{ github.sha }}" in with_block["build-args"]
    assert (
        "recap_dg_client_token=${{ secrets.RECAP_DG_CLIENT_TOKEN }}"
        in with_block["secrets"]
    )
    assert "aquacast_token=${{ secrets.AQUACAST_TOKEN }}" in with_block["secrets"]


def test_ci_aquacast_proof_uses_same_checked_out_sha_identity() -> None:
    assert_step = _named_step(
        "aquacast-release-image-proof", "Assert checkout matches GitHub SHA"
    )
    assert "git rev-parse HEAD" in str(assert_step["run"])
    assert "$GITHUB_SHA" in str(assert_step["run"])
    proof = _named_step(
        "aquacast-release-image-proof", "Prove Aquacast image release identity"
    )
    assert proof.get("env") == {
        "EXPECTED_VERSION": "0.dev0+g${{ github.sha }}",
        "EXPECTED_REVISION": "${{ github.sha }}",
    }
    assert "tools/image_identity_probe.py" in str(proof["run"])
    assert "--variant aquacast" in str(proof["run"])
    assert '--expected-version "$EXPECTED_VERSION"' in str(proof["run"])
    assert '--expected-revision "$EXPECTED_REVISION"' in str(proof["run"])
