import { httpBasic, ForbiddenError } from "eve/channels/auth";
import { defaultEveAuth, eveChannel } from "eve/channels/eve";
import { scanSensitiveValue } from "../../security-guard.mjs";

const MAX_REQUEST_BODY_BYTES = 1_000_000;
const rejectedInput = () => new ForbiddenError({
  code: "sensitive_input_rejected",
  message: "Request rejected by the prototype security boundary.",
});

async function inspectRequestBody(request: Request) {
  if (!request.body) return;
  if (!request.headers.get("content-type")?.toLowerCase().includes("application/json")) throw rejectedInput();
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (declaredLength > MAX_REQUEST_BODY_BYTES) throw rejectedInput();

  const reader = request.clone().body?.getReader();
  if (!reader) return;
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_REQUEST_BODY_BYTES) throw rejectedInput();
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    if (error instanceof ForbiddenError) throw error;
    throw rejectedInput();
  }

  let body: unknown;
  try {
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw rejectedInput();
  }
  const result = scanSensitiveValue(body);
  if (result.knownValue || result.pattern || result.unscannable) throw rejectedInput();
}

const secureAuth = async (request: Request) => {
  const username = process.env.PROTOTYPE_ROUTE_AUTH_USER;
  const password = process.env.PROTOTYPE_ROUTE_AUTH_PASSWORD;
  if (!username || !password) return null;
  const caller = await httpBasic({ username, password }, { realm: "sssf-eve-viability-prototype" })(request);
  if (!caller) return null;
  if (request.method === "POST") await inspectRequestBody(request);
  return caller;
};

export default eveChannel({
  auth: secureAuth,
  uploadPolicy: "disabled",
  async onMessage(ctx, message) {
    const input = { message, clientContext: ctx.eve.clientContext };
    const result = scanSensitiveValue(input);
    if (result.knownValue || result.pattern || result.unscannable) throw rejectedInput();
    return { auth: defaultEveAuth(ctx) };
  },
});
