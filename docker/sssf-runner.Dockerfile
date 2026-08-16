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

# uv (python project runtimes)
RUN pip install --no-cache-dir uv

# bun (JS/TS app runtimes)
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

# pi — the coding-agent CLI the ADW shells to for agent calls
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# sssf itself (the build context is the sssf repo root)
COPY pyproject.toml README.md /opt/sssf/
COPY src/sssf /opt/sssf/src/sssf/
RUN pip install --no-cache-dir /opt/sssf

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
