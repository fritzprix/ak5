/**
 * Error codes, HTTP/exit mappings, and typed API errors
 * (workflow.md §7, events.md §5, cli.md §3.1 / ak5.contracts.errors).
 */

export const ErrorCode = {
  // workflow.md §7
  INVALID_TRANSITION: "INVALID_TRANSITION",
  ROLE_FORBIDDEN: "ROLE_FORBIDDEN",
  SEPARATION_OF_DUTIES: "SEPARATION_OF_DUTIES",
  CLAIM_CONFLICT: "CLAIM_CONFLICT",
  STALE_CLAIM: "STALE_CLAIM",
  TICKET_BLOCKED: "TICKET_BLOCKED",
  WIP_LIMIT: "WIP_LIMIT",
  PAYLOAD_INVALID: "PAYLOAD_INVALID",
  SYSTEM_BOARD: "SYSTEM_BOARD",

  // events.md §5
  CURSOR_EXPIRED: "CURSOR_EXPIRED",
  CONSUMER_CONFLICT: "CONSUMER_CONFLICT",
  SUBSCRIPTION_NOT_FOUND: "SUBSCRIPTION_NOT_FOUND",

  // cli.md §2 / §3.1
  VERSION_MISMATCH: "VERSION_MISMATCH",

  // operations.md §2.4 — retryable, honour `Retry-After` (AK5Error.retryAfter)
  RATE_LIMITED: "RATE_LIMITED",
} as const;

export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

export const HTTP_STATUS: Record<ErrorCode, number> = {
  [ErrorCode.INVALID_TRANSITION]: 409,
  [ErrorCode.ROLE_FORBIDDEN]: 403,
  [ErrorCode.SEPARATION_OF_DUTIES]: 403,
  [ErrorCode.CLAIM_CONFLICT]: 409,
  [ErrorCode.STALE_CLAIM]: 409,
  [ErrorCode.TICKET_BLOCKED]: 423,
  [ErrorCode.WIP_LIMIT]: 409,
  [ErrorCode.PAYLOAD_INVALID]: 422,
  [ErrorCode.SYSTEM_BOARD]: 403,
  [ErrorCode.CURSOR_EXPIRED]: 410,
  [ErrorCode.CONSUMER_CONFLICT]: 409,
  [ErrorCode.SUBSCRIPTION_NOT_FOUND]: 404,
  [ErrorCode.VERSION_MISMATCH]: 409,
  [ErrorCode.RATE_LIMITED]: 429,
};

export const EXIT_CODE: Record<ErrorCode, number> = {
  [ErrorCode.PAYLOAD_INVALID]: 2,
  [ErrorCode.CLAIM_CONFLICT]: 3,
  [ErrorCode.STALE_CLAIM]: 3,
  [ErrorCode.CONSUMER_CONFLICT]: 3,
  [ErrorCode.INVALID_TRANSITION]: 4,
  [ErrorCode.ROLE_FORBIDDEN]: 4,
  [ErrorCode.SEPARATION_OF_DUTIES]: 4,
  [ErrorCode.SYSTEM_BOARD]: 4,
  [ErrorCode.WIP_LIMIT]: 4,
  [ErrorCode.TICKET_BLOCKED]: 5,
  [ErrorCode.VERSION_MISMATCH]: 7,
  [ErrorCode.RATE_LIMITED]: 3,
  [ErrorCode.CURSOR_EXPIRED]: 1,
  [ErrorCode.SUBSCRIPTION_NOT_FOUND]: 1,
};

export interface ErrorDetail {
  code: ErrorCode | string;
  message: string;
  details: Record<string, unknown>;
}

export interface ErrorEnvelope {
  error: ErrorDetail;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isErrorCode(value: string): value is ErrorCode {
  return Object.values(ErrorCode).includes(value as ErrorCode);
}

/** Parse ``{"error":{"code","message","details"}}`` from a response body. */
export function parseErrorEnvelope(body: unknown): ErrorDetail | null {
  if (!isRecord(body)) return null;
  const err = body.error;
  if (!isRecord(err)) return null;
  const code = err.code;
  const message = err.message;
  if (typeof code !== "string" || typeof message !== "string") return null;
  const details = isRecord(err.details) ? err.details : {};
  return { code, message, details };
}

export class AK5Error extends Error {
  readonly code: ErrorCode | string;
  readonly details: Record<string, unknown>;
  readonly status: number;
  readonly exitCode: number;
  readonly body: unknown;
  /** Seconds to wait before retrying (`Retry-After`), when the server asked for it. */
  readonly retryAfter?: number;
  /** Server `X-Request-ID` of the failed call — quote it when asking for support. */
  readonly requestId?: string;

  constructor(
    code: ErrorCode | string,
    message: string,
    options?: {
      details?: Record<string, unknown>;
      status?: number;
      body?: unknown;
      retryAfter?: number;
      requestId?: string;
    }
  ) {
    super(message);
    this.name = "AK5Error";
    this.code = code;
    this.details = options?.details ?? {};
    this.status =
      options?.status ??
      (isErrorCode(code) ? HTTP_STATUS[code] : 500);
    this.exitCode = isErrorCode(code) ? EXIT_CODE[code] : 1;
    this.body = options?.body;
    this.retryAfter = options?.retryAfter;
    this.requestId = options?.requestId;
  }

  static fromResponse(
    status: number,
    body: unknown,
    statusText?: string,
    requestId?: string,
    retryAfter?: number
  ): AK5Error {
    const parsed = parseErrorEnvelope(body);
    if (parsed) {
      return new AK5Error(parsed.code, parsed.message, {
        details: parsed.details,
        status,
        body,
        requestId,
        retryAfter,
      });
    }
    const fallback =
      typeof body === "string" && body.length > 0
        ? body
        : statusText || `HTTP ${status}`;
    return new AK5Error("HTTP_ERROR", fallback, { status, body, requestId, retryAfter });
  }
}

/** Compare semver-ish ``X.Y.Z`` strings; returns negative if a < b. */
export function compareSemver(a: string, b: string): number {
  const pa = a.split(".").map((p) => Number.parseInt(p, 10) || 0);
  const pb = b.split(".").map((p) => Number.parseInt(p, 10) || 0);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const da = pa[i] ?? 0;
    const db = pb[i] ?? 0;
    if (da !== db) return da - db;
  }
  return 0;
}

/** `Retry-After` as whole seconds (delta-seconds form only); `undefined` if absent or unusable. */
export function parseRetryAfter(value: string | null | undefined): number | undefined {
  if (!value || !/^\d{1,6}$/.test(value.trim())) return undefined;
  return Number.parseInt(value.trim(), 10);
}
