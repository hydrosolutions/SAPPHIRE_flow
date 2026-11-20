#!/bin/sh
# Installed next to current; pin the environment directory before starting Python.
set -eu
monitor_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
environment=$(CDPATH='' cd -- "${monitor_root}/current" && pwd -P)
exec "${environment}/bin/python" -I -m sapphire_flow.ops.watchdog "$@"
