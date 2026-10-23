import { appendFileSync } from "node:fs";
import { defineHook } from "eve/hooks";

// Audit only: EVE invokes event hooks after delivery and durable recording.
// This handler only cancels after EVE has persisted a sanitized guard error.
const secretLike = /(?:API[_-]?KEY|ACCESS[_-]?TOKEN|SECRET|PASSWORD)\s*[:=]\s*[A-Za-z0-9._-]{16,}/iu;
const guardError = /SENSITIVE_CONTENT_REJECTED|SecretBoundaryError/u;

export default defineHook({
  events: {
    "*": (event, ctx) => {
      const path = process.env.PROTOTYPE_SECURITY_AUDIT;
      const data = JSON.stringify(event.data ?? {});
      const rejectedByGuard = guardError.test(data);
      if (rejectedByGuard && event.type === "action.result") ctx.cancel();
      if (!path || (!rejectedByGuard && !secretLike.test(data))) return;
      appendFileSync(path, `${JSON.stringify({
        sessionId: ctx.session.id,
        eventId: event.meta?.id,
        eventType: event.type,
        suspectedSecret: true,
        guardRejected: rejectedByGuard,
        enforcement: rejectedByGuard ? "cancel-after-sanitized-guard-error" : "audit-only-after-persistence",
      })}\n`, { mode: 0o600 });
    },
  },
});
