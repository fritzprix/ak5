import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  authMeIndicatesSession,
  isPublicDocsEnabled,
  isPublicPath,
  looksLikeSessionCookie,
} from "./middlewareGate";

describe("isPublicDocsEnabled", () => {
  it("defaults off", () => {
    assert.equal(isPublicDocsEnabled({}), false);
  });

  it("accepts truthy flags", () => {
    assert.equal(isPublicDocsEnabled({ AK5_PUBLIC_DOCS: "1" }), true);
    assert.equal(isPublicDocsEnabled({ AK5_PUBLIC_DOCS: "true" }), true);
  });
});

describe("isPublicPath", () => {
  it("keeps auth + login public", () => {
    assert.equal(isPublicPath("/login", { publicDocs: false }), true);
    assert.equal(isPublicPath("/auth/me", { publicDocs: false }), true);
    assert.equal(isPublicPath("/device", { publicDocs: false }), true);
  });

  it("gates docs and openapi by default", () => {
    assert.equal(isPublicPath("/docs", { publicDocs: false }), false);
    assert.equal(isPublicPath("/docs/oauth2-redirect", { publicDocs: false }), false);
    assert.equal(isPublicPath("/openapi.json", { publicDocs: false }), false);
  });

  it("allows docs when AK5_PUBLIC_DOCS is on", () => {
    assert.equal(isPublicPath("/docs", { publicDocs: true }), true);
    assert.equal(isPublicPath("/docs/oauth2-redirect", { publicDocs: true }), true);
    assert.equal(isPublicPath("/openapi.json", { publicDocs: true }), true);
  });

  it("still protects dashboard paths", () => {
    assert.equal(isPublicPath("/", { publicDocs: false }), false);
    assert.equal(isPublicPath("/board/inbox", { publicDocs: false }), false);
  });
});

describe("looksLikeSessionCookie", () => {
  it("rejects empty or garbage", () => {
    assert.equal(looksLikeSessionCookie(undefined), false);
    assert.equal(looksLikeSessionCookie(""), false);
    assert.equal(looksLikeSessionCookie("no-dot"), false);
    assert.equal(looksLikeSessionCookie(".only-token"), false);
    assert.equal(looksLikeSessionCookie("id-only."), false);
  });

  it("accepts session_id.token shape", () => {
    assert.equal(looksLikeSessionCookie("sess123.abc"), true);
  });
});

describe("authMeIndicatesSession", () => {
  it("requires authenticated true", () => {
    assert.equal(authMeIndicatesSession({ authenticated: true }), true);
    assert.equal(authMeIndicatesSession({ authenticated: false }), false);
    assert.equal(authMeIndicatesSession(null), false);
  });
});
