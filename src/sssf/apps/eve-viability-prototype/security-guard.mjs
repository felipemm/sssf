const MAX_GUARDED_VALUE_BYTES = 1_000_000;
const MAX_STREAM_PARTS = 16_384;
const MAX_CROSS_CHUNK_TAIL_BYTES = 4_096;
const MAX_PROVIDER_SNAPSHOT_DEPTH = 64;
const MAX_PROVIDER_SNAPSHOT_NODES = 100_000;
const secretPattern = /(?:API[_-]?KEY|ACCESS[_-]?TOKEN|SECRET|PASSWORD)\s*[:=]\s*[A-Za-z0-9._-]{16,}/iu;
const attachmentType = /^(?:file|image|audio|video)$/iu;

export class SecretBoundaryError extends Error {
  constructor() {
    super("Sensitive content was rejected by the prototype security boundary.");
    this.name = "SecretBoundaryError";
    this.code = "SENSITIVE_CONTENT_REJECTED";
  }
}

function knownSecretValues(values = []) {
  if (!Array.isArray(values)) throw new SecretBoundaryError();
  let fromEnvironment = [];
  const configured = process.env.PROTOTYPE_SYNTHETIC_CANARIES;
  if (configured) {
    try {
      fromEnvironment = JSON.parse(configured);
    } catch {
      throw new SecretBoundaryError();
    }
    if (!Array.isArray(fromEnvironment) || fromEnvironment.some((value) => typeof value !== "string" || value.length === 0 || value.length > MAX_CROSS_CHUNK_TAIL_BYTES)) {
      throw new SecretBoundaryError();
    }
  }
  const allValues = [...fromEnvironment, ...values];
  if (allValues.some((value) => typeof value !== "string" || value.length === 0 || value.length > MAX_CROSS_CHUNK_TAIL_BYTES)) {
    throw new SecretBoundaryError();
  }
  return allValues;
}

function hasUnscannableValue(value, seen = new Set()) {
  if (value === null || value === undefined || typeof value !== "object") return false;
  if (value instanceof Error || ArrayBuffer.isView(value) || value instanceof ArrayBuffer
    || (typeof Blob !== "undefined" && value instanceof Blob)) return true;
  if (seen.has(value)) return true;
  seen.add(value);
  if (Array.isArray(value)) return value.some((entry) => hasUnscannableValue(entry, seen));
  for (const [key, entry] of Object.entries(value)) {
    if (key === "type" && typeof entry === "string" && attachmentType.test(entry)) return true;
    if (hasUnscannableValue(entry, seen)) return true;
  }
  return false;
}

function copyProviderData(value, state = { active: new Set(), nodes: 0, bytes: 0 }, depth = 0) {
  if (++state.nodes > MAX_PROVIDER_SNAPSHOT_NODES || depth > MAX_PROVIDER_SNAPSHOT_DEPTH) throw new SecretBoundaryError();
  if (typeof value === "string") {
    state.bytes += Buffer.byteLength(value, "utf8");
    if (state.bytes > MAX_GUARDED_VALUE_BYTES) throw new SecretBoundaryError();
    return value;
  }
  if (value === null || value === undefined || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new SecretBoundaryError();
    return value;
  }
  if (typeof value !== "object") throw new SecretBoundaryError();
  if (state.active.has(value)) throw new SecretBoundaryError();

  const prototype = Object.getPrototypeOf(value);
  if (prototype === Date.prototype) {
    state.active.add(value);
    try {
      const timestamp = Date.prototype.getTime.call(value);
      if (!Number.isFinite(timestamp)) throw new SecretBoundaryError();
      const copy = new Date(timestamp);
      for (const key of Reflect.ownKeys(value)) {
        if (typeof key !== "string") throw new SecretBoundaryError();
        state.bytes += Buffer.byteLength(key, "utf8");
        if (state.bytes > MAX_GUARDED_VALUE_BYTES) throw new SecretBoundaryError();
        const descriptor = Object.getOwnPropertyDescriptor(value, key);
        if (!descriptor?.enumerable || !Object.hasOwn(descriptor, "value")) throw new SecretBoundaryError();
        Object.defineProperty(copy, key, {
          value: copyProviderData(descriptor.value, state, depth + 1),
          enumerable: true,
          configurable: true,
          writable: true,
        });
      }
      return copy;
    } finally {
      state.active.delete(value);
    }
  }
  if (Array.isArray(value)) {
    if (prototype !== Array.prototype) throw new SecretBoundaryError();
    state.active.add(value);
    try {
      const lengthDescriptor = Object.getOwnPropertyDescriptor(value, "length");
      if (!lengthDescriptor || !Object.hasOwn(lengthDescriptor, "value") || !Number.isSafeInteger(lengthDescriptor.value)
        || lengthDescriptor.value > MAX_PROVIDER_SNAPSHOT_NODES) {
        throw new SecretBoundaryError();
      }
      const copy = new Array(lengthDescriptor.value);
      let entries = 0;
      for (const key of Reflect.ownKeys(value)) {
        if (key === "length") continue;
        if (typeof key !== "string" || !/^(?:0|[1-9]\d*)$/u.test(key)) throw new SecretBoundaryError();
        const index = Number(key);
        if (!Number.isSafeInteger(index) || index >= lengthDescriptor.value) throw new SecretBoundaryError();
        const descriptor = Object.getOwnPropertyDescriptor(value, key);
        if (!descriptor?.enumerable || !Object.hasOwn(descriptor, "value")) throw new SecretBoundaryError();
        Object.defineProperty(copy, key, {
          value: copyProviderData(descriptor.value, state, depth + 1),
          enumerable: true,
          configurable: true,
          writable: true,
        });
        entries += 1;
      }
      if (entries !== lengthDescriptor.value) throw new SecretBoundaryError();
      return copy;
    } finally {
      state.active.delete(value);
    }
  }
  if (prototype !== Object.prototype && prototype !== null) throw new SecretBoundaryError();

  state.active.add(value);
  try {
    const copy = {};
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== "string") throw new SecretBoundaryError();
      state.bytes += Buffer.byteLength(key, "utf8");
      if (state.bytes > MAX_GUARDED_VALUE_BYTES) throw new SecretBoundaryError();
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor?.enumerable || !Object.hasOwn(descriptor, "value")) throw new SecretBoundaryError();
      Object.defineProperty(copy, key, {
        value: copyProviderData(descriptor.value, state, depth + 1),
        enumerable: true,
        configurable: true,
        writable: true,
      });
    }
    return copy;
  } finally {
    state.active.delete(value);
  }
}

// AI SDK v3/v4 stream results expose a stream plus optional plain request/response metadata.
// Provider accessors, binary/custom objects, cycles, and oversized snapshots fail closed rather than being forwarded.
function snapshotStreamResult(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new SecretBoundaryError();
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) throw new SecretBoundaryError();

  let stream;
  let hasStream = false;
  let metadataProperties = 0;
  let metadataKeyBytes = 0;
  const metadata = {};
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== "string" || ++metadataProperties > MAX_PROVIDER_SNAPSHOT_NODES) throw new SecretBoundaryError();
    metadataKeyBytes += Buffer.byteLength(key, "utf8");
    if (metadataKeyBytes > MAX_GUARDED_VALUE_BYTES) throw new SecretBoundaryError();
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor?.enumerable || !Object.hasOwn(descriptor, "value")) throw new SecretBoundaryError();
    if (key === "stream") {
      stream = descriptor.value;
      hasStream = true;
    } else {
      Object.defineProperty(metadata, key, {
        value: descriptor.value,
        enumerable: true,
        configurable: true,
        writable: true,
      });
    }
  }
  if (!hasStream) throw new SecretBoundaryError();
  const safeMetadata = copyProviderData(metadata);
  assertSafeValue(safeMetadata);
  return { stream, metadata: safeMetadata };
}

function stringValues(value, output = []) {
  if (typeof value === "string") {
    output.push(value);
  } else if (value instanceof Error) {
    output.push(value.name, value.message, value.stack ?? "");
    if (value.cause !== undefined) stringValues(value.cause, output);
  } else if (Array.isArray(value)) {
    for (const entry of value) stringValues(entry, output);
  } else if (value !== null && typeof value === "object") {
    for (const entry of Object.values(value)) stringValues(entry, output);
  }
  return output;
}

function streamStringFields(value, path = "", output = []) {
  if (typeof value === "string") {
    output.push({ path, value });
  } else if (value instanceof Error) {
    output.push({ path: `${path}.message`, value: value.message }, { path: `${path}.stack`, value: value.stack ?? "" });
    if (value.cause !== undefined) streamStringFields(value.cause, `${path}.cause`, output);
  } else if (Array.isArray(value)) {
    value.forEach((entry, index) => streamStringFields(entry, `${path}[${index}]`, output));
  } else if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      if (key === "type") continue;
      streamStringFields(entry, path ? `${path}.${key}` : key, output);
    }
  }
  return output;
}

function streamPayloadStrings(value, output = []) {
  if (typeof value === "string") {
    output.push(value);
  } else if (Array.isArray(value)) {
    for (const entry of value) streamPayloadStrings(entry, output);
  } else if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      if (["type", "id", "toolCallId", "toolName"].includes(key)) continue;
      streamPayloadStrings(entry, output);
    }
  }
  return output;
}

export function scanSensitiveValue(value, values = []) {
  let serialized;
  try {
    if (hasUnscannableValue(value)) return { knownValue: false, pattern: false, unscannable: true };
    serialized = typeof value === "string" ? value : JSON.stringify(value);
    if (typeof serialized !== "string" || Buffer.byteLength(serialized, "utf8") > MAX_GUARDED_VALUE_BYTES) {
      return { knownValue: false, pattern: false, unscannable: true };
    }
  } catch {
    return { knownValue: false, pattern: false, unscannable: true };
  }
  const combinedText = `${serialized}${stringValues(value).join("")}`;
  const candidates = [serialized, combinedText];
  return {
    knownValue: knownSecretValues(values).some((secret) => candidates.some((candidate) => candidate.includes(secret))),
    pattern: candidates.some((candidate) => secretPattern.test(candidate)),
    unscannable: false,
  };
}

export function assertSafeValue(value) {
  const result = scanSensitiveValue(value);
  if (result.knownValue || result.pattern || result.unscannable) throw new SecretBoundaryError();
  return value;
}

export function guardedToolResult(value) {
  return assertSafeValue(value);
}

export async function guardedToolCall(operation) {
  try {
    return await operation();
  } catch (error) {
    let isBoundaryError = false;
    try { isBoundaryError = error instanceof SecretBoundaryError; } catch { /* Unsupported thrown values are replaced below. */ }
    if (isBoundaryError) throw new SecretBoundaryError();
    const sanitized = new Error("The protected prototype tool failed.");
    sanitized.name = "ProtectedToolError";
    sanitized.code = "PROTECTED_TOOL_FAILED";
    throw sanitized;
  }
}

export const securityModelMiddleware = {
  specificationVersion: "v4",
  async transformParams({ params }) {
    assertSafeValue(params.prompt);
    return params;
  },
  async wrapGenerate({ doGenerate }) {
    let result;
    try {
      result = await doGenerate();
    } catch {
      // Provider exceptions may carry credentials in custom fields, causes, or accessors.
      // Never forward an upstream error object into EVE; preserve only a static boundary failure.
      throw new SecretBoundaryError();
    }
    try {
      result = copyProviderData(result);
      assertSafeValue(result);
      return result;
    } catch {
      throw new SecretBoundaryError();
    }
  },
  async wrapStream({ doStream }) {
    let result;
    try {
      result = await doStream();
    } catch {
      // Do not expose unknown provider exception objects to EVE.
      throw new SecretBoundaryError();
    }

    let providerStream;
    let metadata;
    try {
      ({ stream: providerStream, metadata } = snapshotStreamResult(result));
    } catch {
      throw new SecretBoundaryError();
    }

    let reader;
    try {
      reader = providerStream.getReader();
    } catch {
      throw new SecretBoundaryError();
    }
    const buffered = [];
    let bytes = 0;
    let partCount = 0;
    let aggregateTail = "";
    let contentTail = "";
    const streamFields = new Map();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (++partCount > MAX_STREAM_PARTS) throw new SecretBoundaryError();
        const safeValue = copyProviderData(value);
        const scanned = scanSensitiveValue(safeValue);
        if (scanned.unscannable) throw new SecretBoundaryError();
        const serialized = JSON.stringify(safeValue);
        bytes += Buffer.byteLength(serialized, "utf8");
        if (bytes > MAX_GUARDED_VALUE_BYTES) throw new SecretBoundaryError();

        const serializedWindow = `${aggregateTail}${serialized}`;
        const serializedScan = scanSensitiveValue(serializedWindow);
        if (serializedScan.knownValue || serializedScan.pattern || serializedScan.unscannable) throw new SecretBoundaryError();
        aggregateTail = serializedWindow.slice(-MAX_CROSS_CHUNK_TAIL_BYTES);

        const content = streamPayloadStrings(safeValue).join("");
        const contentWindow = `${contentTail}${content}`;
        const contentScan = scanSensitiveValue(contentWindow);
        if (contentScan.knownValue || contentScan.pattern || contentScan.unscannable) throw new SecretBoundaryError();
        contentTail = contentWindow.slice(-MAX_CROSS_CHUNK_TAIL_BYTES);

        for (const field of streamStringFields(safeValue)) {
          const fieldWindow = `${streamFields.get(field.path) ?? ""}${field.value}`;
          const fieldScan = scanSensitiveValue(fieldWindow);
          if (fieldScan.knownValue || fieldScan.pattern || fieldScan.unscannable) throw new SecretBoundaryError();
          streamFields.set(field.path, fieldWindow.slice(-MAX_CROSS_CHUNK_TAIL_BYTES));
        }
        buffered.push(safeValue);
      }
    } catch {
      try { await reader.cancel(); } catch { /* The original provider failure remains deliberately undisclosed. */ }
      throw new SecretBoundaryError();
    }

    return {
      ...metadata,
      stream: new ReadableStream({
        start(controller) {
          for (const value of buffered) controller.enqueue(value);
          controller.close();
        },
      }),
    };
  },
};

export function containsSecurityGuardError(events) {
  return events.some((event) => {
    const serialized = JSON.stringify(event);
    return serialized.includes("SecretBoundaryError") || serialized.includes("SENSITIVE_CONTENT_REJECTED");
  });
}
