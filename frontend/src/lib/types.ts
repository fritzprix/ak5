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
  execution_context?: Record<string, any> | null;
  due_date?: string | null;
  created_at: string;
  updated_at: string;
  subtask_count: number;
  subtask_done_count: number;
  subtasks?: Ticket[];
  comments?: TicketComment[];
  attachments?: TicketAttachment[];
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
