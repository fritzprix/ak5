export type ActorType = "human" | "agent";
export type ActorStatus = "idle" | "busy" | "offline";
export type TicketPriority = "low" | "medium" | "high" | "urgent";
/** Derived API status (workflow.md §2) — not stored. */
export type TicketStatus = "open" | "in_progress" | "blocked" | "done";

/** Workflow stages (workflow.md §2). */
export type ColumnStage =
  | "unclassified"
  | "backlog"
  | "in_progress"
  | "agent_review"
  | "postmortem_pending"
  | "human_review"
  | "done";

export type BoardKind = "inbox" | "standard";

export type BlockedKind = "needs_human" | "dependency" | "external" | "escalation";

export type WorkflowRole = "triage" | "worker" | "gatekeeper" | "human";

export const STANDARD_STAGES: readonly ColumnStage[] = [
  "backlog",
  "in_progress",
  "agent_review",
  "postmortem_pending",
  "human_review",
  "done",
] as const;

export const ALL_STAGES: readonly ColumnStage[] = ["unclassified", ...STANDARD_STAGES] as const;

/** Block kinds that require a human resolution to unblock (workflow.md §4 B3). */
export const HUMAN_UNBLOCK_KINDS: readonly BlockedKind[] = ["needs_human", "escalation"] as const;

export interface Actor {
  actor_id: string;
  actor_type: ActorType;
  name: string;
  role: string;
  description?: string | null;
  capabilities: string[];
  status: ActorStatus;
  avatar_url?: string | null;
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
  /** Legacy free-text blocked reason (compat). Prefer blocked_kind. */
  blocked_by?: string | null;
  execution_context?: Record<string, unknown> | null;
  due_date?: string | null;
  created_at: string;
  updated_at: string;
  subtask_count: number;
  subtask_done_count: number;
  is_archived?: boolean;
  archived_at?: string | null;
  subtasks?: Ticket[];
  comments?: TicketComment[];
  attachments?: TicketAttachment[];

  // Workflow overlays (workflow.md §2 / §9)
  stage?: ColumnStage | string | null;
  attempt?: number;
  max_attempts?: number;
  claim_token?: string | null;
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
  kind?: BoardKind;
  template?: string | null;
  auto_created?: boolean;
}

export interface Board extends BoardSummary {
  columns: Column[];
  /** True when a human already has board admin (creator or BoardMember). */
  has_human_admin?: boolean;
  kind?: BoardKind;
  template?: string | null;
  auto_created?: boolean;
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

export interface AuditLogEntry {
  log_id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  payload?: Record<string, unknown> | null;
  timestamp: string;
}

export interface TicketHistory {
  ticket_id: string;
  revisions: TicketRevision[];
  reviews: TicketReview[];
  postmortems: TicketPostmortem[];
  audit: AuditLogEntry[];
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
