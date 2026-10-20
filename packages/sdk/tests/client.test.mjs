import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { AK5Client, AK5Error, API_VERSION, SDK_VERSION } from "../dist/index.mjs";

/** @type {typeof fetch | undefined} */
let originalFetch;

/**
 * @param {(url: string, init?: RequestInit) => Promise<Response> | Response} handler
 */
function mockFetch(handler) {
  originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    return handler(url, init);
  };
}

function jsonResponse(status, body, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

afterEach(() => {
  if (originalFetch) {
    globalThis.fetch = originalFetch;
    originalFetch = undefined;
  }
});

describe("AK5Client v2 API", () => {
  it("exports API version constants aligned with contracts", () => {
    assert.equal(API_VERSION, "2");
    assert.equal(SDK_VERSION, "2.0.0");
  });

  it("resolves /meta against server origin outside /api/v1", async () => {
    /** @type {string[]} */
    const urls = [];
    mockFetch(async (url) => {
      urls.push(url);
      return jsonResponse(200, {
        api_version: "2",
        min_client: "2.0.0",
        capabilities: ["long_poll"],
      });
    });

    const client = new AK5Client({
      baseUrl: "http://example.test:8000/api/v1",
      skipVersionCheck: true,
    });
    assert.equal(client.serverOrigin, "http://example.test:8000");
    const meta = await client.getMeta();
    assert.equal(meta.api_version, "2");
    assert.deepEqual(urls, ["http://example.test:8000/meta"]);
  });

  it("rejects incompatible api_version on ensureCompatible", async () => {
    mockFetch(async () =>
      jsonResponse(200, {
        api_version: "1",
        min_client: "1.0.0",
        capabilities: [],
      })
    );
    const client = new AK5Client({ skipVersionCheck: true });
    await assert.rejects(
      () => client.ensureCompatible(),
      (err) => {
        assert.ok(err instanceof AK5Error);
        assert.equal(err.code, "VERSION_MISMATCH");
        assert.equal(err.exitCode, 7);
        return true;
      }
    );
  });

  it("rejects when clientVersion is below server min_client", async () => {
    mockFetch(async () =>
      jsonResponse(200, {
        api_version: "2",
        min_client: "9.0.0",
        capabilities: ["long_poll"],
      })
    );
    const client = new AK5Client({
      skipVersionCheck: true,
      clientVersion: "2.0.0",
    });
    await assert.rejects(() => client.ensureCompatible(), (err) => {
      assert.ok(err instanceof AK5Error);
      assert.equal(err.code, "VERSION_MISMATCH");
      return true;
    });
  });

  it("returns null from nextTicket on 204", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      assert.match(url, /\/boards\/inbox\/next$/);
      return new Response(null, { status: 204 });
    });

    const client = new AK5Client({
      baseUrl: "http://example.test/api/v1",
      token: "tok",
    });
    const ticket = await client.nextTicket("inbox");
    assert.equal(ticket, null);
  });

  it("claims a ticket and sends optional capabilities + Idempotency-Key", async () => {
    /** @type {{ url: string, init?: RequestInit }[]} */
    const calls = [];
    mockFetch(async (url, init) => {
      calls.push({ url, init });
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      return jsonResponse(200, {
        ticket: { ticket_id: "t1", stage: "in_progress", status: "in_progress" },
        claim_token: "ctok",
        lease_expires_at: "2099-01-01T00:00:00Z",
        revision: null,
        postmortems: [],
        reviews: [],
      });
    });

    const client = new AK5Client({ token: "tok" });
    const result = await client.claimTicket(
      "t1",
      { capabilities: ["python"] },
      { idempotencyKey: "idem-1" }
    );
    assert.equal(result.claim_token, "ctok");

    const claim = calls.find((c) => c.url.includes("/tickets/t1/claim"));
    assert.ok(claim);
    assert.equal(claim.init?.method, "POST");
    const headers = new Headers(claim.init?.headers);
    assert.equal(headers.get("Idempotency-Key"), "idem-1");
    assert.equal(headers.get("Authorization"), "Bearer tok");
    assert.deepEqual(JSON.parse(String(claim.init?.body)), { capabilities: ["python"] });
  });

  it("submits deliverable and routes with plan payload", async () => {
    /** @type {unknown[]} */
    const bodies = [];
    mockFetch(async (url, init) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      bodies.push({ url, body: JSON.parse(String(init?.body)) });
      return jsonResponse(200, { ticket_id: "t1", stage: "agent_review", status: "open" });
    });

    const client = new AK5Client({ token: "tok" });
    await client.submitTicket("t1", {
      claim_token: "ct",
      deliverable: {
        summary: "done",
        artifacts: [{ kind: "commit", ref: "abc" }],
        criteria_self_check: [{ criterion: "c1", pass: true }],
      },
    });
    await client.routeTicket("t1", {
      claim_token: "ct",
      plan: {
        refined_title: "T",
        refined_spec: "S",
        acceptance_criteria: ["a"],
        target: { board_id: "board-1" },
        confidence: 0.9,
      },
    });

    assert.equal(bodies.length, 2);
    assert.match(String(bodies[0].url), /\/submit$/);
    assert.equal(bodies[0].body.deliverable.summary, "done");
    assert.match(String(bodies[1].url), /\/route$/);
    assert.equal(bodies[1].body.plan.target.board_id, "board-1");
  });

  it("posts verdict nested as { claim_token, verdict }", async () => {
    /** @type {unknown} */
    let body;
    mockFetch(async (url, init) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      body = JSON.parse(String(init?.body));
      return jsonResponse(200, { ticket_id: "t1", stage: "human_review", status: "open" });
    });

    const client = new AK5Client({ token: "tok" });
    await client.verdictTicket("t1", {
      claim_token: "ct",
      verdict: {
        decision: "approve",
        reason: "ok",
        findings: [{ criterion: "c1", pass: true }],
      },
    });
    assert.deepEqual(body, {
      claim_token: "ct",
      verdict: {
        decision: "approve",
        reason: "ok",
        findings: [{ criterion: "c1", pass: true }],
      },
    });
  });

  it("fetches ticket history", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      assert.match(url, /\/tickets\/t1\/history$/);
      return jsonResponse(200, {
        ticket_id: "t1",
        revisions: [],
        reviews: [],
        postmortems: [],
        audit: [],
      });
    });
    const client = new AK5Client({ token: "tok" });
    const hist = await client.getTicketHistory("t1");
    assert.equal(hist.ticket_id, "t1");
  });

  it("long-polls subscription events with X-AK5-Consumer and acks cursor", async () => {
    /** @type {{ url: string, headers: Headers }[]} */
    const seen = [];
    mockFetch(async (url, init) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      seen.push({ url, headers: new Headers(init?.headers) });
      if (url.includes("/events?")) {
        return jsonResponse(200, {
          events: [],
          next_cursor: 42,
        });
      }
      if (url.endsWith("/ack")) {
        assert.deepEqual(JSON.parse(String(init?.body)), { cursor: 42 });
        return jsonResponse(200, { ack_cursor: 42 });
      }
      throw new Error(`unexpected ${url}`);
    });

    const client = new AK5Client({ token: "tok" });
    const batch = await client.pollSubscriptionEvents("sub_1", {
      cursor: 10,
      wait: 5,
      limit: 20,
      consumerId: "daemon-a",
    });
    assert.equal(batch.next_cursor, 42);
    assert.deepEqual(batch.events, []);

    const ack = await client.ackSubscription("sub_1", 42, { consumerId: "daemon-a" });
    assert.equal(ack.ack_cursor, 42);

    const poll = seen.find((s) => s.url.includes("/subscriptions/sub_1/events"));
    assert.ok(poll);
    assert.equal(poll.headers.get("X-AK5-Consumer"), "daemon-a");
    assert.match(poll.url, /cursor=10/);
    assert.match(poll.url, /wait=5/);
    assert.match(poll.url, /limit=20/);

    const ackCall = seen.find((s) => s.url.endsWith("/ack"));
    assert.ok(ackCall);
    assert.equal(ackCall.headers.get("X-AK5-Consumer"), "daemon-a");
  });

  it("creates filter-only subscriptions with start hint", async () => {
    /** @type {unknown} */
    let body;
    mockFetch(async (url, init) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      body = JSON.parse(String(init?.body));
      return jsonResponse(201, {
        subscription_id: "sub_x",
        owner: "actor",
        filter: body.filter,
        ack_cursor: 0,
        consumer: null,
        created_at: "2026-01-01T00:00:00Z",
      });
    });

    const client = new AK5Client({ token: "tok" });
    const sub = await client.createSubscription({
      filter: {
        board_id: "inbox",
        events: ["WORK_AVAILABLE"],
        role: "triage",
      },
      start: "earliest",
    });
    assert.equal(sub.subscription_id, "sub_x");
    assert.deepEqual(body, {
      filter: {
        board_id: "inbox",
        events: ["WORK_AVAILABLE"],
        role: "triage",
      },
      start: "earliest",
    });
  });

  it("surfaces typed API errors from {error:{code,message,details}}", async () => {
    mockFetch(async (url) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      return jsonResponse(423, {
        error: {
          code: "TICKET_BLOCKED",
          message: "blocked",
          details: { kind: "escalation" },
        },
      });
    });

    const client = new AK5Client({ token: "tok" });
    await assert.rejects(() => client.claimTicket("t1"), (err) => {
      assert.ok(err instanceof AK5Error);
      assert.equal(err.code, "TICKET_BLOCKED");
      assert.equal(err.status, 423);
      assert.equal(err.exitCode, 5);
      assert.equal(err.details.kind, "escalation");
      return true;
    });
  });

  it("creates inbox tickets without board_id (T1)", async () => {
    /** @type {unknown} */
    let body;
    mockFetch(async (url, init) => {
      if (url.endsWith("/meta")) {
        return jsonResponse(200, {
          api_version: "2",
          min_client: "2.0.0",
          capabilities: ["long_poll"],
        });
      }
      body = JSON.parse(String(init?.body));
      return jsonResponse(201, {
        ticket_id: "t1",
        board_id: "inbox",
        stage: "unclassified",
        status: "open",
      });
    });

    const client = new AK5Client({ token: "tok" });
    const ticket = await client.createTicket({
      title: "Please help",
      original_request: "unclear ask",
    });
    assert.equal(ticket.board_id, "inbox");
    assert.equal(body.board_id, undefined);
    assert.equal(body.original_request, "unclear ask");
  });
});

describe("subscribeToEvents (#81)", () => {
  it("streams with an Authorization header, resumes with Last-Event-ID, and stops on unsubscribe", async () => {
    const calls = [];
    mockFetch(async (url, init) => {
      calls.push({ url, headers: init.headers });
      const body =
        calls.length === 1
          ? 'id: 5\nevent: WORK_AVAILABLE\ndata: {"ticket_id":"TK-1"}\n\n'
          : 'id: 6\nevent: TICKET_CREATED\ndata: {"ticket_id":"TK-2"}\n\n';
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    });
    const client = new AK5Client({ baseUrl: "http://x/api/v1", token: "ak5_agt_tok", skipVersionCheck: true });
    const events = [];
    const stop = client.subscribeToEvents((e) => events.push(e), undefined, { boardId: "b-1" });
    const deadline = Date.now() + 5000;
    while (events.length < 2 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 20));
    stop();

    assert.deepEqual(
      events.map((e) => [e.eventType, e.data.ticket_id, e.id]),
      [
        ["WORK_AVAILABLE", "TK-1", "5"],
        ["TICKET_CREATED", "TK-2", "6"],
      ]
    );
    assert.equal(calls[0].url, "http://x/api/v1/events/stream?board_id=b-1");
    assert.equal(calls[0].headers.Authorization, "Bearer ak5_agt_tok");
    assert.equal(calls[1].headers["Last-Event-ID"], "5");
    for (const c of calls) assert.ok(!c.url.includes("access_token"));
    const n = calls.length;
    await new Promise((r) => setTimeout(r, 1200));
    assert.equal(calls.length, n, "no reconnect after unsubscribe");
  });
});
