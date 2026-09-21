/**
 * GitLab MR watcher client (issue #98) — the monitor's one question per MR:
 * is the pipeline green/failed and has the MR merged? One REST call answers
 * both (`head_pipeline.status` + `state`). Read-only; the token travels in a
 * PRIVATE-TOKEN header, never in the URL.
 */
export type MrPipeline = "none" | "running" | "success" | "failed";
export type MrState = "open" | "merged" | "closed";

export interface MrStateResult {
  pipeline: MrPipeline;
  state: MrState;
}

export interface FetchMrStateOptions {
  gitlabUrl: string;
  token: string;
  repo: string;
  iid: string;
}

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

const GITLAB_FETCH: Fetcher = (url, init) => fetch(url, init);

/** True when the MR is open but its pipeline has not settled yet (or never
 * ran): pending/preparing/scheduled all read as "running" — still undecided.
 * Skipped/canceled are deliberate no-ops — nothing to alert on. */
function pipelineOf(status: string | null | undefined): MrPipeline {
  switch (status) {
    case "success":
      return "success";
    case "failed":
      return "failed";
    case "running":
      return "running";
    case "skipped":
    case "canceled":
      return "none";
    case null:
    case undefined:
      return "none";
    default:
      // created, waiting_for_resource, preparing, pending, scheduled, manual
      return "running";
  }
}

function stateOf(state: string | null | undefined): MrState {
  if (state === "merged") return "merged";
  if (state === "opened") return "open";
  return "closed"; // closed, locked, all_merged
}

/**
 * Poll one MR. Returns null on 404 — the MR no longer exists, which the
 * caller treats as "nothing to watch" rather than an error.
 */
export async function fetchMrState(
  opts: FetchMrStateOptions,
  fetcher: Fetcher = GITLAB_FETCH,
): Promise<MrStateResult | null> {
  const url = `${opts.gitlabUrl}/api/v4/projects/${encodeURIComponent(opts.repo)}/merge_requests/${opts.iid}`;
  const res = await fetcher(url, { headers: { "PRIVATE-TOKEN": opts.token } });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`gitlab ${res.status} for ${opts.repo}!${opts.iid}`);
  const body = (await res.json()) as { state?: string; head_pipeline?: { status?: string } | null };
  return { pipeline: pipelineOf(body.head_pipeline?.status), state: stateOf(body.state) };
}
