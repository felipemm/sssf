import { describe, expect, test } from "bun:test";
import { parseHash, hrefFor } from "./router.ts";

describe("router", () => {
  test("cockpit routes", () => {
    expect(parseHash("#/")).toEqual({ cockpit: true, project: null, tab: null, adwId: null, phaseId: null });
    expect(parseHash("#/cockpit").cockpit).toBe(true);
  });

  test("per-project drill-down", () => {
    const r = parseHash("#/p/inkwell");
    expect(r.project).toBe("inkwell");
    expect(r.tab).toBe("status");
    expect(parseHash("#/p/inkwell/board").tab).toBe("board");
    expect(parseHash("#/p/inkwell/sessions").tab).toBe("sessions");
    expect(parseHash("#/p/inkwell/archived").tab).toBe("archived");
    expect(parseHash("#/p/inkwell/s/abc123").adwId).toBe("abc123");
    expect(parseHash("#/p/inkwell/s/abc123").project).toBe("inkwell");
    expect(parseHash("#/p/inkwell/s/abc123/ph2").phaseId).toBe("ph2");
  });

  test("legacy routes survive", () => {
    // old-style trace and tab hashes keep parsing (project resolved by the app)
    expect(parseHash("#/abc123").adwId).toBe("abc123");
    expect(parseHash("#/abc123/ph2").phaseId).toBe("ph2");
    expect(parseHash("#/board").tab).toBe("board");
    expect(parseHash("#/sessions").tab).toBe("sessions");
    expect(parseHash("#/status").tab).toBe("status");
  });

  test("hrefFor round-trips", () => {
    expect(hrefFor({})).toBe("#/");
    expect(hrefFor({ project: "inkwell", tab: "board" })).toBe("#/p/inkwell/board");
    expect(hrefFor({ project: "inkwell" })).toBe("#/p/inkwell");
    expect(hrefFor({ project: "inkwell", adwId: "abc" })).toBe("#/p/inkwell/s/abc");
    expect(hrefFor({ project: "inkwell", adwId: "abc", phaseId: "ph2" })).toBe("#/p/inkwell/s/abc/ph2");
    expect(hrefFor({ adwId: "abc" })).toBe("#/abc");
  });
});
