import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  CSRF_HEADER,
  mapOauthDeviceRequest,
  readCsrfToken,
  withCsrfHeaders,
} from "./webSession";

describe("readCsrfToken", () => {
  it("parses ak5_csrf from a cookie header", () => {
    assert.equal(readCsrfToken("foo=1; ak5_csrf=abc%2Bdef; bar=2"), "abc+def");
  });

  it("returns empty when missing", () => {
    assert.equal(readCsrfToken("session=x"), "");
  });
});

describe("withCsrfHeaders", () => {
  it("adds X-CSRF-Token when csrf provided", () => {
    const headers = withCsrfHeaders({ "Content-Type": "application/json" }, "tok");
    assert.equal(headers[CSRF_HEADER], "tok");
    assert.equal(headers["Content-Type"], "application/json");
  });
});

describe("mapOauthDeviceRequest", () => {
  it("maps agent grant fields into DeviceRequestView shape", () => {
    const view = mapOauthDeviceRequest({
      user_code: "ABCD-EFGH",
      principal: "agent",
      status: "pending",
      requested_name: "slice-worker",
      requested_role: "worker",
      requested_capabilities: '["py"]',
      requested_board_scope: ["proj-a"],
    });
    assert.equal(view.user_code, "ABCD-EFGH");
    assert.equal(view.actor_type, "agent");
    assert.equal(view.name, "slice-worker");
    assert.equal(view.role, "worker");
    assert.deepEqual(view.capabilities, ["py"]);
    assert.deepEqual(view.target_board_ids, ["proj-a"]);
  });

  it("maps user principal defaults", () => {
    const view = mapOauthDeviceRequest({
      user_code: "WXYZ-1234",
      principal: "user",
      status: "pending",
    });
    assert.equal(view.actor_type, "human");
    assert.equal(view.role, "human");
  });
});
