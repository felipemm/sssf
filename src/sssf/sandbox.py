"""Deterministic sandbox lifecycle: ports, worktrees, docker, review records.

Every function here is plain Python — no agents, no ad-hoc steps. Creation
and teardown are idempotent so a crash mid-teardown leaves re-runnable
cleanup.
"""
import itertools
import socket


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot fulfil a request (no free port, etc.)."""


def allocate_port(base: int, used: set[int] | None = None) -> int:
    """First free host port >= base. Bind-tests 127.0.0.1 so parallel runs of
    the same project don't collide; `used` skips ports already handed out
    this session. Raises SandboxError once the scan passes 65535."""
    used = used or set()
    for port in itertools.count(base):
        if port > 65535:
            raise SandboxError(f"no free host port from {base}")
        if port in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
