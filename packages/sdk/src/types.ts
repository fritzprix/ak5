/** Shared TypeScript types mirroring ak5.contracts + API schemas (workflow / events v2). */

export type ActorType = "human" | "agent";
export type ActorStatus = "idle" | "busy" | "offline";
export type TicketPriority = "low" | "medium" | "high" | "urgent";
/** Derived API field (workflow.md §2) — not stored. */
export type TicketStatus = "open" | "in_progress" | "blocked" | "done";

export type Stage =
  | "unclassified"
  | "backlog"
  | "in_progress"
  | "agent_review"
  | "postmortem_pending"
  | "human_review"
  | "done";

export type Role = "triage" | "worker" | "gatekeeper" | "human";

export type BlockedKind = "needs_human" | "dependency" | "external" | "escalation";

export type ColumnStage = Stage;

export interface Actor {
  actor_id: string;
  actor_type: ActorType;
  name: string;
  role: string;
  description?: string | null;
  capabilities: string[];
  status: ActorStatus;
  avatar_url?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface TicketComment {
  comment_id: string;
  ticket_id: string;
  actor_id: string;
  content: string;
  is_internal: boolean;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface TicketAttachment {
  attachment_id: string;
  ticket_id: string;
  actor_id: string;
  filename: string;
  file_size: number;
  content_type: string;
  created_at: string;
}

export interface Ticket {
  ticket_id: string;
  board_id: string;
  column_id: string;
  parent_ticket_id?: string | null;
  title: string;
  description?: string | null;
  priority: TicketPriority;
  rank: string;
  labels: string[];
  assigned_to?: string | null;
  created_by: string;
  status: TicketStatus;
  blocked_by?: string | null;
  execution_context?: Record<string, unknown> | null;
  due_date?: string | null;
  is_archived?: boolean;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
  subtask_count: number;
  subtask_done_count: number;
  subtasks?: Ticket[];
  comments?: TicketComment[];
  attachments?: TicketAttachment[];

  // Workflow overlays (workflow.md §2 / §9)
  stage?: Stage | string | null;
  attempt?: number;
  max_attempts?: number;
  claimed_by?: string | null;
  lease_expires_at?: string | null;
  lease_expirations?: number;
  submitted_by?: string | null;
  blocked_kind?: BlockedKind | string | null;
  blocked_since?: string | null;
  blocked_actor?: string | null;
  original_request?: string | null;
}

export interface Column {
  column_id: string;
  board_id: string;
  name: string;
  stage: ColumnStage;
  position: number;
  wip_limit: number;
  created_at: string;
  tickets: Ticket[];
  total_ticket_count?: number | null;
}

export interface BoardSummary {
  board_id: string;
  name: string;
  description?: string | null;
  created_by: string;
  created_at: string;
  kind?: "inbox" | "standard";
  template?: string | null;
  auto_created?: boolean;
}

export interface Board extends BoardSummary {
  columns: Column[];
  has_human_admin?: boolean;
}

export interface AuditLog {
  log_id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  payload?: Record<string, unknown> | null;
  timestamp: string;
}

export interface TicketRevision {
  revision_id: number;
  ticket_id: string;
  spec: string;
  acceptance_criteria: string[];
  author: string;
  reason?: string | null;
  source: string;
  created_at: string;
}

export interface TicketReview {
  review_id: number;
  ticket_id: string;
  stage: string;
  decision: string;
  findings: Array<Record<string, unknown>>;
  reviewer: string;
  attempt: number;
  reason?: string | null;
  misrouted?: boolean;
  created_at: string;
}

export interface TicketPostmortem {
  postmortem_id: number;
  ticket_id: string;
  what_went_wrong: string;
  root_cause: string;
  revision_id?: number | null;
  human_review_id?: number | null;
  author: string;
  created_at: string;
}

export interface TicketHistory {
  ticket_id: string;
  revisions: TicketRevision[];
  reviews: TicketReview[];
  postmortems: TicketPostmortem[];
  audit: AuditLog[];
}

export interface TicketClaimResult {
  ticket: Ticket;
  claim_token: string;
  lease_expires_at: string;
  revision?: TicketRevision | null;
  postmortems: TicketPostmortem[];
  reviews: TicketReview[];
}

// --- Payload schemas (workflow.md §5) ---

export interface RouteCreateBoard {
  name: string;
  template?: string;
  description?: string;
}

export type RouteTarget =
  | { board_id: string }
  | { create_board: RouteCreateBoard };

export interface RoutePlan {
  refined_title: string;
  refined_spec: string;
  acceptance_criteria: string[];
  target: RouteTarget;
  confidence: number;
}

export interface Artifact {
  kind: "url" | "attachment" | "commit" | "text";
  ref: string;
}

export interface CriteriaSelfCheck {
  criterion: string;
  pass: boolean;
  note?: string;
}

export interface Deliverable {
  summary: string;
  artifacts?: Artifact[];
  criteria_self_check?: CriteriaSelfCheck[];
}

export interface Finding {
  criterion: string;
  pass: boolean;
  note?: string;
}

export interface Verdict {
  decision: "approve" | "reject";
  reason?: string;
  misrouted?: boolean;
  findings?: Finding[];
}

export interface Postmortem {
  what_went_wrong: string;
  root_cause: string;
  revised_spec: string;
  revised_acceptance_criteria: string[];
}

// --- Client options & request inputs ---

export interface AK5ClientOptions {
  /**
   * API base including version prefix, e.g. ``http://127.0.0.1:8000/api/v1``.
   * ``GET /meta`` is resolved relative to the server origin (strip ``/api/vN``).
   */
  baseUrl?: string;
  token?: string;
  actorId?: string;
  actorType?: ActorType;
  /** Prefer one of: triage | worker | gatekeeper | human (cli.md §4.2). */
  actorRole?: string;
  actorCapabilities?: string[];
  /** Shared secret when gateway has AK5_IDENTIFY_SECRET set (also read from process.env). */
  identifySecret?: string;
  timeout?: number;
  /**
   * Client version advertised for negotiation. Defaults to SDK_VERSION.
   * Must be >= server ``min_client``.
   */
  clientVersion?: string;
  /** Skip automatic ``GET /meta`` negotiation on first authenticated call. */
  skipVersionCheck?: boolean;
}

export interface TicketCreateInput {
  title: string;
  description?: string;
  priority?: TicketPriority;
  labels?: string[];
  assigned_to?: string | null;
  parent_ticket_id?: string | null;
  execution_context?: Record<string, unknown>;
  due_date?: string | null;
  ticket_id?: string;
  /** Omit for T1 (inbox). Set for T2 (create_direct → backlog). */
  board_id?: string | null;
  /** Legacy; ignored by server for T2. */
  column_id?: string | null;
  /** T1 original request (defaults to description/title). */
  original_request?: string;
  /** T2 refined spec (defaults to description). */
  spec?: string;
  /** T2 acceptance criteria (required when board_id set). */
  acceptance_criteria?: string[];
  max_attempts?: number;
}

export interface TicketMoveInput {
  target_column_id: string;
  previous_ticket_id?: string | null;
  next_ticket_id?: string | null;
  /** Required for stage changes that mirror human reject / reopen. */
  reason?: string | null;
}

export interface TicketDelegateInput {
  subtask_title: string;
  subtask_description?: string;
  spec?: string;
  acceptance_criteria?: string[];
  priority?: TicketPriority;
  labels?: string[];
  board_id?: string | null;
  /** Legacy; ignored by server (pull model). */
  target_actor_id?: string | null;
  execution_context?: Record<string, unknown>;
}

export interface ActorIdentifyInput {
  actor_id: string;
  actor_type?: ActorType;
  name: string;
  role: string;
  description?: string;
  capabilities?: string[];
  avatar_url?: string;
}

export interface ActorDiscoveryFilter {
  capability?: string;
  status?: ActorStatus;
  query?: string;
}

export interface ClaimInput {
  capabilities?: string[];
}

export interface HeartbeatInput {
  claim_token: string;
}

export interface ReleaseInput {
  claim_token: string;
  reason: string;
}

export interface SubmitInput {
  claim_token: string;
  deliverable: Deliverable;
}

export interface BounceInput {
  claim_token: string;
  reason: string;
}

export interface RouteInput {
  claim_token: string;
  plan: RoutePlan;
}

export interface VerdictInput {
  claim_token?: string | null;
  verdict: Verdict;
}

export interface PostmortemInput {
  claim_token: string;
  postmortem: Postmortem;
}

export interface BlockInput {
  kind: BlockedKind;
  reason: string;
  claim_token?: string | null;
}

export interface UnblockInput {
  resolution?: string | null;
}

export interface ReopenInput {
  reason: string;
}

export interface RerouteInput {
  reason: string;
  to_inbox?: boolean;
  target_board_id?: string | null;
}

export interface RequestOptions {
  /** Optional Idempotency-Key for write commands (workflow.md §7). */
  idempotencyKey?: string;
}

// --- Events & subscriptions (events.md) ---

export type EventType =
  | "WORK_AVAILABLE"
  | "TICKET_TRANSITIONED"
  | "TICKET_BLOCKED"
  | "TICKET_UNBLOCKED"
  | "HUMAN_ACTION_REQUIRED"
  | "TICKET_CREATED"
  | "TICKET_UPDATED"
  | "COMMENT_ADDED"
  // Legacy SSE (kept for observation)
  | "TICKET_MOVED"
  | "TICKET_DELEGATED";

export interface WorkAvailableData {
  board_id: string;
  stage: Stage | string;
  required_role: Role;
  capabilities: string[];
}

export interface PersistedEvent {
  seq: number;
  event_id: string;
  type: EventType | string;
  board_id?: string | null;
  ticket_id?: string | null;
  actor_id?: string | null;
  data: Record<string, unknown>;
  created_at?: string | null;
}

export interface EventsPollResult {
  events: PersistedEvent[];
  next_cursor: number;
}

export interface SubscriptionFilter {
  board_id?: string | null;
  events?: string[] | null;
  role?: string | null;
  capabilities?: string[] | null;
  for_actor?: string | null;
}

export interface SubscriptionConsumer {
  consumer_id: string;
  lease_expires_at: string;
}

export interface Subscription {
  subscription_id: string;
  owner: string;
  filter: SubscriptionFilter;
  ack_cursor: number;
  consumer?: SubscriptionConsumer | null;
  created_at: string;
}

export interface SubscriptionCreateInput {
  subscription_id?: string;
  filter?: SubscriptionFilter;
  /** ``now`` = start at tip; ``earliest`` = replay from 0. */
  start?: "now" | "earliest";
}

export interface LongPollEventsResult {
  events: PersistedEvent[];
  next_cursor: number;
}

export interface SubscriptionAckResult {
  ack_cursor: number;
}

export interface LongPollOptions {
  cursor?: number;
  wait?: number;
  limit?: number;
  consumerId: string;
}

export interface MetaResponse {
  api_version: string;
  min_client: string;
  capabilities: string[];
}

/** @deprecated Prefer PersistedEvent / EventType. Kept for SSE helper. */
export type BoardEventType = EventType;

export interface BoardEventPayload {
  eventType: BoardEventType | string;
  data: Record<string, unknown>;
  id?: string;
}
