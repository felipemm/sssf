#!/bin/sh
# sssf-runner entrypoint: prepare git trust + a WRITABLE pi config home.
# The host's ~/.pi/agent is mounted read-only at /opt/pi-agent-host; pi needs
# lock files and auth refreshes, so we copy into $HOME. Only the CONFIG files
# the sandbox actually needs cross — never the skills/git/sessions dirs, which
# would distract the ADW's agents (they read them and burn tokens).
set -e
git config --global --add safe.directory /work 2>/dev/null || true
mkdir -p "$HOME/.pi/agent"
# Explicit whitelist — the model catalog and auth. settings.json is NOT copied
# wholesale: the operator's settings install interactive pi packages
# (honcho-memory, voice-stt, context7, web-search, git:obra/superpowers) into a
# cold home on first use — a network bootstrap that can exceed the ADW's
# 30s pi --list-models catalog timeout and kill config validation. Sandbox
# agents need none of them, so the sandbox gets a minimal settings.json.
# mcp-*.json / stt.json / trust.json are host-interactive state too — copying
# them would make pi try to reach host MCP servers that are not reachable from
# the container.
for f in models.json models-store.json auth.json package.json; do
  [ -f "/opt/pi-agent-host/$f" ] && cp "/opt/pi-agent-host/$f" "$HOME/.pi/agent/"
done
cat > "$HOME/.pi/agent/settings.json" <<'JSON'
{"quietStartup": true}
JSON

# impeccable pi skill — vendored in the image; copied into the pi home here
# because skills are otherwise excluded from the sandbox.
mkdir -p "$HOME/.pi/agent/skills"
cp -r /opt/impeccable-pi/skills/impeccable "$HOME/.pi/agent/skills/"
exec "$@"
