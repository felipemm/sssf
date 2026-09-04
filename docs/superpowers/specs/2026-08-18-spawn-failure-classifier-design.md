# Spawn-Failure Classifier — Design

Date: 2026-08-18
Status: Draft for review
Branch: feat/spawn-failure-classifier (off feat/hardening-wp2)

## Goal

When a sandboxed run dies before the ADW ever starts (spawn-death), the
recorded evidence gets a **remediation hint**: the board/trace shows *why*
it died and *what fixes it*, and `sssf doctor` lists recent spawn failures
with their fixes. Annotate-only — the classifier never acts on its own
conclusions.

## Background

The monitor used to erase the only evidence of a spawn-death (container +
worktree torn down; no session row) — a dead run looked like it "never
started" (issue #21; inkwell ticket `internal:0a04417a7c3d`, 2026-08-18:
stuck at `status=starting`, no session, no container, no worktree).

`record_never_started` (sandbox.py, commit c198930) now leaves evidence:
a failed session, an `error` event carrying `exit_code` + `container_log_tail`,
and the linked ticket flipped to `failed`. What the evidence does NOT yet say
is *why* it failed and what to do about it — that is this feature.

The healer's `diagnose()` classifies *state* (session/ticket/container/
worktree) and recovers. This classifier classifies *content* (the log tail)
and annotates. The two are complementary: the healer acts on the run, the
classifier explains the failure.

## 1. Pure classifier: `src/sssf/postmortem.py`

New module, one public function, zero dependencies on docker/git:

```python
def classify_failure(log_tail: str, exit_code: str = "") -> str | None:
    """A remediation hint for a spawn-death, or None when nothing matches.

    The hint is one line an engineer can act on. When the evidence itself
    is the message (an unknown error), pass the tail through rather than
    inventing a vague category.
    """
```

Signature table (v1 — signatures observed in the field):

| Evidence (log tail) | Hint |
|---|---|
| `can't open file '<path>'` (entry script) | `run's entry file <path> is not in the worktree — the project layout is not committed; commit it (git add -A && git commit) or re-run sssf init` |
| `No such file or directory` naming `adws/` | same as above (layout not committed / entry missing) |
| `ImportError` / `ModuleNotFoundError` mentioning `sssf.adw_modules` | `runner image is stale or broken — rebuild it: sssf sandbox build` |
| `exec: "<binary>": executable file not found` OR exit `127` | `a required binary (<binary>) is missing from the runner image — rebuild it or fix docker/sssf-runner.Dockerfile` |
| exit code set and log tail non-empty, nothing above matched | pass through the trimmed tail as the hint (evidence is the hint) |
| empty tail, unknown exit | `container exited (exit <code>) with no output — inspect the image entrypoint (docker/entrypoint.sh) and the spawned command` |

Matching rules:
- Case-insensitive, substring-based, ordered: specific signatures first,
  pass-through last.
- The 127 check applies only when the tail does NOT already contain a more
  specific signature (a quality gate missing its `requires:` target also
  exits 127 — but that error already self-explains in the envelope and does
  not reach `record_never_started`, which only sees pre-session deaths).
- Hint length is bounded (single line, ≤ 300 chars); the log tail itself is
  already capped at 2000 chars in the event payload.

## 2. Wiring: `record_never_started` (sandbox.py)

After the event payload is built, classify and annotate:

```python
payload = {
    "exit_code": exit_code,
    "container_log_tail": log_tail[-2000:],
    "remediation": classify_failure(log_tail, exit_code),   # str | None
}
```

`remediation` is `null` when nothing matches (the pass-through branches
ensure a hint exists whenever there is evidence). No other code path
changes; `record_never_started` keeps its best-effort contract (teardown
always runs).

## 3. Surfacing: `sssf doctor`

`sssf doctor` (commands/misc.py) gains a section after the tool checks:
"recent spawn failures", scanning the project DB for sessions whose
`adw_name` is `adw_simple_sdlc (never started)`, taking the newest few
(default 5), and printing `adw_id → remediation` (falling back to the
event's log-tail excerpt when no hint was classified). Read-only; never
mutates state.

Scope note: `sssf doctor` currently checks prerequisites; this adds a
read-only triage listing. The visualizer trace already renders the event
payload, so the hint appears there with no UI change.

## Out of scope (v1)

- **Auto-remediation** (healer acting on hints, e.g. auto `sssf sandbox
  build`) — deliberately deferred; annotate-only is the approved scope.
- **Quality-gate environment errors** (e.g. a `requires:` target missing):
  they occur after the session exists, never reach `record_never_started`,
  and already carry an actionable message in the failing envelope.
- **Healer changes**: `diagnose()`/`recover()` are untouched.

## Testing

- `tests/test_postmortem.py` — table-driven: each signature row, case
  insensitivity, pass-through of unknown tails, empty-input behavior, the
  127-with-specific-signature precedence, hint length bound.
- `tests/test_sandbox_docker.py` — extend the `record_never_started`
  evidence test: the event payload now contains a `remediation` key with the
  expected hint for a known signature, and `remediation` is null for zero
  evidence (the pass-through branches guarantee a hint whenever evidence
  exists — null means the container was gone before evidence capture).

## Files

| File | Change |
|---|---|
| `src/sssf/postmortem.py` | new — pure classifier |
| `src/sssf/sandbox.py` | `record_never_started`: classify + annotate payload |
| `src/sssf/commands/misc.py` | `doctor`: recent-spawn-failures section |
| `tests/test_postmortem.py` | new — table tests |
| `tests/test_sandbox_docker.py` | extend evidence test |
| `docs/superpowers/specs/2026-08-18-spawn-failure-classifier-design.md` | this spec |

## Success criteria

1. A spawn-death with a known signature yields an event whose
   `remediation` is the corresponding hint (unit + wiring tests).
2. `sssf doctor` lists a recorded spawn failure with its hint, read-only.
3. No behavior change for runs that start normally (guard unchanged).
4. Full suite green (pytest, ruff, mypy).
