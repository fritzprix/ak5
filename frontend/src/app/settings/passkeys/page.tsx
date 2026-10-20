"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  deletePasskey,
  fetchPasskeys,
  fetchWorkspaces,
  getStoredWorkspaceId,
  registerPasskey,
  requestPresenceToken,
  updateWorkspacePolicy,
  type PasskeySummary,
  type WorkspaceSummary,
} from "@/lib/api";
import { passkeysSupported } from "@/lib/passkey";

function formatWhen(value: string | null): string {
  if (!value) return "never";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export default function PasskeysSettingsPage() {
  const [workspace, setWorkspace] = useState<WorkspaceSummary | null>(null);
  const [passkeys, setPasskeys] = useState<PasskeySummary[]>([]);
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [supported, setSupported] = useState(true);

  const canAdmin =
    workspace?.membership_role === "owner" || workspace?.membership_role === "admin";
  const policyOn = workspace?.policy?.require_user_presence_for_approval === true;

  const reload = useCallback(async () => {
    const rows = await fetchWorkspaces();
    const id = getStoredWorkspaceId();
    setWorkspace(rows.find((w) => w.workspace_id === id) ?? rows[0] ?? null);
    setPasskeys(await fetchPasskeys());
  }, []);

  useEffect(() => {
    setSupported(passkeysSupported());
    void reload().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load");
    });
  }, [reload]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 overflow-y-auto py-2">
      <header>
        <Link href="/settings" className="text-xs text-[var(--muted)] hover:text-[var(--foreground)]">
          ← Settings
        </Link>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Passkeys</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Passkeys prove that a person is present when a ticket is approved to done (T15).
          They need HTTPS or localhost.
        </p>
      </header>

      {error ? <p className="text-sm text-[var(--danger)]">{error}</p> : null}
      {notice ? <p className="text-sm text-[var(--success)]">{notice}</p> : null}
      {!supported ? (
        <p className="text-sm text-[var(--warning)]">
          This browser does not support passkeys (WebAuthn).
        </p>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium">Approval policy</h2>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={policyOn}
            disabled={busy || !canAdmin || (!policyOn && passkeys.length === 0)}
            onChange={(e) =>
              void run(async () => {
                if (!workspace) return;
                const next = e.target.checked;
                if (
                  next &&
                  !window.confirm(
                    "From now on approving tickets requires a passkey confirmation in the web UI. CLI approval will be rejected. Continue?"
                  )
                ) {
                  return;
                }
                setWorkspace(
                  await updateWorkspacePolicy(workspace.workspace_id, {
                    require_user_presence_for_approval: next,
                  })
                );
              })
            }
          />
          <span>
            Require a passkey to approve tickets
            <span className="block text-xs text-[var(--muted)]">
              {canAdmin
                ? passkeys.length === 0 && !policyOn
                  ? "Register a passkey first, otherwise nobody could approve."
                  : "Applies to everyone in this workspace; the CLI can no longer approve."
                : "Only workspace admins can change this."}
            </span>
          </span>
        </label>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium">Your passkeys</h2>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await registerPasskey(label);
              setLabel("");
              setPasskeys(await fetchPasskeys());
              setNotice("Passkey registered.");
            });
          }}
        >
          <label className="flex-1 text-xs text-[var(--muted)]">
            Label
            <input
              className="ak-input mt-1"
              value={label}
              maxLength={128}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="MacBook Touch ID"
            />
          </label>
          <button type="submit" className="ak-btn-primary" disabled={busy || !supported}>
            Add passkey
          </button>
          <button
            type="button"
            className="ak-btn-ghost"
            disabled={busy || !supported || passkeys.length === 0}
            onClick={() =>
              void run(async () => {
                await requestPresenceToken();
                setNotice("Passkey confirmed — it works for approvals.");
              })
            }
          >
            Test passkey
          </button>
        </form>

        {passkeys.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No passkeys registered.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)] border-t border-[var(--border)]">
            {passkeys.map((p) => (
              <li key={p.passkey_id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <div>
                  <div className="text-sm font-medium">{p.label || "Passkey"}</div>
                  <div className="text-xs text-[var(--muted)]">
                    added {formatWhen(p.created_at)} · last used {formatWhen(p.last_used_at)}
                  </div>
                </div>
                <button
                  type="button"
                  className="ak-btn-ghost text-xs"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const last = passkeys.length === 1;
                      const warning =
                        last && policyOn
                          ? "This is your last passkey and approval requires one. You will not be able to approve tickets. Delete anyway?"
                          : "Delete this passkey?";
                      if (!window.confirm(warning)) return;
                      await deletePasskey(p.passkey_id);
                      setPasskeys(await fetchPasskeys());
                    })
                  }
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
