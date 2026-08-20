"""Ops tests for Plan 105 D3 — launchd docker-prune job.

Tests assert:
  1. prune-docker.sh skips the prune when docker ps finds no sapphire container.
  2. install-launchd.sh PLISTS array registers the new docker-prune plist.

These are shell-script tests, not Python unit tests in the usual sense.  We
shell out via subprocess to exercise the actual script; docker is faked by
setting DOCKER_CMD to an absolute path of a stub so the test never touches the
Docker daemon.  (We use DOCKER_CMD rather than PATH injection because
prune-docker.sh exports PATH="/usr/local/bin:..." which prepends system dirs
before any test-injected PATH entry, causing the real docker to be resolved
instead of the fake.)
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_PRUNE_SCRIPT = _SCRIPTS_DIR / "prune-docker.sh"
_INSTALL_SCRIPT = _SCRIPTS_DIR / "install-launchd.sh"


def _write_fake_docker(bin_dir: Path, *, containers: list[str]) -> Path:
    """Write a fake docker executable to bin_dir.

    When invoked with `ps`, it prints one container name per line and exits 0.
    All other invocations (system df, image prune, builder prune) exit 0 with
    no output so they are safe no-ops.
    """
    if containers:
        # printf '%s\\n' "name1" "name2" prints each on its own line.
        printf_args = " ".join(f'"{c}"' for c in containers)
        ps_body = f"printf '%s\\n' {printf_args}"
    else:
        ps_body = "true"

    stub = textwrap.dedent(
        f"""\
        #!/bin/bash
        if [[ "$1" == "ps" ]]; then
            {ps_body}
            exit 0
        fi
        # system df, image prune, builder prune — all safe no-ops in tests.
        exit 0
        """
    )
    fake = bin_dir / "docker"
    fake.write_text(stub)
    fake.chmod(0o755)
    return fake


def _run_prune_script(
    tmp_path: Path, *, docker_cmd: Path
) -> subprocess.CompletedProcess[str]:
    """Run prune-docker.sh with DOCKER_CMD pointing at the given fake docker."""
    env = {**os.environ, "DOCKER_CMD": str(docker_cmd)}
    return subprocess.run(
        ["bash", str(_PRUNE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


class TestPruneDockerStackGuard:
    """prune-docker.sh must skip all pruning when no sapphire container is running."""

    def test_skips_when_no_sapphire_container(self, tmp_path: Path) -> None:
        """When docker ps returns no sapphire container names, the script exits 0
        and prints the 'skipping prune' message without running any prune command."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = _write_fake_docker(bin_dir, containers=["other-container", "unrelated"])

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0, f"script failed: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "skipping prune" in combined, (
            f"expected 'skipping prune' in output; got:\n{combined}"
        )
        # None of the prune commands must appear in stdout when the stack is down.
        assert "image prune" not in combined, (
            "image prune was called despite no sapphire containers"
        )
        assert "builder prune" not in combined, (
            "builder prune was called despite no sapphire containers"
        )

    def test_skips_when_docker_ps_empty(self, tmp_path: Path) -> None:
        """Empty docker ps output (no containers at all) is treated as stack-down."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = _write_fake_docker(bin_dir, containers=[])

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "skipping prune" in combined

    def test_skips_when_only_non_compose_sapphire_container(
        self, tmp_path: Path
    ) -> None:
        """A container named 'sapphire-something' (without the 'sapphire_flow-'
        Compose prefix) must NOT satisfy the stack-up guard.

        The old guard used 'grep -q sapphire' which matched any container
        containing the substring; the new guard requires the exact Compose
        prefix '^sapphire_flow-'. This test locks the tighter pattern.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = _write_fake_docker(
            bin_dir, containers=["sapphire-other", "not-the-real-stack"]
        )

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0, f"script failed: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "skipping prune" in combined, (
            "guard did not fire for non-compose 'sapphire-*' container; "
            f"got:\n{combined}"
        )

    def test_proceeds_when_sapphire_container_present(self, tmp_path: Path) -> None:
        """When a sapphire container is running the stack-up guard passes.

        The fake docker system df returns '0B' reclaimable, so neither
        prune command fires (below the 1 GB threshold) — but the script must
        NOT exit early with 'skipping prune'.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # Override system df to return minimal JSON so the size-guard parses.
        stub = textwrap.dedent(
            """\
            #!/bin/bash
            if [[ "$1" == "ps" ]]; then
                printf 'sapphire_flow-worker-1\\n'
                exit 0
            fi
            if [[ "$1" == "system" && "$2" == "df" ]]; then
                printf '{"Type":"Images","Reclaimable":"0B"}\\n'
                printf '{"Type":"Build Cache","Reclaimable":"0B"}\\n'
                exit 0
            fi
            exit 0
            """
        )
        fake = bin_dir / "docker"
        fake.write_text(stub)
        fake.chmod(0o755)

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0, f"script failed: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "skipping prune" not in combined, (
            f"guard fired despite sapphire container running:\n{combined}"
        )
        # Size guard fires but skips both prunes (0 GB < 1 GB threshold).
        assert "skipping image prune" in combined


class TestInstallLaunchdPruneRegistration:
    """install-launchd.sh PLISTS array must contain the docker-prune plist."""

    def test_plists_contains_docker_prune_plist(self) -> None:
        content = _INSTALL_SCRIPT.read_text()
        assert "ch.hydrosolutions.sapphire-docker-prune.plist" in content, (
            "install-launchd.sh PLISTS array is missing "
            "'ch.hydrosolutions.sapphire-docker-prune.plist'"
        )

    def test_docker_prune_plist_file_exists(self) -> None:
        plist = _SCRIPTS_DIR / "ch.hydrosolutions.sapphire-docker-prune.plist"
        assert plist.exists(), f"plist file not found at {plist}"

    def test_prune_script_exists_and_is_executable(self) -> None:
        assert _PRUNE_SCRIPT.exists(), f"prune-docker.sh not found at {_PRUNE_SCRIPT}"
        assert os.access(_PRUNE_SCRIPT, os.X_OK), (
            f"prune-docker.sh is not executable: {_PRUNE_SCRIPT}"
        )


# --- Rollback-anchor protection ------------------------------------------
#
# The stub below is a Python fake, not a bash one, because the behaviour under
# test is keyed on IMAGE IDS and on which `--format` was requested. A bash stub
# that dispatches on "$1"/"$2" alone cannot represent two tags sharing one image
# id, and answers `ps` and `ps -a` with different FIELDS rather than different
# CONTAINER SETS — which makes a `ps -a` -> `ps` mutation fail for the wrong
# reason and silently validates reference-equality code.

_FAKE_DOCKER = """#!/usr/bin/env python3
import json, sys
fx = json.load(open(FIXTURE))
a = sys.argv[1:]
def out(lines):
    sys.stdout.write("".join(l + "\\n" for l in lines))
if a[:1] == ["ps"]:
    if fx.get("ps_fails"):
        sys.exit(1)
    if fx.get("ps_aq_fails") and "-aq" in a:
        sys.exit(1)
    flags = "".join(
        x[1:] for x in a[1:] if x.startswith("-") and not x.startswith("--")
    )
    allc = fx["containers"]
    sel = allc if "a" in flags else [c for c in allc if c["running"]]
    if "q" in flags:
        out([c["cid"] for c in sel])
    elif "{{.Image}}" in a:
        out([c["image_ref"] for c in sel])
    else:
        out([c["name"] for c in sel])
    sys.exit(0)
if a[:1] == ["inspect"]:
    if fx.get("inspect_fails"):
        sys.exit(1)
    if a[1:3] != ["-f", "{{.Image}}"]:
        sys.exit(64)
    ids = list(a[3:])
    by_cid = {c["cid"]: c for c in fx["containers"]}
    out([by_cid[i]["image_id"] for i in ids if i in by_cid])
    sys.exit(0)
if a[:1] == ["images"]:
    if fx.get("images_fails"):
        sys.exit(1)
    if "--format" not in a:
        sys.exit(64)
    if a[a.index("--format") + 1] != "{{.ID}} {{.Repository}}:{{.Tag}}":
        sys.exit(64)
    trunc = "--no-trunc" not in a
    rows = []
    for i in fx["images"]:
        iid = i["id"]
        if trunc:
            iid = iid.split(":", 1)[-1][:12]
        rows.append(f"{iid} {i['ref']}")
    out(rows)
    sys.exit(0)
if a[:2] == ["system", "df"]:
    rec = fx.get("images_reclaimable", "9.0GB (86%)")
    out([json.dumps({"Type": "Images", "Reclaimable": rec}),
         json.dumps({"Type": "Build Cache", "Reclaimable": "0B"})])
    sys.exit(0)
if a[:1] == ["rmi"]:
    open(RMI_LOG, "a").write(a[1] + "\\n")
    sys.exit(1 if a[1] in fx.get("rmi_fails", []) else 0)
if a == ["image", "prune", "-f"]:
    open(CALL_LOG, "a").write("image-prune\\n")
    sys.exit(1 if fx.get("dangling_prune_fails") else 0)
if a == ["builder", "prune", "-f"]:
    open(CALL_LOG, "a").write("builder-prune\\n")
    sys.exit(0)
sys.exit(64)
"""


def _write_py_fake_docker(bin_dir: Path, fixture: dict, rmi_log: Path) -> Path:
    fixture_path = bin_dir / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    call_log = rmi_log.parent / "calls.log"
    header = (
        f"FIXTURE = {str(fixture_path)!r}\n"
        f"RMI_LOG = {str(rmi_log)!r}\n"
        f"CALL_LOG = {str(call_log)!r}\n"
    )
    body = header + _FAKE_DOCKER.split("\n", 1)[1]
    fake = bin_dir / "docker"
    fake.write_text("#!/usr/bin/env python3\n" + body)
    fake.chmod(0o755)
    return fake


def _container(cid: str, name: str, image_id: str, ref: str, running: bool) -> dict:
    return {
        "cid": cid,
        "name": name,
        "image_id": image_id,
        "image_ref": ref,
        "running": running,
    }


_ID_LIVE = "sha256:" + "a1" * 32
_ID_OLD = "sha256:" + "b2" * 32
_ID_ANCHOR = "sha256:" + "c3" * 32


class TestPruneDockerRollbackProtection:
    """Rollback anchors survive; everything genuinely unused does not."""

    def _run(
        self, tmp_path: Path, fixture: dict
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        rmi_log = tmp_path / "rmi.log"
        fake = _write_py_fake_docker(bin_dir, fixture, rmi_log)
        result = _run_prune_script(tmp_path, docker_cmd=fake)
        removed = rmi_log.read_text().split() if rmi_log.exists() else []
        return result, removed

    def _base(self, **over: object) -> dict:
        fx: dict = {
            "containers": [
                _container(
                    "c1", "sapphire_flow-api-1", _ID_LIVE, "sapphire-flow:0.1.775", True
                )
            ],
            "images": [
                {"id": _ID_LIVE, "ref": "sapphire-flow:0.1.775"},
                {"id": _ID_OLD, "ref": "sapphire-flow:0.1.653"},
                {"id": _ID_ANCHOR, "ref": "sapphire-flow:rollback-backup"},
            ],
        }
        fx.update(over)
        return fx

    def test_rollback_anchor_is_never_removed(self, tmp_path: Path) -> None:
        result, removed = self._run(tmp_path, self._base())
        assert result.returncode == 0, result.stderr
        assert "sapphire-flow:rollback-backup" not in removed, f"removed={removed}"
        assert "protected, not pruned: sapphire-flow:rollback-backup" in (
            result.stdout + result.stderr
        )

    def test_unreferenced_non_anchor_is_removed(self, tmp_path: Path) -> None:
        _, removed = self._run(tmp_path, self._base())
        assert removed == ["sapphire-flow:0.1.653"], f"removed={removed}"

    def test_image_pinned_only_by_an_exited_container_is_kept(
        self, tmp_path: Path
    ) -> None:
        """The exited container pins a DISTINCT image, so `ps` vs `ps -a` is
        genuinely load-bearing here: with plain `ps` this image looks unused."""
        fx = self._base()
        fx["containers"].append(
            _container(
                "c2", "sapphire_flow-init-1", _ID_OLD, "sapphire-flow:0.1.653", False
            )
        )
        _, removed = self._run(tmp_path, fx)
        assert removed == [], f"exited container's image was pruned: {removed}"

    def test_alternate_tag_of_an_in_use_image_is_kept(self, tmp_path: Path) -> None:
        """Two refs, one image id, container created from the other ref.
        Comparing references instead of ids would untag the live image."""
        fx = self._base()
        fx["images"].append({"id": _ID_LIVE, "ref": "sapphire-flow:alias"})
        _, removed = self._run(tmp_path, fx)
        assert "sapphire-flow:alias" not in removed, f"removed={removed}"

    def test_digest_pinned_container_still_protects_its_image(
        self, tmp_path: Path
    ) -> None:
        """docker-compose.yml pins postgis by @sha256, so the container's
        reference never equals the `repo:tag` from `docker images`."""
        fx = self._base()
        fx["containers"].append(
            _container(
                "c3",
                "sapphire_flow-postgres-1",
                ("sha256:" + "d4" * 32),
                "postgis/postgis:16-3.4@sha256:44126d",
                True,
            )
        )
        fx["images"].append(
            {"id": ("sha256:" + "d4" * 32), "ref": "postgis/postgis:16-3.4"}
        )
        _, removed = self._run(tmp_path, fx)
        assert "postgis/postgis:16-3.4" not in removed, f"removed={removed}"

    def test_dangling_rows_are_skipped(self, tmp_path: Path) -> None:
        fx = self._base()
        fx["images"].append({"id": ("sha256:" + "e5" * 32), "ref": "<none>:<none>"})
        _, removed = self._run(tmp_path, fx)
        assert "<none>:<none>" not in removed, f"removed={removed}"

    def test_container_inventory_failure_prunes_nothing_and_fails(
        self, tmp_path: Path
    ) -> None:
        """A daemon error must never read as 'nothing is in use'."""
        result, removed = self._run(tmp_path, self._base(inspect_fails=True))
        assert removed == [], f"pruned despite unusable inventory: {removed}"
        assert result.returncode != 0
        assert "skipping image prune" in (result.stdout + result.stderr)

    def test_image_inventory_failure_prunes_nothing_and_fails(
        self, tmp_path: Path
    ) -> None:
        result, removed = self._run(tmp_path, self._base(images_fails=True))
        assert removed == [], f"removed={removed}"
        assert result.returncode != 0

    def test_rmi_failure_is_reported_in_exit_status(self, tmp_path: Path) -> None:
        result, _ = self._run(tmp_path, self._base(rmi_fails=["sapphire-flow:0.1.653"]))
        assert result.returncode != 0, "a failed rmi must not report success"
        assert "WITH FAILURES" in (result.stdout + result.stderr)

    def test_invalid_protect_pattern_fails_closed(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        rmi_log = tmp_path / "rmi.log"
        fake = _write_py_fake_docker(bin_dir, self._base(), rmi_log)
        env = {**os.environ, "DOCKER_CMD": str(fake), "PRUNE_PROTECT_RE": "*bad["}
        result = subprocess.run(
            ["bash", str(_PRUNE_SCRIPT)], capture_output=True, text=True, env=env
        )
        removed = rmi_log.read_text().split() if rmi_log.exists() else []
        assert removed == [], f"pruned under an invalid pattern: {removed}"
        assert result.returncode != 0

    def test_repository_named_rollback_is_not_protected(self, tmp_path: Path) -> None:
        """Protection is a TAG match; a repo called rollback/* must still prune."""
        fx = self._base()
        fx["images"].append({"id": ("sha256:" + "f6" * 32), "ref": "rollback/tool:1.0"})
        _, removed = self._run(tmp_path, fx)
        assert "rollback/tool:1.0" in removed, f"removed={removed}"

    def test_ps_aq_inventory_failure_prunes_nothing_and_fails(
        self, tmp_path: Path
    ) -> None:
        """Fails only the `ps -aq` inventory call, letting the stack-up guard
        pass first — otherwise a fail-open there is invisible to the suite."""
        result, removed = self._run(tmp_path, self._base(ps_aq_fails=True))
        assert removed == [], f"pruned despite unusable inventory: {removed}"
        assert result.returncode != 0
        assert "could not list containers" in (result.stdout + result.stderr)

    def test_dangling_prune_is_invoked(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        rmi_log = tmp_path / "rmi.log"
        fake = _write_py_fake_docker(bin_dir, self._base(), rmi_log)
        _run_prune_script(tmp_path, docker_cmd=fake)
        calls = (bin_dir.parent / "calls.log").read_text().split()
        assert "image-prune" in calls, f"dangling prune never ran: {calls}"

    def test_dangling_prune_failure_reaches_exit_status(self, tmp_path: Path) -> None:
        result, _ = self._run(tmp_path, self._base(dangling_prune_fails=True))
        assert result.returncode != 0
        assert "dangling-layer prune failed" in (result.stdout + result.stderr)

    def test_untagged_repo_rows_are_never_sent_to_rmi(self, tmp_path: Path) -> None:
        """Live host has `caddy:<none>`; `docker rmi caddy:<none>` targets a
        reference that does not exist and always fails."""
        fx = self._base()
        fx["images"].append({"id": ("sha256:" + "07" * 32), "ref": "caddy:<none>"})
        result, removed = self._run(tmp_path, fx)
        assert "caddy:<none>" not in removed, f"removed={removed}"
        assert result.returncode == 0, "untagged row must not cause a false failure"
