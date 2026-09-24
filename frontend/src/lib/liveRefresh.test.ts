import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  decideLiveRefresh,
  shouldFlushDeferredRefresh,
  ticketFetchKey,
} from "./liveRefresh";

describe("decideLiveRefresh", () => {
  it("refreshes when the board is idle", () => {
    assert.equal(decideLiveRefresh(false), "refresh");
  });

  it("defers while dialogs or ticket drawer block the UI", () => {
    assert.equal(decideLiveRefresh(true), "defer");
  });
});

describe("shouldFlushDeferredRefresh", () => {
  it("flushes only after unblock when events were queued", () => {
    assert.equal(shouldFlushDeferredRefresh(true, true), false);
    assert.equal(shouldFlushDeferredRefresh(false, false), false);
    assert.equal(shouldFlushDeferredRefresh(false, true), true);
  });
});

describe("ticketFetchKey", () => {
  it("keys drawer fetches on ticket_id, not object identity", () => {
    const a = { ticket_id: "t_1", title: "A" };
    const b = { ticket_id: "t_1", title: "B" };
    assert.equal(ticketFetchKey(a), ticketFetchKey(b));
    assert.equal(ticketFetchKey(null), null);
  });
});
