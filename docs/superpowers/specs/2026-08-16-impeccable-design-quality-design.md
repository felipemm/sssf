# Impeccable Design Quality — Design

Date: 2026-08-16
Status: Draft for review

## Goal

Wire [Impeccable](https://github.com/pbakaus/impeccable) into sssf at three levels:

1. **Deterministic gate** — the 59-rule `impeccable detect` runs as a config-driven
   `quality.checks` entry (like inkwell's `bun`/`snyk` entries), plus CI gates on
   sssf's own site (deploy + PR).
2. **Agentic design pass** — a new opt-in ADW variant runs the impeccable skill
   commands (`audit`, `critique`, `polish`, `optimize`) via a new `designer` roster
   agent, whose work is then verified by the deterministic gate.
3. **Documentation** — the factory documents its own integration (site docs page,
   config docs, README), and the variant ADW generates a `DESIGN.md` per project
   via `impeccable document`.

## Deterministic gate (config-driven quality checks, shipped configured by default)

Impeccable's `detect` is the gate: it scans a directory/file/URL for 59
deterministic anti-patterns, is LLM-free, and exits non-zero on hard findings
(verified: exit 2 on anti-patterns, exit 0 on the current sssf site, whose only
finding is the advisory-only em-dash rule triggered by `--` CLI flags).

Two checks ship **configured by default** in the template config
(`src/sssf/templates/sssf.config.yaml`) because both binaries come from the
runner image (snyk since PR #13, impeccable via this change) — they work in any
sandboxed run with zero project wiring, exactly like inkwell's entries:

```yaml
quality:
  checks:
    - name: test            # PLACEHOLDER — runner is project-specific (bun/uv/npm)
    - name: lint            # PLACEHOLDER — runner is project-specific
    - name: typecheck       # PLACEHOLDER — runner is project-specific
    - name: build           # PLACEHOLDER — runner is project-specific
    - name: design          # SHIPPED BY DEFAULT — image-provided binary
      area: frontend
      operation: lint
      argv: ["impeccable", "detect", "site/dist"]
      timeout_seconds: 300
      requires: site/dist    # fail fast (127) when the target is missing
    - name: snyk            # SHIPPED BY DEFAULT — image-provided binary
      area: backend
      operation: security
      argv: ["snyk", "test"]
      timeout_seconds: 300
```

`requires` is an engine field added by this change: a check whose declared
target does not exist fails fast with exit 127 and a clear message instead of
letting the command silently scan nothing and pass. Impeccable's own `detect`
exits 0 on a missing/empty target, so without this the shipped `design` check
would be a silent false-green on projects without a site — the one thing the
module must never produce.

- **Bare binaries, not `npx`**: the runner image provides `impeccable` and
  `snyk` globally (mirroring the existing snyk pattern). No per-run cold npx
  download, no floating version.
- **snyk auth**: `SNYK_TOKEN` is forwarded from the operator machine into the
  sandbox by `sandbox_env` (sandbox.py) when set; absent that, the operator's
  configstore is used. A stamped project without snyk setup removes the entry.
- **design target is per-project**: `site/dist` is the default; a project with a
  different surface edits the argv. A project with no design surface removes the
  entry. The config comment says so.
- **Semantics**: `passed = returncode == 0`, identical to every other check;
  failures flow to the builder through `as_envelope` exactly as today.
- **Placeholders stay for runners**: `test`/`lint`/`typecheck`/`build` remain
  honest placeholders — those commands are inherently project-specific and
  cannot be defaulted; only image-provided tools ship pre-wired.

## New ADW variant: `adw_design_sdlc.py`

A new opt-in template, modeled on the existing `adw_plan_build_test_quality.py`.
Existing ADWs are untouched; projects opt in by choosing the ADW.

```
request → plan → init ──→ build → design ──→ verify_i (run_quality, incl. `design` check)
                  │                   │              │
      documenter: /impeccable       └─→ fix_i (builder) ← loop (MAX_FIX_LOOPS)
      init → PRODUCT.md                │
                     designer:        ▼
                     /impeccable  document (documenter: impeccable document
                     audit → critique → DESIGN.md) → commit (only when verified)
                     polish → optimize
                     (edits site src)
```

Phases:

| phase | kind | owner | notes |
|---|---|---|---|
| `request` | engineer | operator | capture the ask (unchanged) |
| `plan` | agent | planner | unchanged |
| `init` | agent | documenter | runs `/impeccable init` → writes `PRODUCT.md`; answers init's questions from the request/plan context (surface type inferred; `buildPath: code` — the harness has no image generation) |
| `build` | agent | builder | unchanged |
| `design` | agent | **designer** | runs audit → critique → polish → optimize on the design surface (`site/`), applies fixes; retries 1 |
| `verify_i` | code | quality | `quality.run_quality(run)` — includes the `design` detect check; record pass/fail |
| `fix_i` | agent | builder | only when verify failed; envelope = verbatim gate output (unchanged pattern) |
| `document` | agent | documenter | runs `impeccable document` → writes `DESIGN.md` (PRODUCT.md already exists from the `init` phase) |
| `commit` | code | git | only when verified |

The design phase is agentic and bounded: one pass with retries, reading the
`PRODUCT.md` from the `init` phase as design context. The deterministic verify
phase is the arbiter — if the agent's polish still trips the `design`
check, the standard fix loop handles it (same failure path as any gate).

## Designer agent (roster)

New roster entry in the template config (an active editing agent like the
builder, so it inherits the default model rather than a reviewer-grade model):

```yaml
- name: designer
  color: "#f59e0b"
  purpose: Run the impeccable design pass (audit, critique, polish, optimize)
           on the project's design surface and apply the fixes it reports.
  writes:
    - site/                 # per-project design surface; adjust in project configs
  tools: [read, grep, find, ls, bash, edit, write]
```

- `writes:` scoped to the design surface so the designer cannot touch machinery
  (enforced by `adw_modules/permissions.py` like every agent).
- `bash` because the impeccable skill commands shell to its bundled node
  scripts, and `document`/`detect` are CLI invocations.

## Provisioning (host + sandbox)

The entrypoint deliberately excludes host skills from the sandbox, so the
impeccable skill must be provisioned explicitly — both sides.

**Sandbox image** (`docker/sssf-runner.Dockerfile`):

```dockerfile
# impeccable — design quality: CLI for the deterministic gate (detect) and
# document; the pi skill the designer agent runs (/impeccable audit|critique|
# polish|optimize). npm lands in /usr/local (world-readable for the runtime
# uid, like bun). The skill is vendored under docker/impeccable-pi/ and copied
# into the pi home by entrypoint.sh (same pattern as the settings copy).
RUN npm install -g impeccable@3.6.0
COPY docker/impeccable-pi /opt/impeccable-pi
```

`docker/entrypoint.sh` adds:

```sh
mkdir -p "$HOME/.pi/agent/skills"
cp -r /opt/impeccable-pi/skills/impeccable "$HOME/.pi/agent/skills/"
```

The pi skill is **vendored** into the repo (`docker/impeccable-pi/`, produced by
`npx impeccable install --providers=pi --scope=project` — verified shape:
`.pi/skills/impeccable/` with `SKILL.md` + `scripts/`). Vendoring keeps image
builds deterministic and offline; it is the README-sanctioned pattern.

**Host** (`--no-sandbox` runs): `npx impeccable install --providers=pi` into the
pi home so local runs resolve the skill too. Documented in the setup notes.

**Image rebuild is mandatory** after the Dockerfile change (`sssf sandbox build`
or `docker build -t sssf-runner ...`); runs otherwise keep using the stale
image — the exact class of failure fixed by PR #15.

## CI gates on sssf's own site

- `site/package.json`: add `impeccable@3.6.0` to devDependencies (reproducible;
  `npm ci` installs and caches it).
- `.github/workflows/pages.yml`: step after `Build the static site`
  (working-directory is `site`, so target is `dist`):

  ```yaml
  - name: Impeccable design check
    run: npx --yes impeccable detect dist
  ```

  Blocks the Pages deploy on hard anti-patterns (exit 2).
- `.github/workflows/ci.yml`: new `site` job — checkout, node 22, `npm ci`,
  `npm run build`, `npx --yes impeccable detect dist` — added to the
  `aggregate` job's `needs`, so PRs are guarded before merge (branch protection
  already requires the `CI` check).

## Documentation

**(a) Docs about the integration (sssf site):**

- New site page `site/src/pages/docs/design-quality.astro` + entry in
  `site/src/components/DocsSidebar.astro` (`Design quality`).
  Content: what the deterministic gate checks (59 rules), how the agentic pass
  works in the variant ADW, how to opt in (choose the ADW, adjust `site/`
  target, wire the `design` check), and the known advisory-only em-dash false
  positive on `--` CLI flags.
- New site page `site/src/pages/docs/quality-checks.astro` + sidebar entry
  (`Quality checks`) — **the configuration reference for every option** in
  `quality.checks`, the canonical "how to configure properly" doc:
  - every field: `name`, `area`, `operation`, `argv`, `timeout_seconds` —
    semantics, allowed values, defaults;
  - the two rules for writing a real check: `argv` as a LIST (never a shell
    string — no quoting bugs, no injection) and bare binary names (never
    absolute paths — the env inherits the operator's shell);
  - how checks resolve: config entries + honest placeholders for unwired
    defaults (`_specs` merge), what a placeholder does and why a silent
    false-green is forbidden;
  - the shipped-by-default checks (`design`, `snyk`) and how to opt out;
  - wiring examples for real runners (bun, uv/pytest, npm) and per-project
    targets (`site/dist` vs other surfaces);
  - how a check's result surfaces: `gate_results` row, envelope to the builder,
    the dashboard quality-gate KPI;
  - `timeout_seconds` guidance per check class.
- `site/src/pages/docs/configuration.astro`: brief pointer to the quality-checks
  reference; document the `designer` agent.
- **Demo project callout**: the site (docs index `docs/index.astro` + quickstart)
  states that the [inkwell](https://github.com/felipemm/inkwell) repo is provided
  as a demo project for testing sssf end-to-end (factory runs, quality gates,
  sandbox, visualizer) — with a short "run it" pointer (clone, `sssf run`).
- `README.md`: one-line mention.

**(b) Project design docs in-process:** the `init` phase runs `/impeccable init`
→ `PRODUCT.md` (non-interactive: the documenter answers init's surface-type
question from the request/plan and records `buildPath: code`), and the
`document` phase runs `impeccable document` → `DESIGN.md`. Both are committed
with the project and available as design context for the designer pass.

## Verification

1. `npx impeccable detect site/dist` → exit 0 (done; only advisory em-dash).
2. Deliberately bad HTML → exit 2 (done).
3. Rebuilt image: `docker run --rm sssf-runner:latest impeccable --version` and
   `impeccable detect` against mounted `site/dist` → exit 0.
4. Template config parses (`load_config`); a sandboxed run shows `design` and
   `snyk` shipped configured by default and green (given `SNYK_TOKEN`).
5. `astro build` succeeds with the new docs pages (`design-quality`,
   `quality-checks`); sidebar entries render; inkwell demo callout links work.
6. Manual/optional: one sandboxed run of the variant ADW against a site project
   exercising init (PRODUCT.md) → design → verify → document (DESIGN.md) → commit.

## Out of scope

- Per-project `.impeccable/config.json` ignores (the em-dash advisory is
  advisory-only and does not fail the gate; add ignores later if it becomes noise).
- Impeccable hooks / live mode / browser iteration.
- Changes to the standard (non-variant) ADWs.
- snyk project onboarding (grouping, ignore policies, custom rules) — the
  default check is plain `snyk test`; deeper snyk configuration stays per-project.
