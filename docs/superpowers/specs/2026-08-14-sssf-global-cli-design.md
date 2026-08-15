# SSSF as a Global CLI — Design

**Date:** 2026-08-14
**Status:** Approved in brainstorming (sections 1–3 + skill-loading revision)
**Product:** `sssf` — Super Simple Software Factory, repackaged as a globally installed CLI tool.

## 1. Problem

The current SSSF ships as a Claude Code skill that is *stamped into every repo*:
`.claude/skills/sssf/` is copied into the target, `install.py` drops 20+ tracked files
(`adws/`, `adw_modules/`, `prompt_engineering/`, `harness_engineering/`, `justfile`)
into the repo, and upgrading means re-running `install.py --force`, which overwrites
user config and prompts. Machinery in the repo drifts between copies; there is no
versioning story.

The engine (typed envelopes, gates, session-preserving retries, SQLite trace,
write-enforcement) is the part worth keeping. The distribution model is the part to
replace.

**Decisions from brainstorming:**
- Keep SSSF's engine; replace distribution with a global CLI (Approach 1).
- Generic tool — none of The Office's org-specific surface (no Jira, GitLab MR
  lifecycle, knowledge agent, worktree isolation, human-in-the-loop gates).
- Keep the visualizer (Vue + Vite + bun), but bun becomes a **global prerequisite**,
  never a per-repo requirement.
- The pi skill loads **on demand per invocation**; it is never installed globally.

## 2. Package & install

- Package name `sssf`, CLI binary `sssf`. Keeps the existing `/sssf` skill naming.
- Standard Python package: `pyproject.toml`, `[project.scripts] sssf = "sssf.cli:main"`.
- Dependencies declared once in the package: `pydantic`, `python-dotenv`, `pyyaml`,
  `rich`. The per-ADW inline `uv run` dependency headers are removed.
- Install: `uv tool install sssf` (or `pipx install .`). This creates an isolated
  tool venv (`~/.local/share/uv/tools/sssf/`) and a shim in `~/.local/bin/sssf`.
  No registry or registration anywhere — name resolution is pip-style; for v1 the
  practical install is `uv tool install .` or `--editable .` from the repo.
- Upgrade: `sssf upgrade` = `uv tool upgrade sssf`. The skill payload ships inside
  the package, so upgrading the tool updates it automatically (§6).
- Runtime prerequisites (global, checked by `sssf doctor`): `uv`, `pi`, `bun`,
  `sqlite3`, and `~/.local/bin` on PATH.

### Package layout

```
sssf/                          # installed package (versioned with the tool)
├── cli.py                     # the `sssf` entrypoint
├── adw_modules/               # THE ENGINE — ported from SSSF verbatim
│                              #   agents, session, tracer, gates, data_types,
│                              #   permissions, quality, git_helper, agent_pi,
│                              #   changes, prompts, console, utils, runner
├── templates/                 # exactly what `sssf init` stamps (§3)
├── prompt_engineering/        # starter prompts, copied out on init
├── docs/                      # user documentation — README, customizing guide,
│                              #   contributing note. Replaces SSSF's cookbooks (§6)
├── SKILL.md                   # the pi operating manual — runtime payload,
│                              #   referenced by --skill path, never installed
└── apps/visualizer/           # the Vue + Vite + bun trace UI, ported as-is
```

The engine is **never copied into a repo**. Nothing to drift, nothing `--force`
can clobber.

## 3. Per-project footprint (`sssf init`)

`sssf init` stamps only the customization surface:

```
adws/
├── adw_*.py                      # 12 starter chains, USER-OWNED and editable
│                                 #   imports become `from sssf.adw_modules import ...`
├── adw_sssf_config/sssf.config.yaml    # roster, unchanged shape
└── adw_data/
    ├── prompt_engineering/{agent}/     # starter prompts, user-owned
    ├── harness_engineering/            # pi extensions, user-owned
    ├── sessions/                       # runtime — gitignored
    └── sssf.db                         # runtime — gitignored
.env.sample                    # keys (OPENROUTER etc.)
AGENTS.md                      # appended pointer: "this repo runs sssf"
```

- Paths stay exactly as SSSF has them today (`adws/adw_sssf_config/`,
  `adws/adw_data/...`). Porting is a one-line import change per ADW, not a
  restructure. Path renames, if ever wanted, come later with a migration.
- No `justfile` — recipes become first-class `sssf` commands (§5).
- `sssf init` stamps the §3 footprint **and registers the project** in the global
  registry `~/.sssf/projects.json` (name, root path, db path) so the visualizer
  service (§7) can find it.

## 4. The run mechanism

```bash
sssf run adw_build_test "implement the plan" [--adw-id a1b2c3d4]
sssf run adw_prompt "summarize this repo" --agent scout
```

- The CLI resolves the project (cwd containing `adws/`, or `--project <dir>`), then
  executes the ADW **with the tool venv's python** — engine on `sys.path`, all deps
  present. ADWs are only runnable through `sssf run` (isolated-venv trade-off).
- ADW scripts keep their exact current shape (`PhaseParams`, `AgentCall`, gates,
  `run.finish`) — composition and envelope contracts port untouched. Only the
  import line changes (`adw_modules` → `sssf.adw_modules`) and the inline dep
  headers go away.
- `--config` defaults to `adws/adw_sssf_config/sssf.config.yaml`; `--adw-id`
  create-or-continue semantics unchanged.
- The `adw_` prefix is optional: `sssf run build_test` ≡ `sssf run adw_build_test`.

## 5. CLI surface

```bash
sssf init                     # stamp the §3 footprint into this project
sssf run <adw> "<prompt>"     # execute a chain (adw_ prefix optional)
sssf doctor                   # check uv, pi, bun, sqlite3, PATH, project state
sssf upgrade                  # uv tool upgrade sssf — skill payload updates with it

# observability — read-only over the WAL db, never blocks writers
sssf sessions                 # adw_id, status, request, total_tokens (cwd project)
sssf phases <adw-id>          # phase sequence for one run
sssf tail <adw-id>            # live-follow events mid-run
sssf procs <adw-id>           # alive processes, for stuck runs
sssf projects                 # list / remove registered projects (registry)
sssf viz [--port 4600]        # global visualizer service over all projects (§7)
```

## 6. The skill: loaded on demand, never installed globally

- No global install to `~/.pi/agent/skills/`. No `install-skill` command.
- `SKILL.md` ships inside the package as a runtime payload. When `sssf run`
  spawns pi for a coding agent, `agent_pi.py` passes it by explicit path
  (`--skill <package>/SKILL.md`) for **that run only**. A clean pi session
  anywhere else never sees sssf.
- `sssf init` appends a per-project AGENTS.md pointer (standard per-repo
  mechanism) — only active when cwd is inside the project.
- **The cookbooks are dropped as a concept.** They were lazily-loaded recipes for
  an orchestrator agent that no longer exists — the CLI is the orchestrator.
  Their surviving content becomes package docs (`docs/`): the system map and
  prompt-writing guidance fold into the README; create/update ADW + config
  guidance compress into one "Customizing" page; `update_modules` inverts into a
  contributing note — the engine lives in the package now, so extending it is a
  change to the tool repo, not a local edit.

## 7. Visualizer: a global sssf service

- The Vue + Vite + bun app (`apps/visualizer/`) ports **as-is** into the package.
- `sssf viz [--port 4600]` starts **one global service** that shows traces from
  **all registered projects**, not a single project. The UI gains a project level
  above today's sessions → waterfall → tool calls.
- **Project registry:** `~/.sssf/projects.json` — runtime state, not config.
  `sssf init` registers the project; `sssf run` and `sssf sessions` refresh
  last-run metadata; `sssf projects` lists/removes entries. Each project db is
  opened read-only (WAL), so the service never blocks a running workflow — the
  cursor query per db is unchanged.
- `--db` / `SSSF_DB` still override for one ad-hoc db (debugging); the primary
  mode is the registry.
- bun is a global prerequisite (installed once, checked by `sssf doctor`) — never
  a per-repo requirement. The original "I don't like bun" objection was the
  per-repo `bun install` ceremony, not the toolchain itself.

## 8. Upgrade & drift

- `sssf upgrade` = `uv tool upgrade sssf` — the skill payload updates with the
  package, no separate refresh step.
- `sssf init --refresh` re-copies **only missing** template files — never overwrites
  user config, prompts, or ADWs (same skip semantics as today's `install.py`,
  minus the `--force` footgun). Registry entries are updated, never duplicated.

## 9. Deliberately cut (YAGNI)

- No Jira, no GitLab MR lifecycle, no knowledge agent, no worktree isolation, no
  human-in-the-loop gates (brainstorming decision F).
- No `claude_code` backend — pi only; the stub stays.
- No sandbox or branch-per-run — SSSF's documented gap stays a documented gap.
- No global *config* file in v1 — keys come from pi's `models.json`, roster stays
  per-project. (`~/.sssf/projects.json` is runtime registry state, not
  configuration.)

## 10. Porting inventory

**Ported from SSSF (into the package):** `adw_modules/` engine modules; the 12
starter ADW scripts (import line + dep-header changes only); `sssf.config.yaml`
template; `prompt_engineering/` starter prompts; `harness_engineering/` pi
extensions; `apps/visualizer/`; SKILL.md as the `--skill` payload; the cookbooks'
surviving content as package docs.

**Replaced:** skill-stamping install → `uv tool install` + `sssf init`; `justfile`
recipes → `sssf` subcommands; per-repo skill copy → `--skill` path payload +
AGENTS.md pointer.

**Removed:** `install.py` (replaced by `sssf init`); `make_config.py`,
`make_adw.py` (become docs/CLI guidance or later `sssf` subcommands — v1 keeps
manual editing); per-ADW inline `uv run` dep headers; `.claude/skills/` layout.

## 11. Open questions for implementation

- Where the new repo lives (the design doc is committed in the SSSF repo for now;
  the new project repo will be seeded from it).
- Whether `sssf run` needs a `--project <dir>` flag in v1 or cwd resolution alone.
- PyPI publication (later; v1 installs from git/path).
