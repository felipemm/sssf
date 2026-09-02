# `sssf spec create` — Interview-Driven Ticket Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sssf spec create` spawns an interactive pi session with a **product manager** persona and **pre-loaded** grilling/brainstorming skills (installed project-locally by `sssf init`, verified by `sssf doctor`) to interview the user and produce a runnable ticket: a spec in `adws/prompts/NN-<slug>.md` + an internal ticket, runnable from the visualizer.

**Architecture:** Four pieces — (1) ticketing gains rich tickets (`ticket add --description/--prompt-file`, `ticket run` honors an existing prompt file); (2) a skills installer fetches the four skills into `<project>/.pi/skills/` with a version marker (project-local, never global); (3) `sssf doctor` verifies presence + freshness; (4) `sssf spec create` renders a mode-specific product-manager context and spawns interactive pi with `--append-system-prompt`.

**Tech Stack:** Python (sssf CLI), git (skill fetch), pi (interactive session), YAML.

**Spec:** `docs/superpowers/specs/2026-08-21-sssf-spec-create-design.md`

## Global Constraints

- Work on `feat/spec-create` (worktree `.worktrees/spec-create`); commit per task; full suite + visualizer green at the end.
- **Skills are project-local only** (`.pi/skills/`) — never write to `~/.pi/agent/skills/`.
- The four skills + source paths (pinned):
  - `brainstorming` ← `github.com/obra/superpowers`, path `skills/brainstorming`
  - `grilling` ← `github.com/mattpocock/skills`, path `skills/productivity/grilling`
  - `grill-me` ← `github.com/mattpocock/skills`, path `skills/productivity/grill-me`
  - `grill-with-docs` ← `github.com/mattpocock/skills`, path `skills/engineering/grill-with-docs`
- ruff + mypy clean per task (CI gates).

---

### Task 1: Ticketing — rich tickets + run honors the prompt file

**Files:**
- Modify: `src/sssf/commands/ticket.py`, `src/sssf/ticketing.py` (if needed)
- Test: `tests/test_ticket_cli.py`

- [ ] **Step 1: Failing tests**

```python
def test_add_with_description_and_prompt_file(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch, TICKETING_YAML_INTERNAL)
    prompt = root / "adws" / "prompts" / "01-x.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("spec")
    assert ticket.add("New thing", None, description="the spec", prompt_file=str(prompt)) == 0
    conn = _db(root)
    row = conn.execute("SELECT description, prompt_file FROM tickets").fetchone()
    assert row == ("the spec", "adws/prompts/01-x.md")


def test_run_honors_existing_prompt_file(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, TICKETING_YAML_INTERNAL)
    prompt = root / "adws" / "prompts" / "01-x.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("THE SPEC PROMPT")
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, title, description, status, prompt_file)"
                 " VALUES ('internal:x','internal','X','', 'backlog', 'adws/prompts/01-x.md')")
    conn.commit()
    conn.close()
    class P:
        pid = 12345
    monkeypatch.setattr(ticket.subprocess, "Popen", lambda argv, **kw: P())
    assert ticket.run("internal:x", None) == 0
    # the run used the existing prompt file — no new NN file was generated
    assert list((root / "adws" / "prompts").glob("*.md")) == [prompt]
```

- [ ] **Step 2:** Run — Expected: FAIL (`add` has no description/prompt_file params; run regenerates)

- [ ] **Step 3:** `ticket.add(title, project=None, *, description="", prompt_file=None)`:
  - INSERT uses `description`; when `prompt_file` given, store its repo-relative path (`Path(prompt_file).relative_to(root)`).

- [ ] **Step 4:** `ticket.run` — add `prompt_file` to the SELECT; before the regenerate branches (sandbox + non-sandbox), check:
  - if `prompt_file` and the file exists (root-relative; in the worktree for sandboxed) → use it (`rel_prompt = prompt_file`; skip the `next_prompt_name` write);
  - else → current behavior unchanged.

- [ ] **Step 5:** Run: `uv run pytest tests/test_ticket_cli.py -q` — PASS; `uv run pytest -q` green
- [ ] **Step 6: Commit**
```bash
git add src/sssf/commands/ticket.py tests/test_ticket_cli.py
git commit -m "feat(tickets): rich add (description + prompt-file) and run honors an existing prompt file"
```

---

### Task 2: Skills installer — fetch into `.pi/skills/` + version marker

**Files:**
- Create: `src/sssf/adw_modules/skills_install.py`
- Test: `tests/test_skills_install.py`

**Interfaces:**
- `install_skills(root: Path, *, refresh: bool = False) -> int` — fetches the four skills into `<root>/.pi/skills/`, writes `<root>/.pi/skills/.sssf-versions.json` (skill → source commit), returns 0/1.
- `check_skills(root: Path) -> dict` — `{skill: {present, pinned, latest, stale}}` for doctor (offline → `latest: None`).

- [ ] **Step 1: Failing tests**

```python
def test_install_writes_skills_and_marker(tmp_path, monkeypatch):
    # git is faked: a fixture "remote" repo per source providing the skill dirs
    ...

def test_install_never_writes_global(tmp_path, monkeypatch):
    # after install, ~/.pi/agent/skills has no new entries (assert via a fake HOME)
    ...

def test_check_reports_stale(tmp_path, monkeypatch):
    # marker pinned commit != remote HEAD -> stale True
    ...
```

- [ ] **Step 2:** Run — FAIL (module missing)

- [ ] **Step 3:** Implement `skills_install.py`:
  - `_SOURCES`: the four (skill, repo, path) entries from Global Constraints.
  - Fetch: for each unique repo, `git clone --depth 1 <repo> <tmp>/<repo-name>` (network; on failure print + return 1). Copy each skill dir → `<root>/.pi/skills/<skill>/`. Record the clone's `HEAD` commit.
  - Marker: write `.sssf-versions.json` `{skill: {"source": repo, "path": path, "commit": <head>}}`.
  - `refresh=True` re-clones (fresh depth-1) and overwrites.
  - Never touches `~/.pi/agent/skills/`.

- [ ] **Step 4:** `check_skills(root)` — read the marker; for each skill, check the folder exists; compare pinned vs `git ls-remote <repo> HEAD` (offline → None); return the dict.

- [ ] **Step 5:** Run: `uv run pytest tests/test_skills_install.py -q` — PASS (fixture repos, mocked git)
- [ ] **Step 6: Commit**
```bash
git add src/sssf/adw_modules/skills_install.py tests/test_skills_install.py
git commit -m "feat(skills): project-local skill installer + version marker (never global)"
```

---

### Task 3: `sssf init` wiring + `sssf doctor`

**Files:**
- Modify: `src/sssf/commands/init.py`, `src/sssf/commands/misc.py` (or a new `commands/doctor.py`), `src/sssf/cli.py`
- Test: `tests/test_init.py`, `tests/test_doctor.py`

- [ ] **Step 1: Failing tests** — `sssf init` writes `.pi/skills/` (marker present); `sssf init --refresh` re-fetches when the marker is stale; `sssf doctor` exit 0 on a healthy project, exit 1 reporting: missing skills / stale skills / pi missing / provider disabled.

- [ ] **Step 2:** Run — FAIL

- [ ] **Step 3:** `init.run(...)` calls `skills_install.install_skills(root, refresh=refresh)` after the stamp (skip when the fetch fails — init still succeeds, doctor reports it).
- [ ] **Step 4:** `sssf doctor` (`commands/doctor.py`, wired into cli.py):
  - project found? legacy? internal provider enabled? `pi` binary (`misc.which`)?
  - `skills_install.check_skills` → print per-skill status; stale/missing → exit 1; healthy → exit 0; offline → non-fatal note.
- [ ] **Step 5:** Run: `uv run pytest tests/test_init.py tests/test_doctor.py -q` — PASS
- [ ] **Step 6: Commit**
```bash
git add src/sssf/commands/init.py src/sssf/commands/doctor.py src/sssf/cli.py tests
git commit -m "feat(doctor): verify interview prerequisites + skill freshness; init installs project skills"
```

---

### Task 4: Product manager context templates

**Files:**
- Create: `src/sssf/templates/spec_interviewer/idea.md`, `bug.md`, `feature.md`
- Test: `tests/test_templates.py`

- [ ] **Step 1: Failing test** — the three templates exist and each contains the output-contract marker + the pre-loaded skill routing (`/grilling`-style mentions are guidance for the AGENT to run, not the user).

- [ ] **Step 2:** Run — FAIL

- [ ] **Step 3:** Write the three templates (each: product-manager persona, mode questions, output contract with the required spec sections, "run the skill automatically — the user never invokes it"):

`idea.md` — persona: PM validating a new idea; skill: `grilling` then `brainstorming`; hard questions: consequences, effort, complexity, UX improvement, perceived value, risks, alternatives; end with a value/effort verdict.
`bug.md` — persona: PM + bug triager; skills: `grill-me` then `grill-with-docs`; gather: error text, logs, displayed messages, error codes, repro steps, environment, expected vs actual; produce a proposed-fix hypothesis.
`feature.md` — persona: PM shaping a known feature; story-vs-spec distinction (push stories into concrete requirements); scope, acceptance criteria, edge cases, out-of-scope.

- [ ] **Step 4:** Run: `uv run pytest tests/test_templates.py -q` — PASS
- [ ] **Step 5: Commit**
```bash
git add src/sssf/templates/spec_interviewer tests/test_templates.py
git commit -m "feat(spec): product manager interview templates (idea/bug/feature) with pre-loaded skill routing"
```

---

### Task 5: `sssf spec create` — context render + interactive pi spawn

**Files:**
- Create: `src/sssf/commands/spec.py`
- Modify: `src/sssf/cli.py`
- Test: `tests/test_spec_cli.py`

- [ ] **Step 1: Failing tests**
  - `spec create` resolves the project, writes the context file (mode-specific content), spawns `pi` with `--append-system-prompt <path>` (mocked subprocess), respects `--mode`/`--title`;
  - guards: no project / legacy / provider disabled / pi missing → exit 1 with messages.

- [ ] **Step 2:** Run — FAIL

- [ ] **Step 3:** `commands/spec.py`:
  - `create(mode, title, project)`:
    1. `find_project`; legacy warn; `ticketing.load_config` internal enabled check; `misc.which("pi")`.
    2. slug from `--title` or prompt; context path `adws/adw_data/spec_interview/<mode>-<slug>.md`; render the mode template + a project-context block (roster summary, data-dir, existing prompts numbering) — a small `str.format`/`Template` render with a context dict.
    3. `subprocess.call(["pi", "--append-system-prompt", str(context_path)], cwd=root)` — interactive (inherits the TTY).
    4. Return the exit code; print a summary (`spec session ended — check adws/prompts/ + the kanban`).
- [ ] **Step 4:** `cli.py` — `spec` subcommand group with `create`.
- [ ] **Step 5:** Run: `uv run pytest tests/test_spec_cli.py -q` — PASS; full suite green; ruff + mypy clean
- [ ] **Step 6: Commit**
```bash
git add src/sssf/commands/spec.py src/sssf/cli.py tests/test_spec_cli.py
git commit -m "feat(spec): sssf spec create — interactive product-manager interview"
```

---

### Task 6: e2e + docs

- [ ] **Step 1:** `sssf doctor` on a stamped project → healthy (after install).
- [ ] **Step 2:** Manual e2e: `sssf spec create --mode idea --title "..."` — the interactive session interviews, writes `adws/prompts/NN-...md`, creates the ticket; the ticket appears on the kanban; Run executes with the spec.
- [ ] **Step 3:** Site docs: a `docs/spec-create` page (the command, doctor, project-local skills) + sidebar entry.
- [ ] **Step 4:** Full suite + visualizer + ruff + mypy green.
- [ ] **Step 5: Commit + push + PR**
```bash
git add -A
git commit -m "docs(site): spec create + doctor + project-local skills page"
```

---

## Self-Review

**Spec coverage:** CLI `spec create` ✓ (T5) · `doctor` ✓ (T3) · project-local skills via init + refresh ✓ (T2, T3) · never-global constraint ✓ (T2 test) · product-manager templates with pre-loaded routing ✓ (T4) · runnable output: spec file + rich ticket + run-honors-prompt ✓ (T1, T4, T5) · tests ✓ per task · e2e + docs ✓ (T6). No placeholders — each task carries concrete code/tests.
