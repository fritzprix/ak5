import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { SseParser, type SseMessage, streamSse } from "./sseFetch";

describe("SseParser", () => {
  it("parses named events, ids, multi-line data, comments, and CRLF", () => {
    const p = new SseParser();
    const msgs = p.push(
      ": keep-alive\r\nid: 7\r\nevent: TICKET_CREATED\r\ndata: {\"a\":1}\r\n\r\n" +
        "data: line1\ndata: line2\n\n"
    );
    assert.deepEqual(msgs, [
      { event: "TICKET_CREATED", data: '{"a":1}', id: "7" },
      { event: "message", data: "line1\nline2", id: "7" },
    ]);
  });

  it("handles messages split across chunks, including a split CRLF", () => {
    const p = new SseParser();
    assert.deepEqual(p.push("event: CONNEC"), []);
    assert.deepEqual(p.push("TED\r"), []);
    assert.deepEqual(p.push("\ndata: x\r\n\r"), []);
    assert.deepEqual(p.push("\n"), [{ event: "CONNECTED", data: "x", id: null }]);
  });

  it("ignores blank-line-only frames", () => {
    assert.deepEqual(new SseParser().push("event: X\n\n"), []);
  });
});

function sseResponse(body: string, status = 200): Response {
  const bytes = new TextEncoder().encode(body);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      // Split into two chunks to exercise incremental decoding.
      controller.enqueue(bytes.slice(0, 5));
      controller.enqueue(bytes.slice(5));
      controller.close();
    },
  });
  return new Response(status === 200 ? stream : null, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("streamSse", () => {
  it("sends the token in Authorization and Last-Event-ID as a header, never in the URL", async () => {
    const seen: { url: string; headers: Record<string, string> }[] = [];
    const got: SseMessage[] = [];
    await streamSse("/api/v1/events/stream?board_id=b", {
      fetch: async (url, init) => {
        seen.push({ url, headers: init.headers as Record<string, string> });
        return sseResponse("id: 3\nevent: WORK_AVAILABLE\ndata: {}\n\n");
      },
      getToken: async () => "jwt-1",
      invalidate: () => {},
      lastEventId: "2",
      onMessage: (m) => got.push(m),
    });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].headers.Authorization, "Bearer jwt-1");
    assert.equal(seen[0].headers["Last-Event-ID"], "2");
    assert.ok(!seen[0].url.includes("access_token"));
    assert.deepEqual(got, [{ event: "WORK_AVAILABLE", data: "{}", id: "3" }]);
  });

  it("keeps reconnecting past token expiry: stale JWT → 401 → re-mint → stream", async () => {
    // Dashboard open past the TTL: the cached token expired between connections.
    let cached: string | null = "expired";
    let minted = 0;
    const auths: string[] = [];
    const opened: number[] = [];
    const deps = {
      fetch: async (_url: string, init: RequestInit) => {
        const auth = (init.headers as Record<string, string>).Authorization;
        auths.push(auth);
        return auth === "Bearer expired" ? sseResponse("", 401) : sseResponse("event: CONNECTED\ndata: {}\n\n");
      },
      getToken: async () => {
        if (!cached) {
          minted += 1;
          cached = `fresh-${minted}`;
        }
        return cached;
      },
      invalidate: () => {
        cached = null;
      },
      onMessage: () => {},
    };
    // First (re)connection after expiry, then a second reconnect reuses the fresh token.
    await streamSse("/s", { ...deps, onOpen: () => opened.push(1) });
    await streamSse("/s", { ...deps, onOpen: () => opened.push(2) });
    assert.deepEqual(auths, ["Bearer expired", "Bearer fresh-1", "Bearer fresh-1"]);
    assert.equal(minted, 1);
    assert.deepEqual(opened, [1, 2]);
  });

  it("throws when the retry still fails so the caller can back off", async () => {
    await assert.rejects(
      streamSse("/s", {
        fetch: async () => sseResponse("", 403),
        getToken: async () => "t",
        invalidate: () => {},
        onMessage: () => {},
      }),
      /HTTP 403/
    );
  });
});
