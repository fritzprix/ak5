"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  fetchWorkspaceMembers,
  fetchWorkspaces,
  getStoredWorkspaceId,
  inviteWorkspaceMember,
  updateWorkspaceMemberRole,
  type WorkspaceMember,
  type WorkspaceSummary,
} from "@/lib/api";

export default function MembersSettingsPage() {
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canAdmin =
    workspace?.membership_role === "owner" ||
    workspace?.membership_role === "admin";

  const reload = useCallback(async () => {
    const rows = await fetchWorkspaces();
    const id = getStoredWorkspaceId();
    const ws = rows.find((w) => w.workspace_id === id) ?? rows[0] ?? null;
    setWorkspace(ws);
    if (!ws) return;
    setMembers(await fetchWorkspaceMembers(ws.workspace_id));
  }, []);

  useEffect(() => {
    void reload().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load");
    });
  }, [reload]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-y-auto py-2">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <Link href="/settings" className="text-xs text-[var(--muted)] hover:text-[var(--foreground)]">
            ← Settings
          </Link>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">Members</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {workspace?.name ?? "Workspace"} · invite by email
          </p>
        </div>
      </header>

      {error ? (
        <p className="text-sm text-[var(--danger)]" role="alert">
          {error}
        </p>
      ) : null}

      {canAdmin ? (
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!workspace || !email.trim()) return;
            setBusy(true);
            setError(null);
            setInviteToken(null);
            try {
              const inv = await inviteWorkspaceMember(
                workspace.workspace_id,
                email.trim(),
                role
              );
              setInviteToken(inv.token);
              setEmail("");
              await reload();
            } catch (err) {
              setError(err instanceof Error ? err.message : "Invite failed");
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="flex-1 text-xs text-[var(--muted)]">
            Email
            <input
              className="ak-input mt-1"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="text-xs text-[var(--muted)]">
            Role
            <select
              className="ak-input mt-1"
              value={role}
              onChange={(e) => setRole(e.target.value as "admin" | "member")}
            >
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <button type="submit" className="ak-btn-primary" disabled={busy}>
            Invite
          </button>
        </form>
      ) : (
        <p className="text-sm text-[var(--muted)]">
          Only workspace admins can invite members.
        </p>
      )}

      {inviteToken ? (
        <p className="break-all font-mono text-xs text-[var(--accent)]">
          Invite token (share once): {inviteToken}
        </p>
      ) : null}

      <ul className="divide-y divide-[var(--border)] border-t border-[var(--border)]">
        {members.map((m) => (
          <li
            key={m.membership_id}
            className="flex flex-wrap items-center justify-between gap-2 py-3"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{m.display_name}</div>
              <div className="truncate text-xs text-[var(--muted)]">
                {m.email || m.user_id}
              </div>
            </div>
            {canAdmin ? (
              <select
                className="ak-input w-auto py-1 text-xs"
                value={m.role}
                aria-label={`Role for ${m.display_name}`}
                onChange={async (e) => {
                  if (!workspace) return;
                  const next = e.target.value as "owner" | "admin" | "member";
                  setBusy(true);
                  setError(null);
                  try {
                    await updateWorkspaceMemberRole(
                      workspace.workspace_id,
                      m.user_id,
                      next
                    );
                    await reload();
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Update failed");
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <option value="owner">owner</option>
                <option value="admin">admin</option>
                <option value="member">member</option>
              </select>
            ) : (
              <span className="text-xs text-[var(--muted)]">{m.role}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
