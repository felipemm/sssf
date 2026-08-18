/**
 * SSSF visualizer server — JSON API over registered projects' sssf.db files
 * (the global `sssf viz` mode), plus the single-db adhoc mode it replaced, plus
 * the built UI when ./dist exists. Reads are read-only; the single write is
 * POST /api/sessions/:adw_id/archive, which sets one review flag on a row.
 *
 * There is no ingest endpoint and no websocket. The data path is
 * agents → sqlite → web ui, and the UI gets there by polling.
 *
 * Global mode (default):
 *   bun run server/index.ts
 *   — serves over ~/.sssf/projects.json (SSSF_REGISTRY to override);
 *     /api/projects lists them; /api/projects/:project/... scopes a route.
 *
 * Adhoc single-db mode (backwards compat):
 *   bun run server/index.ts --db /path/to/repo/adws/data/sssf.db
 *   SSSF_DB=/path/to/sssf.db PORT=4600 bun run server/index.ts
 *   — the old unscoped /api/sessions routes keep working.
 */
import { existsSync, statSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { SssfDb, resolveDbPath } from "./db.ts";
import { ProjectRegistry } from "./registry.ts";
import { sweepAll } from "./sweep.ts";
import { isEnabled, readTickets } from "./tickets.ts";
import { computeStatus } from "./status.ts";
import { computeCockpit, computeCockpitContributions, containerLogs, defaultSpawnCli, handleControl } from "./cockpit.ts";
import type { AgentPrompts, ApiError, ControlResult, HealthResponse } from "../shared/types.ts";

const PORT = Number(process.env.PORT ?? 4600);
const DIST_DIR = resolve(import.meta.dir, "..", "dist");

const projects = new ProjectRegistry();

// Adhoc mode is opt-in: --db, --db=…, or SSSF_DB. Otherwise the server is the
// global service over the registry, and unscoped routes have no db to serve.
const hasAdhocDb =
  process.env.SSSF_DB !== undefined ||
  Bun.argv.some((a) => a === "--db" || a.startsWith("--db="));
let adhocDb: SssfDb | null = null;
if (hasAdhocDb) {
  const dbPath = resolveDbPath();
  try {
    adhocDb = new SssfDb(dbPath);
  } catch (error) {
    console.error(`[sssf] ${(error as Error).message}`);
    process.exit(1);
  }
}

/** SssfDb bindings per project name, built over the registry's cached connections. */
const projectDbs = new Map<string, SssfDb>();

function projectRoot(name: string): string | null {
  return projects.list().find((p) => p.name === name)?.root ?? null;
}

/** Resolve the db a request targets: a :project scope, or adhoc when unscoped. */
function dbForProject(projectName: string | null): SssfDb | null {
  if (!projectName) return adhocDb;
  let db = projectDbs.get(projectName);
  if (db) return db;
  const path = projects.pathFor(projectName);
  const raw = projects.dbFor(projectName);
  if (!path || !raw) return null;
  db = new SssfDb(path, raw);
  projectDbs.set(projectName, db);
  return db;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function notFound(message: string): Response {
  return json({ error: message } satisfies ApiError, 404);
}

/** True when the sqlite file was replaced under an open connection. */
function isIoError(error: unknown): boolean {
  // SQLITE_IOERR: the file was replaced underneath a cached connection.
  // SQLITE_ERROR + 'no such table': the cached connection predates the schema
  // (the ADW created the tables after the server first opened an empty file)
  // — same fix: drop the cached connections and retry once.
  if (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof (error as { code?: unknown }).code === "string" &&
    (error as { code: string }).code.startsWith("SQLITE_IOERR")
  ) {
    return true;
  }
  return (
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    typeof (error as { message?: unknown }).message === "string" &&
    (error as { message: string }).message.includes("no such table")
  );
}

/**
 * Drop every cached connection and rebuild the adhoc one: the db file was
 * replaced underneath us (an agent git-checkout over a live WAL, a restore,
 * …), so the open vnodes are dead. The retry reopens fresh.
 */
function resetDbCaches(): void {
  projects.reset();
  projectDbs.clear();
  if (hasAdhocDb) {
    adhocDb?.close();
    try {
      adhocDb = new SssfDb(resolveDbPath());
    } catch {
      adhocDb = null;
    }
  }
}

/** Guard every handler so a malformed query can't take the server down mid-run. */
function safely(
  handler: (req: Request) => Response | Promise<Response>,
): (req: Request) => Promise<Response> {
  return async (req) => {
    try {
      return await handler(req);
    } catch (error) {
      if (isIoError(error)) {
        resetDbCaches();
        try {
          return await handler(req);
        } catch (retryError) {
          console.error(`[sssf] ${req.method} ${new URL(req.url).pathname}:`, retryError);
          return json({ error: (retryError as Error).message } satisfies ApiError, 500);
        }
      }
      console.error(`[sssf] ${req.method} ${new URL(req.url).pathname}:`, error);
      return json({ error: (error as Error).message } satisfies ApiError, 500);
    }
  };
}

/**
 * adw_ids and agent names are path segments on disk, so anything that isn't a
 * plain identifier is rejected outright rather than sanitized into something
 * that might still escape the sessions directory.
 */
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;

function isSafeSegment(value: string): boolean {
  return SAFE_SEGMENT.test(value) && value !== "." && value !== "..";
}

function param(req: Request, key: string): string {
  return decodeURIComponent(
    (req as Request & { params: Record<string, string> }).params[key] ?? "",
  );
}

function intQuery(req: Request, key: string, fallback: number): number {
  const raw = new URL(req.url).searchParams.get(key);
  if (raw === null || raw.trim() === "") return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

// ── handlers — the query code from the single-db server, unchanged ──────────

function sessionsHandler(req: Request, db: SssfDb): Response {
  const onlyArchived = intQuery(req, "archived", 0) === 1;
  return json(db.sessions(intQuery(req, "limit", 200), onlyArchived));
}

function sessionDetailHandler(req: Request, db: SssfDb): Response {
  const adwId = param(req, "adw_id");
  const detail = db.sessionDetail(adwId);
  return detail ? json(detail) : notFound(`no session ${adwId}`);
}

// The one write. Archiving is review triage — it belongs to the reader, not
// to the run — so it never touches anything a tracer wrote.
async function archiveHandler(req: Request, db: SssfDb): Promise<Response> {
  const adwId = param(req, "adw_id");
  if (!isSafeSegment(adwId)) {
    return json({ error: "invalid adw_id" } satisfies ApiError, 400);
  }
  const body = (await req.json().catch(() => ({}))) as { archived?: unknown };
  const archived = body.archived === undefined ? true : Boolean(body.archived);
  return db.setArchived(adwId, archived)
    ? json({ adw_id: adwId, archived })
    : notFound(`no session ${adwId}`);
}

function eventsHandler(req: Request, db: SssfDb): Response {
  return json(
    db.events(
      param(req, "adw_id"),
      intQuery(req, "after", 0),
      intQuery(req, "limit", 500),
    ),
  );
}

function envelopesHandler(req: Request, db: SssfDb): Response {
  return json(db.envelopes(param(req, "adw_id")));
}

function gatesHandler(req: Request, db: SssfDb): Response {
  return json(db.gates(param(req, "adw_id")));
}

// The exact prompts an agent was sent, read from the session dir. Files are
// the raw record; the db has no copy of them.
async function promptsHandler(req: Request, db: SssfDb): Promise<Response> {
  const adwId = param(req, "adw_id");
  const agent = param(req, "agent");
  if (!isSafeSegment(adwId) || !isSafeSegment(agent)) {
    return json({ error: "invalid adw_id or agent" } satisfies ApiError, 400);
  }
  if (!db.session(adwId)) return notFound(`no session ${adwId}`);

  const dir = resolve(db.sessionsDir, adwId, agent, "prompts");
  // Defense in depth: the segment check already forbids traversal.
  if (dir !== db.sessionsDir && !dir.startsWith(db.sessionsDir + sep)) {
    return json({ error: "invalid path" } satisfies ApiError, 400);
  }

  // A prompt file is absent whenever the agent never ran in this session —
  // a normal state, so it reads as null rather than an error.
  const read = async (name: string): Promise<string | null> => {
    const file = Bun.file(join(dir, `${name}.md`));
    return (await file.exists()) ? await file.text() : null;
  };
  return json({
    system: await read("system"),
    user: await read("user"),
  } satisfies AgentPrompts);
}

// ── route wiring ────────────────────────────────────────────────────────────

/** Bind a handler to a :project-scoped db; 404 when the project has no db. */
function scoped(handler: (req: Request, db: SssfDb) => Response | Promise<Response>) {
  return safely(async (req) => {
    const name = param(req, "project");
    const db = dbForProject(name);
    return db ? await handler(req, db) : notFound(`no trace db for project ${name}`);
  });
}

/** Unscoped routes only exist in adhoc mode — the global service 404s them. */
function adhocOnly(handler: (req: Request, db: SssfDb) => Response | Promise<Response>) {
  return safely((req) =>
    adhocDb ? handler(req, adhocDb) : notFound("no adhoc db — set SSSF_DB or use /api/projects/:project routes"),
  );
}

/** Serve the built SPA if it has been built; otherwise point at the dev server. */
async function serveStatic(req: Request): Promise<Response> {
  const { pathname } = new URL(req.url);

  if (!existsSync(DIST_DIR)) {
    return new Response(
      `SSSF visualizer API is running on :${PORT}.\n\n` +
        `No ./dist build found. Run "bun run dev" for the Vite dev server ` +
        `(it proxies /api here), or "bun run build" to serve the UI from this process.\n`,
      { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  // Reject traversal before touching the filesystem.
  const candidate = resolve(join(DIST_DIR, pathname));
  if (candidate === DIST_DIR || candidate.startsWith(DIST_DIR + "/")) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return new Response(Bun.file(candidate));
    }
  }

  // SPA fallback: breadcrumb routes are client-side.
  const indexHtml = join(DIST_DIR, "index.html");
  if (existsSync(indexHtml)) {
    return new Response(Bun.file(indexHtml), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }
  return notFound("not found");
}

const server = Bun.serve({
  port: PORT,
  routes: {
    "/api/health": safely(() =>
      json({
        ok: true,
        db: adhocDb?.path ?? projects.path,
        journal_mode: adhocDb?.journalMode ?? "registry",
        sessions: adhocDb?.sessionCount() ?? 0,
      } satisfies HealthResponse),
    ),

    "/api/projects": safely(() =>
      json(
        projects.list().map((p) => ({
          name: p.name,
          root: p.root,
          dbExists: projects.dbFor(p.name) !== null,
          lastRun: p.lastRun,
        })),
      ),
    ),

    // Mission Control — cross-project aggregate + controls.
    "/api/cockpit": safely(async () => json(await computeCockpit({ registry: projects }))),
    "/api/cockpit/projects/:project/refresh": {
      POST: safely(async (req) =>
        json(await handleControl("refresh", { project: param(req, "project") },
          { registry: projects, spawnCli: defaultSpawnCli })),
      ),
    },
    "/api/cockpit/projects/add": {
      POST: safely(async (req) => {
        let root = "";
        try {
          root = (await req.json()).root ?? "";
        } catch {
          return json({ ok: false, error: "malformed json" } satisfies ControlResult, 400);
        }
        return json(await handleControl("add", { root }, { registry: projects, spawnCli: defaultSpawnCli }));
      }),
    },
    "/api/cockpit/projects/:project/remove": {
      POST: safely(async (req) => {
        let confirm = false;
        try {
          confirm = (await req.json()).confirm === true;
        } catch {
          return json({ ok: false, error: "malformed json" } satisfies ControlResult, 400);
        }
        return json(await handleControl("remove", { project: param(req, "project"), confirm },
          { registry: projects, spawnCli: defaultSpawnCli }));
      }),
    },
    "/api/cockpit/contributions": safely(() => json(computeCockpitContributions(projects))),
    "/api/cockpit/containers/:name/logs": safely(async (req) => {
      const name = param(req, "name");
      const tail = Number(intQuery(req, "tail", 100));
      return json(await containerLogs(name, tail));
    }),
    "/api/cockpit/heal/:action": {
      POST: safely(async (req) => {
        const action = param(req, "action");
        if (action !== "start" && action !== "stop") {
          return json({ ok: false, error: "action must be start or stop" } satisfies ControlResult, 400);
        }
        const r = await defaultSpawnCli(["heal", action]);
        return json(r.code === 0 ? { ok: true, output: r.out } : { ok: false, error: r.out } satisfies ControlResult);
      }),
    },

    // Manual archival sweep across every registered project (the `sssf sweep`
    // CLI equivalent) — review triage, the only batch write the server makes.
    "/api/sweep": {
      POST: safely(() => json({ results: sweepAll(projects, adhocDb?.path ?? null) })),
    },

    // Adhoc single-db mode (backwards compat).
    "/api/sessions": adhocOnly(sessionsHandler),
    "/api/sessions/:adw_id": adhocOnly(sessionDetailHandler),
    "/api/sessions/:adw_id/archive": {
      POST: adhocOnly(archiveHandler),
    },
    "/api/sessions/:adw_id/events": adhocOnly(eventsHandler),
    "/api/sessions/:adw_id/envelopes": adhocOnly(envelopesHandler),
    "/api/sessions/:adw_id/gates": adhocOnly(gatesHandler),
    "/api/sessions/:adw_id/agents/:agent/prompts": adhocOnly(promptsHandler),

    // Global multi-project scope.
    "/api/projects/:project/tickets": scoped((req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root) return notFound(`no project ${name}`);
      const db = dbForProject(name);
      if (!db) return notFound("no trace db for project");
      return json({ enabled: isEnabled(root), tickets: readTickets(db.path) });
    }),
    "/api/projects/:project/status": scoped((req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root) return notFound(`no project ${name}`);
      const db = dbForProject(name);
      if (!db) return notFound("no trace db for project");
      const w = intQuery(req, "window", 30);
      const windowDays = [7, 30, 90].includes(w) ? w : 30;
      return json(computeStatus(db.path, root, name, windowDays));
    }),
    "/api/projects/:project/tickets/sync": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root || !isEnabled(root)) return json({ error: "ticketing not configured" }, 400);
      const res = await syncTickets(root);
      return json(res);
    }),
    "/api/projects/:project/tickets/:id/run": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      const id = param(req, "id");
      if (!root || !isEnabled(root)) return json({ error: "ticketing not configured" }, 400);
      const res = await runTicket(root, id);
      return res.ok ? json(res) : json(res, 409);
    }),
    "/api/projects/:project/tickets/:id/backlog": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      const id = param(req, "id");
      if (!root || !isEnabled(root)) return json({ error: "ticketing not configured" }, 400);
      const res = await backlogTicket(root, id);
      return res.ok ? json(res) : json(res, 409);
    }),
    "/api/projects/:project/sessions": scoped(sessionsHandler),
    "/api/projects/:project/sessions/:adw_id": scoped(sessionDetailHandler),
    "/api/projects/:project/sessions/:adw_id/restart": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      const adwId = param(req, "adw_id");
      if (!root) return notFound(`no project ${name}`);
      const proc = Bun.spawn(["sssf", "run", "restart", adwId, "--project", root],
        { stdout: "pipe", stderr: "pipe" });
      const output = await new Response(proc.stdout).text();
      await proc.exited;
      return json({ ok: proc.exitCode === 0, output });
    }),
    "/api/projects/:project/sessions/:adw_id/stop": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      const adwId = param(req, "adw_id");
      if (!root) return notFound(`no project ${name}`);
      const proc = Bun.spawn(["sssf", "run", "stop", adwId, "--project", root],
        { stdout: "pipe", stderr: "pipe" });
      const output = await new Response(proc.stdout).text();
      await proc.exited;
      return json({ ok: proc.exitCode === 0, output });
    }),
    "/api/projects/:project/sessions/:adw_id/archive": {
      POST: scoped(archiveHandler),
    },
    "/api/projects/:project/sessions/:adw_id/events": scoped(eventsHandler),
    "/api/projects/:project/sessions/:adw_id/envelopes": scoped(envelopesHandler),
    "/api/projects/:project/sessions/:adw_id/gates": scoped(gatesHandler),
    "/api/projects/:project/sessions/:adw_id/agents/:agent/prompts": scoped(promptsHandler),
  },

  fetch(req) {
    const { pathname } = new URL(req.url);
    if (pathname.startsWith("/api/")) return notFound(`no route ${pathname}`);
    return serveStatic(req);
  },
});

console.log(`[sssf] visualizer api  http://localhost:${server.port}`);
if (adhocDb) {
  console.log(`[sssf] db              ${adhocDb.path}  [journal_mode=${adhocDb.journalMode}]`);
} else {
  console.log(`[sssf] registry        ${projects.path}  (${projects.list().length} project(s))`);
}
console.log(
  existsSync(DIST_DIR)
    ? `[sssf] serving ui from  ${DIST_DIR}`
    : `[sssf] no ./dist — use "bun run dev" for the Vite dev server on :4601`,
);

process.on("SIGINT", () => {
  adhocDb?.close();
  for (const db of projectDbs.values()) db.close();
  process.exit(0);
});

// ── automatic archival sweep (review triage): on boot, then every 6h ───────
function runSweep(): void {
  for (const r of sweepAll(projects, adhocDb?.path ?? null)) {
    if (r.error) console.log(`[sssf] sweep ${r.project}: ${r.error}`);
    else if (r.archived > 0) console.log(`[sssf] sweep ${r.project}: archived ${r.archived} session(s)`);
  }
}
runSweep();
setInterval(runSweep, 6 * 60 * 60 * 1000);
