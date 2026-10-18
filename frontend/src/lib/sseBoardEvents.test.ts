import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildBoardStreamUrl,
  eventMatchesBoard,
  nextSseBackoffMs,
  reduceSseBoardEvent,
} from "./sseBoardEvents";

describe("reduceSseBoardEvent", () => {
  it("maps CONNECTED / RESYNC / ticket events", () => {
    assert.equal(reduceSseBoardEvent("CONNECTED"), "connected");
    assert.equal(reduceSseBoardEvent("RESYNC"), "resync");
    assert.equal(reduceSseBoardEvent("TICKET_TRANSITIONED"), "refresh");
    assert.equal(reduceSseBoardEvent("HUMAN_ACTION_REQUIRED"), "refresh");
    assert.equal(reduceSseBoardEvent("COMMENT_ADDED"), "refresh");
    assert.equal(reduceSseBoardEvent("UNKNOWN"), "ignore");
  });
});

describe("nextSseBackoffMs", () => {
  it("grows exponentially and caps", () => {
    assert.equal(nextSseBackoffMs(0), 500);
    assert.equal(nextSseBackoffMs(1), 1000);
    assert.equal(nextSseBackoffMs(2), 2000);
    assert.equal(nextSseBackoffMs(10), 15_000);
  });
});

describe("buildBoardStreamUrl", () => {
  it("carries only board_id — no token or cursor in the URL (#81)", () => {
    const url = buildBoardStreamUrl("http://x/api/v1", "board-a");
    assert.equal(url, "http://x/api/v1/events/stream?board_id=board-a");
    assert.ok(!url.includes("access_token"));
  });
});

describe("eventMatchesBoard", () => {
  it("filters by board_id when present", () => {
    assert.equal(eventMatchesBoard({ board_id: "a" }, "a"), true);
    assert.equal(eventMatchesBoard({ board_id: "b" }, "a"), false);
    assert.equal(eventMatchesBoard({ data: { board_id: "a" } }, "a"), true);
    assert.equal(eventMatchesBoard({}, "a"), true);
  });
});
