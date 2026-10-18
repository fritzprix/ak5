/** fetch-based Server-Sent Events with a Bearer header (#81).
 *
 * ``EventSource`` cannot send headers, which forced the JWT into the URL
 * (access logs, history). This client reads ``text/event-stream`` from
 * ``fetch`` instead, so the token travels in ``Authorization`` and a 401
 * re-mints it through :func:`fetchWithAuthRetry`.
 */
import { fetchWithAuthRetry } from "./authToken";

export interface SseMessage {
  event: string;
  data: string;
  id: string | null;
}

/** Incremental ``text/event-stream`` parser (WHATWG HTML §9.2.6, minus ``retry``). */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id: string | null = null;

  /** Feed a decoded chunk; returns the messages completed by it. */
  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) !== -1) {
      const line = this.buffer.slice(0, newline);
      const sep = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      // A lone trailing "\r" may be the first half of "\r\n": wait for more input.
      if (sep === 1 && this.buffer[newline] === "\r" && newline === this.buffer.length - 1) break;
      this.buffer = this.buffer.slice(newline + sep);
      const message = this.line(line);
      if (message) out.push(message);
    }
    return out;
  }

  private line(line: string): SseMessage | null {
    if (line === "") {
      if (this.data.length === 0) {
        this.event = "";
        return null;
      }
      const message = { event: this.event || "message", data: this.data.join("\n"), id: this.id };
      this.event = "";
      this.data = [];
      return message;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    else if (field === "id" && !value.includes("\0")) this.id = value;
    return null;
  }
}

export interface StreamSseOptions {
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  getToken: () => Promise<string>;
  invalidate: () => void;
  onMessage: (message: SseMessage) => void;
  onOpen?: () => void;
  lastEventId?: string | null;
  signal?: AbortSignal;
}

/**
 * Hold one SSE connection open until the server ends it or ``signal`` aborts.
 * Throws on a non-OK response (after one 401 re-mint) so the caller can back off.
 */
export async function streamSse(url: string, opts: StreamSseOptions): Promise<void> {
  const res = await fetchWithAuthRetry(
    (token) => {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (token) headers.Authorization = `Bearer ${token}`;
      if (opts.lastEventId) headers["Last-Event-ID"] = opts.lastEventId;
      return opts.fetch(url, { headers, cache: "no-store", signal: opts.signal });
    },
    opts.getToken,
    opts.invalidate
  );
  if (!res.ok || !res.body) {
    throw new Error(`SSE connect failed (HTTP ${res.status})`);
  }
  opts.onOpen?.();
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const message of parser.push(decoder.decode(value, { stream: true }))) {
        opts.onMessage(message);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
