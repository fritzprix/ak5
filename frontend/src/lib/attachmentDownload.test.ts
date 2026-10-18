import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { attachmentUrl, fetchAttachmentBlob } from "./attachmentDownload";

describe("attachmentUrl", () => {
  it("never carries a token in the query string", () => {
    const url = attachmentUrl("/api/v1", "TK-1", "att_1");
    assert.equal(url, "/api/v1/tickets/TK-1/attachments/att_1");
    assert.ok(!url.includes("access_token"));
  });
});

describe("fetchAttachmentBlob", () => {
  it("re-mints on 401 with an expired cached token and retries once", async () => {
    let cached: string | null = "expired";
    let minted = 0;
    const requests: { url: string; auth: string | undefined }[] = [];

    const blob = await fetchAttachmentBlob(attachmentUrl("/api/v1", "TK-1", "att_1"), {
      fetch: async (url, init) => {
        const auth = (init.headers as Record<string, string>).Authorization;
        requests.push({ url, auth });
        if (auth === "Bearer expired") return new Response(null, { status: 401 });
        return new Response("file-bytes", { status: 200 });
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
    });

    assert.equal(await blob.text(), "file-bytes");
    assert.equal(minted, 1);
    assert.deepEqual(
      requests.map((r) => r.auth),
      ["Bearer expired", "Bearer fresh-1"]
    );
    for (const r of requests) assert.ok(!r.url.includes("access_token"));
  });

  it("throws on a non-OK response after the retry", async () => {
    await assert.rejects(
      fetchAttachmentBlob("/api/v1/tickets/TK-1/attachments/missing", {
        fetch: async () => new Response(null, { status: 404 }),
        getToken: async () => "tok",
        invalidate: () => {},
      }),
      /HTTP 404/
    );
  });
});
