import {
  Actor,
  ActorDiscoveryFilter,
  ActorIdentifyInput,
  AK5ClientOptions,
  Board,
  BoardEventPayload,
  BoardEventType,
  BoardSummary,
  Ticket,
  TicketComment,
  TicketCreateInput,
  TicketDelegateInput,
  TicketMoveInput,
} from "./types";

export class AK5Client {
  public readonly baseUrl: string;
  private token: string | null;
  private actorId: string;
  private actorType: "human" | "agent";
  private actorRole: string;
  private actorCapabilities: string[];

  constructor(options?: AK5ClientOptions) {
    this.baseUrl = (options?.baseUrl || "http://127.0.0.1:8000/api/v1").replace(/\/+$/, "");
    this.token = options?.token || null;
    this.actorId = options?.actorId || "sdk_user";
    this.actorType = options?.actorType || "agent";
    this.actorRole = options?.actorRole || "Developer/Agent";
    this.actorCapabilities = options?.actorCapabilities || ["sdk-client"];
  }

  /**
   * Set or update bearer token explicitly.
   */
  public setToken(token: string): void {
    this.token = token;
  }

  /**
   * Get current token or authenticate with AK5 to issue a new JWT.
   */
  public async ensureToken(): Promise<string> {
    if (this.token) return this.token;

    const res = await fetch(`${this.baseUrl}/auth/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_id: this.actorId,
        actor_type: this.actorType,
        name: this.actorId.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
        role: this.actorRole,
        capabilities: this.actorCapabilities,
      }),
    });

    if (!res.ok) {
      throw new Error(`AK5 Authentication failed: ${res.status} ${res.statusText}`);
    }

    const data = await res.json();
    this.token = data.access_token;
    return this.token!;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    requireAuth: boolean = false
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };

    if (requireAuth || this.token) {
      const token = await this.ensureToken();
      headers["Authorization"] = `Bearer ${token}`;
    }

    const url = `${this.baseUrl}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;
    const res = await fetch(url, { ...options, headers });

    if (!res.ok) {
      let errBody = "";
      try {
        errBody = await res.text();
      } catch {}
      throw new Error(`AK5 Request Error (${res.status} ${res.statusText}): ${errBody}`);
    }

    return res.json();
  }

  // --- Auth & Identity ---

  public async identify(input: ActorIdentifyInput): Promise<{ access_token: string; actor: Actor }> {
    const res = await fetch(`${this.baseUrl}/auth/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });

    if (!res.ok) {
      throw new Error(`Failed to identify actor: ${res.statusText}`);
    }

    const data = await res.json();
    this.token = data.access_token;
    this.actorId = input.actor_id;
    return data;
  }

  // --- Boards ---

  public async listBoards(): Promise<BoardSummary[]> {
    return this.request<BoardSummary[]>("/boards");
  }

  public async getBoard(boardId: string = "proj-core-engine"): Promise<Board> {
    return this.request<Board>(`/boards/${boardId}`);
  }

  // --- Actors & Discovery ---

  public async listActors(actorType?: "human" | "agent"): Promise<Actor[]> {
    const query = actorType ? `?actor_type=${actorType}` : "";
    return this.request<Actor[]>(`/actors${query}`);
  }

  public async discoverAgents(filter?: ActorDiscoveryFilter): Promise<Actor[]> {
    const params = new URLSearchParams();
    if (filter?.capability) params.append("capability", filter.capability);
    if (filter?.status) params.append("status", filter.status);
    if (filter?.query) params.append("query", filter.query);

    const queryStr = params.toString() ? `?${params.toString()}` : "";
    return this.request<Actor[]>(`/actors/discovery${queryStr}`);
  }

  // --- Tickets ---

  public async getTicket(ticketId: string): Promise<Ticket> {
    return this.request<Ticket>(`/tickets/${ticketId}`);
  }

  public async createTicket(input: TicketCreateInput): Promise<Ticket> {
    return this.request<Ticket>("/tickets", {
      method: "POST",
      body: JSON.stringify(input),
    }, true);
  }

  public async moveTicket(ticketId: string, input: TicketMoveInput): Promise<Ticket> {
    return this.request<Ticket>(`/tickets/${ticketId}/move`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }, true);
  }

  public async delegateSubtask(ticketId: string, input: TicketDelegateInput): Promise<Ticket> {
    return this.request<Ticket>(`/tickets/${ticketId}/delegate`, {
      method: "POST",
      body: JSON.stringify(input),
    }, true);
  }

  public async addComment(
    ticketId: string,
    content: string,
    isInternal: boolean = false,
    metadata?: Record<string, any>
  ): Promise<TicketComment> {
    return this.request<TicketComment>(`/tickets/${ticketId}/comments`, {
      method: "POST",
      body: JSON.stringify({
        content,
        is_internal: isInternal,
        metadata: metadata || {},
      }),
    }, true);
  }

  // --- Real-Time Events (SSE) ---

  /**
   * Subscribe to the board's SSE stream for real-time ticket and status updates.
   * Returns an unsubscribe function.
   */
  public subscribeToEvents(
    onEvent: (event: BoardEventPayload) => void,
    onError?: (err: any) => void
  ): () => void {
    if (typeof EventSource === "undefined") {
      console.warn("EventSource is not natively available in this runtime environment.");
      return () => {};
    }

    const eventSource = new EventSource(`${this.baseUrl}/events/stream`);

    const eventTypes: BoardEventType[] = [
      "TICKET_CREATED",
      "TICKET_MOVED",
      "TICKET_DELEGATED",
      "TICKET_UPDATED",
      "COMMENT_ADDED",
    ];

    eventTypes.forEach((type) => {
      eventSource.addEventListener(type, (msg: MessageEvent) => {
        try {
          const data = JSON.parse(msg.data);
          onEvent({
            eventType: type,
            data,
            id: msg.lastEventId,
          });
        } catch (e) {
          console.error("Failed to parse event message:", e);
        }
      });
    });

    if (onError) {
      eventSource.onerror = onError;
    }

    return () => {
      eventSource.close();
    };
  }
}
