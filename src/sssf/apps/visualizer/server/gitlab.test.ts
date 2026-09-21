import { describe, expect, test } from "bun:test";
import { fetchMrState } from "./gitlab.ts";

const OPTS = { gitlabUrl: "https://gitlab.example.com", token: "glpat-x", repo: "group/proj", iid: "42" };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("fetchMrState", () => {
  test("maps success pipeline + open state", async () => {
    const mr = await fetchMrState(OPTS, () =>
      Promise.resolve(
        jsonResponse({ state: "opened", head_pipeline: { status: "success" } }),
      ),
    );
    expect(mr).toEqual({ pipeline: "success", state: "open" });
  });

  test("maps failed pipeline + merged state", async () => {
    const mr = await fetchMrState(OPTS, () =>
      Promise.resolve(
        jsonResponse({ state: "merged", head_pipeline: { status: "failed" } }),
      ),
    );
    expect(mr).toEqual({ pipeline: "failed", state: "merged" });
  });

  test("maps running pipeline", async () => {
    const mr = await fetchMrState(OPTS, () =>
      Promise.resolve(
        jsonResponse({ state: "opened", head_pipeline: { status: "running" } }),
      ),
    );
    expect(mr).toEqual({ pipeline: "running", state: "open" });
  });

  test("no head_pipeline reads as none", async () => {
    const mr = await fetchMrState(OPTS, () => Promise.resolve(jsonResponse({ state: "opened" })));
    expect(mr).toEqual({ pipeline: "none", state: "open" });
  });

  test("skipped/canceled pipelines read as none — never alert on them", async () => {
    for (const status of ["skipped", "canceled"]) {
      const mr = await fetchMrState(OPTS, () =>
        Promise.resolve(jsonResponse({ state: "opened", head_pipeline: { status } })),
      );
      expect(mr!.pipeline).toBe("none");
    }
  });

  test("closed state maps to closed", async () => {
    const mr = await fetchMrState(OPTS, () =>
      Promise.resolve(jsonResponse({ state: "closed", head_pipeline: { status: "failed" } })),
    );
    expect(mr).toEqual({ pipeline: "failed", state: "closed" });
  });

  test("404 returns null — the MR is gone, not an error", async () => {
    const mr = await fetchMrState(OPTS, () => Promise.resolve(jsonResponse({ message: "Not Found" }, 404)));
    expect(mr).toBeNull();
  });

  test("sends PRIVATE-TOKEN header and encoded repo path", async () => {
    let seen: { url: string; headers: Headers } | null = null;
    await fetchMrState(OPTS, (url, init) => {
      seen = { url, headers: new Headers(init?.headers) };
      return Promise.resolve(jsonResponse({ state: "opened" }));
    });
    expect(seen!.url).toBe("https://gitlab.example.com/api/v4/projects/group%2Fproj/merge_requests/42");
    expect(seen!.headers.get("PRIVATE-TOKEN")).toBe("glpat-x");
  });
});
