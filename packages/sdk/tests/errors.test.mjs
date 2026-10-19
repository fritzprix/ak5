import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  AK5Error,
  ErrorCode,
  EXIT_CODE,
  HTTP_STATUS,
  compareSemver,
  parseErrorEnvelope,
} from "../dist/index.mjs";

describe("error contracts", () => {
  it("maps workflow and events error codes to HTTP/exit status", () => {
    assert.equal(HTTP_STATUS[ErrorCode.TICKET_BLOCKED], 423);
    assert.equal(HTTP_STATUS[ErrorCode.CURSOR_EXPIRED], 410);
    assert.equal(HTTP_STATUS[ErrorCode.QUOTA_EXCEEDED], 403);
    assert.equal(EXIT_CODE[ErrorCode.CLAIM_CONFLICT], 3);
    assert.equal(EXIT_CODE[ErrorCode.VERSION_MISMATCH], 7);
    assert.equal(EXIT_CODE[ErrorCode.TICKET_BLOCKED], 5);
    assert.equal(EXIT_CODE[ErrorCode.QUOTA_EXCEEDED], 4);
  });

  it("parses error envelopes from API bodies", () => {
    const detail = parseErrorEnvelope({
      error: {
        code: "STALE_CLAIM",
        message: "lease expired",
        details: { ticket_id: "t1" },
      },
    });
    assert.deepEqual(detail, {
      code: "STALE_CLAIM",
      message: "lease expired",
      details: { ticket_id: "t1" },
    });
    assert.equal(parseErrorEnvelope({ oops: true }), null);
  });

  it("builds AK5Error from typed envelope responses", () => {
    const err = AK5Error.fromResponse(409, {
      error: {
        code: "CLAIM_CONFLICT",
        message: "already claimed",
        details: {},
      },
    });
    assert.ok(err instanceof AK5Error);
    assert.equal(err.code, "CLAIM_CONFLICT");
    assert.equal(err.status, 409);
    assert.equal(err.exitCode, 3);
  });

  it("compares semver for version negotiation", () => {
    assert.ok(compareSemver("2.0.0", "2.0.0") === 0);
    assert.ok(compareSemver("1.9.9", "2.0.0") < 0);
    assert.ok(compareSemver("2.1.0", "2.0.0") > 0);
  });
});
