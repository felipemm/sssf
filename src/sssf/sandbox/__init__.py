"""Deterministic sandbox lifecycle: worktrees, docker, auto-teardown.

Split into concern modules: orchestrator (run lifecycle), worktree_git
(worktrees + integration), docker (image + container lifecycle), rundb
(project db + per-run sync), session_env (env + reopen). This package
re-exports the full historical surface so existing call sites compile
unchanged; the public interface trims down to the run-lifecycle verbs.
"""

from sssf.sandbox.docker import _FINGERPRINT_PATH as _FINGERPRINT_PATH
from sssf.sandbox.docker import SandboxError as SandboxError
from sssf.sandbox.docker import _docker as _docker
from sssf.sandbox.docker import _engine_fingerprint as _engine_fingerprint
from sssf.sandbox.docker import _fingerprint_cache as _fingerprint_cache
from sssf.sandbox.docker import build_image as build_image
from sssf.sandbox.docker import build_runner_image as build_runner_image
from sssf.sandbox.docker import container_name as container_name
from sssf.sandbox.docker import docker_available as docker_available
from sssf.sandbox.docker import ensure_image_current as ensure_image_current
from sssf.sandbox.docker import image_engine_fingerprint as image_engine_fingerprint
from sssf.sandbox.docker import image_is_current as image_is_current
from sssf.sandbox.docker import run_sandbox as run_sandbox
from sssf.sandbox.docker import runner_dockerfile as runner_dockerfile
from sssf.sandbox.docker import runner_source_root as runner_source_root
from sssf.sandbox.docker import stop_container as stop_container
from sssf.sandbox.docker import stop_remove as stop_remove
from sssf.sandbox.orchestrator import _container_gone as _container_gone
from sssf.sandbox.orchestrator import _flip_sandbox_run_stopped as _flip_sandbox_run_stopped
from sssf.sandbox.orchestrator import _run_ended as _run_ended
from sssf.sandbox.orchestrator import abort_sandbox as abort_sandbox
from sssf.sandbox.orchestrator import enabled as enabled
from sssf.sandbox.orchestrator import monitor_run as monitor_run
from sssf.sandbox.orchestrator import prune_sandbox as prune_sandbox
from sssf.sandbox.orchestrator import record_never_started as record_never_started
from sssf.sandbox.orchestrator import spawn_monitor as spawn_monitor
from sssf.sandbox.orchestrator import spawn_sandbox as spawn_sandbox
from sssf.sandbox.orchestrator import stamp_adw_template as stamp_adw_template
from sssf.sandbox.orchestrator import stop_run as stop_run
from sssf.sandbox.orchestrator import teardown_sandbox as teardown_sandbox
from sssf.sandbox.rundb import _forward_merge as _forward_merge
from sssf.sandbox.rundb import _record_sandbox_run as _record_sandbox_run
from sssf.sandbox.rundb import _row_obeys as _row_obeys
from sssf.sandbox.rundb import _session_status as _session_status
from sssf.sandbox.rundb import project_db_path as project_db_path
from sssf.sandbox.rundb import sandbox_run_db as sandbox_run_db
from sssf.sandbox.rundb import sync_run_db as sync_run_db
from sssf.sandbox.session_env import _git_identity as _git_identity
from sssf.sandbox.session_env import reopen_session as reopen_session
from sssf.sandbox.session_env import sandbox_env as sandbox_env
from sssf.sandbox.worktree_git import _exclude_worktrees as _exclude_worktrees
from sssf.sandbox.worktree_git import _finish_merged as _finish_merged
from sssf.sandbox.worktree_git import _git_head as _git_head
from sssf.sandbox.worktree_git import _has_remote as _has_remote
from sssf.sandbox.worktree_git import _integrate as _integrate
from sssf.sandbox.worktree_git import _integration_branch as _integration_branch
from sssf.sandbox.worktree_git import _integration_cfg as _integration_cfg
from sssf.sandbox.worktree_git import _outcome as _outcome
from sssf.sandbox.worktree_git import _push_target as _push_target
from sssf.sandbox.worktree_git import _ref_exists as _ref_exists
from sssf.sandbox.worktree_git import _resolve_with_agent as _resolve_with_agent
from sssf.sandbox.worktree_git import _run_git as _run_git
from sssf.sandbox.worktree_git import _worktree_registered as _worktree_registered
from sssf.sandbox.worktree_git import create_worktree as create_worktree
from sssf.sandbox.worktree_git import delete_branch as delete_branch
from sssf.sandbox.worktree_git import integrate_run as integrate_run
from sssf.sandbox.worktree_git import integrate_successful_run as integrate_successful_run
from sssf.sandbox.worktree_git import remove_worktree as remove_worktree
from sssf.sandbox.worktree_git import sandbox_dir as sandbox_dir
