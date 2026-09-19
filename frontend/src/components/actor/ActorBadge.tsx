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
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-400">
        Unassigned
      </span>
    );
  }

  const id = actor?.actor_id || actorId || "";
  const isAgent = id.startsWith("agent") || actor?.actor_type === "agent";
  const role = actor?.role || (isAgent ? "Agent" : "PM");
  const name = actor?.name || id;

  if (isAgent) {
    return (
      <span
        title={`AI Agent: ${name} (${role})`}
        className={`inline-flex items-center gap-1.5 font-medium rounded-full bg-purple-950/70 border border-purple-600/50 text-purple-300 ${
          size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm"
        }`}
      >
        <Bot className={size === "sm" ? "w-3 h-3 text-purple-400" : "w-4 h-4 text-purple-400"} />
        <span>Agent: {role}</span>
      </span>
    );
  }

  return (
    <span
      title={`Human: ${name} (${role})`}
      className={`inline-flex items-center gap-1.5 font-medium rounded-full bg-emerald-950/70 border border-emerald-600/50 text-emerald-300 ${
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm"
      }`}
    >
      <User className={size === "sm" ? "w-3 h-3 text-emerald-400" : "w-4 h-4 text-emerald-400"} />
      <span>{role}</span>
    </span>
  );
};
