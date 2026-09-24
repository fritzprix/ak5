import React from "react";
import { Bot, User } from "lucide-react";
import { Actor } from "@/lib/types";

interface ActorBadgeProps {
  actorId?: string | null;
  actor?: Actor | null;
  size?: "sm" | "md";
}

export const ActorBadge: React.FC<ActorBadgeProps> = ({ actorId, actor, size = "sm" }) => {
  if (!actorId && !actor) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md bg-[var(--background)] px-2 py-0.5 text-[11px] text-[var(--muted)]">
        Unassigned
      </span>
    );
  }

  const id = actor?.actor_id || actorId || "";
  const isAgent = id.startsWith("agent") || actor?.actor_type === "agent";
  const role = actor?.role || (isAgent ? "Agent" : "Human");
  const name = actor?.name || id;
  const pad = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";

  if (isAgent) {
    return (
      <span
        title={`AI Agent: ${name} (${role})`}
        className={`inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--accent-muted)] font-medium text-[var(--accent)] ${pad}`}
      >
        <Bot className={size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5"} />
        <span className="max-w-[7rem] truncate">{role}</span>
      </span>
    );
  }

  return (
    <span
      title={`Human: ${name} (${role})`}
      className={`inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--background)] font-medium text-[var(--foreground)] ${pad}`}
    >
      <User className={size === "sm" ? "h-3 w-3 text-[var(--success)]" : "h-3.5 w-3.5 text-[var(--success)]"} />
      <span className="max-w-[7rem] truncate">{role}</span>
    </span>
  );
};
