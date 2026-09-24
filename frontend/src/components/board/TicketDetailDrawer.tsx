"use client";

import React, { useEffect, useState } from "react";
import { X } from "lucide-react";
import { fetchTicket } from "@/lib/api";
import { Ticket } from "@/lib/types";
import { ActorBadge } from "../actor/ActorBadge";

interface TicketDetailDrawerProps {
  ticket: Ticket | null;
  onClose: () => void;
  onDelegate?: (ticket: Ticket) => void;
}

export function TicketDetailDrawer({ ticket, onClose, onDelegate }: TicketDetailDrawerProps) {
  const [detail, setDetail] = useState<Ticket | null>(ticket);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!ticket) {
      setDetail(null);
      return;
    }
    setDetail(ticket);
    let cancelled = false;
    setLoading(true);
    fetchTicket(ticket.ticket_id)
      .then((full) => {
        if (!cancelled) setDetail(full);
      })
      .catch(() => {
        if (!cancelled) setDetail(ticket);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticket]);

  useEffect(() => {
    if (!ticket) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ticket, onClose]);

  if (!ticket || !detail) return null;

  const progress =
    detail.subtask_count > 0
      ? Math.round((detail.subtask_done_count / detail.subtask_count) * 100)
      : null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "rgba(6, 10, 16, 0.45)" }}>
      <button type="button" className="flex-1 cursor-default" aria-label="Close ticket detail" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Ticket ${detail.ticket_id}`}
        className="flex h-full w-full max-w-md flex-col border-l border-[var(--border)] bg-[var(--surface)] shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <p className="font-mono text-[11px] text-[var(--muted)]">{detail.ticket_id}</p>
            <h2 className="mt-1 text-lg font-semibold leading-snug text-[var(--foreground)]">{detail.title}</h2>
          </div>
          <button type="button" className="ak-btn-ghost p-1.5" aria-label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
          {loading ? <p className="text-xs text-[var(--muted)]">Loading full context…</p> : null}

          <div className="flex flex-wrap gap-2">
            <span className="rounded-md border border-[var(--border)] px-2 py-1 text-[11px] uppercase text-[var(--muted)]">
              {detail.status}
            </span>
            <span className="rounded-md border border-[var(--border)] px-2 py-1 text-[11px] uppercase text-[var(--muted)]">
              {detail.priority}
            </span>
            <ActorBadge actorId={detail.assigned_to} />
          </div>

          <section>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">Description</h3>
            <p className="whitespace-pre-wrap leading-relaxed text-[var(--foreground)]">
              {detail.description || "No description."}
            </p>
          </section>

          {progress !== null ? (
            <section>
              <div className="mb-1.5 flex items-center justify-between text-[11px] text-[var(--muted)]">
                <span>
                  Subtasks {detail.subtask_done_count}/{detail.subtask_count}
                </span>
                <span className="font-mono text-[var(--accent)]">{progress}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--background)]">
                <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${progress}%` }} />
              </div>
              {detail.subtasks && detail.subtasks.length > 0 ? (
                <ul className="mt-3 space-y-1.5">
                  {detail.subtasks.map((st) => (
                    <li
                      key={st.ticket_id}
                      className="flex items-center justify-between gap-2 rounded-md border border-[var(--border)] bg-[var(--background)] px-2.5 py-1.5 text-xs"
                    >
                      <span className="truncate">{st.title}</span>
                      <span className="shrink-0 font-mono text-[var(--muted)]">{st.status}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}

          {detail.comments && detail.comments.length > 0 ? (
            <section>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
                Recent comments
              </h3>
              <ul className="space-y-2">
                {detail.comments.slice(0, 8).map((c) => (
                  <li
                    key={c.comment_id}
                    className="rounded-md border border-[var(--border)] bg-[var(--background)] p-2.5 text-xs"
                  >
                    <p className="mb-1 font-mono text-[10px] text-[var(--muted)]">
                      @{c.actor_id}
                      {c.is_internal ? " · internal" : ""}
                    </p>
                    <p className="whitespace-pre-wrap text-[var(--foreground)]">{c.content}</p>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
          <button type="button" className="ak-btn-ghost" onClick={onClose}>
            Close
          </button>
          {onDelegate ? (
            <button
              type="button"
              className="ak-btn-primary"
              onClick={() => {
                onDelegate(detail);
                onClose();
              }}
            >
              Delegate
            </button>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
