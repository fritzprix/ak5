"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  fetchWorkspaces,
  getStoredWorkspaceId,
  type WorkspaceSummary,
} from "@/lib/api";

export default function SettingsIndexPage() {
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const rows = await fetchWorkspaces();
        const id = getStoredWorkspaceId();
        setWorkspace(rows.find((w) => w.workspace_id === id) ?? rows[0] ?? null);
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
    </div>
  );
}
