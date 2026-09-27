"use client";

import React from "react";
import { Bot, Circle, Plus } from "lucide-react";
import { Actor } from "@/lib/types";

interface AgentFleetStripProps {
  actors: Actor[];
  onAddAgent?: () => void;
}

const statusLabel: Record<string, string> = {
  idle: "Idle — polling tickets",
  busy: "Busy",
  offline: "Offline",
};

export function AgentFleetStrip({ actors, onAddAgent }: AgentFleetStripProps) {
  const agents = actors.filter((a) => a.actor_type === "agent");

  const busy = agents.filter((a) => a.status === "busy").length;
  const idle = agents.filter((a) => a.status === "idle").length;
  const offline = agents.filter((a) => a.status === "offline").length;

  return (
    <section
      aria-label="Agent fleet"
      className="flex shrink-0 flex-col gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface)]/80 px-3 py-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3 sm:py-2.5"
    >
      <div className="flex items-center gap-2 text-xs font-medium text-[var(--muted)]">
        <Bot className="h-3.5 w-3.5 text-[var(--accent)]" />
        <span>Board Agents</span>
        {agents.length > 0 ? (
          <span className="font-mono text-[11px] text-[var(--foreground)]">
            {busy} busy · {idle} idle · {offline} off
          </span>
        ) : (
          <span className="text-[11px] text-[var(--muted)]">(None enrolled)</span>
        )}
      </div>

      <div className="flex min-w-0 items-center gap-1.5 overflow-x-auto overscroll-x-contain pb-0.5 sm:flex-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
        {agents.map((agent) => {
          const tone =
            agent.status === "busy"
              ? "text-[var(--accent)]"
              : agent.status === "offline"
                ? "text-[var(--muted)]"
                : "text-[var(--success)]";
          return (
            <span
              key={agent.actor_id}
              title={`${agent.name} · ${statusLabel[agent.status] || agent.status}`}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[11px] text-[var(--foreground)]"
            >
              <Circle className={`h-2 w-2 fill-current ${tone}`} aria-hidden />
              <span className="max-w-[9rem] truncate font-mono">@{agent.actor_id}</span>
            </span>
          );
        })}

        {onAddAgent && (
          <button
            type="button"
            onClick={onAddAgent}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-dashed border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-[11px] font-medium text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
          >
            <Plus className="h-3 w-3" />
            <span>Enroll Agent</span>
          </button>
        )}
      </div>

      <p className="hidden text-[10px] text-[var(--muted)] sm:ml-auto sm:block sm:w-auto">
        Ticket-pull model — only enrolled agents poll this board
      </p>
    </section>
  );
}
