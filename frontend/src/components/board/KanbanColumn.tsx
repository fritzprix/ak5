"use client";

import React from "react";
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { Column, Ticket } from "@/lib/types";
import { TicketCard } from "./TicketCard";

interface KanbanColumnProps {
  column: Column;
  onDelegateClick?: (ticket: Ticket) => void;
  onAddTicketClick?: (columnId: string) => void;
}

export const KanbanColumn: React.FC<KanbanColumnProps> = ({
  column,
  onDelegateClick,
  onAddTicketClick,
}) => {
  const { setNodeRef, isOver } = useDroppable({
    id: column.column_id,
    data: {
      type: "Column",
      column,
    },
  });

  const stageBadgeColors: Record<string, string> = {
    open: "bg-blue-500/10 text-blue-400 border-blue-500/30",
    in_progress: "bg-amber-500/10 text-amber-400 border-amber-500/30",
    review: "bg-purple-500/10 text-purple-400 border-purple-500/30",
    done: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  };

  const isOverWip = column.wip_limit > 0 && column.tickets.length > column.wip_limit;

  return (
    <div
      ref={setNodeRef}
      className={`flex flex-col flex-1 min-w-[300px] max-w-[360px] bg-slate-950/60 rounded-2xl border transition-colors ${
        isOver
          ? "border-cyan-500/80 bg-slate-900/50"
          : "border-slate-800/80 hover:border-slate-800"
      }`}
    >
      {/* Column Header */}
      <div className="flex items-center justify-between p-4 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <span
            className={`w-2.5 h-2.5 rounded-full ${
              column.stage === "open"
                ? "bg-blue-400"
                : column.stage === "in_progress"
                ? "bg-amber-400 animate-pulse"
                : column.stage === "review"
                ? "bg-purple-400"
                : "bg-emerald-400"
            }`}
          />
          <h3 className="font-semibold text-sm text-slate-200">{column.name}</h3>
          <span className="text-xs font-mono text-slate-500">
            {column.tickets.length}
            {column.wip_limit > 0 && `/${column.wip_limit}`}
          </span>
        </div>

        {isOverWip && (
          <span className="text-[10px] font-medium text-rose-400 bg-rose-950/80 px-2 py-0.5 rounded border border-rose-800/50">
            WIP Exceeded
          </span>
        )}
      </div>

      {/* Ticket List (Droppable & Sortable) */}
      <div className="flex-1 p-3 space-y-3 overflow-y-auto min-h-[300px]">
        <SortableContext
          items={column.tickets.map((t) => t.ticket_id)}
          strategy={verticalListSortingStrategy}
        >
          {column.tickets.map((ticket) => (
            <TicketCard
              key={ticket.ticket_id}
              ticket={ticket}
              onDelegateClick={onDelegateClick}
            />
          ))}
        </SortableContext>

        {column.tickets.length === 0 && (
          <div className="h-32 flex items-center justify-center border-2 border-dashed border-slate-900 rounded-xl text-xs text-slate-600">
            Drop cards here
          </div>
        )}
      </div>
    </div>
  );
};
