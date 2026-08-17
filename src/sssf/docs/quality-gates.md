# Configuring quality checks

Deterministic quality commands — `test`, `lint`, `typecheck`, `build` — are
**code, not agents** (SKILL.md rule 8). They run as `kind="code"` phases in
your chain, and each one records a `gate_results` row, so the status
dashboard's **quality KPI** (gate pass rate, hotspot, retries) counts the real
runs — not just the agents' claim gates.

You configure them per project in `adws/config/sssf.config.yaml`:

```yaml
quality:
  checks:
    - name: test
      area: backend          # frontend | backend — how the dashboard buckets it
      operation: build       # lint | typecheck | build | security — what the check is
      argv: ["bun", "test"]  # a LIST, never a shell string
      timeout_seconds: 600   # hard budget; the run fails if the command hangs
```

Unwired names keep their honest placeholders — an `echo` that exits 0 and says
out loud that it is fake. A configured entry replaces its name; nothing else
is touched. Delete the checks you don't want.

## How a check runs

1. The chain's `kind="code"` phase calls `quality.run_tests(run)` (test only)
   or `quality.run_quality(run)` (every configured check).
2. The command runs with `cwd = repo root` and the **operator's own shell
   environment** — `bun`, `uv`, `pytest` resolve exactly as they do in your
   terminal.
3. Exit 0 → pass. Anything else → fail: **124** on timeout, **127** when a
   binary is missing. The tail of stdout+stderr rides back inside the envelope
   (bounded to 4k chars), and the full log is written to
   `<data_dir>/sessions/<adw_id>/context_handoff/quality/<seq>_<name>/command.log`.
4. Pass/fail lands in `gate_results` as `quality:<name>` — one check per run,
   item = the command, note = `exit N, Ds` (plus the artifact path on failure).
5. A **failing check does not fail the phase**. The result is handed to the
   builder as an envelope, and the bounded repair loop (fix → retest, up to
   `MAX_FIX_LOOPS` in your chain) decides the run's fate. Red tests or a
   rejected review stop the chain with the code uncommitted.

## The three rules

1. **argv is a LIST, never a shell string.** `["bash", "-c", "a && b"]` is the
   only way to get shell operators — no quoting bugs, no injection. Prefer
   splitting into full argv (`["uv", "run", "pytest", "-q"]`) over a `bash -c`
   wrapper.
2. **Call binaries by bare name.** The blocks inherit the operator's PATH —
   `bun`, `uv`, `npx` resolve as they do in your terminal. Never hard-code an
   absolute path like `/Users/you/.bun/bin/bun`; that bakes your machine into
   the trace and breaks the moment the project moves machines.
3. **Paths are repo-relative.** The command runs at the repo root, so
   `argv: ["bun", "test", "apps/web/server.test.ts"]` addresses files exactly
   as you would from the project root.

## Picking commands per stack

| stack | test | typecheck | lint | build |
|---|---|---|---|---|
| bun | `["bun", "test"]` | `["bun", "x", "tsc", "--noEmit"]` | `["bun", "x", "oxlint@1.36.0", "src"]` | `["bun", "build", "src/index.ts", "--outdir", "dist"]` |
| python (uv) | `["uv", "run", "pytest", "-q"]` | — | `["uv", "run", "ruff", "check"]` | `["uv", "build"]` |
| node/npm | `["npm", "test", "--", "--ci"]` | `["npm", "run", "typecheck"]` | `["npm", "run", "lint"]` | `["npm", "run", "build"]` |
| rust | `["cargo", "test"]` | `["cargo", "check"]` | `["cargo", "clippy", "--", "-D", "warnings"]` | `["cargo", "build", "--release"]` |

**Security scans** use `operation: security` — e.g. `["snyk", "test"]` (SCA
over your lockfile; exit 0 clean, non-zero on findings, so a finding fails the
check and reaches the builder like any other red gate). Snyk needs auth: run
`snyk auth` once on the operator machine. In sandboxed runs the host's
`~/.config` mount carries the token (`/tmp/.config/configstore/snyk.json`);
the sssf-runner image ships the snyk CLI.

Start with **test** — it is the highest-value block. Wire `typecheck`/`build`
when the suite alone cannot catch the failure mode you care about (a red
typecheck costs less than a review round trip). Delete what the stack doesn't
have: a project with no build step should not carry a build check.

## Budgets

`timeout_seconds` is a hard ceiling — the check fails as **124** if the
command exceeds it. Defaults: `test` 600s, `build` 300s, `lint`/`typecheck`
120s. A slow-but-honest suite is better served by raising the budget than by a
flaky-slow command; a check that routinely eats its full budget is a signal
the command itself is the problem.

## Sandboxed runs

In a sandboxed run the command executes **inside the container**
(`cwd = repo root` in the sandbox), with the operator's environment. The
binaries must exist in the `sssf-runner:<version>` image (python, node, bun,
uv are included). A missing binary fails as 127 with the real message — no
pre-flight probe, and none wanted.

## Verifying it works

1. Run a session: `sssf run "…"` (or `sssf sandbox run`).
2. In the trace (`sssf viz` → session → trace), the `test_N` phase shows the
   command, exit code, and duration; the envelope handed to the builder after
   a failure carries the verbatim output tail.
3. The dashboard's **Quality** KPI (gate pass rate, hotspot phase, retries)
   now counts `quality:<name>` rows. Directly:
   ```sql
   SELECT gate, passed, checks_json FROM gate_results WHERE gate LIKE 'quality:%';
   ```
4. A placeholder check passes loudly — its command text says
   `PLACEHOLDER … wire quality.checks …`. If you see that in a trace, that
   check is not wired yet.
