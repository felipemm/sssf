# The sssf-runner image: python + git + node/pi + bun + uv + sssf.
# NO credentials, NO project files — the host provides those at container start.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Modern node (apt's node 18 is too old for current pi/undici) — nodesource 22
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# pi — the coding-agent CLI the ADW shells to for agent calls
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# uv (python project runtimes)
RUN pip install --no-cache-dir uv

# bun (JS/TS app runtimes) — installed via npm so it lands in /usr/local
# (world-readable; /root would be 700 and unreachable by the runtime uid).
RUN npm install -g bun

# snyk — the security quality gate (quality.checks). Static binary (the
# canonical Snyk pattern — no npm wrapper, no first-run extraction into
# root-owned dirs, works as the non-root container user). Arch-aware: the
# bare snyk-linux is x64-only, ARM needs snyk-linux-arm64. Auth comes from
# SNYK_TOKEN (forwarded by sandbox_env), never a configstore file.
RUN ARCH=$(uname -m); [ "$ARCH" = "x86_64" ] && SUF=linux || SUF=linux-arm64; \
    curl -fsSL "https://static.snyk.io/cli/latest/snyk-$SUF" -o /usr/local/bin/snyk \
    && chmod +x /usr/local/bin/snyk

# impeccable — design quality: CLI for the deterministic gate (detect) and the
# pi skill the designer agent runs (/impeccable audit|critique|polish|optimize,
# init, document). npm lands in /usr/local (world-readable for the runtime uid,
# like bun). The skill is vendored under docker/impeccable-pi/ and copied into
# the pi home by entrypoint.sh — skills are otherwise excluded from the sandbox.
RUN npm install -g impeccable@3.6.1
COPY docker/impeccable-pi /opt/impeccable-pi

# Headless Chrome — impeccable's browser engine (audit/critique/visual
# contrast) launches it through puppeteer for REAL rendered-page checks; the
# node-only detector is the degraded fallback when no browser exists.
#   * Chrome for Testing publishes NO linux-arm64 build (linux64/mac/win only),
#     and impeccable's pinned puppeteer resolves arm64 to the x86_64 build,
#     which cannot run in an arm64 container — so install Google Chrome's
#     arm64 .deb instead and pin puppeteer to it via PUPPETEER_EXECUTABLE_PATH.
#   * Containers cannot namespace-clone (docker seccomp denies it even with
#     the setuid helper: "Failed to move to new namespace ... Operation not
#     permitted"), and impeccable only passes --no-sandbox under CI — so every
#     chrome launch goes through a wrapper that adds the container flags.
RUN apt-get update -qq \
    && curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb fonts-liberation \
    && rm -f /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/chrome-bin \
    && printf '#!/bin/sh\nexec /usr/bin/google-chrome-stable --no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage "$@"\n' \
       > /opt/chrome-bin/chrome \
    && chmod 755 /opt/chrome-bin/chrome
ENV PUPPETEER_EXECUTABLE_PATH=/opt/chrome-bin/chrome

# sssf itself (the build context is the sssf repo root)
COPY pyproject.toml README.md /opt/sssf/
COPY src/sssf /opt/sssf/src/sssf/
RUN pip install --no-cache-dir /opt/sssf

# Staleness marker: fingerprint of the engine source at build time. The CLI
# recomputes it against its own package and refuses to spawn on mismatch
# (issue #21) — engine changes silently broke every sandboxed run until the
# image was rebuilt.
RUN find /opt/sssf/src/sssf -type f \
    ! -path "*node_modules*" ! -path "*__pycache__*" ! -path "*/.venv/*" ! -path "*/.git/*" \
    ! -path "*visualizer*" \
    ! -name "*.pyc" ! -type l \
    | sort | xargs sha256sum | awk '{print $1}' | sha256sum | awk '{print $1}' > /opt/sssf-fingerprint

# Entrypoint: git trust + a writable copy of the pi config (see entrypoint.sh)
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run as the host uid:gid (overrides USER at runtime). HOME=/tmp is writable by
# any uid — the entrypoint copies the ro pi config there and pi gets its locks.
RUN groupadd -g 1000 agent && useradd -u 1000 -g 1000 -m agent && chmod 755 /home/agent
USER 1000:1000
ENV HOME=/tmp
RUN git config --global --add safe.directory /work

ENTRYPOINT ["/entrypoint.sh"]
