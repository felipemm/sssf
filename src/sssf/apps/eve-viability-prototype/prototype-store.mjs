import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const packageRoot = new URL(".", import.meta.url).pathname;
export const prototypeDirectory = resolve(packageRoot, ".prototype");
export const databasePath = resolve(process.env.PROTOTYPE_DB ?? `${prototypeDirectory}/probe.sqlite`);

mkdirSync(dirname(databasePath), { recursive: true });

function database() {
  const db = new DatabaseSync(databasePath, { timeout: 5000 });
  db.exec("PRAGMA busy_timeout = 5000; PRAGMA journal_mode = WAL;");
  db.exec(`
    CREATE TABLE IF NOT EXISTS executions (
      execution_id TEXT PRIMARY KEY,
      mode TEXT NOT NULL,
      session_id TEXT,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      result_json TEXT,
      artifact_path TEXT,
      blocked_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS operations (
      operation_id TEXT PRIMARY KEY,
      execution_id TEXT NOT NULL,
      invocation_count INTEGER NOT NULL,
      effect_count INTEGER NOT NULL,
      status TEXT NOT NULL,
      effect_path TEXT NOT NULL,
      result_json TEXT,
      updated_at TEXT NOT NULL
    );
  `);
  return db;
}

export function withDatabase(callback) {
  const db = database();
  try {
    return callback(db);
  } finally {
    db.close();
  }
}

export function createExecution({ executionId, mode, status = "accepted", blockedReason = null }) {
  const now = new Date().toISOString();
  withDatabase((db) => {
    db.prepare(`
      INSERT INTO executions (execution_id, mode, status, created_at, updated_at, blocked_reason)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(executionId, mode, status, now, now, blockedReason);
  });
}

export function updateExecution(executionId, values) {
  const fields = [];
  const parameters = [];
  for (const [key, value] of Object.entries(values)) {
    const column = {
      sessionId: "session_id",
      status: "status",
      result: "result_json",
      artifactPath: "artifact_path",
      blockedReason: "blocked_reason",
    }[key];
    if (!column) throw new Error(`Unsupported execution field: ${key}`);
    fields.push(`${column} = ?`);
    parameters.push(key === "result" && value !== null ? JSON.stringify(value) : value);
  }
  fields.push("updated_at = ?");
  parameters.push(new Date().toISOString(), executionId);
  withDatabase((db) => {
    db.prepare(`UPDATE executions SET ${fields.join(", ")} WHERE execution_id = ?`).run(...parameters);
  });
}

export function getExecution(executionId) {
  return withDatabase((db) => db.prepare("SELECT * FROM executions WHERE execution_id = ?").get(executionId));
}

export function getOperation(operationId) {
  return withDatabase((db) => db.prepare("SELECT * FROM operations WHERE operation_id = ?").get(operationId));
}

export function beginOperation({ operationId, executionId, effectPath }) {
  return withDatabase((db) => {
    db.exec("BEGIN IMMEDIATE");
    try {
      const current = db.prepare("SELECT * FROM operations WHERE operation_id = ?").get(operationId);
      if (current) {
        db.prepare("UPDATE operations SET invocation_count = invocation_count + 1, updated_at = ? WHERE operation_id = ?")
          .run(new Date().toISOString(), operationId);
      } else {
        db.prepare(`
          INSERT INTO operations (operation_id, execution_id, invocation_count, effect_count, status, effect_path, updated_at)
          VALUES (?, ?, 1, 0, 'running', ?, ?)
        `).run(operationId, executionId, effectPath, new Date().toISOString());
      }
      const operation = db.prepare("SELECT * FROM operations WHERE operation_id = ?").get(operationId);
      db.exec("COMMIT");
      return operation;
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  });
}

export function completeOperation(operationId, result) {
  withDatabase((db) => {
    db.prepare(`
      UPDATE operations
      SET status = 'completed', effect_count = 1, result_json = ?, updated_at = ?
      WHERE operation_id = ? AND status <> 'completed'
    `).run(JSON.stringify(result), new Date().toISOString(), operationId);
  });
}

export function operationResult(operationId) {
  return getOperation(operationId);
}
