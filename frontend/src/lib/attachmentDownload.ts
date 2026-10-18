/** Attachment download through the authorized fetch path (#72): no token in URLs. */
import { fetchWithAuthRetry } from "./authToken";

export function attachmentUrl(apiBase: string, ticketId: string, attachmentId: string): string {
  return `${apiBase}/tickets/${encodeURIComponent(ticketId)}/attachments/${encodeURIComponent(attachmentId)}`;
}

export interface AttachmentFetchDeps {
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  getToken: () => Promise<string>;
  invalidate: () => void;
}

/** Fetch at click time with a Bearer header so an expired JWT is re-minted (401 → retry). */
export async function fetchAttachmentBlob(url: string, deps: AttachmentFetchDeps): Promise<Blob> {
  const res = await fetchWithAuthRetry(
    (token) =>
      deps.fetch(url, {
        cache: "no-store",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      }),
    deps.getToken,
    deps.invalidate
  );
  if (!res.ok) {
    throw new Error(`Download failed (HTTP ${res.status})`);
  }
  return res.blob();
}

/** Hand a blob to the browser as a file download (browser only). */
export function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoke after the click has been handled so the download can start.
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
