# Installed host monitor

The watchdog must keep running when Docker or Prefect is unavailable. Install a
wheel in a separate environment; never import application source from an editable
checkout or resolve dependencies during a scheduled tick. The deployment directory
is still required as the scheduler's working directory and for configuration,
secrets and state. This change does not make those paths independent of the checkout.

## Provision or update

Use Python >=3.12 and the repository's pinned uv version. Prepare a **clean
checkout of the approved release commit**. Do not build from a deployment checkout
with local configuration edits. No Docker or private forecast dependencies are
needed: the `host-monitor` dependency group exports the existing locked HTTP and
logging dependencies. The application wheel is deliberately installed without
its forecasting dependencies; this environment is only for the watchdog.

On the Mac mini, as the `sapphire` user:

```sh
uv run --no-project python /path/to/approved-release/scripts/install_host_monitor.py \
  --source /path/to/approved-release \
  --root /Users/sapphire/.local/share/sapphire-monitor
```

The same installer works on Linux with a service-owned installation root. Do not
run it as root merely to work around ownership. It builds the normal package
wheel, installs hashed requirements from `uv.lock`, and checks package version and
watchdog import/start from outside the checkout with Python isolated mode. A
manifest records the source commit, package version, wheel hash and requirements
hash. Failed candidates remain inactive for diagnosis. Successful installs switch
`current` atomically and retain `previous`. The installed `run-monitor` launcher
resolves the environment directory once before executing its Python interpreter,
so existing processes finish with the environment they started in even when
`current` changes. No scheduler or service is restarted by the installer.

After installation, install the Mac LaunchAgents using the existing
`scripts/launchd/install-launchd.sh`. That installer checks watchdog startup
before replacing any agent. The watchdog's stable launcher runs the resolved
environment's `bin/python -I`;
its working directory still supplies deployment-specific secret and state paths,
but cannot override its installed Python package. Repeat the wheel installation
on every approved host-monitor upgrade. Updating the application checkout alone
does not update the monitor.

Linux installation is shared, but **Linux scheduler monitoring is not certified
by this M1 change**: the existing watchdog still probes launchd. The later portable
scheduler milestone must select systemd checks, service identity and rootful or
rootless Docker restart privileges before unattended Linux recovery is enabled.
Do not interpret missing launchd on Linux as evidence that Docker is unhealthy.

## Verification and readiness

1. Read `current/manifest.json` and verify the intended release and wheel hash.
2. Observe a completed scheduled tick, state persistence and health results.
   Successful `--help` is only an installation smoke check, not service health.
3. Verify the existing external dead-man service receives a heartbeat only after
   completion; rehearse a missing tick in a maintenance window. The monitor's own
   process cannot alert when it never starts. Without a configured off-host
   channel, readiness is **partial** and unattended runtime recovery stays off.
4. On macOS, verify the service user's GUI session, automatic-login policy and
   sleep settings. Do not silently change them. Rehearse reboot and scheduled-tick
   recovery before declaring unattended operation ready.
5. Preserve incident diagnostics outside `/tmp`, in an operator-owned directory
   with archive mode `0600`, before restart/update exercises. Never publish raw
   archives; they can contain operational configuration or credentials.

M1 adds no Docker update, restart automation, new alert destination, or flow-run
reconciliation. These require the subsequent recovery milestones.

## Rollback

Stop new installation activity, confirm `previous/manifest.json` is the intended
known-good release and run its interpreter's `-I -m sapphire_flow.ops.watchdog
--help` smoke check. With the monitor scheduler paused, atomically replace the
`current` symlink with a symlink to that previous environment, then resume and
verify a scheduled tick. Do not rename environment directories: virtualenv
scripts contain absolute paths. Keep both manifests and candidate environments
until release acceptance; cleanup is a deliberate operator action.
