import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { AK5Client, AK5Error } from "../dist/index.mjs";

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

describe("X-Request-ID on errors (#113)", () => {
  it("exposes the server request id on an error envelope", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) return Response.json(META);
      return Response.json(
        { error: { code: "TICKET_BLOCKED", message: "blocked", details: {} } },
        { status: 423, headers: { "X-Request-ID": "req_0123456789abcdef" } }
      );
    });
    const client = new AK5Client({ token: "tok" });
    await assert.rejects(() => client.claimTicket("t1"), (err) => {
      assert.ok(err instanceof AK5Error);
      assert.equal(err.requestId, "req_0123456789abcdef");
      return true;
    });
  });

  it("also keeps it when the body is not an error envelope", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) return Response.json(META);
      return new Response("upstream exploded", {
        status: 502,
        headers: { "X-Request-ID": "req_feedfacecafebeef" },
      });
    });
    const client = new AK5Client({ token: "tok" });
    await assert.rejects(() => client.claimTicket("t1"), (err) => {
      assert.equal(err.code, "HTTP_ERROR");
      assert.equal(err.requestId, "req_feedfacecafebeef");
      return true;
    });
  });

  it("leaves requestId undefined when the server sent none", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) return Response.json(META);
      return Response.json(
        { error: { code: "NOT_FOUND", message: "nope", details: {} } },
        { status: 404 }
      );
    });
    const client = new AK5Client({ token: "tok" });
    await assert.rejects(() => client.claimTicket("t1"), (err) => {
      assert.equal(err.requestId, undefined);
      return true;
    });
  });
});
