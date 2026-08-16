#!/bin/sh
# sssf-runner entrypoint: prepare git trust + a WRITABLE pi config home.
# The host's ~/.pi/agent is mounted read-only at /opt/pi-agent-host; pi needs
# lock files and auth refreshes, so we copy into $HOME. Only the CONFIG files
# cross (top-level *.json + settings) — never the skills/git/sessions dirs,
# which would distract the ADW's agents (they read them and burn tokens).
set -e
git config --global --add safe.directory /work 2>/dev/null || true
mkdir -p "$HOME/.pi/agent"
for f in /opt/pi-agent-host/*.json; do
  [ -f "$f" ] && cp "$f" "$HOME/.pi/agent/"
done
[ -d /opt/pi-agent-host/settings ] && cp -r /opt/pi-agent-host/settings "$HOME/.pi/agent/"
exec "$@"
