"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  createWorkspace,
  fetchWorkspaces,
  getStoredWorkspaceId,
  setStoredWorkspaceId,
  type WorkspaceSummary,
} from "@/lib/api";

export function WorkspaceSwitcher() {
  const router = useRouter();
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    try {
      const rows = await fetchWorkspaces();
      setWorkspaces(rows);
      const stored = getStoredWorkspaceId();
      const match =
        rows.find((w) => w.workspace_id === stored) ?? rows[0] ?? null;
      if (match) {
        setActiveId(match.workspace_id);
        if (stored !== match.workspace_id) {
          setStoredWorkspaceId(match.workspace_id);
        }
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Workspaces unavailable");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (error) {
    return (
      <span
        className="hidden max-w-[10rem] truncate text-[11px] text-[var(--muted)] sm:inline"
        title={error}
      >
        No workspace
      </span>
    );
  }

  if (workspaces.length === 0) {
    return (
      <span className="hidden text-[11px] text-[var(--muted)] sm:inline">
        Loading…
      </span>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <label className="flex min-w-0 max-w-[9rem] items-center sm:max-w-[14rem]">
        <span className="sr-only">Workspace</span>
        <select
          className="ak-input min-w-0 py-1.5 text-base sm:text-xs"
          value={activeId}
          aria-label="Select workspace"
          onChange={(e) => {
            const id = e.target.value;
            if (!id || id === activeId) return;
            setStoredWorkspaceId(id);
            setActiveId(id);
            router.refresh();
            window.location.href = "/";
          }}
        >
          {workspaces.map((ws) => (
            <option key={ws.workspace_id} value={ws.workspace_id}>
              {ws.name}
              {ws.membership_role ? ` · ${ws.membership_role}` : ""}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="ak-btn-ghost hidden px-2 text-xs sm:inline-flex"
        disabled={creating}
        title="Create workspace"
        onClick={async () => {
          const name = window.prompt("New workspace name");
          if (!name?.trim()) return;
          setCreating(true);
          try {
            const ws = await createWorkspace(name.trim());
            setStoredWorkspaceId(ws.workspace_id);
            window.location.href = "/";
          } catch (err) {
            setError(err instanceof Error ? err.message : "Create failed");
            setCreating(false);
          }
        }}
      >
        +
      </button>
    </div>
  );
}
