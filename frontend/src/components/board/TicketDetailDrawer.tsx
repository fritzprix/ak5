"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { Check, RotateCcw, Send, X } from "lucide-react";
import { addComment, fetchTicket, moveTicket } from "@/lib/api";
import {
  buildReviewDecisionComment,
  columnStageForTicket,
  resolveReviewTargetColumnId,
  reviewDecisionRequiresNote,
  type ReviewDecision,
} from "@/lib/reviewDecision";
import { Column, Ticket, TicketComment } from "@/lib/types";
import { ActorBadge } from "../actor/ActorBadge";

interface TicketDetailDrawerProps {
  ticket: Ticket | null;
  columns: Column[];
  onClose: () => void;
  onDelegate?: (ticket: Ticket) => void;
  /** Called after a successful comment so the parent can queue a board refresh. */
  onCommented?: () => void;
  /** Called after approve / request-changes so the parent can refresh and close. */
  onReviewDecision?: () => void;
}

export function TicketDetailDrawer({
  ticket,
  columns,
  onClose,
  onDelegate,
  onCommented,
  onReviewDecision,
}: TicketDetailDrawerProps) {
  const ticketId = ticket?.ticket_id ?? null;
  const [detail, setDetail] = useState<Ticket | null>(ticket);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const commentFieldId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!ticketId) {
      setDetail(null);
      setDraft("");
      setCommentError(null);
      return;
    }
    setDetail(ticket);
    setDraft("");
    setCommentError(null);
    let cancelled = false;
    setLoading(true);
    fetchTicket(ticketId)
      .then((full) => {
        if (!cancelled) setDetail(full);
      })
      .catch(() => {
        if (!cancelled && ticket) setDetail(ticket);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ticketId is the stable key
  }, [ticketId]);

  useEffect(() => {
    if (!ticketId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [ticketId]);

  const appendCommentLocally = (created: TicketComment) => {
    setDetail((prev) => {
      if (!prev) return prev;
      return { ...prev, comments: [created, ...(prev.comments ?? [])] };
    });
  };

  const handleSubmitComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticketId || !draft.trim() || submitting) return;
    setSubmitting(true);
    setCommentError(null);
    try {
      const created = await addComment(ticketId, draft.trim(), false);
      appendCommentLocally(created);
      setDraft("");
      onCommented?.();
    } catch (err) {
      setCommentError(err instanceof Error ? err.message : "Failed to post comment");
    } finally {
      setSubmitting(false);
    }
  };

  const handleReviewDecision = async (decision: ReviewDecision) => {
    if (!ticketId || !detail || submitting) return;
    if (reviewDecisionRequiresNote(decision, draft)) {
      setCommentError(
        decision === "approve"
          ? "Add an approval note before marking Done."
          : "Add a reason before sending this back."
      );
      return;
    }
    const targetColumnId = resolveReviewTargetColumnId(columns, decision);
    if (!targetColumnId) {
      setCommentError(
        decision === "approve"
          ? "No Done column found on this board."
          : "No In Progress column found on this board."
      );
      return;
    }

    setSubmitting(true);
    setCommentError(null);
    try {
      const created = await addComment(
        ticketId,
        buildReviewDecisionComment(decision, draft),
        false
      );
      appendCommentLocally(created);
      await moveTicket(ticketId, targetColumnId);
      setDraft("");
      onCommented?.();
      onReviewDecision?.();
      onClose();
    } catch (err) {
      setCommentError(err instanceof Error ? err.message : "Review decision failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (!ticketId || !detail) return null;

  const stage = columnStageForTicket(columns, detail.column_id);
  const isReview = stage === "review";

  const progress =
    detail.subtask_count > 0
      ? Math.round((detail.subtask_done_count / detail.subtask_count) * 100)
      : null;

  const comments = detail.comments ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-end sm:items-stretch"
      style={{ background: "rgba(6, 10, 16, 0.45)" }}
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="Close ticket detail"
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Ticket ${detail.ticket_id}`}
        className="relative flex h-[min(92dvh,100%)] w-full max-w-md flex-col rounded-t-2xl border border-[var(--border)] bg-[var(--surface)] shadow-xl sm:h-full sm:rounded-none sm:border-l sm:border-y-0 sm:border-r-0"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--border)] px-4 py-4 pt-[max(1rem,env(safe-area-inset-top))] sm:px-5">
          <div className="min-w-0">
            <p className="font-mono text-[11px] text-[var(--muted)]">{detail.ticket_id}</p>
            <h2 className="mt-1 text-lg font-semibold leading-snug text-[var(--foreground)]">{detail.title}</h2>
          </div>
          <button type="button" className="ak-btn-ghost p-1.5" aria-label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-4 py-4 text-sm sm:px-5">
          {loading ? <p className="text-xs text-[var(--muted)]">Loading full context…</p> : null}

          <div className="flex flex-wrap gap-2">
            <span className="rounded-md border border-[var(--border)] px-2 py-1 text-[11px] uppercase text-[var(--muted)]">
              {detail.status}
            </span>
            {stage ? (
              <span className="rounded-md border border-[var(--accent)]/40 bg-[var(--accent-muted)] px-2 py-1 text-[11px] uppercase text-[var(--accent)]">
                {stage.replace("_", " ")}
              </span>
            ) : null}
            <span className="rounded-md border border-[var(--border)] px-2 py-1 text-[11px] uppercase text-[var(--muted)]">
              {detail.priority}
            </span>
            <ActorBadge actorId={detail.assigned_to} />
          </div>

          {isReview ? (
            <p className="rounded-lg border border-[var(--accent)]/30 bg-[var(--accent-muted)] px-3 py-2 text-xs text-[var(--foreground)]">
              Review gate — leave a note, then <strong>Approve</strong> (Done) or{" "}
              <strong>Request changes</strong> (back to In Progress). Drag still works on the board.
            </p>
          ) : null}

          <section>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
              Description
            </h3>
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

          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">
              Comments {comments.length > 0 ? `(${comments.length})` : ""}
            </h3>
            {comments.length === 0 ? (
              <p className="text-xs text-[var(--muted)]">
                {isReview ? "No review notes yet." : "No comments yet."}
              </p>
            ) : (
              <ul className="space-y-2">
                {comments.slice(0, 20).map((c) => (
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
            )}
          </section>
        </div>

        <div className="shrink-0 border-t border-[var(--border)] px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-5">
          <form onSubmit={handleSubmitComment} className="space-y-2">
            <label
              htmlFor={commentFieldId}
              className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]"
            >
              {isReview ? "Review note" : "Add comment"}
            </label>
            <textarea
              id={commentFieldId}
              className="ak-input min-h-[4.5rem] resize-y"
              rows={3}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                isReview
                  ? "What looks good, or what should change…"
                  : "Notes, questions, or progress…"
              }
              disabled={submitting}
            />
            {commentError ? <p className="text-xs text-[var(--danger)]">{commentError}</p> : null}

            {isReview ? (
              <div className="flex flex-col gap-2">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <button
                    type="button"
                    className="ak-btn-secondary w-full"
                    disabled={submitting}
                    onClick={() => void handleReviewDecision("request_changes")}
                  >
                    <RotateCcw className="h-4 w-4" />
                    Request changes
                  </button>
                  <button
                    type="button"
                    className="ak-btn-primary w-full"
                    disabled={submitting}
                    onClick={() => void handleReviewDecision("approve")}
                  >
                    <Check className="h-4 w-4" />
                    Approve → Done
                  </button>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <button type="button" className="ak-btn-ghost" onClick={onClose}>
                    Close
                  </button>
                  <button
                    type="submit"
                    className="ak-btn-ghost text-[11px]"
                    disabled={submitting || !draft.trim()}
                    title="Post note without moving the ticket"
                  >
                    <Send className="h-3.5 w-3.5" />
                    Comment only
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <button type="button" className="ak-btn-ghost" onClick={onClose}>
                    Close
                  </button>
                  {onDelegate ? (
                    <button
                      type="button"
                      className="ak-btn-secondary"
                      onClick={() => {
                        onDelegate(detail);
                        onClose();
                      }}
                    >
                      Delegate
                    </button>
                  ) : null}
                </div>
                <button
                  type="submit"
                  className="ak-btn-primary"
                  disabled={submitting || !draft.trim()}
                >
                  <Send className="h-4 w-4" />
                  {submitting ? "Posting…" : "Post"}
                </button>
              </div>
            )}
          </form>
        </div>
      </aside>
    </div>
  );
}
