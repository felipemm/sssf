# The sssf-runner image: python + git + node/pi + bun + uv + sssf.
# NO credentials, NO project files — the host provides those at container start.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates nodejs npm \
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

# Run as the host uid:gid; safe.directory so git trusts the mounted worktree.
RUN groupadd -g 1000 agent && useradd -u 1000 -g 1000 -m agent
USER 1000:1000
ENV HOME=/home/agent
RUN git config --global --add safe.directory /work

ENTRYPOINT ["/bin/sh", "-c", "git config --global --add safe.directory /work 2>/dev/null; exec \"$@\"", "--"]
