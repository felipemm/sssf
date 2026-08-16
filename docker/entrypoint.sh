#!/bin/sh
# sssf-runner entrypoint: prepare git trust + a WRITABLE copy of the pi config
# (the host's ~/.pi/agent is mounted read-only at /opt/pi-agent-host — pi needs
# lock files and auth refreshes, so we copy into $HOME instead of mounting
# the real config), then exec the ADW command.
set -e
git config --global --add safe.directory /work 2>/dev/null || true
mkdir -p "$HOME/.pi/agent"
cp -r /opt/pi-agent-host/. "$HOME/.pi/agent/" 2>/dev/null || true
exec "$@"
