import { AK5Error, compareSemver, parseRetryAfter } from "./errors";
import {
  Actor,
  ActorDiscoveryFilter,
  ActorIdentifyInput,
  AK5ClientOptions,
  BlockInput,
  Board,
  BoardEventPayload,
  BoardSummary,
  BounceInput,
  ClaimInput,
  EventsPollResult,
  HeartbeatInput,
  LongPollEventsResult,
  LongPollOptions,
  MetaResponse,
  PostmortemInput,
  ReleaseInput,
  ReopenInput,
  RequestOptions,
  RerouteInput,
  RouteInput,
  SubmitInput,
  Subscription,
  SubscriptionAckResult,
  SubscriptionCreateInput,
  Ticket,
  TicketClaimResult,
  TicketComment,
  TicketCreateInput,
  TicketDelegateInput,
  TicketHistory,
  TicketMoveInput,
  UnblockInput,
  VerdictInput,
} from "./types";
import { API_VERSION, SDK_VERSION } from "./version";
import { SseParser } from "./sse";

const IDENTIFY_SECRET_HEADER = "X-AK5-Identify-Secret";
const CONSUMER_HEADER = "X-AK5-Consumer";
const IDEMPOTENCY_HEADER = "Idempotency-Key";

export class AK5Client {
  public readonly baseUrl: string;
  public readonly clientVersion: string;
  private token: string | null;
  private actorId: string;
  private actorType: "human" | "agent";
  private actorRole: string;
  private actorCapabilities: string[];
  private identifySecret: string | null;
  private skipVersionCheck: boolean;
  private versionChecked = false;
  private timeoutMs: number | undefined;

  constructor(options?: AK5ClientOptions) {
    this.baseUrl = (options?.baseUrl || "http://127.0.0.1:8000/api/v1").replace(/\/+$/, "");
    this.token = options?.token || null;
    this.actorId = options?.actorId || "sdk_user";
    this.actorType = options?.actorType || "agent";
    this.actorRole = options?.actorRole || "worker";
    this.actorCapabilities = options?.actorCapabilities || ["sdk-client"];
    this.clientVersion = options?.clientVersion || SDK_VERSION;
    this.skipVersionCheck = options?.skipVersionCheck ?? false;
    this.timeoutMs = options?.timeout;
    const envProcess = (
      globalThis as { process?: { env?: Record<string, string | undefined> } }
    ).process;
    const fromEnv = envProcess?.env?.AK5_IDENTIFY_SECRET ?? "";
    this.identifySecret = options?.identifySecret || fromEnv || null;
  }

  /** Server origin for endpoints outside ``/api/vN`` (e.g. ``/meta``). */
  public get serverOrigin(): string {
    const stripped = this.baseUrl.replace(/\/api\/v\d+$/i, "");
    return stripped.length > 0 ? stripped : this.baseUrl;
  }

  private identifyHeaders(): Record<string, string> {
    if (!this.identifySecret) return {};
    return { [IDENTIFY_SECRET_HEADER]: this.identifySecret };
  }

  public setToken(token: string): void {
    this.token = token;
  }

  public async ensureToken(): Promise<string> {
    if (this.token) return this.token;

    const res = await this.rawFetch(`${this.baseUrl}/auth/identify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...this.identifyHeaders(),
      },
      body: JSON.stringify({
        actor_id: this.actorId,
        actor_type: this.actorType,
        name: this.actorId.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
        role: this.actorRole,
        capabilities: this.actorCapabilities,
      }),
    });

    if (!res.ok) {
      throw await this.toError(res);
    }

    const data = (await res.json()) as { access_token: string };
    this.token = data.access_token;
    return this.token;
  }

  private async rawFetch(url: string, init: RequestInit = {}): Promise<Response> {
    if (this.timeoutMs == null || typeof AbortSignal === "undefined" || !("timeout" in AbortSignal)) {
      return fetch(url, init);
    }
    const signal =
      init.signal ??
      (AbortSignal as unknown as { timeout: (ms: number) => AbortSignal }).timeout(this.timeoutMs);
    return fetch(url, { ...init, signal });
  }

  private async toError(res: Response): Promise<AK5Error> {
    let body: unknown = null;
    const text = await res.text();
    if (text) {
      try {
        body = JSON.parse(text) as unknown;
      } catch {
        body = text;
      }
    }
    return AK5Error.fromResponse(
      res.status,
      body,
      res.statusText,
      parseRetryAfter(res.headers.get("retry-after"))
    );
  }

  private buildHeaders(
    extra?: Record<string, string>,
    opts?: RequestOptions & { requireAuth?: boolean; consumerId?: string }
  ): Promise<Record<string, string>> {
    return (async () => {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        ...extra,
      };
      if (opts?.idempotencyKey) {
        headers[IDEMPOTENCY_HEADER] = opts.idempotencyKey;
      }
      if (opts?.consumerId) {
        headers[CONSUMER_HEADER] = opts.consumerId;
      }
      if (opts?.requireAuth || this.token) {
        const token = await this.ensureToken();
        headers["Authorization"] = `Bearer ${token}`;
      }
      return headers;
    })();
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    opts: RequestOptions & {
      requireAuth?: boolean;
      consumerId?: string;
      allowEmpty?: boolean;
      absoluteUrl?: boolean;
    } = {}
  ): Promise<T> {
    if (!this.skipVersionCheck && !this.versionChecked && !opts.absoluteUrl) {
      await this.ensureCompatible();
    }

    const headers = await this.buildHeaders(
      options.headers as Record<string, string> | undefined,
      opts
    );

    const url = opts.absoluteUrl
      ? endpoint
      : `${this.baseUrl}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;

    const res = await this.rawFetch(url, { ...options, headers });

    if (res.status === 204) {
      if (opts.allowEmpty) {
        return null as T;
      }
      throw new AK5Error("HTTP_ERROR", "Unexpected empty response (204)", { status: 204 });
    }

    if (!res.ok) {
      throw await this.toError(res);
    }

    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return null as T;
    }

    const text = await res.text();
    if (!text) {
      return null as T;
    }
    return JSON.parse(text) as T;
  }

  private async postJson<T>(
    endpoint: string,
    body: object,
    opts: RequestOptions & { requireAuth?: boolean } = {}
  ): Promise<T> {
    return this.request<T>(
      endpoint,
      { method: "POST", body: JSON.stringify(body) },
      { requireAuth: true, ...opts }
    );
  }

  // --- Version negotiation (cli.md §2) ---

  public async getMeta(): Promise<MetaResponse> {
    return this.request<MetaResponse>(
      `${this.serverOrigin}/meta`,
      { method: "GET" },
      { absoluteUrl: true, requireAuth: false }
    );
  }

  /**
   * Negotiate with ``GET /meta``. Throws ``AK5Error(VERSION_MISMATCH)`` when
   * the server API major differs or this client is older than ``min_client``.
   */
  public async ensureCompatible(): Promise<MetaResponse> {
    const meta = await this.getMeta();
    this.versionChecked = true;

    if (meta.api_version !== API_VERSION) {
      throw new AK5Error(
        "VERSION_MISMATCH",
        `Server api_version=${meta.api_version} is incompatible with client API_VERSION=${API_VERSION}`,
        {
          details: {
            api_version: meta.api_version,
            expected: API_VERSION,
            min_client: meta.min_client,
            client_version: this.clientVersion,
          },
        }
      );
    }

    if (compareSemver(this.clientVersion, meta.min_client) < 0) {
      throw new AK5Error(
        "VERSION_MISMATCH",
        `Client version ${this.clientVersion} is below server min_client ${meta.min_client}`,
        {
          details: {
            api_version: meta.api_version,
            min_client: meta.min_client,
            client_version: this.clientVersion,
          },
        }
      );
    }

    return meta;
  }

  // --- Auth & Identity ---

  public async identify(
    input: ActorIdentifyInput
  ): Promise<{ access_token: string; actor: Actor }> {
    const res = await this.rawFetch(`${this.baseUrl}/auth/identify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...this.identifyHeaders(),
      },
      body: JSON.stringify(input),
    });

    if (!res.ok) {
      throw await this.toError(res);
    }

    const data = (await res.json()) as { access_token: string; actor: Actor };
    this.token = data.access_token;
    this.actorId = input.actor_id;
    return data;
  }

  // --- Boards ---

  public async listBoards(): Promise<BoardSummary[]> {
    return this.request<BoardSummary[]>("/boards", {}, { requireAuth: true });
  }

  public async getBoard(boardId: string): Promise<Board> {
    return this.request<Board>(`/boards/${encodeURIComponent(boardId)}`, {}, { requireAuth: true });
  }

  /**
   * Oldest unclaimed unblocked ticket for the board (incl. ``inbox``).
   * Returns ``null`` when the server responds 204 (cli.md exit 6).
   */
  public async nextTicket(
    boardId: string,
    options?: { capabilities?: string[] }
  ): Promise<Ticket | null> {
    const params = new URLSearchParams();
    if (options?.capabilities?.length) {
      params.set("capabilities", options.capabilities.join(","));
    }
    const q = params.toString() ? `?${params.toString()}` : "";
    return this.request<Ticket | null>(
      `/boards/${encodeURIComponent(boardId)}/next${q}`,
      {},
      { requireAuth: true, allowEmpty: true }
    );
  }

  // --- Actors & Discovery ---

  public async listActors(actorType?: "human" | "agent"): Promise<Actor[]> {
    const query = actorType ? `?actor_type=${actorType}` : "";
    return this.request<Actor[]>(`/actors${query}`, {}, { requireAuth: true });
  }

  public async discoverAgents(filter?: ActorDiscoveryFilter): Promise<Actor[]> {
    const params = new URLSearchParams();
    if (filter?.capability) params.append("capability", filter.capability);
    if (filter?.status) params.append("status", filter.status);
    if (filter?.query) params.append("query", filter.query);

    const queryStr = params.toString() ? `?${params.toString()}` : "";
    return this.request<Actor[]>(`/actors/discovery${queryStr}`, {}, { requireAuth: true });
  }

  // --- Tickets (read / legacy) ---

  public async getTicket(ticketId: string): Promise<Ticket> {
    return this.request<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}`,
      {},
      { requireAuth: true }
    );
  }

  public async createTicket(input: TicketCreateInput, opts?: RequestOptions): Promise<Ticket> {
    return this.postJson<Ticket>("/tickets", input, opts);
  }

  public async moveTicket(
    ticketId: string,
    input: TicketMoveInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.request<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/move`,
      { method: "PATCH", body: JSON.stringify(input) },
      { requireAuth: true, ...opts }
    );
  }

  /**
   * Create a child ticket via create_direct (pull model).
   * ``target_actor_id`` is accepted for compat but ignored by the server.
   */
  public async delegateSubtask(
    ticketId: string,
    input: TicketDelegateInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/delegate`,
      input,
      opts
    );
  }

  public async addComment(
    ticketId: string,
    content: string,
    isInternal: boolean = false,
    metadata?: Record<string, unknown>,
    opts?: RequestOptions
  ): Promise<TicketComment> {
    return this.postJson<TicketComment>(
      `/tickets/${encodeURIComponent(ticketId)}/comments`,
      {
        content,
        is_internal: isInternal,
        metadata: metadata || {},
      },
      opts
    );
  }

  public async getTicketHistory(ticketId: string): Promise<TicketHistory> {
    return this.request<TicketHistory>(
      `/tickets/${encodeURIComponent(ticketId)}/history`,
      {},
      { requireAuth: true }
    );
  }

  // --- Workflow transition commands (workflow.md §3 / §4) ---

  public async claimTicket(
    ticketId: string,
    input?: ClaimInput,
    opts?: RequestOptions
  ): Promise<TicketClaimResult> {
    return this.postJson<TicketClaimResult>(
      `/tickets/${encodeURIComponent(ticketId)}/claim`,
      input ?? {},
      opts
    );
  }

  public async heartbeatTicket(
    ticketId: string,
    input: HeartbeatInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/heartbeat`,
      input,
      opts
    );
  }

  public async releaseTicket(
    ticketId: string,
    input: ReleaseInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/release`,
      input,
      opts
    );
  }

  public async submitTicket(
    ticketId: string,
    input: SubmitInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/submit`,
      input,
      opts
    );
  }

  public async bounceTicket(
    ticketId: string,
    input: BounceInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/bounce`,
      input,
      opts
    );
  }

  public async routeTicket(
    ticketId: string,
    input: RouteInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/route`,
      input,
      opts
    );
  }

  public async verdictTicket(
    ticketId: string,
    input: VerdictInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/verdict`,
      input,
      opts
    );
  }

  public async postmortemTicket(
    ticketId: string,
    input: PostmortemInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/postmortem`,
      input,
      opts
    );
  }

  public async blockTicket(
    ticketId: string,
    input: BlockInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/block`,
      input,
      opts
    );
  }

  public async unblockTicket(
    ticketId: string,
    input?: UnblockInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/unblock`,
      input ?? {},
      opts
    );
  }

  public async reopenTicket(
    ticketId: string,
    input: ReopenInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/reopen`,
      input,
      opts
    );
  }

  public async rerouteTicket(
    ticketId: string,
    input: RerouteInput,
    opts?: RequestOptions
  ): Promise<Ticket> {
    return this.postJson<Ticket>(
      `/tickets/${encodeURIComponent(ticketId)}/reroute`,
      input,
      opts
    );
  }

  // --- Subscriptions & long-poll (events.md §2–§3) ---

  public async createSubscription(
    input: SubscriptionCreateInput = {},
    opts?: RequestOptions
  ): Promise<Subscription> {
    const body: {
      subscription_id?: string;
      filter: NonNullable<SubscriptionCreateInput["filter"]> | Record<string, never>;
      start: "now" | "earliest";
    } = {
      filter: input.filter ?? {},
      start: input.start ?? "now",
    };
    if (input.subscription_id) {
      body.subscription_id = input.subscription_id;
    }
    return this.postJson<Subscription>("/subscriptions", body, opts);
  }

  public async listSubscriptions(boardId?: string): Promise<Subscription[]> {
    const q = boardId ? `?board_id=${encodeURIComponent(boardId)}` : "";
    return this.request<Subscription[]>(`/subscriptions${q}`, {}, { requireAuth: true });
  }

  public async getSubscription(subscriptionId: string): Promise<Subscription> {
    return this.request<Subscription>(
      `/subscriptions/${encodeURIComponent(subscriptionId)}`,
      {},
      { requireAuth: true }
    );
  }

  public async deleteSubscription(subscriptionId: string): Promise<{ status: string; subscription_id: string }> {
    return this.request<{ status: string; subscription_id: string }>(
      `/subscriptions/${encodeURIComponent(subscriptionId)}`,
      { method: "DELETE" },
      { requireAuth: true }
    );
  }

  /**
   * Long-poll subscription events. Always use returned ``next_cursor`` for the
   * next call / ack — the cursor may advance even when ``events`` is empty.
   */
  public async pollSubscriptionEvents(
    subscriptionId: string,
    options: LongPollOptions
  ): Promise<LongPollEventsResult> {
    const params = new URLSearchParams();
    if (options.cursor != null) params.set("cursor", String(options.cursor));
    if (options.wait != null) params.set("wait", String(options.wait));
    if (options.limit != null) params.set("limit", String(options.limit));
    const q = params.toString() ? `?${params.toString()}` : "";
    return this.request<LongPollEventsResult>(
      `/subscriptions/${encodeURIComponent(subscriptionId)}/events${q}`,
      {},
      { requireAuth: true, consumerId: options.consumerId }
    );
  }

  public async ackSubscription(
    subscriptionId: string,
    cursor: number,
    options?: { consumerId?: string }
  ): Promise<SubscriptionAckResult> {
    return this.request<SubscriptionAckResult>(
      `/subscriptions/${encodeURIComponent(subscriptionId)}/ack`,
      { method: "POST", body: JSON.stringify({ cursor }) },
      { requireAuth: true, consumerId: options?.consumerId }
    );
  }

  /** One-shot event log poll without a subscription (cli.md ``events poll``). */
  public async pollEvents(options?: {
    boardId?: string;
    since?: number;
    limit?: number;
  }): Promise<EventsPollResult> {
    const params = new URLSearchParams();
    if (options?.boardId) params.set("board_id", options.boardId);
    if (options?.since != null) params.set("since", String(options.since));
    if (options?.limit != null) params.set("limit", String(options.limit));
    const q = params.toString() ? `?${params.toString()}` : "";
    return this.request<EventsPollResult>(`/events${q}`, {}, { requireAuth: true });
  }

  // --- Real-Time Events (SSE, human observation — events.md §6) ---

  /**
   * Subscribe to the SSE stream for observation (events.md §6).
   * Prefer long-poll subscriptions for agent daemons. Returns an unsubscribe function.
   *
   * Uses ``fetch`` with an ``Authorization`` header (no token in the URL, #81) and
   * reconnects with backoff, resuming from the last seen event id.
   */
  public subscribeToEvents(
    onEvent: (event: BoardEventPayload) => void,
    onError?: (err: unknown) => void,
    options?: { boardId?: string }
  ): () => void {
    const controller = new AbortController();
    let lastEventId: string | null = null;
    let attempt = 0;
    const query = options?.boardId ? `?board_id=${encodeURIComponent(options.boardId)}` : "";
    const url = `${this.baseUrl}/events/stream${query}`;

    const run = async (): Promise<void> => {
      while (!controller.signal.aborted) {
        try {
          const token = await this.ensureToken();
          const headers: Record<string, string> = {
            Accept: "text/event-stream",
            Authorization: `Bearer ${token}`,
          };
          if (lastEventId) headers["Last-Event-ID"] = lastEventId;
          const res = await fetch(url, { headers, signal: controller.signal });
          if (!res.ok || !res.body) throw await this.toError(res);
          attempt = 0;
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          const parser = new SseParser();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            for (const msg of parser.push(decoder.decode(value, { stream: true }))) {
              if (msg.id) lastEventId = msg.id;
              try {
                onEvent({
                  eventType: msg.event,
                  data: JSON.parse(msg.data) as Record<string, unknown>,
                  id: msg.id ?? undefined,
                });
              } catch (e) {
                console.error("Failed to parse event message:", e);
              }
            }
          }
        } catch (err) {
          if (controller.signal.aborted) return;
          onError?.(err);
        }
        const delay = Math.min(15_000, 500 * 2 ** attempt);
        attempt += 1;
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    };
    void run();

    return () => {
      controller.abort();
    };
  }
}
