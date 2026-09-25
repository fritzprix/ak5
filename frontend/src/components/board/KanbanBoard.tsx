"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  closestCorners,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import {
  fetchBoard,
  fetchActors,
  moveTicket,
  createTicket,
  delegateSubtask,
  subscribeToBoardEvents,
} from "@/lib/api";
import { buildAgentSetupMarkdown } from "@/lib/agentSetup";
import { decideLiveRefresh, shouldFlushDeferredRefresh } from "@/lib/liveRefresh";
import {
  buildTicketIndex,
  isEmptyColumnSlotId,
  resolveDropLabel,
  resolveTargetColumnId,
  resolveTicketTitle,
} from "@/lib/boardDnD";
import { Board, Column, Ticket, Actor } from "@/lib/types";
import { KanbanColumn } from "./KanbanColumn";
import { TicketCard } from "./TicketCard";
import { TicketDetailDrawer } from "./TicketDetailDrawer";
import { AgentFleetStrip } from "../actor/AgentFleetStrip";
import { Dialog } from "../ui/Dialog";
import { Plus, RefreshCw, Radio, ClipboardCopy, Check, Terminal } from "lucide-react";

interface KanbanBoardProps {
  boardId: string;
}

const LIVE_REFRESH_DEBOUNCE_MS = 700;

export const KanbanBoard: React.FC<KanbanBoardProps> = ({ boardId }) => {
  const [board, setBoard] = useState<Board | null>(null);
  const [actors, setActors] = useState<Actor[]>([]);
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null);
  const [detailTicket, setDetailTicket] = useState<Ticket | null>(null);
  const [delegatingTicket, setDelegatingTicket] = useState<Ticket | null>(null);
  const [isNewTicketOpen, setIsNewTicketOpen] = useState(false);
  const [isAgentSetupOpen, setIsAgentSetupOpen] = useState(false);
  const [isConnectedSSE, setIsConnectedSSE] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoadingBoard, setIsLoadingBoard] = useState(true);
  const copiedResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const liveRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pauseLiveRefreshRef = useRef(false);
  const pendingLiveRefreshRef = useRef(false);
  const boardIdRef = useRef(boardId);

  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState<string>("medium");
  const [newAssignee, setNewAssignee] = useState<string>("");

  const [subtaskTitle, setSubtaskTitle] = useState("");
  const [subtaskDesc, setSubtaskDesc] = useState("");
  const [subtaskAgent, setSubtaskAgent] = useState("");

  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: { distance: 8 },
    }),
    useSensor(TouchSensor, {
      // Delay so vertical/horizontal board scroll is not stolen by drag on phones
      activationConstraint: { delay: 220, tolerance: 8 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const isUiBlocking =
    isNewTicketOpen || Boolean(delegatingTicket) || isAgentSetupOpen || Boolean(detailTicket);

  useEffect(() => {
    boardIdRef.current = boardId;
  }, [boardId]);

  useEffect(() => {
    return () => {
      if (copiedResetTimer.current) clearTimeout(copiedResetTimer.current);
      if (liveRefreshTimer.current) clearTimeout(liveRefreshTimer.current);
    };
  }, []);

  const reloadBoard = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = Boolean(opts?.silent);
    const id = boardIdRef.current;
    try {
      if (!silent) {
        setIsLoadingBoard(true);
        setLoadError(null);
      }
      const data = await fetchBoard(id);
      setBoard(data);
      // Keep detailTicket object identity stable while the drawer is open so
      // typing/comment drafts are not wiped by SSE-driven board refreshes.
      setDetailTicket((prev) => {
        if (!prev) return null;
        const stillOnBoard = data.columns.some((col) =>
          col.tickets.some((t) => t.ticket_id === prev.ticket_id)
        );
        return stillOnBoard ? prev : null;
      });
    } catch (err) {
      console.error("Failed to load board:", err);
      if (!silent) {
        setBoard(null);
        setLoadError(err instanceof Error ? err.message : `Failed to load board '${id}'`);
      }
    } finally {
      if (!silent) setIsLoadingBoard(false);
    }
  }, []);

  const scheduleLiveRefresh = useCallback(() => {
    if (pauseLiveRefreshRef.current) {
      pendingLiveRefreshRef.current = true;
      return;
    }
    if (liveRefreshTimer.current) clearTimeout(liveRefreshTimer.current);
    liveRefreshTimer.current = setTimeout(() => {
      if (pauseLiveRefreshRef.current) {
        pendingLiveRefreshRef.current = true;
        return;
      }
      void reloadBoard({ silent: true });
    }, LIVE_REFRESH_DEBOUNCE_MS);
  }, [reloadBoard]);

  useEffect(() => {
    pauseLiveRefreshRef.current = decideLiveRefresh(isUiBlocking) === "defer";
    if (shouldFlushDeferredRefresh(isUiBlocking, pendingLiveRefreshRef.current)) {
      pendingLiveRefreshRef.current = false;
      void reloadBoard({ silent: true });
    }
  }, [isUiBlocking, reloadBoard]);

  const closeNewTicket = useCallback(() => setIsNewTicketOpen(false), []);
  const closeDelegate = useCallback(() => setDelegatingTicket(null), []);
  const closeAgentSetup = useCallback(() => {
    setIsAgentSetupOpen(false);
    setCopyError(null);
  }, []);
  const closeDetail = useCallback(() => setDetailTicket(null), []);

  const copyAgentSetup = async () => {
    if (!board) return;
    const instructions = buildAgentSetupMarkdown(board, actors);
    try {
      await navigator.clipboard.writeText(instructions);
      setCopyError(null);
      setCopied(true);
      if (copiedResetTimer.current) clearTimeout(copiedResetTimer.current);
      copiedResetTimer.current = setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy:", err);
      setCopied(false);
      setCopyError(err instanceof Error ? err.message : "Failed to copy to clipboard");
    }
  };

  useEffect(() => {
    setBoard(null);
    setIsConnectedSSE(false);
    setActionError(null);
    setLoadError(null);
    setCopyError(null);
    setCopied(false);
    setDetailTicket(null);
    pendingLiveRefreshRef.current = false;
    if (liveRefreshTimer.current) clearTimeout(liveRefreshTimer.current);
    void reloadBoard();
    fetchActors().then(setActors).catch(console.error);

    const cleanup = subscribeToBoardEvents(
      () => {
        scheduleLiveRefresh();
      },
      {
        onOpen: () => setIsConnectedSSE(true),
        onError: () => setIsConnectedSSE(false),
      }
    );

    return () => {
      cleanup();
      if (liveRefreshTimer.current) clearTimeout(liveRefreshTimer.current);
    };
  }, [boardId, reloadBoard, scheduleLiveRefresh]);

  const handleDragStart = (event: DragStartEvent) => {
    const ticketId = event.active.id as string;
    if (!board) return;
    for (const col of board.columns) {
      const found = col.tickets.find((t) => t.ticket_id === ticketId);
      if (found) {
        setActiveTicket(found);
        break;
      }
    }
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveTicket(null);
    if (!over || !board) return;

    const activeId = active.id as string;
    if (isEmptyColumnSlotId(activeId)) return;

    const overId = over.id as string;
    const targetColumnId = resolveTargetColumnId(overId, board);
    if (!targetColumnId) return;

    let targetCol: Column | undefined = board.columns.find((c) => c.column_id === targetColumnId);
    if (!targetCol) return;

    let targetIndex = -1;
    if (!isEmptyColumnSlotId(overId) && overId !== targetColumnId) {
      targetIndex = targetCol.tickets.findIndex((t) => t.ticket_id === overId);
    }

    const destTickets = targetCol.tickets.filter((t) => t.ticket_id !== activeId);
    let prevId: string | null = null;
    let nextId: string | null = null;

    if (targetIndex === -1 || targetIndex >= destTickets.length) {
      prevId = destTickets.length > 0 ? destTickets[destTickets.length - 1].ticket_id : null;
    } else if (targetIndex === 0) {
      nextId = destTickets.length > 0 ? destTickets[0].ticket_id : null;
    } else {
      prevId = destTickets[targetIndex - 1].ticket_id;
      nextId = destTickets[targetIndex].ticket_id;
    }

    try {
      setActionError(null);
      await moveTicket(activeId, targetCol.column_id, prevId, nextId);
      await reloadBoard({ silent: true });
    } catch (err) {
      console.error("Move ticket failed:", err);
      setActionError(err instanceof Error ? err.message : "Move ticket failed");
      await reloadBoard({ silent: true });
    }
  };

  const handleCreateTicket = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!board || !newTitle.trim()) return;
    const firstCol = board.columns[0];
    try {
      setActionError(null);
      await createTicket(
        board.board_id,
        firstCol.column_id,
        newTitle,
        newDesc,
        newPriority,
        newAssignee || null
      );
      setNewTitle("");
      setNewDesc("");
      setIsNewTicketOpen(false);
      await reloadBoard({ silent: true });
    } catch (err) {
      console.error("Create ticket failed:", err);
      setActionError(err instanceof Error ? err.message : "Create ticket failed");
    }
  };

  const handleDelegateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!delegatingTicket || !subtaskAgent || !subtaskTitle.trim()) return;
    try {
      setActionError(null);
      await delegateSubtask(delegatingTicket.ticket_id, subtaskAgent, subtaskTitle, subtaskDesc);
      setSubtaskTitle("");
      setSubtaskDesc("");
      setSubtaskAgent("");
      setDelegatingTicket(null);
      await reloadBoard({ silent: true });
    } catch (err) {
      console.error("Delegate subtask failed:", err);
      setActionError(err instanceof Error ? err.message : "Delegate subtask failed");
    }
  };

  if (isLoadingBoard && !board) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3">
        <RefreshCw className="h-7 w-7 animate-spin text-[var(--accent)]" />
        <p className="text-sm text-[var(--muted)]">Connecting to AK5 Gateway…</p>
      </div>
    );
  }

  if (loadError || !board) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4">
        <p className="max-w-md text-center text-sm text-[var(--danger)]">
          {loadError || `Board '${boardId}' was not found.`}
        </p>
        <button type="button" onClick={() => reloadBoard()} className="ak-btn-secondary">
          <RefreshCw className="h-4 w-4" />
          Retry
        </button>
      </div>
    );
  }

  const agents = actors.filter((a) => a.actor_type === "agent");
  const ticketIndex = buildTicketIndex(board);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 sm:gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-semibold tracking-tight text-[var(--foreground)]">
              {board.name}
            </h2>
            <span
              className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[10px] ${
                isConnectedSSE
                  ? "border-[var(--success)]/40 text-[var(--success)]"
                  : "border-[var(--border)] text-[var(--muted)]"
              }`}
            >
              <Radio className={`h-3 w-3 ${isConnectedSSE ? "animate-pulse" : ""}`} />
              {isConnectedSSE ? "Live" : "Reconnecting…"}
            </span>
            <span className="hidden font-mono text-[10px] text-[var(--muted)] sm:inline">{board.board_id}</span>
          </div>
          {board.description ? (
            <p className="mt-0.5 truncate text-xs text-[var(--muted)]">{board.description}</p>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <button
            type="button"
            onClick={() => reloadBoard()}
            className="ak-btn-secondary p-2"
            title="Refresh"
            aria-label="Refresh board"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setIsAgentSetupOpen(true)}
            className="ak-btn-secondary p-2 sm:px-3.5"
            title="Agent Setup"
            aria-label="Agent setup"
          >
            <Terminal className="h-4 w-4" />
            <span className="hidden sm:inline">Agent Setup</span>
          </button>
          <button type="button" onClick={() => setIsNewTicketOpen(true)} className="ak-btn-primary">
            <Plus className="h-4 w-4" />
            <span className="sm:hidden">New</span>
            <span className="hidden sm:inline">New Ticket</span>
          </button>
        </div>
      </div>

      <AgentFleetStrip actors={actors} />

      {actionError ? (
        <div className="shrink-0 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 px-3 py-2 text-sm text-[var(--danger)]">
          {actionError}
        </div>
      ) : null}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        accessibility={{
          announcements: {
            onDragStart({ active }) {
              const title = resolveTicketTitle(String(active.id), ticketIndex);
              return `Picked up ticket "${title}". Use arrow keys to move, Space to drop.`;
            },
            onDragOver({ active, over }) {
              const title = resolveTicketTitle(String(active.id), ticketIndex);
              if (!over) return `Ticket "${title}" is no longer over a droppable area.`;
              const where = resolveDropLabel(String(over.id), board.columns, ticketIndex);
              return `Ticket "${title}" is over ${where}.`;
            },
            onDragEnd({ active, over }) {
              const title = resolveTicketTitle(String(active.id), ticketIndex);
              if (!over) return `Dragging cancelled for ticket "${title}".`;
              const where = resolveDropLabel(String(over.id), board.columns, ticketIndex);
              return `Dropped "${title}" into ${where}.`;
            },
            onDragCancel({ active }) {
              const title = resolveTicketTitle(String(active.id), ticketIndex);
              return `Dragging cancelled for ticket "${title}".`;
            },
          },
        }}
      >
        <p className="sr-only">
          Keyboard: focus a ticket handle, press Space to pick up, arrow keys to move between columns,
          Space to drop.
        </p>
        <div className="ak-board-scroll flex min-h-0 flex-1 gap-3 overflow-x-auto overflow-y-hidden pb-1">
          {board.columns.map((column) => (
            <KanbanColumn
              key={column.column_id}
              column={column}
              onOpenTicket={setDetailTicket}
              onDelegateClick={setDelegatingTicket}
            />
          ))}
        </div>

        <DragOverlay>
          {activeTicket ? <TicketCard ticket={activeTicket} compact /> : null}
        </DragOverlay>
      </DndContext>

      <TicketDetailDrawer
        ticket={detailTicket}
        columns={board.columns}
        onClose={closeDetail}
        onDelegate={setDelegatingTicket}
        onCommented={() => {
          // Drawer stays open (live refresh paused); flush board when it closes.
          pendingLiveRefreshRef.current = true;
        }}
        onReviewDecision={() => {
          pendingLiveRefreshRef.current = true;
          void reloadBoard({ silent: true });
        }}
      />

      <Dialog
        open={isNewTicketOpen}
        onClose={closeNewTicket}
        title="Create ticket"
        footer={
          <>
            <button type="button" className="ak-btn-ghost" onClick={closeNewTicket}>
              Cancel
            </button>
            <button type="submit" form="create-ticket-form" className="ak-btn-primary">
              Create
            </button>
          </>
        }
      >
        <form id="create-ticket-form" onSubmit={handleCreateTicket} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-[var(--muted)]">Title</label>
            <input
              className="ak-input"
              required
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="e.g. Implement WebP Avatar Resizer"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-[var(--muted)]">Description</label>
            <textarea
              className="ak-input"
              rows={3}
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Acceptance criteria or agent instructions"
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-[var(--muted)]">Priority</label>
              <select className="ak-input" value={newPriority} onChange={(e) => setNewPriority(e.target.value)}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--muted)]">Assignee</label>
              <select className="ak-input" value={newAssignee} onChange={(e) => setNewAssignee(e.target.value)}>
                <option value="">Unassigned</option>
                {actors.map((a) => (
                  <option key={a.actor_id} value={a.actor_id}>
                    {a.name} ({a.role})
                  </option>
                ))}
              </select>
            </div>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(delegatingTicket)}
        onClose={closeDelegate}
        title="Delegate subtask"
        size="lg"
        footer={
          <>
            <button type="button" className="ak-btn-ghost" onClick={closeDelegate}>
              Cancel
            </button>
            <button type="submit" form="delegate-form" className="ak-btn-primary">
              Delegate
            </button>
          </>
        }
      >
        {delegatingTicket ? (
          <form id="delegate-form" onSubmit={handleDelegateSubmit} className="space-y-3">
            <p className="text-xs text-[var(--muted)]">
              Parent{" "}
              <span className="font-mono text-[var(--accent)]">{delegatingTicket.ticket_id}</span> —{" "}
              {delegatingTicket.title}
            </p>
            <div>
              <label className="mb-1 block text-xs text-[var(--muted)]">Target agent</label>
              <select
                className="ak-input"
                required
                value={subtaskAgent}
                onChange={(e) => setSubtaskAgent(e.target.value)}
              >
                <option value="">Select an agent…</option>
                {agents.map((a) => (
                  <option key={a.actor_id} value={a.actor_id}>
                    @{a.actor_id} — {a.role} [{a.capabilities.join(", ")}]
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--muted)]">Subtask title</label>
              <input
                className="ak-input"
                required
                value={subtaskTitle}
                onChange={(e) => setSubtaskTitle(e.target.value)}
                placeholder="e.g. 200x200 WebP Thumbnail Generator"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-[var(--muted)]">Instructions</label>
              <textarea
                className="ak-input"
                rows={3}
                value={subtaskDesc}
                onChange={(e) => setSubtaskDesc(e.target.value)}
                placeholder="Requirements, formats, tests"
              />
            </div>
          </form>
        ) : null}
      </Dialog>

      <Dialog
        open={isAgentSetupOpen}
        onClose={closeAgentSetup}
        title="Agent setup"
        size="xl"
        footer={
          <>
            <button type="button" className="ak-btn-ghost" onClick={closeAgentSetup}>
              Close
            </button>
            <button type="button" className="ak-btn-primary" onClick={copyAgentSetup}>
              {copied ? <Check className="h-4 w-4" /> : <ClipboardCopy className="h-4 w-4" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </>
        }
      >
        <p className="mb-3 text-xs text-[var(--muted)]">
          CLI login once, then poll tickets on a schedule — no push interrupts.
        </p>
        <pre className="max-h-[50vh] overflow-auto rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 font-mono text-[11px] leading-relaxed text-[var(--foreground)] whitespace-pre-wrap">
          {buildAgentSetupMarkdown(board, actors)}
        </pre>
        {copyError ? <p className="mt-2 text-sm text-[var(--danger)]">{copyError}</p> : null}
      </Dialog>
    </div>
  );
};
