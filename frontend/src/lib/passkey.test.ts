import assert from "node:assert/strict";
import test from "node:test";

import {
  b64urlToBuffer,
  bufferToB64url,
  describeWebAuthnError,
  isPresenceRequiredMessage,
  toCreationOptions,
  toRequestOptions,
} from "./passkey";

test("base64url round-trips and is url-safe without padding", () => {
  const bytes = new Uint8Array([251, 255, 254, 0, 1, 2, 3]);
  const encoded = bufferToB64url(bytes.buffer);
  assert.equal(encoded, "-__-AAECAw");
  assert.ok(!/[+/=]/.test(encoded));
  assert.deepEqual(new Uint8Array(b64urlToBuffer(encoded)), bytes);
});

test("toCreationOptions decodes challenge, user id and excluded credentials", () => {
  const out = toCreationOptions({
    challenge: bufferToB64url(new Uint8Array([1, 2, 3]).buffer),
    rp: { id: "example.test", name: "AK5" },
    user: {
      id: bufferToB64url(new Uint8Array([9, 9]).buffer),
      name: "a@b.c",
      displayName: "A",
    },
    pubKeyCredParams: [{ type: "public-key", alg: -7 }],
    excludeCredentials: [{ id: bufferToB64url(new Uint8Array([7]).buffer), type: "public-key" }],
  });
  assert.deepEqual(new Uint8Array(out.challenge as ArrayBuffer), new Uint8Array([1, 2, 3]));
  assert.deepEqual(new Uint8Array(out.user.id as ArrayBuffer), new Uint8Array([9, 9]));
  assert.equal(out.excludeCredentials?.length, 1);
  assert.deepEqual(
    new Uint8Array(out.excludeCredentials![0].id as ArrayBuffer),
    new Uint8Array([7])
  );
  assert.equal(out.rp.id, "example.test");
});

test("toRequestOptions decodes allowCredentials and tolerates their absence", () => {
  const withKeys = toRequestOptions({
    challenge: bufferToB64url(new Uint8Array([4]).buffer),
    allowCredentials: [{ id: bufferToB64url(new Uint8Array([5, 6]).buffer), type: "public-key" }],
    userVerification: "required",
  });
  assert.deepEqual(new Uint8Array(withKeys.challenge as ArrayBuffer), new Uint8Array([4]));
  assert.deepEqual(
    new Uint8Array(withKeys.allowCredentials![0].id as ArrayBuffer),
    new Uint8Array([5, 6])
  );
  assert.equal(withKeys.userVerification, "required");

  const without = toRequestOptions({ challenge: bufferToB64url(new Uint8Array([1]).buffer) });
  assert.deepEqual(without.allowCredentials, []);
});

test("isPresenceRequiredMessage matches the API error format only", () => {
  assert.ok(isPresenceRequiredMessage("USER_PRESENCE_REQUIRED: Approving requires a recent passkey"));
  assert.ok(isPresenceRequiredMessage("USER_PRESENCE_REQUIRED"));
  assert.ok(!isPresenceRequiredMessage("ROLE_FORBIDDEN: nope"));
  assert.ok(!isPresenceRequiredMessage("note: USER_PRESENCE_REQUIRED"));
});

test("describeWebAuthnError explains the common failures", () => {
  assert.match(describeWebAuthnError(new DOMException("x", "NotAllowedError")), /cancelled/);
  assert.match(describeWebAuthnError(new DOMException("x", "InvalidStateError")), /already/);
  assert.match(describeWebAuthnError(new DOMException("x", "SecurityError")), /HTTPS|localhost/);
  assert.equal(describeWebAuthnError(new Error("boom")), "boom");
  assert.equal(describeWebAuthnError("weird"), "Passkey operation failed.");
});
