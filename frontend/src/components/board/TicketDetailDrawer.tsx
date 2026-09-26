"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { AlertOctagon, Check, RotateCcw, Save, Send, X } from "lucide-react";
import { addComment, fetchTicket, moveTicket, updateTicket } from "@/lib/api";
import {
  buildReviewDecisionComment,
  columnStageForTicket,
  resolveReviewTargetColumnId,
  reviewDecisionRequiresNote,
  type ReviewDecision,
} from "@/lib/reviewDecision";
import {
  buildTicketUpdatePayload,
  isTicketEditDirty,
  ticketToEditFields,
  type TicketEditFields,
} from "@/lib/ticketEdit";
import { Actor, Column, Ticket, TicketComment, TicketPriority } from "@/lib/types";
import { preferReaderFocus, trapTabKey } from "@/lib/focusTrap";
import { ActorBadge } from "../actor/ActorBadge";

interface TicketDetailDrawerProps {
  ticket: Ticket | null;
  columns: Column[];
  actors: Actor[];
  onClose: () => void;
  onDelegate?: (ticket: Ticket) => void;
  /** Called after a successful comment so the parent can queue a board refresh. */
  onCommented?: () => void;
  /** Called after fields are saved so the parent can queue a board refresh. */
  onUpdated?: () => void;
  /** Called after approve / request-changes so the parent can refresh and close. */
  onReviewDecision?: () => void;
}

const PRIORITIES: TicketPriority[] = ["low", "medium", "high", "urgent"];

export function TicketDetailDrawer({
  ticket,
  columns,
  actors,
  onClose,
  onDelegate,
  onCommented,
  onUpdated,
  onReviewDecision,
}: TicketDetailDrawerProps) {
  const ticketId = ticket?.ticket_id ?? null;
  const [detail, setDetail] = useState<Ticket | null>(ticket);
  const [edit, setEdit] = useState<TicketEditFields | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const commentFieldId = useId();
  const titleFieldId = useId();
  const descFieldId = useId();
  const priorityFieldId = useId();
  const assigneeFieldId = useId();
  const blockedFieldId = useId();
  const blockedByFieldId = useId();
  const panelRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!ticketId) {
      setDetail(null);
      setEdit(null);
      setDraft("");
      setCommentError(null);
      setEditError(null);
      return;
    }
    setDetail(ticket);
    setEdit(ticket ? ticketToEditFields(ticket) : null);
    setDraft("");
    setCommentError(null);
    setEditError(null);
    let cancelled = false;
    setLoading(true);
    fetchTicket(ticketId)
      .then((full) => {
        if (!cancelled) {
          setDetail(full);
          setEdit(ticketToEditFields(full));
        }
      })
      .catch(() => {
        if (!cancelled && ticket) {
          setDetail(ticket);
          setEdit(ticketToEditFields(ticket));
        }
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
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      const panel = panelRef.current;
      if (panel) trapTabKey(panel, e);
    };
    document.addEventListener("keydown", onKey);

    const focusTimer = window.setTimeout(() => {
      const panel = panelRef.current;
      if (!panel) return;
      if (!panel.hasAttribute("tabindex")) panel.tabIndex = -1;
      // Reader mode: focus Close (not the bottom comment box) so mobile keyboards
      // and screen-reader cursors stay at the top of the ticket.
      preferReaderFocus(panel).focus();
    }, 0);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      previous?.focus?.();
    };
  }, [ticketId]);

  const appendCommentLocally = (created: TicketComment) => {
    setDetail((prev) => {
      if (!prev) return prev;
      return { ...prev, comments: [created, ...(prev.comments ?? [])] };
    });
  };

  const patchEdit = <K extends keyof TicketEditFields>(key: K, value: TicketEditFields[K]) => {
    setEdit((prev) => (prev ? { ...prev, [key]: value } : prev));
    setEditError(null);
  };

  const handleSaveEdits = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticketId || !detail || !edit || saving || submitting) return;
    if (!edit.title.trim()) {
      setEditError("Title is required.");
      return;
    }
    const stage = columnStageForTicket(columns, detail.column_id);
    const payload = buildTicketUpdatePayload(detail, edit, stage);
    if (!payload) return;

    setSaving(true);
    setEditError(null);
    try {
      const updated = await updateTicket(ticketId, payload);
      setDetail((prev) => {
        if (!prev) return updated;
        return {
          ...prev,
          ...updated,
          subtask_count: prev.subtask_count,
          subtask_done_count: prev.subtask_done_count,
          comments: prev.comments,
          subtasks: prev.subtasks,
        };
      });
      setEdit(ticketToEditFields(updated));
      onUpdated?.();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to save changes");
    } finally {
      setSaving(false);
    }
  };

  const handleSubmitComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticketId || !draft.trim() || submitting || saving) return;
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
    if (!ticketId || !detail || submitting || saving) return;
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

  if (!ticketId || !detail || !edit) return null;

  const stage = columnStageForTicket(columns, detail.column_id);
  const isReview = stage === "review";
  const dirty = isTicketEditDirty(detail, edit);

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
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Ticket ${detail.ticket_id}`}
        className="relative flex h-[min(92dvh,100%)] w-full max-w-md flex-col rounded-t-2xl border border-[var(--border)] bg-[var(--surface)] shadow-xl sm:h-full sm:rounded-none sm:border-l sm:border-y-0 sm:border-r-0"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--border)] px-4 py-4 pt-[max(1rem,env(safe-area-inset-top))] sm:px-5">
          <div className="min-w-0 flex-1">
            <p className="font-mono text-[11px] text-[var(--muted)]">{detail.ticket_id}</p>
            <label htmlFor={titleFieldId} className="sr-only">
              Title
            </label>
            <input
              id={titleFieldId}
              form="ticket-edit-form"
              className="ak-input mt-1 text-lg font-semibold leading-snug"
              value={edit.title}
              onChange={(e) => patchEdit("title", e.target.value)}
              disabled={saving}
              maxLength={256}
            />
          </div>
          <button
            type="button"
            className="ak-btn-ghost shrink-0 p-1.5"
            aria-label="Close"
            data-drawer-close
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-4 py-4 text-sm sm:px-5">
          {loading ? <p className="text-xs text-[var(--muted)]">Loading full context…</p> : null}

          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-md border px-2 py-1 text-[11px] uppercase ${
                detail.status === "blocked"
                  ? "border-[var(--danger)]/40 text-[var(--danger)]"
                  : "border-[var(--border)] text-[var(--muted)]"
              }`}
            >
              {detail.status}
            </span>
            {stage ? (
              <span className="rounded-md border border-[var(--accent)]/40 bg-[var(--accent-muted)] px-2 py-1 text-[11px] uppercase text-[var(--accent)]">
                {stage.replace("_", " ")}
              </span>
            ) : null}
            <ActorBadge actorId={detail.assigned_to} />
          </div>

          {isReview ? (
            <p className="rounded-lg border border-[var(--accent)]/30 bg-[var(--accent-muted)] px-3 py-2 text-xs text-[var(--foreground)]">
              Review gate — leave a note, then <strong>Approve</strong> (Done) or{" "}
              <strong>Request changes</strong> (back to In Progress). Drag still works on the board.
            </p>
          ) : null}

          <form id="ticket-edit-form" onSubmit={handleSaveEdits} className="space-y-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label
                  htmlFor={priorityFieldId}
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]"
                >
                  Priority
                </label>
                <select
                  id={priorityFieldId}
                  className="ak-input"
                  value={edit.priority}
                  onChange={(e) => patchEdit("priority", e.target.value as TicketPriority)}
                  disabled={saving}
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p.charAt(0).toUpperCase() + p.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label
                  htmlFor={assigneeFieldId}
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]"
                >
                  Assignee
                </label>
                <select
                  id={assigneeFieldId}
                  className="ak-input"
                  value={edit.assigned_to}
                  onChange={(e) => patchEdit("assigned_to", e.target.value)}
                  disabled={saving}
                >
                  <option value="">Unassigned</option>
                  {edit.assigned_to && !actors.some((a) => a.actor_id === edit.assigned_to) ? (
                    <option value={edit.assigned_to}>@{edit.assigned_to}</option>
                  ) : null}
                  {actors.map((a) => (
                    <option key={a.actor_id} value={a.actor_id}>
                      {a.name} ({a.role})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label
                htmlFor={descFieldId}
                className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]"
              >
                Description
              </label>
              <textarea
                id={descFieldId}
                className="ak-input min-h-[5rem] resize-y"
                rows={4}
                value={edit.description}
                onChange={(e) => patchEdit("description", e.target.value)}
                placeholder="Acceptance criteria or agent instructions"
                disabled={saving}
              />
            </div>

            <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2.5">
              <div className="flex items-center gap-2">
                <input
                  id={blockedFieldId}
                  type="checkbox"
                  className="h-4 w-4 accent-[var(--danger)]"
                  checked={edit.blocked}
                  onChange={(e) => patchEdit("blocked", e.target.checked)}
                  disabled={saving}
                />
                <label
                  htmlFor={blockedFieldId}
                  className="flex items-center gap-1.5 text-xs font-medium text-[var(--foreground)]"
                >
                  <AlertOctagon className="h-3.5 w-3.5 text-[var(--danger)]" />
                  Mark as blocked
                </label>
              </div>
              {edit.blocked ? (
                <div>
                  <label
                    htmlFor={blockedByFieldId}
                    className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]"
                  >
                    Blocked by / reason
                  </label>
                  <input
                    id={blockedByFieldId}
                    className="ak-input"
                    value={edit.blocked_by}
                    onChange={(e) => patchEdit("blocked_by", e.target.value)}
                    placeholder="e.g. waiting on API credentials"
                    disabled={saving}
                  />
                </div>
              ) : null}
            </div>

            {editError ? <p className="text-xs text-[var(--danger)]">{editError}</p> : null}

            <div className="flex items-center justify-end">
              <button
                type="submit"
                className="ak-btn-primary"
                disabled={saving || submitting || !dirty || !edit.title.trim()}
              >
                <Save className="h-4 w-4" />
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>

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
              disabled={submitting || saving}
            />
            {commentError ? <p className="text-xs text-[var(--danger)]">{commentError}</p> : null}

            {isReview ? (
              <div className="flex flex-col gap-2">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <button
                    type="button"
                    className="ak-btn-secondary w-full"
                    disabled={submitting || saving}
                    onClick={() => void handleReviewDecision("request_changes")}
                  >
                    <RotateCcw className="h-4 w-4" />
                    Request changes
                  </button>
                  <button
                    type="button"
                    className="ak-btn-primary w-full"
                    disabled={submitting || saving}
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
                    disabled={submitting || saving || !draft.trim()}
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
                  disabled={submitting || saving || !draft.trim()}
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
