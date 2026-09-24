"use client";

import React from "react";
import { Bot, Circle } from "lucide-react";
import { Actor } from "@/lib/types";

interface AgentFleetStripProps {
  actors: Actor[];
}

const statusLabel: Record<string, string> = {
  idle: "Idle — polling tickets",
  busy: "Busy",
  offline: "Offline",
};

export function AgentFleetStrip({ actors }: AgentFleetStripProps) {
  const agents = actors.filter((a) => a.actor_type === "agent");
  if (agents.length === 0) return null;

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
        <span>Agents</span>
        <span className="font-mono text-[11px] text-[var(--foreground)]">
          {busy} busy · {idle} idle · {offline} off
        </span>
      </div>
      <div className="flex min-w-0 gap-1.5 overflow-x-auto overscroll-x-contain pb-0.5 sm:flex-1 sm:flex-wrap sm:overflow-visible sm:pb-0">
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
      </div>
      <p className="hidden text-[10px] text-[var(--muted)] sm:ml-auto sm:block sm:w-auto">
        Ticket-pull model — no push interrupts; agents poll on their schedule
      </p>
    </section>
  );
}
