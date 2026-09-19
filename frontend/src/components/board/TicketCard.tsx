"use client";

import React, { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ChevronDown, ChevronRight, CornerDownRight, PlusCircle, AlertCircle } from "lucide-react";
import { Ticket } from "@/lib/types";
import { ActorBadge } from "../actor/ActorBadge";

interface TicketCardProps {
  ticket: Ticket;
  onDelegateClick?: (ticket: Ticket) => void;
}

export const TicketCard: React.FC<TicketCardProps> = ({ ticket, onDelegateClick }) => {
  const [showSubtasks, setShowSubtasks] = useState(false);

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: ticket.ticket_id,
    data: {
      type: "Ticket",
      ticket,
    },
  });

  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  const isAgentActive =
    ticket.status === "in_progress" &&
    ticket.assigned_to &&
    ticket.assigned_to.startsWith("agent");

  const priorityColors: Record<string, string> = {
    urgent: "bg-red-500/20 text-red-400 border-red-500/40",
    high: "bg-orange-500/20 text-orange-400 border-orange-500/40",
    medium: "bg-amber-500/20 text-amber-300 border-amber-500/40",
    low: "bg-slate-500/20 text-slate-400 border-slate-500/40",
  };

  const hasSubtasks = ticket.subtask_count > 0;
  const progressPercent = hasSubtasks
    ? Math.round((ticket.subtask_done_count / ticket.subtask_count) * 100)
    : 0;

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      className={`relative group bg-slate-900/90 rounded-xl p-4 border transition-all cursor-grab active:cursor-grabbing ${
        isAgentActive
          ? "border-purple-500 animate-agent-pulse bg-slate-900"
          : "border-slate-800 hover:border-slate-700 bg-slate-900/70"
      }`}
    >
      {/* Priority & Ticket ID Header */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <span
          className={`text-[11px] font-semibold uppercase px-2 py-0.5 rounded border ${
            priorityColors[ticket.priority] || priorityColors.medium
          }`}
        >
          {ticket.priority}
        </span>
        <span className="text-xs text-slate-500 font-mono">{ticket.ticket_id}</span>
      </div>

      {/* Ticket Title */}
      <h4 className="text-sm font-medium text-slate-100 mb-1 leading-snug group-hover:text-cyan-300 transition-colors">
        {ticket.title}
      </h4>

      {/* Description Preview */}
      {ticket.description && (
        <p className="text-xs text-slate-400 line-clamp-2 mb-3 leading-relaxed">
          {ticket.description}
        </p>
      )}

      {/* Subtask Progress Bar & Accordion Toggle */}
      {hasSubtasks && (
        <div className="mt-2 mb-3 pt-2 border-t border-slate-800/80">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowSubtasks(!showSubtasks);
              }}
              className="flex items-center gap-1 text-slate-400 hover:text-slate-200"
            >
              {showSubtasks ? (
                <ChevronDown className="w-3.5 h-3.5 text-cyan-400" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5 text-cyan-400" />
              )}
              <span className="font-medium text-[11px]">
                Subtasks: {ticket.subtask_done_count}/{ticket.subtask_count} Done
              </span>
            </button>
            <span className="text-[11px] font-mono text-cyan-400">{progressPercent}%</span>
          </div>

          <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
            <div
              className="bg-gradient-to-r from-cyan-500 to-purple-500 h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>

          {/* Expanded Subtasks List */}
          {showSubtasks && ticket.subtasks && ticket.subtasks.length > 0 && (
            <div className="mt-2 space-y-1.5 pl-2 border-l-2 border-slate-800">
              {ticket.subtasks.map((st) => (
                <div
                  key={st.ticket_id}
                  className="text-xs flex items-center justify-between py-1 px-2 rounded bg-slate-950/60 text-slate-300"
                >
                  <div className="flex items-center gap-1.5 truncate">
                    <CornerDownRight className="w-3 h-3 text-purple-400 flex-shrink-0" />
                    <span className="truncate">{st.title}</span>
                  </div>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                      st.status === "done"
                        ? "bg-green-950/60 text-green-400"
                        : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {st.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer: Actor Badge & Action Buttons */}
      <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-800/60">
        <ActorBadge actorId={ticket.assigned_to} />

        {/* Delegate Button */}
        {onDelegateClick && (
          <button
            type="button"
            title="Delegate Subtask to Agent"
            onClick={(e) => {
              e.stopPropagation();
              onDelegateClick(ticket);
            }}
            className="p-1 rounded-md text-slate-400 hover:text-cyan-400 hover:bg-slate-800 transition-colors"
          >
            <PlusCircle className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
};
