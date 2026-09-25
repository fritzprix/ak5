"use client";

import React from "react";
import { useSortable } from "@dnd-kit/sortable";
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { Column, Ticket } from "@/lib/types";
import { emptyColumnSlotId } from "@/lib/boardDnD";
import { TicketCard } from "./TicketCard";

interface KanbanColumnProps {
  column: Column;
  onOpenTicket?: (ticket: Ticket) => void;
  onDelegateClick?: (ticket: Ticket) => void;
}

function EmptyColumnSlot({ id, label }: { id: string; label: string }) {
  const { setNodeRef, attributes, listeners, isDragging } = useSortable({
    id,
    data: { type: "EmptySlot", columnId: id },
  });

  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      aria-label={`Empty ${label} column. Drop here.`}
      className={`flex h-24 items-center justify-center rounded-lg border border-dashed border-[var(--border)] text-[11px] text-[var(--muted)] outline-none focus-visible:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
        isDragging ? "opacity-40" : ""
      }`}
    >
      Drop tickets here
    </div>
  );
}

export const KanbanColumn: React.FC<KanbanColumnProps> = ({
  column,
  onOpenTicket,
  onDelegateClick,
}) => {
  const { setNodeRef, isOver } = useDroppable({
    id: column.column_id,
    data: { type: "Column", column },
  });

  const isOverWip = column.wip_limit > 0 && column.tickets.length > column.wip_limit;
  const emptySlotId = emptyColumnSlotId(column.column_id);
  const sortableIds =
    column.tickets.length > 0 ? column.tickets.map((t) => t.ticket_id) : [emptySlotId];

  const stageDot =
    column.stage === "open"
      ? "bg-sky-400"
      : column.stage === "in_progress"
        ? "bg-[var(--warning)]"
        : column.stage === "review"
          ? "bg-[var(--accent)]"
          : "bg-[var(--success)]";

  return (
    <div
      ref={setNodeRef}
      className={`flex h-full w-[min(85vw,20rem)] shrink-0 snap-center flex-col rounded-xl border bg-[var(--background-elevated)]/80 transition-colors sm:w-auto sm:min-w-[280px] sm:max-w-[340px] sm:flex-1 sm:[scroll-snap-align:none] ${
        isOver ? "border-[var(--accent)]" : "border-[var(--border)]"
      }`}
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full ${stageDot}`} aria-hidden />
          <h3 className="truncate text-sm font-semibold text-[var(--foreground)]">{column.name}</h3>
          <span className="font-mono text-[11px] text-[var(--muted)]">
            {column.tickets.length}
            {column.wip_limit > 0 ? `/${column.wip_limit}` : ""}
          </span>
        </div>
        {isOverWip ? (
          <span className="shrink-0 text-[10px] font-medium text-[var(--danger)]">WIP</span>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto overscroll-contain p-2.5">
        <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
          {column.tickets.map((ticket) => (
            <TicketCard
              key={ticket.ticket_id}
              ticket={ticket}
              onOpen={onOpenTicket}
              onDelegateClick={onDelegateClick}
            />
          ))}
          {column.tickets.length === 0 ? (
            <EmptyColumnSlot id={emptySlotId} label={column.name} />
          ) : null}
        </SortableContext>
      </div>
    </div>
  );
};
