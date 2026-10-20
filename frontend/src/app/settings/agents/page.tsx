"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  createWorkspaceAgent,
  disableWorkspaceAgent,
  fetchWorkspaces,
  fetchWorkspaceAgents,
  getStoredWorkspaceId,
  issueAgentCredential,
  revokeAgentCredential,
  type WorkspaceAgent,
  type WorkspaceSummary,
} from "@/lib/api";

export default function AgentsSettingsPage() {
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [agents, setAgents] = useState<WorkspaceAgent[]>([]);
  const [name, setName] = useState("");
  const [workflowRole, setWorkflowRole] = useState<
    "triage" | "worker" | "gatekeeper"
  >("worker");
  const [caps, setCaps] = useState("");
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
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
    setAgents(await fetchWorkspaceAgents(ws.workspace_id));
  }, []);

  useEffect(() => {
    void reload().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load");
    });
  }, [reload]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-y-auto py-2">
      <header>
        <Link href="/settings" className="text-xs text-[var(--muted)] hover:text-[var(--foreground)]">
          ← Settings
        </Link>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Agents</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Issue opaque tokens once, then revoke or disable as needed.
        </p>
      </header>

      {error ? (
        <p className="text-sm text-[var(--danger)]" role="alert">
          {error}
        </p>
      ) : null}

      {issuedToken ? (
        <div className="rounded-lg border border-[var(--accent)]/40 bg-[var(--accent-muted)] p-3">
          <p className="text-xs font-medium text-[var(--accent)]">
            Copy this token now — it will not be shown again.
          </p>
          <p className="mt-2 break-all font-mono text-xs">{issuedToken}</p>
          <button
            type="button"
            className="ak-btn-ghost mt-2 text-xs"
            onClick={() => setIssuedToken(null)}
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {canAdmin ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!workspace || !name.trim()) return;
            setBusy(true);
            setError(null);
            setIssuedToken(null);
            try {
              const agent = await createWorkspaceAgent(workspace.workspace_id, {
                name: name.trim(),
                workflow_role: workflowRole,
                capabilities: caps
                  .split(",")
                  .map((c) => c.trim())
                  .filter(Boolean),
              });
              const token = agent.credentials[0]?.access_token ?? null;
              setIssuedToken(token);
              setName("");
              setCaps("");
              await reload();
            } catch (err) {
              setError(err instanceof Error ? err.message : "Create failed");
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="flex-1 text-xs text-[var(--muted)]">
              Name
              <input
                className="ak-input mt-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            <label className="text-xs text-[var(--muted)]">
              Role
              <select
                className="ak-input mt-1"
                value={workflowRole}
                onChange={(e) =>
                  setWorkflowRole(
                    e.target.value as "triage" | "worker" | "gatekeeper"
                  )
                }
              >
                <option value="worker">worker</option>
                <option value="triage">triage</option>
                <option value="gatekeeper">gatekeeper</option>
              </select>
            </label>
          </div>
          <label className="text-xs text-[var(--muted)]">
            Capabilities (comma-separated)
            <input
              className="ak-input mt-1"
              value={caps}
              onChange={(e) => setCaps(e.target.value)}
              placeholder="python, review"
            />
          </label>
          <button type="submit" className="ak-btn-primary self-start" disabled={busy}>
            Create agent & issue token
          </button>
        </form>
      ) : (
        <p className="text-sm text-[var(--muted)]">
          Only workspace admins can create agents.
        </p>
      )}

      <ul className="divide-y divide-[var(--border)] border-t border-[var(--border)]">
        {agents.map((agent) => {
          const activeCreds = agent.credentials.filter((c) => !c.revoked_at);
          return (
            <li key={agent.agent_id} className="flex flex-col gap-2 py-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-medium">
                    {agent.name}{" "}
                    <span className="font-mono text-xs text-[var(--muted)]">
                      {agent.agent_id}
                    </span>
                  </div>
                  <div className="text-xs text-[var(--muted)]">
                    {agent.workflow_role}
                    {agent.capabilities.length
                      ? ` · ${agent.capabilities.join(", ")}`
                      : ""}
                    {agent.disabled_at ? " · disabled" : ""}
                  </div>
                </div>
                {canAdmin && !agent.disabled_at ? (
                  <div className="flex flex-wrap gap-1">
                    <button
                      type="button"
                      className="ak-btn-ghost text-xs"
                      disabled={busy}
                      onClick={async () => {
                        if (!workspace) return;
                        setBusy(true);
                        setError(null);
                        try {
                          const cred = await issueAgentCredential(
                            workspace.workspace_id,
                            agent.agent_id
                          );
                          setIssuedToken(cred.access_token ?? null);
                          await reload();
                        } catch (err) {
                          setError(
                            err instanceof Error ? err.message : "Issue failed"
                          );
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Issue token
                    </button>
                    <button
                      type="button"
                      className="ak-btn-ghost text-xs text-[var(--danger)]"
                      disabled={busy}
                      onClick={async () => {
                        if (!workspace) return;
                        setBusy(true);
                        try {
                          await disableWorkspaceAgent(
                            workspace.workspace_id,
                            agent.agent_id
                          );
                          await reload();
                        } catch (err) {
                          setError(
                            err instanceof Error ? err.message : "Disable failed"
                          );
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Disable
                    </button>
                  </div>
                ) : null}
              </div>
              <ul className="space-y-1 pl-1 text-xs text-[var(--muted)]">
                {activeCreds.length === 0 ? (
                  <li>No active credentials</li>
                ) : (
                  activeCreds.map((c) => (
                    <li
                      key={c.credential_id}
                      className="flex flex-wrap items-center justify-between gap-2"
                    >
                      <span className="font-mono">
                        {c.credential_id}
                        {c.last_used_at
                          ? ` · last used ${c.last_used_at}`
                          : " · never used"}
                      </span>
                      {canAdmin ? (
                        <button
                          type="button"
                          className="ak-btn-ghost px-1.5 py-0.5 text-[11px]"
                          disabled={busy}
                          onClick={async () => {
                            if (!workspace) return;
                            setBusy(true);
                            try {
                              await revokeAgentCredential(
                                workspace.workspace_id,
                                agent.agent_id,
                                c.credential_id
                              );
                              await reload();
                            } catch (err) {
                              setError(
                                err instanceof Error
                                  ? err.message
                                  : "Revoke failed"
                              );
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          Revoke
                        </button>
                      ) : null}
                    </li>
                  ))
                )}
              </ul>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
