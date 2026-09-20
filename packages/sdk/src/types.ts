export type ActorType = "human" | "agent";
export type ActorStatus = "idle" | "busy" | "offline";
export type TicketPriority = "low" | "medium" | "high" | "urgent";
export type TicketStatus = "open" | "in_progress" | "blocked" | "done";
export type ColumnStage = "open" | "in_progress" | "review" | "done";

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
  metadata?: Record<string, any>;
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
  execution_context?: Record<string, any> | null;
  due_date?: string | null;
  created_at: string;
  updated_at: string;
  subtask_count: number;
  subtask_done_count: number;
  subtasks?: Ticket[];
  comments?: TicketComment[];
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
}

export interface BoardSummary {
  board_id: string;
  name: string;
  description?: string | null;
  created_by: string;
  created_at: string;
}

export interface Board extends BoardSummary {
  columns: Column[];
}

export interface AuditLog {
  log_id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  payload?: Record<string, any> | null;
  timestamp: string;
}

export interface AK5ClientOptions {
  baseUrl?: string;
  token?: string;
  actorId?: string;
  actorType?: ActorType;
  actorRole?: string;
  actorCapabilities?: string[];
  timeout?: number;
}

export interface TicketCreateInput {
  board_id: string;
  column_id: string;
  title: string;
  description?: string;
  priority?: TicketPriority;
  labels?: string[];
  assigned_to?: string | null;
  parent_ticket_id?: string | null;
  execution_context?: Record<string, any>;
  due_date?: string | null;
}

export interface TicketMoveInput {
  target_column_id: string;
  previous_ticket_id?: string | null;
  next_ticket_id?: string | null;
}

export interface TicketDelegateInput {
  target_actor_id: string;
  subtask_title: string;
  subtask_description?: string;
  priority?: TicketPriority;
  labels?: string[];
  execution_context?: Record<string, any>;
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

export type BoardEventType =
  | "TICKET_CREATED"
  | "TICKET_MOVED"
  | "TICKET_DELEGATED"
  | "TICKET_UPDATED"
  | "COMMENT_ADDED";

export interface BoardEventPayload {
  eventType: BoardEventType;
  data: Record<string, any>;
  id?: string;
}
