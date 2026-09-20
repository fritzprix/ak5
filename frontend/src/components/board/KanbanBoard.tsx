"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors,
  closestCorners,
} from "@dnd-kit/core";
import {
  fetchBoard,
  fetchActors,
  moveTicket,
  createTicket,
  delegateSubtask,
  subscribeToBoardEvents,
} from "@/lib/api";
import { buildAgentSetupMarkdown } from "@/lib/agentSetup";
import { Board, Column, Ticket, Actor } from "@/lib/types";
import { KanbanColumn } from "./KanbanColumn";
import { TicketCard } from "./TicketCard";
import { Plus, RefreshCw, Radio, Sparkles, ClipboardCopy, Check, Terminal } from "lucide-react";

interface KanbanBoardProps {
  boardId: string;
}

export const KanbanBoard: React.FC<KanbanBoardProps> = ({ boardId }) => {
  const [board, setBoard] = useState<Board | null>(null);
  const [actors, setActors] = useState<Actor[]>([]);
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null);
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

  // Form states
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState<string>("medium");
  const [newAssignee, setNewAssignee] = useState<string>("");

  const [subtaskTitle, setSubtaskTitle] = useState("");
  const [subtaskDesc, setSubtaskDesc] = useState("");
  const [subtaskAgent, setSubtaskAgent] = useState("");

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 5,
      },
    })
  );

  useEffect(() => {
    return () => {
      if (copiedResetTimer.current) clearTimeout(copiedResetTimer.current);
    };
  }, []);

  const reloadBoard = async () => {
    try {
      setIsLoadingBoard(true);
      setLoadError(null);
      const data = await fetchBoard(boardId);
      setBoard(data);
    } catch (err) {
      console.error("Failed to load board:", err);
      setBoard(null);
      setLoadError(err instanceof Error ? err.message : `Failed to load board '${boardId}'`);
    } finally {
      setIsLoadingBoard(false);
    }
  };

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
    reloadBoard();
    fetchActors().then(setActors).catch(console.error);

    const cleanup = subscribeToBoardEvents(
      () => {
        reloadBoard();
      },
      {
        onOpen: () => setIsConnectedSSE(true),
        onError: () => setIsConnectedSSE(false),
      }
    );

    return () => {
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when boardId changes
  }, [boardId]);

  const handleDragStart = (event: DragStartEvent) => {
    const { active } = event;
    const ticketId = active.id as string;
    // Find ticket in columns
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
    const overId = over.id as string;

    // Determine target column and position
    let targetCol: Column | undefined;
    let targetIndex = -1;

    // Check if dropped directly onto a Column container
    targetCol = board.columns.find((c) => c.column_id === overId);

    if (!targetCol) {
      // Dropped onto a Ticket card
      for (const col of board.columns) {
        const idx = col.tickets.findIndex((t) => t.ticket_id === overId);
        if (idx !== -1) {
          targetCol = col;
          targetIndex = idx;
          break;
        }
      }
    }

    if (!targetCol) return;

    // Optimistically calculate previous and next tickets for Lexorank
    const destTickets = targetCol.tickets.filter((t) => t.ticket_id !== activeId);
    let prevId: string | null = null;
    let nextId: string | null = null;

    if (targetIndex === -1 || targetIndex >= destTickets.length) {
      // Append to the end
      prevId = destTickets.length > 0 ? destTickets[destTickets.length - 1].ticket_id : null;
    } else if (targetIndex === 0) {
      // Insert at the beginning
      nextId = destTickets.length > 0 ? destTickets[0].ticket_id : null;
    } else {
      // Insert in between
      prevId = destTickets[targetIndex - 1].ticket_id;
      nextId = destTickets[targetIndex].ticket_id;
    }

    try {
      setActionError(null);
      await moveTicket(activeId, targetCol.column_id, prevId, nextId);
      await reloadBoard();
    } catch (err) {
      console.error("Move ticket failed:", err);
      setActionError(err instanceof Error ? err.message : "Move ticket failed");
      await reloadBoard();
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
      await reloadBoard();
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
      await delegateSubtask(
        delegatingTicket.ticket_id,
        subtaskAgent,
        subtaskTitle,
        subtaskDesc
      );
      setSubtaskTitle("");
      setSubtaskDesc("");
      setSubtaskAgent("");
      setDelegatingTicket(null);
      await reloadBoard();
    } catch (err) {
      console.error("Delegate subtask failed:", err);
      setActionError(err instanceof Error ? err.message : "Delegate subtask failed");
    }
  };

  if (isLoadingBoard && !board) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
        <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin" />
        <p className="text-slate-400 text-sm">Connecting to AK5 Gateway...</p>
      </div>
    );
  }

  if (loadError || !board) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <p className="text-rose-300 text-sm text-center max-w-md">
          {loadError || `Board '${boardId}' was not found.`}
        </p>
        <button
          type="button"
          onClick={() => reloadBoard()}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 text-sm hover:border-slate-500"
        >
          <RefreshCw className="w-4 h-4" />
          Retry
        </button>
      </div>
    );
  }

  const agents = actors.filter((a) => a.actor_type === "agent");

  return (
    <div className="flex flex-col h-full">
      {/* Top Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6 pb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-3">
          <div>
            <h2 className="text-lg font-bold text-slate-100">{board.name}</h2>
            <p className="text-xs text-slate-400">{board.description || "Kanban board"}</p>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono border ${
              isConnectedSSE
                ? "bg-emerald-950/60 text-emerald-400 border-emerald-700/50"
                : "bg-slate-800 text-slate-400 border-slate-700"
            }`}
          >
            <Radio className={`w-3 h-3 ${isConnectedSSE ? "animate-pulse text-emerald-400" : ""}`} />
            <span>{isConnectedSSE ? "Live SSE Stream" : "Connecting..."}</span>
          </span>
          <span className="text-[11px] font-mono text-slate-500">{board.board_id}</span>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => reloadBoard()}
            className="p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:text-white hover:border-slate-700 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setIsAgentSetupOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-sm font-medium transition-colors shadow-lg shadow-purple-900/30"
            title="Copy Agent Setup Instructions"
          >
            <Terminal className="w-4 h-4" />
            <span>Agent Setup</span>
          </button>
          <button
            onClick={() => setIsNewTicketOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium transition-colors shadow-lg shadow-cyan-900/30"
          >
            <Plus className="w-4 h-4" />
            <span>New Ticket</span>
          </button>
        </div>
      </div>

      {actionError && (
        <div className="mb-4 px-3 py-2 rounded-lg border border-rose-800/60 bg-rose-950/50 text-rose-300 text-sm">
          {actionError}
        </div>
      )}

      {/* Kanban Canvas with DnD */}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <div className="flex gap-4 overflow-x-auto pb-6">
          {board.columns.map((column) => (
            <KanbanColumn
              key={column.column_id}
              column={column}
              onDelegateClick={(t) => setDelegatingTicket(t)}
            />
          ))}
        </div>

        <DragOverlay>
          {activeTicket ? <TicketCard ticket={activeTicket} /> : null}
        </DragOverlay>
      </DndContext>

      {/* New Ticket Modal */}
      {isNewTicketOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 w-full max-w-md shadow-2xl">
            <h3 className="text-lg font-semibold text-slate-100 mb-4">Create New Ticket</h3>
            <form onSubmit={handleCreateTicket} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Title</label>
                <input
                  type="text"
                  required
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. Implement WebP Avatar Resizer"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Description</label>
                <textarea
                  rows={3}
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="Provide context, acceptance criteria or instructions for AI agents"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Priority</label>
                  <select
                    value={newPriority}
                    onChange={(e) => setNewPriority(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Assignee</label>
                  <select
                    value={newAssignee}
                    onChange={(e) => setNewAssignee(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="">Unassigned</option>
                    {actors.map((a) => (
                      <option key={a.actor_id} value={a.actor_id}>
                        {a.name} ({a.role})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsNewTicketOpen(false)}
                  className="px-4 py-2 text-sm text-slate-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-sm font-medium"
                >
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delegate Subtask Modal */}
      {delegatingTicket && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 w-full max-w-lg shadow-2xl">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-5 h-5 text-purple-400" />
              <h3 className="text-lg font-semibold text-slate-100">
                Delegate Subtask to Agent
              </h3>
            </div>
            <p className="text-xs text-slate-400 mb-4">
              Parent Ticket: <span className="font-mono text-cyan-400">{delegatingTicket.ticket_id}</span> ({delegatingTicket.title})
            </p>

            <form onSubmit={handleDelegateSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Target AI Agent</label>
                <select
                  required
                  value={subtaskAgent}
                  onChange={(e) => setSubtaskAgent(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-purple-500"
                >
                  <option value="">Select an Agent...</option>
                  {agents.map((a) => (
                    <option key={a.actor_id} value={a.actor_id}>
                      @{a.actor_id} — {a.name} ({a.role}) [Caps: {a.capabilities.join(", ")}]
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Subtask Title</label>
                <input
                  type="text"
                  required
                  value={subtaskTitle}
                  onChange={(e) => setSubtaskTitle(e.target.value)}
                  placeholder="e.g. 200x200 WebP Thumbnail Generator"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-purple-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Subtask Instructions</label>
                <textarea
                  rows={3}
                  value={subtaskDesc}
                  onChange={(e) => setSubtaskDesc(e.target.value)}
                  placeholder="Specific requirements, formats, test parameters"
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-purple-500"
                />
              </div>

              <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setDelegatingTicket(null)}
                  className="px-4 py-2 text-sm text-slate-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-medium flex items-center gap-2"
                >
                  <Sparkles className="w-4 h-4" />
                  <span>Delegate</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Agent Setup Instructions Modal */}
      {isAgentSetupOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 w-full max-w-2xl shadow-2xl flex flex-col max-h-[85vh]">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Terminal className="w-5 h-5 text-purple-400" />
                <h3 className="text-lg font-semibold text-slate-100">Agent Setup Instructions</h3>
              </div>
              <button
                type="button"
                aria-label="Close agent setup"
                onClick={() => {
                  setIsAgentSetupOpen(false);
                  setCopyError(null);
                }}
                className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
              >
                ✕
              </button>
            </div>

            <div className="flex-1 overflow-auto mb-4">
              <pre className="bg-slate-950 border border-slate-800 rounded-lg p-4 text-xs text-slate-300 font-mono whitespace-pre-wrap leading-relaxed">
                {buildAgentSetupMarkdown(board, actors)}
              </pre>
            </div>

            {copyError && (
              <p className="mb-3 text-sm text-rose-300">{copyError}</p>
            )}

            <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
              <button
                type="button"
                onClick={() => {
                  setIsAgentSetupOpen(false);
                  setCopyError(null);
                }}
                className="px-4 py-2 text-sm text-slate-400 hover:text-white"
              >
                Close
              </button>
              <button
                type="button"
                onClick={copyAgentSetup}
                className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                  copied
                    ? "bg-emerald-600 text-white"
                    : "bg-purple-600 hover:bg-purple-500 text-white"
                }`}
              >
                {copied ? (
                  <>
                    <Check className="w-4 h-4" />
                    <span>Copied!</span>
                  </>
                ) : (
                  <>
                    <ClipboardCopy className="w-4 h-4" />
                    <span>Copy to Clipboard</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
