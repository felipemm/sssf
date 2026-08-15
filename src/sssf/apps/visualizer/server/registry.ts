/** Reads ~/.sssf/projects.json (SSSF_REGISTRY override) — runtime state, not config. */
import { readFileSync } from "fs";
import { homedir } from "os";
import { resolve } from "path";
import { Database } from "bun:sqlite";
import { openReadonly } from "./db.ts";

export interface Project {
  name: string;
  root: string;
  db: string;
  lastRun: string | null;
}

export class ProjectRegistry {
  readonly path: string;
  /** Read-only connections, opened lazily and kept for the process lifetime. */
  private cache = new Map<string, Database>();

  constructor(path?: string) {
    this.path = path ?? process.env.SSSF_REGISTRY ?? resolve(homedir(), ".sssf", "projects.json");
  }

  list(): Project[] {
    try {
      const data = JSON.parse(readFileSync(this.path, "utf8"));
      return (data.projects ?? []).map((p: Project) => ({
        name: p.name,
        root: p.root,
        db: p.db,
        lastRun: p.lastRun ?? null,
      }));
    } catch {
      return [];
    }
  }

  pathFor(name: string): string | null {
    return this.list().find((p) => p.name === name)?.db ?? null;
  }

  /** Open (and cache) a project's trace db read-only; null when absent/unreadable. */
  dbFor(name: string): Database | null {
    const cached = this.cache.get(name);
    if (cached) return cached;
    const path = this.pathFor(name);
    if (!path) return null;
    try {
      const db = openReadonly(path);
      this.cache.set(name, db);
      return db;
    } catch {
      return null;
    }
  }
}
