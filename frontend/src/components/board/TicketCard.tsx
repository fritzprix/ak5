"use client";

import React from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { AlertOctagon, GitBranch } from "lucide-react";
import { Ticket } from "@/lib/types";
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

export const TicketCard: React.FC<TicketCardProps> = ({
  ticket,
  onOpen,
  onDelegateClick,
  compact = false,
}) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: ticket.ticket_id,
    data: { type: "Ticket", ticket },
  });

  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.45 : 1,
  };

  const isBlocked = ticket.status === "blocked";
  const hasSubtasks = ticket.subtask_count > 0;
  const isAgentActive =
    ticket.status === "in_progress" &&
    Boolean(ticket.assigned_to?.startsWith("agent"));

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
      <div className="mb-2 flex items-start justify-between gap-2">
        <button
          type="button"
          className="min-w-0 flex-1 cursor-grab touch-none text-left active:cursor-grabbing"
          {...attributes}
          {...listeners}
          onDoubleClick={() => onOpen?.(ticket)}
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

      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-[var(--muted)]">{ticket.ticket_id}</span>
        {isBlocked ? (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-[var(--danger)]">
            <AlertOctagon className="h-3 w-3" />
            Blocked
          </span>
        ) : null}
      </div>

      {!compact && hasSubtasks ? (
        <p className="mb-2 inline-flex items-center gap-1 text-[10px] text-[var(--muted)]">
          <GitBranch className="h-3 w-3" />
          {ticket.subtask_done_count}/{ticket.subtask_count} subtasks
        </p>
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
