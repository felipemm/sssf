# `sssf viz` as a Background Service — Implementation (as-executed)

> **Status:** DONE — committed, tested, and smoke-verified live.
> Retrospective record written 2026-08-15 from the approved chat design.
>
> **Spec:** `docs/superpowers/specs/2026-08-15-viz-background-service-design.md`
> **Implementation repo:** `~/dev/lab/mvp/sssf`

## Commit

| Commit | What landed |
|---|---|
| `c06feb9` | `sssf viz [start|stop]` background service: detached spawn, `~/.sssf/viz.pid` + `viz.log`, already-running idempotence, spawn-failure detection, browser open after health poll; `viz.py` rewritten, `cli.py` subcommand (bare `viz` = `start`), `tests/test_viz.py` rewritten (7 tests) |

## Files

- `src/sssf/commands/viz.py` — `start` / `stop` / `_spawn` / `_running_pid` / `_pid_alive` / `_wait_for_server`
- `src/sssf/cli.py` — `viz` subparser gains an `action` positional (`start` default, `stop`)
- `tests/test_viz.py` — missing bun, spawn + pid + browser, spawn-failure (no browser, pid cleaned), already-running, stop not-running, stop kills + cleans, `_wait_for_server` up/down

## Design decisions captured during implementation

1. **`PORT` env, not `--port` argv** — the bun server reads `process.env.PORT`; the original plan sketch passed `--port` argv which was silently ignored (server always bound 4600).
2. **Spawn-failure detection added after the first smoke**: an unmanaged pre-existing server on 4600 made the first `start` spawn crash with `EADDRINUSE`; the health poll then answered from the *old* server and `start` printed a fake "started" for a dead pid. The fix (check the spawned pid is still alive after the bounded wait) was folded into the same commit.
3. **Browser opens on success only** — the failure path prints the error and opens nothing.
4. **Log file fd closed in the parent** after `Popen` (the child holds its own copy).

## Verification

```bash
cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_viz.py -v   # 7 passed
```

Live smoke: `sssf viz --port 4602` → `started (pid …)` → `~/.sssf/viz.pid` written → `GET /api/health` 200 → repeat start → `already running (pid …)` → `sssf viz stop` → pid file removed, server gone.
