import { existsSync, readFileSync } from "fs";
import { resolve } from "path";

/** ticketing.yaml with an uncommented providers line → the kanban/status
 *  stages are enabled. v2 layout: adws/config/ticketing.yaml.
 *  Single implementation (audit C1) — tickets.ts and status.ts both use it. */
export function ticketingEnabled(root: string): boolean {
  const path = resolve(root, "adws", "config", "ticketing.yaml");
  if (!existsSync(path)) return false;
  try {
    return readFileSync(path, "utf8")
      .split("\n")
      .some((line) => /^\s*providers\s*:/.test(line));
  } catch {
    return false;
  }
}
