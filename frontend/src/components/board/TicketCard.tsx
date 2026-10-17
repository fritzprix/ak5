"use client";

import React, { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { AlertOctagon, ChevronDown, ChevronRight, GitBranch, GripVertical } from "lucide-react";
import { SubtaskSummary, Ticket } from "@/lib/types";
import { ActorBadge } from "../actor/ActorBadge";

interface TicketCardProps {
  ticket: Ticket;
  onOpen?: (ticket: Ticket) => void;
  onDelegateClick?: (ticket: Ticket) => void;
  compact?: boolean;
}

const priorityClass: Record<string, string> = {
  urgent: "border-[var(--danger)]/50 text-[var(--danger)]",
  high: "border-[var(--warning)]/50 text-[var(--warning)]",
  medium: "border-[var(--border-strong)] text-[var(--muted)]",
  low: "border-[var(--border)] text-[var(--muted)]",
};

function isSubtaskSummary(item: SubtaskSummary | Ticket): item is SubtaskSummary {
  // Board summaries omit Ticket.status/rank; detail payloads include them.
  return !("status" in item) || !("rank" in item);
}

function subtaskRows(ticket: Ticket): SubtaskSummary[] {
  if (!ticket.subtasks?.length) return [];
  return ticket.subtasks.map((st) => {
    if (isSubtaskSummary(st)) return st;
    return {
      ticket_id: st.ticket_id,
      title: st.title,
      stage: st.stage ?? null,
      board_id: st.board_id,
      blocked: st.status === "blocked" || Boolean(st.blocked_kind),
      claimed: Boolean(st.claimed_by),
    };
  });
}

export const TicketCard: React.FC<TicketCardProps> = ({
  ticket,
  onOpen,
  onDelegateClick,
  compact = false,
}) => {
  const [expanded, setExpanded] = useState(false);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: ticket.ticket_id,
    data: { type: "Ticket", ticket },
  });

  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.45 : 1,
  };

  const isBlocked = ticket.status === "blocked" || Boolean(ticket.blocked_kind);
  const blockedLabel =
    ticket.blocked_kind === "needs_human"
      ? "Needs human"
      : ticket.blocked_kind === "escalation"
        ? "Escalated"
        : ticket.blocked_kind === "dependency"
          ? "Dependency"
          : ticket.blocked_kind === "external"
            ? "External"
            : "Blocked";
  const summaries = subtaskRows(ticket);
  const hasSubtasks = ticket.subtask_count > 0 || summaries.length > 0;
  const isClaimed = Boolean(ticket.claimed_by);
  const isAgentActive =
    (ticket.status === "in_progress" || isClaimed) &&
    Boolean(ticket.assigned_to?.startsWith("agent") || ticket.claimed_by?.startsWith("agent"));

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group rounded-lg border bg-[var(--surface)] p-3 transition-colors ${
        isBlocked
          ? "border-[var(--danger)]/40"
          : isAgentActive
            ? "border-[var(--accent)]/50"
            : "border-[var(--border)] hover:border-[var(--border-strong)]"
      }`}
    >
      <div className="mb-2 flex items-start gap-1.5">
        <button
          type="button"
          className="mt-0.5 shrink-0 cursor-grab touch-none rounded p-0.5 text-[var(--muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:cursor-grabbing"
          aria-label={`Move ticket ${ticket.ticket_id}. Press Space to pick up, arrow keys to move, Space to drop.`}
          title="Drag or keyboard: Space + arrows"
          {...attributes}
          {...listeners}
        >
          <GripVertical className="h-4 w-4" />
        </button>
        <button
          type="button"
          className="min-w-0 flex-1 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface)]"
          onClick={() => onOpen?.(ticket)}
        >
          <h4 className="text-sm font-medium leading-snug text-[var(--foreground)] group-hover:text-[var(--accent-hover)]">
            {ticket.title}
          </h4>
        </button>
        <span
          className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            priorityClass[ticket.priority] || priorityClass.medium
          }`}
        >
          {ticket.priority}
        </span>
      </div>

      <div className="mb-2 flex items-center justify-between gap-2 pl-5">
        <span className="font-mono text-[10px] text-[var(--muted)]">{ticket.ticket_id}</span>
        {isBlocked ? (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-[var(--danger)]">
            <AlertOctagon className="h-3 w-3" />
            {blockedLabel}
          </span>
        ) : null}
      </div>

      {!compact && hasSubtasks ? (
        <div className="mb-2 pl-5">
          <button
            type="button"
            className="inline-flex items-center gap-1 text-[10px] text-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            <GitBranch className="h-3 w-3" />
            {ticket.subtask_done_count}/{ticket.subtask_count} subtasks
          </button>
          {expanded && summaries.length > 0 ? (
            <ul className="mt-1.5 space-y-1 border-l border-[var(--border)] pl-2">
              {summaries.map((st) => (
                <li
                  key={st.ticket_id}
                  className="flex items-center justify-between gap-2 text-[10px] text-[var(--muted)]"
                >
                  <span className="min-w-0 truncate text-[var(--foreground)]">{st.title}</span>
                  <span className="shrink-0 font-mono">
                    {st.stage ?? "—"}
                    {st.board_id !== ticket.board_id ? ` · ${st.board_id}` : ""}
                    {st.blocked ? " · blocked" : ""}
                    {st.claimed ? " · claimed" : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-2 border-t border-[var(--border)] pt-2">
        <ActorBadge actorId={ticket.assigned_to} />
        <div className="flex items-center gap-1">
          {onOpen ? (
            <button
              type="button"
              className="ak-btn-ghost px-2 py-1 text-[11px]"
              onClick={() => onOpen(ticket)}
            >
              Open
            </button>
          ) : null}
          {onDelegateClick ? (
            <button
              type="button"
              className="ak-btn-ghost px-2 py-1 text-[11px]"
              onClick={() => onDelegateClick(ticket)}
            >
              Delegate
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
};
