import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  type CachedAuthToken,
  TOKEN_REFRESH_SKEW_MS,
  expiresAtFromNow,
  fetchWithAuthRetry,
  isCachedTokenUsable,
} from "./authToken";

describe("expiresAtFromNow", () => {
  it("converts expires_in seconds to epoch ms", () => {
    assert.equal(expiresAtFromNow(900, 1_000), 1_000 + 900_000);
  });

  it("returns null when expires_in is missing", () => {
    assert.equal(expiresAtFromNow(undefined, 1_000), null);
  });
});

describe("isCachedTokenUsable", () => {
  const entry: CachedAuthToken = {
    token: "jwt-1",
    workspaceId: "ws_a",
    expiresAt: 1_000_000,
  };

  it("reuses a fresh token for the same workspace", () => {
    assert.equal(isCachedTokenUsable(entry, "ws_a", 0), true);
    assert.equal(isCachedTokenUsable(entry, null, 0), true);
  });

  it("re-mints when the workspace changes", () => {
    assert.equal(isCachedTokenUsable(entry, "ws_b", 0), false);
  });

  it("re-mints within the refresh skew before exp", () => {
    const now = entry.expiresAt! - TOKEN_REFRESH_SKEW_MS;
    assert.equal(isCachedTokenUsable(entry, "ws_a", now - 1), true);
    assert.equal(isCachedTokenUsable(entry, "ws_a", now), false);
  });

  it("keeps a token without expiry until a 401 invalidates it", () => {
    assert.equal(
      isCachedTokenUsable({ ...entry, expiresAt: null }, "ws_a", Number.MAX_SAFE_INTEGER),
      true
    );
  });
});

describe("fetchWithAuthRetry", () => {
  /** Dashboard left open past TTL: server rejects the stale JWT once. */
  function harness() {
    let minted = 0;
    let cached: string | null = "stale";
    const sent: string[] = [];
    const getToken = async () => {
      if (!cached) {
        minted += 1;
        cached = `fresh-${minted}`;
      }
      return cached;
    };
    const invalidate = () => {
      cached = null;
    };
    const send = async (token: string) => {
      sent.push(token);
      return new Response(null, { status: token === "stale" ? 401 : 200 });
    };
    return { send, getToken, invalidate, sent, minted: () => minted };
  }

  it("on 401 clears the cache, mints again, and retries once", async () => {
    const h = harness();
    const res = await fetchWithAuthRetry(h.send, h.getToken, h.invalidate);
    assert.equal(res.status, 200);
    assert.deepEqual(h.sent, ["stale", "fresh-1"]);
    assert.equal(h.minted(), 1);
  });

  it("does not retry non-401 responses", async () => {
    const sent: string[] = [];
    const res = await fetchWithAuthRetry(
      async (token) => {
        sent.push(token);
        return new Response(null, { status: 403 });
      },
      async () => "tok",
      () => assert.fail("must not invalidate")
    );
    assert.equal(res.status, 403);
    assert.deepEqual(sent, ["tok"]);
  });

  it("retries at most once when the fresh token is also rejected", async () => {
    let calls = 0;
    const res = await fetchWithAuthRetry(
      async () => {
        calls += 1;
        return new Response(null, { status: 401 });
      },
      async () => "tok",
      () => {}
    );
    assert.equal(res.status, 401);
    assert.equal(calls, 2);
  });
});
