import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { listFocusable, preferInitialFocus, trapTabKey } from "./focusTrap";

describe("focusTrap exports", () => {
  it("exposes listFocusable, preferInitialFocus, and trapTabKey", () => {
    assert.equal(typeof listFocusable, "function");
    assert.equal(typeof preferInitialFocus, "function");
    assert.equal(typeof trapTabKey, "function");
  });
});
