import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import {
  AK5Client,
  AK5Error,
  EXIT_CODE,
  ErrorCode,
  HTTP_STATUS,
  parseRetryAfter,
} from "../dist/index.mjs";

let originalFetch;

function mockFetch(handler) {
  originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    return handler(url, init);
  };
}

afterEach(() => {
  if (originalFetch) {
    globalThis.fetch = originalFetch;
    originalFetch = undefined;
  }
});

const META = { api_version: "2", min_client: "2.0.0", capabilities: ["long_poll"] };

describe("RATE_LIMITED (#114)", () => {
  it("is a retryable 429 with exit code 3", () => {
    assert.equal(ErrorCode.RATE_LIMITED, "RATE_LIMITED");
    assert.equal(HTTP_STATUS[ErrorCode.RATE_LIMITED], 429);
    assert.equal(EXIT_CODE[ErrorCode.RATE_LIMITED], 3);
  });

  it("exposes Retry-After as retryAfter seconds", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) return Response.json(META);
      return Response.json(
        {
          error: {
            code: "RATE_LIMITED",
            message: "Too many requests; retry later.",
            details: { limit: 120, window_seconds: 60, scope: "principal" },
          },
        },
        { status: 429, headers: { "Retry-After": "17", "X-Request-ID": "req_0123456789abcdef" } }
      );
    });
    const client = new AK5Client({ token: "tok" });
    await assert.rejects(() => client.claimTicket("t1"), (err) => {
      assert.ok(err instanceof AK5Error);
      assert.equal(err.code, "RATE_LIMITED");
      assert.equal(err.status, 429);
      assert.equal(err.exitCode, 3);
      assert.equal(err.retryAfter, 17);
      assert.equal(err.details.scope, "principal");
      return true;
    });
  });

  it("parses only the delta-seconds form of Retry-After", () => {
    assert.equal(parseRetryAfter("30"), 30);
    assert.equal(parseRetryAfter(" 5 "), 5);
    assert.equal(parseRetryAfter("Wed, 21 Oct 2026 07:28:00 GMT"), undefined);
    assert.equal(parseRetryAfter("-3"), undefined);
    assert.equal(parseRetryAfter(null), undefined);
    assert.equal(parseRetryAfter(""), undefined);
  });
});

describe("CONNECTION_LIMIT (#115)", () => {
  it("is a retryable 429 with exit code 3", () => {
    assert.equal(ErrorCode.CONNECTION_LIMIT, "CONNECTION_LIMIT");
    assert.equal(HTTP_STATUS[ErrorCode.CONNECTION_LIMIT], 429);
    assert.equal(EXIT_CODE[ErrorCode.CONNECTION_LIMIT], 3);
  });

  it("surfaces typed error with retryAfter and scope details", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) return Response.json(META);
      return Response.json(
        {
          error: {
            code: "CONNECTION_LIMIT",
            message: "Connection limit exceeded for principal; retry later.",
            details: { limit: 5, scope: "principal" },
          },
        },
        { status: 429, headers: { "Retry-After": "5" } }
      );
    });
    const client = new AK5Client({ token: "tok" });
    await assert.rejects(() => client.nextTicket(), (err) => {
      assert.ok(err instanceof AK5Error);
      assert.equal(err.code, "CONNECTION_LIMIT");
      assert.equal(err.status, 429);
      assert.equal(err.exitCode, 3);
      assert.equal(err.retryAfter, 5);
      assert.equal(err.details.scope, "principal");
      return true;
    });
  });
});

