# #88 — Hermetic skills + runner sessions

Parent: #86. No blockers. The flows (#89/#91/#92) bake skill names into their
phases (grill-with-docs → to-spec → to-tickets; implement → /tdd, /code-review),
and `sssf spec create` already relies on project-local `.pi/skills/` discovery
for its interviewer prompts (grilling, brainstorming, grill-me,
grill-with-docs). Today the stamped set is only those four skills, agent
sessions load the operator's ENTIRE global skill library by discovery, and a
machine's skill library drift changes what an agent sees. This slice makes the
stamped project the whole story: `init` stamps the transitive closure of the
workflow skills, agent sessions run `pi --no-skills` and load only the
per-agent `skills:` subsets the roster declares, and external setup references
in the stamped skills are rewritten to sssf's own commands.

## The closure root

The issue names 12 workflow skills, all from one canonical repo
(mattpocock/skills): grill-me, wayfinder, grill-with-docs, to-spec, to-tickets,
triage, implement, code-review, tdd, grilling, domain-modeling, codebase-design
(repo paths: productivity/{grill-me,grilling},
engineering/{wayfinder,to-spec,to-tickets,triage,implement,code-review,tdd,
domain-modeling,codebase-design,grill-with-docs}).

`brainstorming` (superpowers) is NOT in the issue's list but the
spec_interviewer templates reference it directly, so it stays in the stamp
root — the closure of (12 workflow skills + brainstorming). A skill's whole
directory rides along (SKILL.md + in-directory reference docs like
triage/AGENT-BRIEF.md, tdd/mocking.md, domain-modeling/ADR-FORMAT.md,
codebase-design/DEEPENING.md, plus each skill's `agents/` agent-def yaml) —
"plus in-directory reference docs and agent defs".

## Scope

1. **skills_install.py** — manifest 4 → 13; `WORKFLOW_SKILLS` seed tuple;
   `closure()` fixpoint scan (each included skill's files scanned for other
   manifest skills by frontmatter name, word-boundary); `install_skills`
   stamps closure(root) with whole-directory copies; external-setup rewrite
   (`/setup-matt-pocock-skills` → sssf's own `sssf init --refresh` guidance) in
   every stamped .md; the `.sssf-versions.json` marker pins every STAMPED
   skill's source commit. `check_skills` (doctor) covers the full manifest.

2. **data_types.py** — `AgentConfig.skills: list[str] = []` (per-agent subset,
   names from the stamped closure); `ConfigDefaults.skills_hermetic: bool =
   False` (model default stays backward-compatible; the stamped template turns
   it on); `PiRequest.no_skills: bool = False`, `PiRequest.skill_paths:
   list[str] = []`.

3. **agent_pi.py** — extract `build_command(request) -> list[str]` (pure, unit-
   testable); when `no_skills`, add `--no-skills`; append one `--skill <path>`
   per `skill_paths` (pi: `--skill` is repeatable and additive even with
   `--no-skills`). The sssf package SKILL.md (`skill_path`) keeps loading.

4. **agents.py** — `execute()`: hermetic → skill paths resolve from
   `<repo_root>/.pi/skills/<name>/SKILL.md` and the request carries
   `no_skills=True` + `skill_paths`; `validate()`: hermetic mode fail-fast —
   every `skills:` name on a required agent must resolve to a stamped
   SKILL.md (error guides `sssf init --refresh` + commit the stamped set).

5. **templates/adws/config/sssf.config.yaml** — `defaults.skills_hermetic:
   true` + per-agent `skills:` subsets (planner: grilling/grill-with-docs/
   to-spec/to-tickets/domain-modeling/codebase-design; builder:
   implement/tdd/code-review; scout: wayfinder/triage; reviewer: code-review;
   designer/documenter: none — impeccable is a CLI quality gate here).

6. **Tests** — test_skills_install (closure pull, reference-docs/agent-defs
   ride-along, external-ref rewrite, marker pins stamped closure, never-global,
   doctor); test_agent_pi (build_command flags); test_templates (starter
   config validates with stamped stubs — hermetic check exercised); validate()
   hermetic fail-fast test.

7. **Docs** — CHANGELOG entry; src docs note on the hermetic set + committing
   `.pi/skills` (sandbox worktrees branch from origin/main, so the stamp must
   be committed to reach the container).

## Blocker notes for the next iteration

- The designer/documenter agents ship with an empty subset; a project that
  needs a skill outside the closure adds it to the manifest AND the roster in
  the same change (AC3).
- `sssf spec create` spawns interactive pi WITHOUT `--no-skills` — it relies on
  `.pi/skills/` discovery from cwd. The closure keeps the interviewer skills so
  this path stays hermetic-by-content even though it is discovery-loaded.
