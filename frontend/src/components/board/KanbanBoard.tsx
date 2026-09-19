"use client";

import React, { useEffect, useState, useTransition } from "react";
import {
  DndContext,
  DragEndEvent,
  DragOverEvent,
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
import { Board, Column, Ticket, Actor } from "@/lib/types";
import { KanbanColumn } from "./KanbanColumn";
import { TicketCard } from "./TicketCard";
import { Plus, RefreshCw, Radio, Sparkles } from "lucide-react";

export const KanbanBoard: React.FC = () => {
  const [board, setBoard] = useState<Board | null>(null);
  const [actors, setActors] = useState<Actor[]>([]);
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null);
  const [delegatingTicket, setDelegatingTicket] = useState<Ticket | null>(null);
  const [isNewTicketOpen, setIsNewTicketOpen] = useState(false);
  const [isConnectedSSE, setIsConnectedSSE] = useState(false);

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

  const reloadBoard = async () => {
    try {
      const data = await fetchBoard();
      setBoard(data);
    } catch (err) {
      console.error("Failed to load board:", err);
    }
  };

  useEffect(() => {
    reloadBoard();
    fetchActors().then(setActors).catch(console.error);

    // Subscribe to SSE
    const cleanup = subscribeToBoardEvents((evt, data) => {
      setIsConnectedSSE(true);
      // Reload on any Kanban event
      reloadBoard();
    });

    return () => {
      cleanup();
    };
  }, []);

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
      await moveTicket(activeId, targetCol.column_id, prevId, nextId);
      await reloadBoard();
    } catch (err) {
      console.error("Move ticket failed:", err);
      await reloadBoard();
    }
  };

  const handleCreateTicket = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!board || !newTitle.trim()) return;

    const firstCol = board.columns[0];
    try {
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
    }
  };

  const handleDelegateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!delegatingTicket || !subtaskAgent || !subtaskTitle.trim()) return;

    try {
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
    }
  };

  if (!board) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
        <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin" />
        <p className="text-slate-400 text-sm">Connecting to AK5 Gateway...</p>
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
            onClick={() => setIsNewTicketOpen(true)}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium transition-colors shadow-lg shadow-cyan-900/30"
          >
            <Plus className="w-4 h-4" />
            <span>New Ticket</span>
          </button>
        </div>
      </div>

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
    </div>
  );
};
