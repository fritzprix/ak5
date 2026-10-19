"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  fetchWorkspaces,
  fetchWorkspaceUsage,
  getStoredWorkspaceId,
  type WorkspaceSummary,
  type WorkspaceUsage,
} from "@/lib/api";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KiB", "MiB", "GiB", "TiB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

const QUOTA_LABELS: Record<string, string> = {
  max_boards: "Boards",
  max_open_tickets: "Open Tickets",
  max_attachment_bytes_total: "Storage (Attachments)",
  max_members: "Members",
  max_agents: "Active Agents",
  event_retention_days: "Event Retention (Days)",
};

export default function SettingsIndexPage() {
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [usage, setUsage] = useState<WorkspaceUsage | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const rows = await fetchWorkspaces();
        const id = getStoredWorkspaceId();
        const target = rows.find((w) => w.workspace_id === id) ?? rows[0] ?? null;
        setWorkspace(target);
        if (target) {
          try {
            const u = await fetchWorkspaceUsage(target.workspace_id);
            setUsage(u);
          } catch {
            setUsage(null);
          }
        }
      } catch {
        setWorkspace(null);
      }
    })();
  }, []);

  const presence =
    workspace?.policy?.require_user_presence_for_approval === true;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-y-auto py-2">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Manage workspace membership and agent credentials
          {workspace ? ` for ${workspace.name}` : ""}.
        </p>
      </header>

      <nav className="flex flex-col gap-2 sm:flex-row">
        <Link href="/settings/members" className="ak-btn-ghost justify-start px-3 py-2 text-sm">
          Members & invites
        </Link>
        <Link href="/settings/agents" className="ak-btn-ghost justify-start px-3 py-2 text-sm">
          Agents & credentials
        </Link>
        <Link href="/settings/passkeys" className="ak-btn-ghost justify-start px-3 py-2 text-sm">
          Passkeys
        </Link>
        <Link href="/device" className="ak-btn-ghost justify-start px-3 py-2 text-sm">
          Device approval
        </Link>
      </nav>

      {presence ? (
        <p className="text-sm text-[var(--warning)]">
          This workspace requires user presence (passkey) for human approval
          (T15): approvals happen in the web UI with a passkey confirmation and
          the CLI cannot approve. Manage them under{" "}
          <Link href="/settings/passkeys" className="underline">
            Passkeys
          </Link>
          .
        </p>
      ) : null}

      <section className="rounded-lg border border-[var(--border)] p-4 bg-[var(--surface)]">
        <h2 className="text-base font-medium">Resource Quotas & Usage</h2>
        <p className="text-xs text-[var(--muted)] mb-4">
          Current workspace resource consumption against configured limits (operations.md §3).
        </p>
        {usage && usage.usage ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {Object.entries(usage.usage).map(([key, item]) => {
              const label = QUOTA_LABELS[key] ?? key;
              const isBytes = key === "max_attachment_bytes_total";
              const currentStr = isBytes ? formatBytes(item.current) : item.current.toLocaleString();
              const limitStr =
                item.limit === 0
                  ? "Unlimited"
                  : isBytes
                  ? formatBytes(item.limit)
                  : item.limit.toLocaleString();
              const percent =
                item.limit > 0
                  ? Math.min(100, Math.round((item.current / item.limit) * 100))
                  : null;

              return (
                <div
                  key={key}
                  className="flex flex-col p-3 rounded-md bg-[var(--background)] border border-[var(--border)]"
                >
                  <div className="flex justify-between items-center text-sm">
                    <span className="font-medium">{label}</span>
                    <span className="text-xs text-[var(--muted)]">
                      {percent !== null ? `${percent}%` : ""}
                    </span>
                  </div>
                  <div className="mt-1 flex items-baseline justify-between text-sm">
                    <span className="text-lg font-semibold">{currentStr}</span>
                    <span className="text-xs text-[var(--muted)]">/ {limitStr}</span>
                  </div>
                  {percent !== null && (
                    <div className="mt-2 h-1.5 w-full rounded-full bg-[var(--border)] overflow-hidden">
                      <div
                        className={`h-full ${
                          percent >= 90
                            ? "bg-[var(--error)]"
                            : percent >= 75
                            ? "bg-[var(--warning)]"
                            : "bg-[var(--accent)]"
                        }`}
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-xs text-[var(--muted)]">
            {workspace ? "Loading quota usage..." : "Select a workspace to view quotas."}
          </p>
        )}
      </section>
    </div>
  );
}
