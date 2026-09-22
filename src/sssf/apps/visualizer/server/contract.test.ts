/**
 * The cross-language contract test: a db built by Python's db_schema
 * (apply_schema, the real writer) is served by every SssfDb query, and the
 * reader's MIN_SCHEMA_VERSION equals the writer's stamped user_version.
 * Drift between src/sssf/db_schema.py and the TS reader fails HERE, in CI,
 * not on a user's machine.
 */
import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { MIN_SCHEMA_VERSION, SssfDb } from "./db.ts";
import { spawnSync } from "node:child_process";

const ROOT = join(import.meta.dir, "..", "..", "..", "..", "..");

function python(): string {
  return join(ROOT, ".venv", "bin", "python");
}

/** Build a fresh db with Python's apply_schema and seed one full run. */
function buildContractDb(dir: string): string {
  const path = join(dir, "adws", "data", "sssf.db");
  mkdirSync(dirname(path), { recursive: true });
  const script = `
import json, sqlite3, sys
from sssf import db_schema
conn = sqlite3.connect(${JSON.stringify(path)})
db_schema.apply_schema(conn)
conn.execute("INSERT INTO sessions (adw_id, adw_name, request, status, engineer, started_at, ended_at, total_tokens, total_cost) VALUES (?,?,?,?,?,?,?,?,?)",
  ("r1", "adw_plan", "req1", "success", "eng", "2026-09-21T10:00:00", "2026-09-21T10:30:00", 10, 1.0))
conn.execute("INSERT INTO phases (phase_id, adw_id, seq, name, kind, owner, description, status, attempt, retries, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
  ("p1", "r1", 1, "explore", "agent", "scout", "d", "success", 1, 0, "2026-09-21T10:00:00", "2026-09-21T10:05:00"))
conn.execute("INSERT INTO events (event_id, adw_id, phase_id, type, name, payload_json, tokens, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?)",
  ("evt1", "r1", "p1", "agent_start", "scout", json.dumps({"input": "x"}), 5, "2026-09-21T10:00:00", "2026-09-21T10:05:00"))
conn.execute("INSERT INTO events (event_id, adw_id, phase_id, type, name, payload_json, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?)",
  ("evt2", "r1", "p1", "integration", "merge", "{}", "2026-09-21T10:05:00", "2026-09-21T10:06:00"))
conn.execute("INSERT INTO events (event_id, adw_id, phase_id, type, name, payload_json, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?)",
  ("evt3", "r1", "p1", "agent_end", "scout", json.dumps({"usage": {"input_tokens": 10, "output_tokens": 4}}), "2026-09-21T10:05:00", "2026-09-21T10:06:00"))
conn.execute("INSERT INTO envelopes (envelope_id, adw_id, phase_id, agent, output_type, payload_json, valid, attempt, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
  ("env1", "r1", "p1", "scout", "ScoutOutput", json.dumps({"findings": []}), 1, 1, "2026-09-21T10:05:00"))
conn.execute("INSERT INTO gate_results (adw_id, phase_id, attempt, gate, passed, violations_json, checks_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
  ("r1", "p1", 1, "artifacts_exist", 1, "[]", json.dumps([{"item": "spec.md", "ok": True, "note": ""}]), "2026-09-21T10:05:00"))
conn.execute("INSERT INTO agent_sessions (adw_id, agent, coding_agent, model, color, session_id, context_tokens, context_window, created_at, last_used_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ("r1", "scout", "pi", "gpt-x", "#a78bfa", "sess1", 100, 200, "2026-09-21T10:00:00", "2026-09-21T10:05:00"))
conn.execute("INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin, adw_id) VALUES (?,?,?,?,?,?,?,?,?)",
  ("internal:abc", "internal", "", "T", "done", "implementation", 1, "internal", "r1"))
conn.commit()
print(conn.execute("PRAGMA user_version").fetchone()[0])
conn.close()
`;
  const r = spawnSync(python(), ["-c", script], { cwd: ROOT, encoding: "utf8" });
  if (r.status !== 0)
    throw new Error(
      `python build failed (status ${r.status}): ${r.error?.message ?? r.stderr}`,
    );
  return path;
}

describe("cross-language schema contract", () => {
  test("writer SCHEMA_VERSION == reader MIN_SCHEMA_VERSION", () => {
    const dir = mkdtempSync(join(tmpdir(), "contract-"));
    try {
      const path = buildContractDb(dir);
      const sdb = new SssfDb(path);
      // the writer stamped this; the reader must agree it is current
      expect(sdb.schemaVersion).toBe(MIN_SCHEMA_VERSION);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("every reader query serves the Python-built db", () => {
    const dir = mkdtempSync(join(tmpdir(), "contract-"));
    try {
      const path = buildContractDb(dir);
      const sdb = new SssfDb(path);
      const sessions = sdb.sessions();
      expect(sessions.length).toBeGreaterThan(0);
      const s1 = sessions.find((s) => s.adw_id === "r1")!;
      expect(s1.ticket_id).toBe("internal:abc");

      const detail = sdb.sessionDetail("r1")!;
      expect(detail.session.adw_id).toBe("r1");
      expect(detail.phases.length).toBe(1);
      expect(detail.agents.length).toBe(1);
      expect(detail.agents[0].color).toBe("#a78bfa");

      const page = sdb.events("r1");
      const types = page.events.map((e) => e.type);
      expect(types).toContain("agent_start");
      expect(types).toContain("integration");  // the 11th type the old viz union missed
      expect(page.events.length).toBe(3);

      expect(sdb.envelopes("r1").length).toBe(1);
      const gates = sdb.gates("r1");
      expect(gates.length).toBe(1);
      expect(JSON.parse(gates[0].checks_json!)[0].item).toBe("spec.md");
      expect(sdb.usage("r1").read).toBe(10);
      expect(sdb.sessionCount()).toBe(1);
      expect(sdb.reviewRow("r1")).toBeNull();  // no sandbox_run row seeded
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
