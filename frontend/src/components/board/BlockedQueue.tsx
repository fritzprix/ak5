"use client";

import React, { useState } from "react";
import { AlertOctagon, Unlock } from "lucide-react";
import { unblockTicket } from "@/lib/api";
import { unblockRequiresResolution } from "@/lib/ticketHistory";
import type { Ticket } from "@/lib/types";

interface BlockedQueueProps {
  tickets: Ticket[];
  onOpenTicket: (ticket: Ticket) => void;
  onUnblocked: () => void;
}

export function BlockedQueue({ tickets, onOpenTicket, onUnblocked }: BlockedQueueProps) {
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [resolution, setResolution] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (tickets.length === 0) return null;

  const handleUnblock = async (ticket: Ticket) => {
    const needsResolution = unblockRequiresResolution(ticket.blocked_kind);
    if (needsResolution) {
      if (resolvingId !== ticket.ticket_id) {
        setResolvingId(ticket.ticket_id);
        setResolution("");
        setError(null);
        return;
      }
      if (!resolution.trim()) {
        setError("Resolution is required to unblock needs_human / escalation tickets.");
        return;
      }
    }

    setBusyId(ticket.ticket_id);
    setError(null);
    try {
      await unblockTicket(
        ticket.ticket_id,
        needsResolution ? resolution.trim() : undefined
      );
      setResolvingId(null);
      setResolution("");
      onUnblocked();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unblock failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="shrink-0 rounded-xl border border-[var(--danger)]/35 bg-[var(--danger)]/5 px-3 py-2.5">
      <div className="mb-2 flex items-center gap-2">
        <AlertOctagon className="h-4 w-4 text-[var(--danger)]" />
        <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--danger)]">
          Blocked · needs human ({tickets.length})
        </h3>
      </div>
      <p className="mb-2 text-[11px] text-[var(--muted)]">
        Tickets blocked with <code className="font-mono">needs_human</code> or{" "}
        <code className="font-mono">escalation</code>. Unblock records a resolution revision.
      </p>
      {error ? <p className="mb-2 text-xs text-[var(--danger)]">{error}</p> : null}
      <ul className="space-y-2">
        {tickets.map((t) => (
          <li
            key={t.ticket_id}
            className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => onOpenTicket(t)}
              >
                <p className="truncate text-sm font-medium text-[var(--foreground)]">{t.title}</p>
                <p className="mt-0.5 font-mono text-[10px] text-[var(--muted)]">
                  {t.ticket_id}
                  {t.blocked_kind ? ` · ${t.blocked_kind}` : ""}
                  {t.blocked_by ? ` — ${t.blocked_by}` : ""}
                </p>
              </button>
              <button
                type="button"
                className="ak-btn-secondary py-1 px-2.5 text-xs"
                disabled={busyId === t.ticket_id}
                onClick={() => void handleUnblock(t)}
              >
                <Unlock className="h-3.5 w-3.5" />
                {busyId === t.ticket_id
                  ? "Unblocking…"
                  : resolvingId === t.ticket_id
                    ? "Confirm unblock"
                    : "Unblock"}
              </button>
            </div>
            {resolvingId === t.ticket_id ? (
              <div className="mt-2">
                <label className="mb-1 block text-[10px] font-semibold uppercase text-[var(--muted)]">
                  Resolution (required)
                </label>
                <textarea
                  className="ak-input min-h-[3rem] text-xs"
                  rows={2}
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  placeholder="What did the human decide / provide?"
                />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
