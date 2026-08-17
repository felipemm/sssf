# GitHub + GitLab Ticketing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `github` (via the `gh` CLI) and `gitlab` (via the `glab` CLI) ticketing providers to sssf — origin-driven repo resolution, a manual `sssf ticket pr` draft PR/MR command, and a provider picker on the kanban refresh button.

**Architecture:** Extends the existing ticketing subsystem. The Python engine owns everything: two new adapters in `sssf/ticketing.py` shelling to `gh`/`glab` exactly like `fetch_jira` shells to `acli`; a `detect_origin` helper parses `git config --get remote.origin.url` and each provider matches the origin host against its expected hosts — the cloud standard URL (`github.com`/`gitlab.com`) by default, extended per-provider with `self_hosted: true` and an optional `custom_url:`; any origin-host mismatch is reported as a config-issue warning (never a silent assumption), and `repo:` overrides win. The CLI grows `sync --provider` and the new `pr` command; the viz server passes through `?provider=` and reports enabled providers; the kanban shows a picker when >1 external provider is enabled.

**Tech Stack:** Python 3.11+ (subprocess, sqlite3, pyyaml — no new deps), `gh` CLI, `glab` CLI, bun + Vue 3 visualizer, pytest + bun test.

**Spec:** `docs/superpowers/specs/2026-08-17-github-gitlab-ticketing-design.md`

## Global Constraints

- Work in the isolated worktree `.worktrees/github-gitlab-ticketing` on branch `feat/github-gitlab-ticketing` — main stays untouched; PR at the end (create the worktree at execution time via superpowers:using-git-worktrees).
- **v2 paths only**: `adws/config/ticketing.yaml`, `adws/data/sssf.db`, `adws/modules/`, `adws/prompts/`. Never the v1 `adw_sssf_config`/`adw_data` paths.
- **Providers remain a set**: `jira | linear | github | gitlab | internal`; a broken/missing block for one provider skips it with a clear error, the rest still sync.
- **Origin mismatch = skip with a warning message, never an error.** Resolution is per-provider against expected hosts: cloud standard URL by default (`github` ↔ `github.com`, `gitlab` ↔ `gitlab.com`); `self_hosted: true` (optional `custom_url:`) extends a provider to a self-hosted instance — its host when `custom_url` is set, any host otherwise. `repo:` override wins over host matching entirely.
- **No tokens in sssf**: `gh`/`glab` own their auth (same as `acli`). External sync is read-only; PR/MR creation is the only write path.
- `external_id` = `<repo>#<number|iid>` (e.g. `owner/repo#12`); db `id` = `provider:external_id`; the existing upsert dedupe is unchanged.
- `pr.auto` is **reserved**: it parses and warns "not implemented yet" — no behavior.
- Tests: `uv run pytest` for Python (no new deps — no `uv sync` needed); `cd src/sssf/apps/visualizer && bun test` for the visualizer.
- Commit per task with conventional messages; run the FULL suite at the end.
- **The sandbox runner image bakes the engine** (pip install of sssf at build time) — `ticketing.py` changes require `docker build -t sssf-runner -f docker/sssf-runner.Dockerfile .` from the repo root BEFORE any sandboxed verification (Task 7).
- `gh` and `glab` are already installed on this host (gh 2.97.0, glab 1.113.0) — field checks need no installs.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/sssf/ticketing.py` | + `github`/`gitlab` config blocks; `detect_origin`, `host_matches`, `repo_for`, `pr_target`; `_run_cli`; `fetch_github`, `fetch_gitlab`; `sync_tickets(only=)`; `warn_reserved_pr` |
| `src/sssf/commands/ticket.py` | `sync(project, provider)`; new `pr(ticket_id, project, no_comment)`; `pr.auto` warning in `run`/`sync` |
| `src/sssf/cli.py` | `sync --provider` flag; `pr` subcommand + dispatch |
| `src/sssf/templates/adws/config/ticketing.yaml` | + commented `github:`/`gitlab:` blocks |
| `src/sssf/apps/visualizer/server/tickets.ts` | + `readProviders(root)`, `syncSpawnArgs(root, provider)` |
| `src/sssf/apps/visualizer/server/index.ts` | GET `/tickets` → `{enabled, providers, tickets}`; sync route passes `?provider=` |
| `src/sssf/apps/visualizer/src/lib/api.ts` | `TicketsResponse.providers`; `syncTickets(provider?)` |
| `src/sssf/apps/visualizer/src/components/KanbanBoard.vue` | refresh picker menu (All + each provider) when >1 external provider |
| `src/sssf/apps/visualizer/src/components/TicketCard.vue`, `TicketModal.vue` | provider badges `GH`/`GL` |
| `tests/test_ticketing.py` | config parse, origin detection, repo resolution, adapters, sync filter |
| `tests/test_ticket_cli.py` | `sync --provider`, `ticket pr` flows, reserved `pr.auto` warning |
| `tests/test_init.py` | template stamps the `github:`/`gitlab:` blocks |
| `src/sssf/apps/visualizer/server/tickets.test.ts` | `readProviders`, `syncSpawnArgs` |

---

### Task 1: Config — `github`/`gitlab` blocks + template

**Files:**
- Modify: `src/sssf/ticketing.py` (TicketingConfig dataclass + `load_config`)
- Modify: `src/sssf/templates/adws/config/ticketing.yaml`
- Modify: `tests/test_ticketing.py`
- Modify: `tests/test_init.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TicketingConfig(providers, jira, linear, github, gitlab)` — `github`/`gitlab` are plain dicts (`labels`, optional `repo`, `pr.auto`). `load_config(root)` keeps returning `None` when missing/empty and raising on bad YAML.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ticketing.py`:

```python
def test_github_gitlab_config_parses(tmp_path):
    _write(tmp_path, (
        "providers:\n  - github\n  - gitlab\n"
        "github:\n  labels: [bug]\n  repo: acme/widgets\n"
        "  self_hosted: true\n  custom_url: https://github.company.com\n  pr:\n    auto: true\n"
        "gitlab:\n  labels: [backend]\n"))
    cfg = ticketing.load_config(tmp_path)
    assert cfg is not None
    assert cfg.providers == ["github", "gitlab"]
    assert cfg.github["labels"] == ["bug"]
    assert cfg.github["repo"] == "acme/widgets"
    assert cfg.github["self_hosted"] is True
    assert cfg.github["custom_url"] == "https://github.company.com"
    assert cfg.github["pr"]["auto"] is True
    assert cfg.gitlab["labels"] == ["backend"]
    assert cfg.jira == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ticketing.py::test_github_gitlab_config_parses -v`
Expected: FAIL (`AttributeError: 'TicketingConfig' object has no attribute 'github'`)

- [ ] **Step 3: Implement**

In `src/sssf/ticketing.py`, extend the dataclass:

```python
@dataclass
class TicketingConfig:
    providers: list[str]
    jira: dict = field(default_factory=dict)
    linear: dict = field(default_factory=dict)
    github: dict = field(default_factory=dict)
    gitlab: dict = field(default_factory=dict)
```

And `load_config`'s return:

```python
    return TicketingConfig(providers=list(providers),
                           jira=data.get("jira") or {},
                           linear=data.get("linear") or {},
                           github=data.get("github") or {},
                           gitlab=data.get("gitlab") or {})
```

- [ ] **Step 4: Update the template**

Replace `src/sssf/templates/adws/config/ticketing.yaml` with:

```yaml
# Ticketing integration (optional). The kanban Backlog stage stays hidden until
# this file configures at least one provider.
#
providers:            # any subset of jira | linear | github | gitlab | internal
  - internal
#   # - jira
#   # - linear
#   # - github
#   # - gitlab
#
# jira:                 # via the acli CLI (install and authenticate it first)
#   jql: 'project = ACME AND status in (Backlog, "To Do")'
#
# linear:               # token from the project .env (token_env)
#   team: ENG
#   token_env: LINEAR_TOKEN
#   states: [Backlog, "To Do"]
#
# github:               # via the gh CLI (install + `gh auth login`); the repo
#                       # is parsed from the git remote origin unless overridden
#   labels: []          # optional label filter
#   self_hosted: false  # true for GitHub Enterprise — origin must match custom_url
#   # custom_url: https://github.company.com
#   # repo: owner/repo  # override the origin-derived repo
#   pr:
#     auto: false       # RESERVED — auto PR/MR on run success (not implemented yet)
#
# gitlab:               # via the glab CLI (install + `glab auth login`)
#   labels: []
#   self_hosted: false  # true for self-hosted GitLab (e.g. git.ifoodcorp.com.br)
#   # custom_url: https://git.ifoodcorp.com.br
#   # repo: group/project
#   pr:
#     auto: false
```

- [ ] **Step 5: Extend the init template test**

In `tests/test_init.py`, inside `test_init_stamps_ticketing_template_with_internal_enabled` (after the existing `assert "providers" in text and "- internal" in text`), add:

```python
    assert "github:" in text and "gitlab:" in text
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticketing.py tests/test_init.py -v`
Expected: all PASS (new parse test + the init template test)

- [ ] **Step 7: Commit**

```bash
git add src/sssf/ticketing.py src/sssf/templates/adws/config/ticketing.yaml tests/test_ticketing.py tests/test_init.py
git commit -m "feat: github/gitlab ticketing config blocks + stamped template"
```

---

### Task 2: Origin detection + repo resolution

**Files:**
- Modify: `src/sssf/ticketing.py`
- Modify: `tests/test_ticketing.py`

**Interfaces:**
- Consumes: `TicketingConfig` (Task 1).
- Produces:
  - `detect_origin(root: Path) -> tuple[str, str] | None` — `(host, repo)` from `git -C <root> config --get remote.origin.url`; `None` on any failure (no origin, unparseable).
  - `host_matches(cfg: TicketingConfig, provider: str, host: str) -> bool` — the host satisfies the provider's expected hosts: cloud standard URL (`github.com`/`gitlab.com`) unless `self_hosted: true`; then the `custom_url` host when set, any host otherwise.
  - `repo_for(root: Path, cfg: TicketingConfig, provider: str) -> tuple[str | None, str | None]` — `(repo, None)` when the provider applies; `(None, warning)` when skipped. `repo:` override wins; otherwise the origin host must match the provider's expected hosts.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticketing.py`:

```python
def _git_config(root: Path, url: str) -> Path:
    git = root / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(f'[remote "origin"]\n\turl = {url}\n')
    return root


def test_detect_origin_https(tmp_path):
    root = _git_config(tmp_path, "https://github.com/owner/repo.git")
    assert ticketing.detect_origin(root) == ("github.com", "owner/repo")


def test_detect_origin_scp_ssh(tmp_path):
    root = _git_config(tmp_path, "git@github.com:owner/repo.git")
    assert ticketing.detect_origin(root) == ("github.com", "owner/repo")


def test_detect_origin_ssh_url_form(tmp_path):
    root = _git_config(tmp_path, "ssh://git@gitlab.com/group/proj.git")
    assert ticketing.detect_origin(root) == ("gitlab.com", "group/proj")


def test_detect_origin_selfhosted_gitlab(tmp_path):
    root = _git_config(tmp_path, "git@git.ifoodcorp.com.br:data/viz/repo.git")
    assert ticketing.detect_origin(root) == ("git.ifoodcorp.com.br", "data/viz/repo")


def test_detect_origin_missing(tmp_path):
    assert ticketing.detect_origin(tmp_path) is None


def test_host_matches_cloud_by_default():
    cfg = ticketing.TicketingConfig(providers=["github", "gitlab"])
    assert ticketing.host_matches(cfg, "github", "github.com") is True
    assert ticketing.host_matches(cfg, "github", "git.ifoodcorp.com.br") is False
    assert ticketing.host_matches(cfg, "gitlab", "gitlab.com") is True
    assert ticketing.host_matches(cfg, "gitlab", "github.com") is False


def test_host_matches_self_hosted_custom_url():
    cfg = ticketing.TicketingConfig(
        providers=["gitlab"],
        gitlab={"self_hosted": True, "custom_url": "https://git.ifoodcorp.com.br"})
    assert ticketing.host_matches(cfg, "gitlab", "git.ifoodcorp.com.br") is True
    assert ticketing.host_matches(cfg, "gitlab", "gitlab.com") is False


def test_host_matches_self_hosted_without_custom_url_matches_any():
    cfg = ticketing.TicketingConfig(providers=["github"], github={"self_hosted": True})
    assert ticketing.host_matches(cfg, "github", "gitlab.company.com") is True


def test_repo_for_override_wins_over_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("bitbucket.org", "x/y"))
    cfg = ticketing.TicketingConfig(providers=["github"], github={"repo": "acme/widgets"})
    repo, reason = ticketing.repo_for(tmp_path, cfg, "github")
    assert repo == "acme/widgets" and reason is None


def test_repo_for_cloud_mismatch_warns_self_hosted(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("git.ifoodcorp.com.br", "group/proj"))
    cfg = ticketing.TicketingConfig(providers=["gitlab"])
    repo, reason = ticketing.repo_for(tmp_path, cfg, "gitlab")
    assert repo is None
    assert "gitlab.com" in reason and "self_hosted" in reason


def test_repo_for_custom_url_mismatch_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("other.company.com", "a/b"))
    cfg = ticketing.TicketingConfig(
        providers=["gitlab"],
        gitlab={"self_hosted": True, "custom_url": "https://git.ifoodcorp.com.br"})
    repo, reason = ticketing.repo_for(tmp_path, cfg, "gitlab")
    assert repo is None
    assert "custom_url" in reason


def test_repo_for_selfhosted_gitlab_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("git.ifoodcorp.com.br", "group/proj"))
    cfg = ticketing.TicketingConfig(
        providers=["gitlab"],
        gitlab={"self_hosted": True, "custom_url": "https://git.ifoodcorp.com.br"})
    repo, reason = ticketing.repo_for(tmp_path, cfg, "gitlab")
    assert repo == "group/proj" and reason is None


def test_repo_for_no_origin_hints_override(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: None)
    cfg = ticketing.TicketingConfig(providers=["github"])
    repo, reason = ticketing.repo_for(tmp_path, cfg, "github")
    assert repo is None
    assert "repo:" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticketing.py -v -k "origin or repo_for"`
Expected: FAIL (`AttributeError: module 'sssf.ticketing' has no attribute 'detect_origin'`)

- [ ] **Step 3: Implement**

Append to `src/sssf/ticketing.py`:

```python
def detect_origin(root: Path) -> tuple[str, str] | None:
    """(host, repo) from `git config --get remote.origin.url`; None when absent."""
    result = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, timeout=30)
    url = (result.stdout or "").strip()
    if result.returncode != 0 or not url:
        return None
    url = url.rstrip("/")
    if url.startswith("ssh://"):
        host, _, path = url[len("ssh://"):].partition("/")
        repo = path
    elif "://" in url:
        rest = url.split("://", 1)[1]
        parts = rest.split("/", 1)
        host, repo = parts[0], parts[1] if len(parts) > 1 else ""
    else:                                   # scp-like: git@host:owner/repo.git
        host, _, repo = url.partition(":")
    host = host.split("@")[-1]              # strip git@ from ssh/scp forms
    repo = repo.removesuffix(".git")
    if not host or not repo:
        return None
    return host, repo


CLOUD_HOSTS = {"github": "github.com", "gitlab": "gitlab.com"}


def _host_of(url: str) -> str:
    """The hostname of a custom_url (with or without scheme)."""
    return url.split("://", 1)[-1].split("/", 1)[0]


def host_matches(cfg: TicketingConfig, provider: str, host: str) -> bool:
    """Does the origin host satisfy this provider's expected host(s)?

    Cloud standard URL by default; `self_hosted: true` extends to the
    `custom_url` host when set, any host otherwise.
    """
    block = getattr(cfg, provider) or {}
    if not block.get("self_hosted"):
        return host == CLOUD_HOSTS[provider]
    custom = str(block.get("custom_url") or "").strip().rstrip("/")
    if custom:
        return host == _host_of(custom)
    return True


def _host_warning(cfg: TicketingConfig, provider: str, host: str) -> str:
    """A config-issue warning for an origin host outside the provider's expected hosts."""
    block = getattr(cfg, provider) or {}
    custom = str(block.get("custom_url") or "").strip().rstrip("/")
    if not block.get("self_hosted"):
        return (f"{provider} configured but origin is {host} — the cloud host is "
                f"{CLOUD_HOSTS[provider]}; is this a self-hosted instance? set "
                "`self_hosted: true` and `custom_url:` (or add a `repo:` override)")
    return (f"{provider} configured with custom_url {custom} but origin is {host} — "
            "fix custom_url or add a `repo:` override")


def repo_for(root: Path, cfg: TicketingConfig, provider: str) -> tuple[str | None, str | None]:
    """(repo, None) when the provider applies; (None, warning) when skipped.

    A `repo:` override wins; otherwise the origin host must match the
    provider's expected hosts (cloud by default, extended by self_hosted /
    custom_url). Never raises — a missing or mismatched origin yields a warning.
    """
    block = getattr(cfg, provider) or {}
    override = str(block.get("repo") or "").strip().rstrip("/")
    if override:
        return override, None
    origin = detect_origin(root)
    if origin is None:
        return None, (f"{provider} configured but the repo has no git remote origin — "
                      "add a `repo:` override in ticketing.yaml")
    host, repo = origin
    if not host_matches(cfg, provider, host):
        return None, _host_warning(cfg, provider, host)
    return repo, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticketing.py -v -k "origin or repo_for"`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/ticketing.py tests/test_ticketing.py
git commit -m "feat: ticketing origin detection + repo resolution (override wins)"
```

---

### Task 3: Adapters — `fetch_github`, `fetch_gitlab`, sync wiring

**Files:**
- Modify: `src/sssf/ticketing.py`
- Modify: `tests/test_ticketing.py`

**Interfaces:**
- Consumes: `TicketingConfig`, `TicketRecord`, `repo_for` (Task 2).
- Produces:
  - `_run_cli(binary: str, args: list[str], install_hint: str, timeout: int = 60) -> str` — stdout string; `RuntimeError` with an actionable message when the binary is missing or exits nonzero.
  - `fetch_github(cfg: TicketingConfig, repo: str) -> list[TicketRecord]`
  - `fetch_gitlab(cfg: TicketingConfig, repo: str) -> list[TicketRecord]`
  - `sync_tickets(root: Path, cfg: TicketingConfig, only: str | None = None) -> list[ProviderSyncResult]` — the `only` param restricts to one provider; `github`/`gitlab` go through `repo_for` (skip reason becomes a per-provider error result).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticketing.py`:

```python
def test_fetch_github_parses_and_sends_labels(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)
        class R:
            returncode = 0
            stdout = json.dumps([{
                "number": 12, "title": "Add dark mode", "body": "The app needs a dark theme.",
                "url": "https://github.com/owner/repo/issues/12", "state": "open",
                "labels": [{"name": "bug"}]}])
            stderr = ""
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")
    cfg = ticketing.TicketingConfig(providers=["github"], github={"labels": ["bug"]})
    records = ticketing.fetch_github(cfg, "owner/repo")
    assert calls[0][:8] == ["gh", "issue", "list", "--repo", "owner/repo", "--state", "open", "--json"]
    assert "--label" in calls[0] and "bug" in calls[0]
    assert records[0].external_id == "owner/repo#12"
    assert records[0].source_url == "https://github.com/owner/repo/issues/12"


def test_fetch_github_missing_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="gh"):
        ticketing.fetch_github(ticketing.TicketingConfig(providers=["github"]), "owner/repo")


def test_fetch_gitlab_parses(tmp_path, monkeypatch):
    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = json.dumps([{
                "iid": 42, "title": "GitLab ticket", "description": "Do the thing",
                "web_url": "https://gitlab.com/group/proj/-/issues/42", "state": "opened",
                "labels": ["backend"]}])
            stderr = ""
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/glab")
    cfg = ticketing.TicketingConfig(providers=["gitlab"])
    records = ticketing.fetch_gitlab(cfg, "group/proj")
    assert records[0].external_id == "group/proj#42"
    assert records[0].source_url == "https://gitlab.com/group/proj/-/issues/42"


def test_fetch_gitlab_missing_glab(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="glab"):
        ticketing.fetch_gitlab(ticketing.TicketingConfig(providers=["gitlab"]), "group/proj")


def test_sync_github_syncs_and_gitlab_skips_on_github_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("github.com", "owner/repo"))
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")

    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = json.dumps([{
                "number": 12, "title": "Add dark mode", "body": "dark",
                "url": "https://github.com/owner/repo/issues/12", "state": "open",
                "labels": []}])
            stderr = ""
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    (tmp_path / "adws" / "data").mkdir(parents=True)
    cfg = ticketing.TicketingConfig(providers=["github", "gitlab"], github={}, gitlab={})
    results = ticketing.sync_tickets(tmp_path, cfg)
    by_provider = {r.provider: r for r in results}
    assert by_provider["github"].error is None and by_provider["github"].tickets == 1
    assert by_provider["gitlab"].error is not None and "self_hosted" in by_provider["gitlab"].error


def test_sync_provider_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("github.com", "owner/repo"))
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")

    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = json.dumps([])
            stderr = ""
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    (tmp_path / "adws" / "data").mkdir(parents=True)
    cfg = ticketing.TicketingConfig(providers=["jira", "github"],
                                    jira={"jql": "project = ACME"}, github={})
    results = ticketing.sync_tickets(tmp_path, cfg, only="github")
    assert [r.provider for r in results] == ["github"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticketing.py -v -k "fetch_github or fetch_gitlab or sync_github or sync_provider"`
Expected: FAIL (`AttributeError: module 'sssf.ticketing' has no attribute 'fetch_github'`)

- [ ] **Step 3: Implement**

Append to `src/sssf/ticketing.py`:

```python
def _run_cli(binary: str, args: list[str], install_hint: str, timeout: int = 60) -> str:
    """Run a user-authenticated CLI; return stdout. Actionable RuntimeError when
    missing or failing (mirrors the acli pattern)."""
    if shutil.which(binary) is None:
        raise RuntimeError(f"the provider needs the {binary} CLI — {install_hint}")
    result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"{binary} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}\n"
            f"Is it authenticated? Run `{binary} auth login` (self-hosted: set the host first).")
    return result.stdout


def fetch_github(cfg: TicketingConfig, repo: str) -> list[TicketRecord]:
    args = ["issue", "list", "--repo", repo, "--state", "open",
            "--json", "number,title,body,url,state,labels", "--limit", "100"]
    for label in (cfg.github.get("labels") or []):
        args += ["--label", str(label)]
    data = json.loads(_run_cli(
        "gh", args, "install gh — https://cli.github.com — and run `gh auth login`") or "[]")
    issues = data if isinstance(data, list) else []
    records = []
    for issue in issues:
        number = issue.get("number")
        records.append(TicketRecord(
            provider="github", external_id=f"{repo}#{number}",
            title=str(issue.get("title") or ""),
            description=str(issue.get("body") or ""),
            source_url=str(issue.get("url") or ""),
        ))
    return records


def fetch_gitlab(cfg: TicketingConfig, repo: str) -> list[TicketRecord]:
    args = ["issue", "list", "--repo", repo, "--state", "opened", "--output", "json"]
    for label in (cfg.gitlab.get("labels") or []):
        args += ["--label", str(label)]
    data = json.loads(_run_cli(
        "glab", args, "install glab — https://gitlab.com/gitlab-org/cli — and run `glab auth login`") or "[]")
    issues = data if isinstance(data, list) else []
    records = []
    for issue in issues:
        iid = issue.get("iid")
        records.append(TicketRecord(
            provider="gitlab", external_id=f"{repo}#{iid}",
            title=str(issue.get("title") or ""),
            description=str(issue.get("description") or ""),
            source_url=str(issue.get("web_url") or ""),
        ))
    return records
```

Replace `sync_tickets` in `src/sssf/ticketing.py`:

```python
def sync_tickets(root: Path, cfg: TicketingConfig, only: str | None = None) -> list[ProviderSyncResult]:
    """Load .env, fetch every enabled provider (or just `only`), upsert; one result per provider."""
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except ImportError:
        pass
    from sssf.adw_modules import paths
    db_path = paths.data_dir(root) / "sssf.db"
    results: list[ProviderSyncResult] = []
    for provider in cfg.providers:
        if only is not None and provider != only:
            continue
        try:
            if provider == "jira":
                records = fetch_jira(cfg)
            elif provider == "linear":
                records = fetch_linear(cfg)
            elif provider in ("github", "gitlab"):
                repo, reason = repo_for(root, cfg, provider)
                if repo is None:
                    results.append(ProviderSyncResult(provider, error=reason))
                    continue
                records = fetch_github(cfg, repo) if provider == "github" else fetch_gitlab(cfg, repo)
            elif provider == "internal":
                continue            # internal tickets already live in the db
            else:
                results.append(ProviderSyncResult(provider, error=f"unknown provider {provider!r}"))
                continue
            results.append(ProviderSyncResult(provider, tickets=upsert_tickets(db_path, records)))
        except (RuntimeError, OSError, sqlite3.Error) as error:
            results.append(ProviderSyncResult(provider, error=str(error)))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticketing.py -v`
Expected: all PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add src/sssf/ticketing.py tests/test_ticketing.py
git commit -m "feat: gh/glab ticketing adapters + sync --provider filter"
```

---

### Task 4: CLI — `sync --provider`, `ticket pr`, reserved `pr.auto`

**Files:**
- Modify: `src/sssf/ticketing.py` (+ `warn_reserved_pr`, `pr_target`)
- Modify: `src/sssf/commands/ticket.py`
- Modify: `src/sssf/cli.py`
- Modify: `tests/test_ticket_cli.py`

**Interfaces:**
- Consumes: `repo_for`, `_run_cli`, `TicketingConfig` (Tasks 1–3).
- Produces:
  - `ticketing.warn_reserved_pr(cfg: TicketingConfig) -> None` — prints a "not implemented yet" warning to stderr when any `github.pr.auto`/`gitlab.pr.auto` is true.
  - `ticketing.pr_target(root: Path, cfg: TicketingConfig, provider: str) -> tuple[str | None, str | None, str | None]` — `(forge, repo, None)` when a PR/MR can be created; `(None, None, reason)` otherwise. github/gitlab tickets target their own forge; internal/jira/linear tickets target the origin's forge.
  - `ticket.sync(project: str | None, provider: str | None = None) -> int`
  - `ticket.pr(ticket_id: str, project: str | None = None, no_comment: bool = False) -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticket_cli.py`:

```python
CONFIG_GITHUB = "providers:\n  - internal\n  - github\ngithub:\n  repo: owner/repo\n"


def test_sync_provider_passes_only(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG_GITHUB)
    seen = {}

    def fake_sync(root, cfg, only=None):
        seen["only"] = only
        return [ticketing.ProviderSyncResult("github", tickets=2)]

    monkeypatch.setattr(ticketing, "sync_tickets", fake_sync)
    assert ticket.sync(None, "github") == 0
    assert seen["only"] == "github"


def test_pr_creates_github_draft(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG_GITHUB)
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, description, status, source_url)"
                 " VALUES ('github:owner/repo#12', 'github', 'owner/repo#12', 'Add dark mode',"
                 " 'Make it dark', 'backlog', 'https://github.com/owner/repo/issues/12')")
    conn.commit()
    conn.close()
    calls = []

    def fake_cli(binary, args, install_hint, timeout=60):
        calls.append((binary, args))
        return "https://github.com/owner/repo/pull/99\n" if binary == "gh" and args[0] == "pr" else ""

    monkeypatch.setattr(ticketing, "_run_cli", fake_cli)
    assert ticket.pr("github:owner/repo#12", None) == 0
    pr_calls = [c for c in calls if c[0] == "gh" and c[1][0] == "pr"]
    assert len(pr_calls) == 1
    args = pr_calls[0][1]
    assert args[:4] == ["pr", "create", "--draft", "--title"]
    assert "Closes #12" in args[args.index("--body") + 1]
    comment = [c for c in calls if c[0] == "gh" and c[1][0] == "issue"]
    assert len(comment) == 1
    assert "PR: https://github.com/owner/repo/pull/99" in comment[0][1][comment[0][1].index("--body") + 1]
    assert "pr" in capsys.readouterr().out.lower()


def test_pr_no_comment_skips_issue_comment(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG_GITHUB)
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, status)"
                 " VALUES ('github:owner/repo#12', 'github', 'owner/repo#12', 'X', 'backlog')")
    conn.commit()
    conn.close()
    calls = []

    def fake_cli(binary, args, install_hint, timeout=60):
        calls.append((binary, args))
        return "https://github.com/owner/repo/pull/99\n"

    monkeypatch.setattr(ticketing, "_run_cli", fake_cli)
    assert ticket.pr("github:owner/repo#12", None, no_comment=True) == 0
    assert all(not (c[0] == "gh" and c[1][0] == "issue") for c in calls)


def test_pr_internal_uses_origin_forge(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, status)"
                 " VALUES ('internal:abc', 'internal', '', 'Ship it', 'backlog')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("github.com", "owner/repo"))
    calls = []

    def fake_cli(binary, args, install_hint, timeout=60):
        calls.append((binary, args))
        return "https://github.com/owner/repo/pull/7\n"

    monkeypatch.setattr(ticketing, "_run_cli", fake_cli)
    assert ticket.pr("internal:abc", None) == 0
    gh_pr = [c for c in calls if c[0] == "gh" and c[1][0] == "pr"]
    assert len(gh_pr) == 1
    body = gh_pr[0][1][gh_pr[0][1].index("--body") + 1]
    assert "Closes" not in body          # internal ticket: no issue link
    assert not any(c[0] == "gh" and c[1][0] == "issue" for c in calls)


def test_pr_internal_selfhosted_origin_needs_flag(tmp_path, monkeypatch, capsys):
    # A self-hosted origin (e.g. git.ifoodcorp.com.br) is NOT a cloud gitlab
    # host — without self_hosted/custom_url there is no matching forge, and pr
    # must fail with a config hint instead of guessing.
    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, status)"
                 " VALUES ('internal:abc', 'internal', '', 'Ship it', 'backlog')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("git.ifoodcorp.com.br", "group/proj"))
    assert ticket.pr("internal:abc", None) == 1
    assert "self_hosted" in capsys.readouterr().err


def test_pr_mismatched_origin_fails_with_hint(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, "providers:\n  - github\n")
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, status)"
                 " VALUES ('github:owner/repo#12', 'github', 'owner/repo#12', 'X', 'backlog')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("gitlab.com", "group/proj"))
    assert ticket.pr("github:owner/repo#12", None) == 1
    err = capsys.readouterr().err
    assert "gitlab.com" in err and "repo:" in err


def test_pr_auto_reserved_warns_on_sync(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch,
                    "providers:\n  - github\ngithub:\n  repo: owner/repo\n  pr:\n    auto: true\n")
    monkeypatch.setattr(ticketing, "detect_origin", lambda root: ("github.com", "owner/repo"))
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")

    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = json.dumps([])
            stderr = ""
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    assert ticket.sync(None) == 0
    assert "not implemented" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticket_cli.py -v`
Expected: FAIL (`ticket.sync()` takes 2 positional args; `ticket.pr` missing)

- [ ] **Step 3: Add `warn_reserved_pr` + `pr_target` to `ticketing.py`**

Append:

```python
def warn_reserved_pr(cfg: TicketingConfig) -> None:
    """pr.auto is reserved — warn when someone enables it."""
    for provider in ("github", "gitlab"):
        block = getattr(cfg, provider) or {}
        if (block.get("pr") or {}).get("auto"):
            print(f"sssf ticket: {provider}.pr.auto is reserved — auto PR/MR on run success is not implemented yet",
                  file=sys.stderr)


def matching_provider(root: Path, cfg: TicketingConfig) -> str | None:
    """The hosted-git provider whose expected hosts match the origin (github first)."""
    origin = detect_origin(root)
    if origin is None:
        return None
    host = origin[0]
    for provider in ("github", "gitlab"):
        if host_matches(cfg, provider, host):
            return provider
    return None


def pr_target(root: Path, cfg: TicketingConfig, provider: str) -> tuple[str | None, str | None, str | None]:
    """(forge, repo, None) when a PR/MR can be created; (None, None, reason) otherwise.

    github/gitlab tickets target their own forge (a mismatched origin host
    fails via repo_for); internal/jira/linear tickets target the provider
    whose expected hosts match the origin.
    """
    forge = provider if provider in ("github", "gitlab") else None
    if forge is None:
        origin = detect_origin(root)
        if origin is None:
            return None, None, ("no git remote origin — create the PR/MR from a repo "
                                "with a github/gitlab origin (or add a `repo:` override)")
        forge = matching_provider(root, cfg)
        if forge is None:
            return None, None, (
                f"origin {origin[0]} matches no configured github/gitlab host — "
                "set `self_hosted: true` and `custom_url:` for the right provider "
                "(or add a `repo:` override)")
    repo, reason = repo_for(root, cfg, forge)
    if repo is None:
        return None, None, reason
    return forge, repo, None
```

- [ ] **Step 4: Update `sync` in `commands/ticket.py`**

In `src/sssf/commands/ticket.py`, replace the `sync` function:

```python
def sync(project: str | None = None, provider: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/config/ticketing.yaml", file=sys.stderr)
        return 1
    ticketing.warn_reserved_pr(cfg)
    results = ticketing.sync_tickets(root, cfg, only=provider)
    for r in results:
        if r.error:
            print(f"sssf ticket: {r.provider}: {r.error}")
        else:
            print(f"sssf ticket: {r.provider}: {r.tickets} ticket(s) synced")
    return 0
```

- [ ] **Step 5: Add the `pr` command to `commands/ticket.py`**

Append (after `backlog`):

```python
def pr(ticket_id: str, project: str | None = None, no_comment: bool = False) -> int:
    """Create a draft PR/MR from the current branch, linked to the ticket.

    External github/gitlab tickets target their own forge; internal (and
    jira/linear) tickets target the origin's forge. The branch must already
    have commits ahead of its base — sssf never pushes or creates branches.
    """
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/config/ticketing.yaml", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute(
        "SELECT id, title, description, provider, external_id, source_url"
        " FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    conn.close()
    if row is None:
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    tid, title, description, provider, external_id, source_url = row
    forge, repo, reason = ticketing.pr_target(root, cfg, provider)
    if repo is None:
        print(f"sssf ticket pr: {reason}", file=sys.stderr)
        return 1
    number = external_id.rsplit("#", 1)[-1] if "#" in external_id else ""
    body = description or ""
    if forge == "github":
        if provider == "github" and number:
            body += f"\n\nCloses #{number}"
        url = ticketing._run_cli(
            "gh", ["pr", "create", "--draft", "--title", title, "--body", body, "--repo", repo],
            "install gh — https://cli.github.com — and run `gh auth login`").strip()
        if not no_comment and provider == "github" and number:
            ticketing._run_cli(
                "gh", ["issue", "comment", number, "--repo", repo, "--body", f"PR: {url}"],
                "install gh — https://cli.github.com — and run `gh auth login`")
    else:
        if provider == "gitlab" and source_url:
            body += f"\n\nCloses {source_url}"
        url = ticketing._run_cli(
            "glab", ["mr", "create", "--draft", "--title", title, "--description", body, "--repo", repo],
            "install glab — https://gitlab.com/gitlab-org/cli — and run `glab auth login`").strip()
        if not no_comment and provider == "gitlab" and number:
            ticketing._run_cli(
                "glab", ["issue", "note", number, "-m", f"MR: {url}", "--repo", repo],
                "install glab — https://gitlab.com/gitlab-org/cli — and run `glab auth login`")
    print(f"sssf ticket pr: {provider} ticket {tid} -> {url}")
    return 0
```

Also in `commands/ticket.py`, inside `run()` (the ticket-spawning command), right after its `if cfg is None:` block, add:

```python
    ticketing.warn_reserved_pr(cfg)
```

- [ ] **Step 6: Wire the CLI in `src/sssf/cli.py`**

Update `_dispatch_ticket`:

```python
def _dispatch_ticket(a) -> int:
    action = a.ticket_action
    if action == "add":
        return ticket.add(a.title, a.project)
    if action == "sync":
        return ticket.sync(a.project, a.provider)
    if action == "list":
        return ticket.list_tickets(a.project)
    if action == "run":
        return ticket.run(a.ticket_id, a.project, a.no_sandbox)
    if action == "pr":
        return ticket.pr(a.ticket_id, a.project, a.no_comment)
    if action == "backlog":
        return ticket.backlog(a.ticket_id, a.project)
    return 1
```

Update the ticket subparser block (replace the `p_ticket` … `p_backlog` block):

```python
    p_ticket = sub.add_parser("ticket", help="ticketing integration (add / sync / list / run / pr / backlog)")
    tsub = p_ticket.add_subparsers(dest="ticket_action", required=True)
    p_add = tsub.add_parser("add", help="create an internal ticket")
    p_add.add_argument("title")
    p_add.add_argument("--project", default=None)
    p_sync = tsub.add_parser("sync", help="fetch external tickets into the backlog")
    p_sync.add_argument("--project", default=None)
    p_sync.add_argument("--provider", default=None, choices=["jira", "linear", "github", "gitlab"],
                        help="sync only this provider")
    p_list = tsub.add_parser("list", help="list tickets")
    p_list.add_argument("--project", default=None)
    p_run = tsub.add_parser("run", help="spawn simple_sdlc for a ticket")
    p_run.add_argument("ticket_id")
    p_run.add_argument("--project", default=None)
    p_run.add_argument("--no-sandbox", action="store_true",
                       help="run in the current dir instead of a sandbox container")
    p_pr = tsub.add_parser("pr", help="create a draft PR/MR from the current branch, linked to the ticket")
    p_pr.add_argument("ticket_id")
    p_pr.add_argument("--project", default=None)
    p_pr.add_argument("--no-comment", action="store_true",
                      help="skip commenting the PR/MR link back on the issue")
    p_backlog = tsub.add_parser("backlog", help="return a ticket to the backlog (keeps run history)")
    p_backlog.add_argument("ticket_id")
    p_backlog.add_argument("--project", default=None)
    p_ticket.set_defaults(func=lambda a: _dispatch_ticket(a))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticket_cli.py -v`
Expected: all PASS (existing + 6 new)

- [ ] **Step 8: Manual smoke — sync `--provider` and pr errors without touching a real forge**

```bash
cd /tmp && rm -rf tkt-pr-smoke && mkdir tkt-pr-smoke && cd tkt-pr-smoke && git init -q
printf 'providers:\n  - internal\n  - github\ngithub:\n  labels: []\n' > adws/config/ticketing.yaml
sssf ticket sync --provider github
# Expected: "github configured but the repo has no git remote origin — add a `repo:` override…"
sssf ticket add "smoke"
sssf ticket pr internal:$(sssf ticket list | grep smoke | awk '{print $1}' | cut -d: -f2)
# Expected: pr fails with the no-origin hint (no forge to target) — exit 1, friendly message
```

- [ ] **Step 9: Commit**

```bash
git add src/sssf/ticketing.py src/sssf/commands/ticket.py src/sssf/cli.py tests/test_ticket_cli.py
git commit -m "feat: sssf ticket pr (draft PR/MR) + sync --provider + reserved pr.auto warning"
```

---

### Task 5: Viz server — `providers` list + `?provider=` passthrough

**Files:**
- Modify: `src/sssf/apps/visualizer/server/tickets.ts`
- Modify: `src/sssf/apps/visualizer/server/index.ts`
- Modify: `src/sssf/apps/visualizer/server/tickets.test.ts`

**Interfaces:**
- Consumes: the existing `isEnabled`/`readTickets`.
- Produces:
  - `readProviders(root: string) -> string[]` — enabled EXTERNAL providers from `adws/config/ticketing.yaml` (internal excluded; regex parse mirroring `isEnabled`, no yaml lib in bun).
  - `syncSpawnArgs(root: string, provider: string | null) -> string[]` — `["sssf", "ticket", "sync", "--project", root, ...]` plus `["--provider", provider]` when set.
  - `GET /api/projects/:project/tickets` → `{enabled, providers, tickets}`; the sync route reads `?provider=` and spawns via `syncSpawnArgs`.

- [ ] **Step 1: Write the failing tests**

In `src/sssf/apps/visualizer/server/tickets.test.ts`, extend the imports and append:

```ts
import { mkdirSync, mkdtempSync, writeFileSync } from "fs";
import { readProviders, readTickets, syncSpawnArgs } from "./tickets";

describe("readProviders", () => {
  test("enabled external providers, internal excluded", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-providers-"));
    const root = join(dir, "proj");
    mkdirSync(join(root, "adws", "config"), { recursive: true });
    writeFileSync(join(root, "adws", "config", "ticketing.yaml"),
      "providers:\n  - internal\n  - github\n  - gitlab\n\ngithub:\n  labels: [bug]\n");
    expect(readProviders(root)).toEqual(["github", "gitlab"]);
  });

  test("commented list parses as none", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-providers-"));
    const root = join(dir, "proj");
    mkdirSync(join(root, "adws", "config"), { recursive: true });
    writeFileSync(join(root, "adws", "config", "ticketing.yaml"),
      "# providers:\n#   - github\n");
    expect(readProviders(root)).toEqual([]);
  });

  test("missing file returns empty", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-providers-"));
    expect(readProviders(join(dir, "nope"))).toEqual([]);
  });
});

describe("syncSpawnArgs", () => {
  test("no provider -> plain sync", () => {
    expect(syncSpawnArgs("/x", null)).toEqual(["sssf", "ticket", "sync", "--project", "/x"]);
  });

  test("provider -> --provider flag", () => {
    expect(syncSpawnArgs("/x", "github")).toEqual(
      ["sssf", "ticket", "sync", "--project", "/x", "--provider", "github"]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/sssf/apps/visualizer && bun test server/tickets.test.ts`
Expected: FAIL (`readProviders`/`syncSpawnArgs` are not exported)

- [ ] **Step 3: Implement in `server/tickets.ts`**

Append:

```ts
/** Enabled external providers from ticketing.yaml (internal excluded — nothing to fetch). */
export function readProviders(root: string): string[] {
  const path = resolve(root, "adws", "config", "ticketing.yaml");
  if (!existsSync(path)) return [];
  try {
    const text = readFileSync(path, "utf8");
    const head = /^\s*providers\s*:/m.exec(text);
    if (!head) return [];
    const providers: string[] = [];
    for (const line of text.slice(head.index).split("\n").slice(1)) {
      const trimmed = line.trimStart();
      if (trimmed.startsWith("#")) continue;
      if (!/^-\s+/.test(trimmed)) break;        // end of the list: next top-level key
      const item = /^-\s+([\w-]+)/.exec(trimmed);
      if (item) providers.push(item[1]!);
    }
    return providers.filter((p) => p !== "internal");
  } catch {
    return [];
  }
}

/** argv for `sssf ticket sync`, with an optional single-provider filter. */
export function syncSpawnArgs(root: string, provider: string | null): string[] {
  const args = ["sssf", "ticket", "sync", "--project", root];
  if (provider) args.push("--provider", provider);
  return args;
}
```

- [ ] **Step 4: Wire the routes in `server/index.ts`**

Change the import line:

```ts
import { isEnabled, readProviders, readTickets, syncSpawnArgs } from "./tickets.ts";
```

Change the GET tickets route response:

```ts
      return json({ enabled: isEnabled(root), providers: readProviders(root), tickets: readTickets(db.path) });
```

Change the sync route to read `?provider=` and spawn via `syncSpawnArgs`:

```ts
    "/api/projects/:project/tickets/sync": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root || !isEnabled(root)) return json({ error: "ticketing not configured" }, 400);
      const provider = new URL(req.url).searchParams.get("provider");
      const proc = Bun.spawn(syncSpawnArgs(root, provider), { stdout: "pipe", stderr: "pipe" });
      const output = await new Response(proc.stdout).text();
      await proc.exited;
      return json({ ok: proc.exitCode === 0, output });
    }),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/sssf/apps/visualizer && bun test server/tickets.test.ts`
Expected: 10 PASS (5 existing + 5 new)

- [ ] **Step 6: Commit**

```bash
git add -A src/sssf/apps/visualizer/server
git commit -m "feat: viz tickets api — providers list + per-provider sync passthrough"
```

---

### Task 6: Kanban — refresh picker + provider badges

**Files:**
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`
- Modify: `src/sssf/apps/visualizer/src/components/KanbanBoard.vue`
- Modify: `src/sssf/apps/visualizer/src/components/TicketCard.vue`
- Modify: `src/sssf/apps/visualizer/src/components/TicketModal.vue`

**Interfaces:**
- Consumes: `fetchTickets()` response now carries `providers: string[]` (Task 5).
- Produces: `TicketsResponse.providers: string[]`; `syncTickets(provider?: string)` sends `?provider=`; the Backlog refresh button opens a menu (All + each external provider) when `providers.length > 1`, else syncs directly; `TicketCard`/`TicketModal` show `GH`/`GL` badges.

- [ ] **Step 1: Update `api.ts`**

```ts
export interface TicketsResponse {
  enabled: boolean
  tickets: Ticket[]
  providers: string[]
}

export async function syncTickets(provider?: string): Promise<{ ok: boolean; output?: string }> {
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : ''
  const res = await fetch(`${base()}/tickets/sync${qs}`, { method: 'POST' })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; output?: string }
  return { ok: data.ok ?? res.ok, output: data.output }
}
```

- [ ] **Step 2: Update `KanbanBoard.vue` — script**

Replace the ticketing state block:

```ts
const tickets = ref<TicketsResponse>({ enabled: false, tickets: [], providers: [] })
const activeTicket = ref<Ticket | null>(null)
const syncing = ref(false)
const pickerOpen = ref(false)

const PROVIDER_LABEL: Record<string, string> = {
  github: 'GitHub', gitlab: 'GitLab', jira: 'Jira', linear: 'Linear',
}
```

Replace `onSync`:

```ts
async function onSync(provider?: string) {
  // One external provider (or none) syncs directly; several show a picker so
  // the operator chooses which system to fetch from.
  if (!provider && (tickets.value.providers ?? []).length > 1) {
    pickerOpen.value = true
    return
  }
  pickerOpen.value = false
  syncing.value = true
  try {
    await syncTickets(provider)
  } finally {
    syncing.value = false
  }
  void pullTickets()
}
```

- [ ] **Step 3: Update `KanbanBoard.vue` — template + styles**

Replace the Backlog refresh button block in the column header:

```vue
          <button
            v-if="col.key === 'backlog'"
            class="sync-link"
            type="button"
            :disabled="syncing"
            :title="(tickets.providers ?? []).length > 1 ? 'Choose which system to fetch tickets from' : 'Fetch external tickets'"
            @click="onSync()"
          >
            <RefreshCw :size="13" /> {{ syncing ? 'syncing…' : 'refresh' }}
          </button>
          <div v-if="pickerOpen" class="sync-menu">
            <button type="button" @click="onSync()">All providers</button>
            <button v-for="p in tickets.providers" :key="p" type="button" @click="onSync(p)">
              {{ PROVIDER_LABEL[p] ?? p }}
            </button>
          </div>
```

In the `<style>` block, make `.col-head` position-relative and add the menu styles:

```css
.col-head {
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;   /* anchors the sync provider picker */
}

.sync-menu {
  position: absolute;
  z-index: 40;
  top: 100%;
  right: 0;
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: rgba(11, 15, 24, 0.98);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}
.sync-menu button {
  text-align: left;
  padding: 6px 10px;
  border-radius: 6px;
  border: none;
  background: none;
  color: var(--dim);
  font-size: 12px;
  cursor: pointer;
}
.sync-menu button:hover {
  color: var(--text);
  background: rgba(200, 155, 255, 0.12);
}
```

- [ ] **Step 4: Update the badges in `TicketCard.vue` and `TicketModal.vue`**

In both files, replace the `BADGE` map:

```ts
const BADGE: Record<string, string> = { jira: 'J', linear: 'L', github: 'GH', gitlab: 'GL', internal: '⚙' }
```

- [ ] **Step 5: Verify the build + server tests**

Run: `cd src/sssf/apps/visualizer && bun test` (server suite)
Expected: all PASS

Run: `cd src/sssf/apps/visualizer && bun run build 2>/dev/null || npx vue-tsc --noEmit -p tsconfig.json 2>/dev/null || echo "build not configured — skip"`
Expected: no type errors (or the repo's normal build command passes)

- [ ] **Step 6: Commit**

```bash
git add src/sssf/apps/visualizer/src
git commit -m "feat: kanban refresh picker for multiple ticketing providers + gh/glab badges"
```

---

### Task 7: Full verification + runner image + field checks

**Files:** none (verification only)

- [ ] **Step 1: Full Python suite**

Run: `uv run pytest`
Expected: all PASS (engine + CLI + init)

- [ ] **Step 2: Full visualizer suite**

Run: `cd src/sssf/apps/visualizer && bun test`
Expected: all PASS

- [ ] **Step 3: Rebuild the sandbox runner image**

The runner image bakes the engine at build time (`pip install /opt/sssf`), so the `ticketing.py` changes must be baked before any sandboxed run:

```bash
cd /Users/felipe.matos/dev/lab/mvp/sssf && docker build -t sssf-runner -f docker/sssf-runner.Dockerfile .
```

- [ ] **Step 4: Field checks (host has gh 2.97.0 + glab 1.113.0 already)**

1. **Cloud mismatch warns** — in a repo whose origin is a github host, configure `gitlab:` (cloud default) and run `sssf ticket sync`; expect the config-issue warning ("gitlab configured but origin is … — the cloud host is gitlab.com; is this a self-hosted instance? set `self_hosted: true` and `custom_url:`…") and the github result to follow.
2. **Self-hosted opt-in** — a repo with a self-hosted origin (e.g. `git.ifoodcorp.com.br`): `gitlab:` without `self_hosted` warns; with `self_hosted: true` + `custom_url: https://git.ifoodcorp.com.br` it syncs; a `custom_url` that doesn't match the origin warns.
3. **Provider picker** — a project with two external providers configured: open the visualizer, click refresh on the Backlog column; expect the All/GitHub/GitLab menu; pick one and watch it sync only that provider (`GET /tickets` should carry `providers`).
4. **`ticket pr`** — after a `--no-sandbox` run on a branch with local commits, run `sssf ticket pr <id>`; expect a draft PR/MR linking the issue and a comment on the issue with the link.
5. **`repo:` override** — a repo with no origin (or a non-gh/glab host): set `repo:` in the yaml block and confirm sync + `pr` work.

- [ ] **Step 5: Finish the branch**

```bash
git status   # clean
git push -u origin feat/github-gitlab-ticketing
```

Then open the PR via the gitlab skill conventions (draft MR, `--squash-before-merge=false`, remove-source-branch) or the repo's normal PR flow.
