---
name: sssf
description: Super Simple Software Factory — repeatable agents-plus-code workflows (ADWs) in any codebase. Use when running sssf ADWs, managing the roster in sssf.config.yaml, or observing agent workflows.
---

# Super Simple Software Factory (SSSF)

Reusable combination of **agents plus code**: deterministic Python ADW scripts own
sequencing, retries, and acceptance; coding agents (Pi) work inside bounded
phases; typed JSON envelopes carry context between them; everything streams into
SQLite for the polled visualizer. Agent proposes, code disposes.

## Operating model

You are one bounded node in a factory run by the `sssf` CLI. Your job is the work
inside your phase: read the context handed to you, do the phase's task, and
return a valid envelope JSON. The CLI owns sequencing, retries, gates, and the
trace. You do not decide what comes next.

## Hard rules (enforced across everything the factory generates)

1. **Validate before running** — every ADW declares `REQUIRED_AGENTS` and calls `agents.validate()` first; a missing/misnamed agent fails before anything spawns.
2. **Typed outputs only** — every agent call pairs with a concrete `EnvelopeBase` subclass in `adw_modules/data_types.py`; parse failures re-prompt the same session (context intact), never restart.
   **The output contract is a synced triad**: (a) the type in `data_types.py`, (b) the JSON example in the agent's `user.md` `## Report` section, (c) `output_type=` at every call site. These are ONE contract — change any one, update all three in the same edit (grep the type name to find every call site).
3. **Gates validate claims, not guesses** — `gate(envelope, run) -> list[str]` violations; failures return to the same session as corrections.
4. **Four-param rule** — any function with more than 4 parameters takes one concrete data type instead (`AgentCall`, `PhaseParams` are the pattern).
5. **One agent, one prompt, one purpose** — identity lives in `system.md`; task shape (user prompt + output type) lives at the call site.
6. **ADW scripts stay thin** — all low-level logic lives in `adw_modules/`.
7. **Every phase earns a description** — one sentence on what it does and why, never a restatement of its name. It is the only intent the trace, the console, and the UI ever show; `commit_plan: "Commit the plan"` is rejected at construction, blank is too.
8. **A known command is code, not an agent** — if you can write the invocation down (`bun test`, `ruff check`), it belongs in a `kind="code"` phase via `adw_modules/quality.py`. Agents are for the parts that need reading and deciding; failures come back to the builder as an envelope either way.
9. **`tools:` is a capability list, `writes:` is the boundary** — `bash` runs anything (including `git checkout`) and `write` reaches any path, so a tool list can never make "this agent changes nothing" true. `writes:` per agent and `protected_files` in defaults are enforced in `adw_modules/permissions.py` after every agent call: unauthorized changes are rolled back and the phase dies. The session runtime under `data_dir` is always writable — a read-only agent is read-only with respect to the REPO, never mute.
10. **Every ADW ends in `run.finish()`** — phases passing is not the same as the run being accepted. A test phase that ran a red suite succeeded at its job. Pass `accepted=` so the exit code, the session status, and the banner are decided together and cannot disagree.

## v1 scope

Pi coding agent only (`coding_agent: pi`). `claude_code` is schema-valid but
stubbed. Observe via `sssf sessions / phases / tail / procs` or `sssf viz`.
