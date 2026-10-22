import { access, lstat, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { spawn, spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const appRoot = dirname(fileURLToPath(import.meta.url));
export const worktreeRoot = resolve(appRoot, "../../../../");
export const prototypeRoot = resolve(appRoot, ".prototype");
export const eveCli = resolve(appRoot, "node_modules/eve/bin/eve.js");

function resolveDockerHost() {
  const inherited = process.env.DOCKER_HOST;
  const result = inherited
    ? { status: 0, stdout: `${inherited}\n`, stderr: "" }
    : spawnSync("docker", ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], {
        encoding: "utf8",
        timeout: 5000,
        env: {
          PATH: process.env.PATH ?? "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
          HOME: process.env.HOME ?? "",
        },
      });
  const endpoint = result.stdout?.trim();
  if (result.status !== 0 || !endpoint?.startsWith("unix://")) {
    throw new Error("The prototype requires an available local Unix-socket Docker endpoint.");
  }
  return endpoint;
}

export async function cleanRuntimeEnvironment(extra = {}) {
  const home = resolve(prototypeRoot, "home");
  const temp = resolve(prototypeRoot, "tmp");
  await mkdir(home, { recursive: true });
  await mkdir(temp, { recursive: true });
  // Expose installed CLI plugins, never the operator's Docker config/auth.
  const dockerConfig = resolve(prototypeRoot, "docker-config");
  const cliPluginsExtraDirs = [];
  for (const directory of [
    "/Applications/Docker.app/Contents/Resources/cli-plugins",
    "/usr/local/lib/docker/cli-plugins",
    "/usr/local/libexec/docker/cli-plugins",
    "/usr/lib/docker/cli-plugins",
    "/usr/libexec/docker/cli-plugins",
    "/opt/homebrew/lib/docker/cli-plugins",
  ]) {
    try {
      await access(resolve(directory, "docker-buildx"));
      cliPluginsExtraDirs.push(directory);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  await mkdir(dockerConfig, { recursive: true });
  await writeFile(resolve(dockerConfig, "config.json"), `${JSON.stringify({ cliPluginsExtraDirs })}\n`, { mode: 0o600 });
  const path = [dirname(process.execPath), process.env.PATH ?? "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"].filter(Boolean).join(":");
  return {
    PATH: path,
    HOME: home,
    TMPDIR: temp,
    TMP: temp,
    TEMP: temp,
    LANG: "C.UTF-8",
    CI: "1",
    NODE_ENV: "production",
    EVE_TELEMETRY_DISABLED: "1",
    DOCKER_HOST: resolveDockerHost(),
    DOCKER_CONFIG: dockerConfig,
    DOCKER_BUILDKIT: "1",
    PROTOTYPE_DB: resolve(process.env.PROTOTYPE_DB ?? `${prototypeRoot}/probe.sqlite`),
    PROTOTYPE_OBSERVATIONS: resolve(prototypeRoot, "fixture-observations.jsonl"),
    PROTOTYPE_SECURITY_AUDIT: resolve(prototypeRoot, "security-audit.jsonl"),
    PROTOTYPE_HOST_CHECKOUT: worktreeRoot,
    ...extra,
  };
}

// Call only before starting this prototype's service. Preserve old evidence,
// but do not let an earlier crashed probe resume in an unrelated test run.
export async function archivePrototypeWorkflowState(runId) {
  if (!/^[a-zA-Z0-9_-]{1,100}$/u.test(runId)) throw new Error("Invalid probe run identity.");
  const source = resolve(appRoot, ".eve/.workflow-data");
  let info;
  try { info = await lstat(source); } catch (error) {
    if (error.code === "ENOENT") return { archived: false };
    throw error;
  }
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("Refusing to move non-directory prototype Workflow state.");
  const history = resolve(prototypeRoot, "workflow-history");
  await mkdir(history, { recursive: true });
  await rename(source, resolve(history, `${runId}-${Date.now()}`));
  return { archived: true, purpose: "fresh state between tests; unchanged state across restarts within a test" };
}

export async function buildEve({ output = true, timeoutMs = 120_000 } = {}) {
  const env = await cleanRuntimeEnvironment();
  const result = await runChild(process.execPath, [eveCli, "build"], { env, output, label: "eve build", timeoutMs });
  const manifest = JSON.parse(await readFile(resolve(appRoot, ".output/.eve/compile/compiled-agent-manifest.json"), "utf8"));
  if (manifest.sandbox?.sourceId !== "sandbox/sandbox.ts" || manifest.sandbox?.providerName !== "docker") {
    throw new Error("Compiled sandbox does not select the authored Docker definition; refusing framework fallback.");
  }
  const allowedTools = ["approved_operation", "replay_operation", "secret_tool_result", "workspace_operation", "workspace_resume_check"];
  const compiledTools = (manifest.tools ?? []).map((tool) => tool.name).sort();
  if (JSON.stringify(compiledTools) !== JSON.stringify([...allowedTools].sort())
    || (manifest.connections ?? []).length > 0
    || (manifest.subagents ?? []).length > 0) {
    throw new Error("Compiled agent exposes an unreviewed default, deferred, native, connection, or subagent capability.");
  }
  return result;
}

export async function startEve({ port = 3217, crashOnce, auth, secretValues = [], output = false } = {}) {
  if (!Array.isArray(secretValues) || secretValues.some((value) => typeof value !== "string" || !/^API_KEY=synthetic_[A-Za-z0-9_-]{16,}$/u.test(value))) {
    throw new Error("Only explicit synthetic canaries may be installed in the prototype security fixture.");
  }
  const extra = { PORT: String(port) };
  if (secretValues.length > 0) extra.PROTOTYPE_SYNTHETIC_CANARIES = JSON.stringify(secretValues);
  if (crashOnce) {
    extra.PROTOTYPE_CRASH_ONCE = crashOnce;
    extra.PROTOTYPE_CRASH_MARKER = resolve(prototypeRoot, `crash-${crashOnce}.json`);
  }
  if (auth) {
    extra.PROTOTYPE_ROUTE_AUTH_USER = auth.username;
    extra.PROTOTYPE_ROUTE_AUTH_PASSWORD = auth.password;
  }
  const env = await cleanRuntimeEnvironment(extra);
  const child = spawn(process.execPath, [eveCli, "start", "--host", "127.0.0.1", "--port", String(port), "--no-schedules"], {
    cwd: appRoot,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let text = "";
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      text += chunk;
      if (text.length > 128_000) text = text.slice(-128_000);
      if (output) process.stdout.write(chunk);
    });
  }
  const exit = new Promise((resolveExit) => child.once("exit", (code, signal) => resolveExit({ code, signal })));
  return { child, exit, get output() { return text; } };
}

function runChild(command, args, { env, output, label, timeoutMs }) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, { cwd: appRoot, env, stdio: ["ignore", "pipe", "pipe"] });
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3000).unref();
    }, timeoutMs);
    timeout.unref();
    let text = "";
    for (const stream of [child.stdout, child.stderr]) {
      stream.setEncoding("utf8");
      stream.on("data", (chunk) => {
        text += chunk;
        if (text.length > 128_000) text = text.slice(-128_000);
        if (output) process.stdout.write(chunk);
      });
    }
    child.once("error", (error) => {
      clearTimeout(timeout);
      rejectRun(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      if (code === 0) resolveRun({ code, signal, output: text });
      else rejectRun(new Error(`${label} exited with code ${code ?? signal}; output: ${text.slice(-4000)}`));
    });
  });
}
