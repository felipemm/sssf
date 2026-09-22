"""Deterministic sandbox lifecycle: worktrees, docker, auto-teardown.

Split into concern modules: orchestrator (run lifecycle), worktree_git
(worktrees + integration), docker (image + container lifecycle), rundb
(project db + per-run sync), session_env (env + reopen).

Public interface (Q2a): ONLY the run-lifecycle verbs. Callers that need
more import the concern module directly — healer → docker/rundb/
orchestrator/session_env(+worktree_git for sandbox_dir), ticket → worktree_git,
sweep → docker verb + worktree_git + rundb, sandbox_cmd → per-module.
"""

from sssf.sandbox.docker import SandboxError as SandboxError
from sssf.sandbox.orchestrator import (
    abort_sandbox as abort_sandbox,
)
from sssf.sandbox.orchestrator import enabled as enabled
from sssf.sandbox.orchestrator import (
    spawn_monitor as spawn_monitor,
)
from sssf.sandbox.orchestrator import spawn_sandbox as spawn_sandbox
from sssf.sandbox.orchestrator import stop_run as stop_run
from sssf.sandbox.orchestrator import (
    teardown_sandbox as teardown_sandbox,
)
from sssf.sandbox.worktree_git import sandbox_dir as sandbox_dir
