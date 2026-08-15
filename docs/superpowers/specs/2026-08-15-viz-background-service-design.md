# `sssf viz` as a Background Service — Design

**Status:** implemented 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` §7 (CLI surface of `viz`) · **Plan:** `2026-08-15-viz-background-service-plan.md`

The visualizer server previously ran as a **foreground** `subprocess.call` — the terminal was tied up for the life of the demo, Ctrl+C was the only stop, and a crashed server left no trace. This spec turns `sssf viz` into a managed background service.

## CLI surface

```
sssf viz                # = start
sssf viz start [--port N] [--db PATH] [--project DIR]
sssf viz stop
```

- `start` spawns the bun server **detached** (`start_new_session=True`, stdin `/dev/null`), appends output to `~/.sssf/viz.log`, and writes the pid to `~/.sssf/viz.pid`. Runtime state lives beside the registry (`~/.sssf/projects.json`).
- Repeating `start` while a server is tracked and alive prints `already running (pid N)` and does not spawn again — no `EADDRINUSE` crash.
- `stop` SIGTERMs the tracked pid, removes the pid file, and says `not running` (cleaning a stale pid file) when nothing is alive.
- `--port` / `--db` / `--project` pass through unchanged (adhoc single-db mode and project-scoped registry mode still work).

## Browser open

After a successful start (fresh spawn or already running), `sssf viz start` polls `GET /api/health` on the target port (bounded, ~5 s) and then opens the default browser at `http://localhost:PORT` via `webbrowser.open()`. The browser opens regardless of whether the server was freshly spawned or already running — the user asked to start viz, they want the UI.

## Error handling

- **Spawn-failure detection** (the important one, found in the first smoke): the spawned server can die instantly — port already taken, bad env. `start` gives it a moment (`_wait_for_server`), then checks the pid is still alive; if it died it prints `server exited during startup (is port N in use?) — see ~/.sssf/viz.log`, cleans the pid file, returns 1, and **does not open the browser**.
- **Stale pid file** (process died without `stop`): `_running_pid()` treats a dead pid as not-running; `stop` removes the file.
- Missing `bun` → existing `sssf doctor`-style error, exit 1.

## Why not a system daemon

A plain pid file + detached process is enough for the demo loop: no launchd plist, no port/state service. `~/.sssf/` is already the factory's state dir. YAGNI — a `status`/`logs` subcommand was deliberately cut (the log path is printed on start).

## Verification

- pytest (monkeypatched subprocess/webbrowser/urllib): spawn writes pid + opens browser, spawn-failure reports + cleans + opens nothing, already-running idempotence, stop kills + cleans, `_wait_for_server` up/down.
- Live smoke: `sssf viz --port 4602` → pid file → health 200 → repeat start "already running" → `stop` kills and removes the pid file.
