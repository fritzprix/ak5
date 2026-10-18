"use client";

import { useEffect, useState } from "react";
import { LogOut, ShieldCheck } from "lucide-react";
import { clearAuthTokenCache } from "@/lib/api";
import { fetchAuthMe, fetchWithCsrf } from "@/lib/webSession";

interface AuthStatus {
  authEnabled: boolean;
  authenticated: boolean;
  username: string | null;
}

export function AuthStatusButton() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    void (async () => {
      const me = await fetchAuthMe();
      if (me?.authenticated) {
        setStatus({
          authEnabled: true,
          authenticated: true,
          username: me.display_name || me.user_id,
        });
        return;
      }
      try {
        const res = await fetch("/api/auth/status");
        if (res.ok) {
          const data = (await res.json()) as AuthStatus;
          setStatus(data);
        }
      } catch {
        /* ignore */
      }
    })();
  }, []);

  if (!status || !status.authenticated) {
    return null;
  }

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      const res = await fetchWithCsrf("/api/auth/logout", { method: "POST" });
      if (!res.ok) {
        // Fallback: OIDC logout path
        const oidc = await fetchWithCsrf("/auth/logout", { method: "POST" });
        if (!oidc.ok) {
          setLoggingOut(false);
          return;
        }
      }
      clearAuthTokenCache();
      window.location.href = "/login";
    } catch {
      setLoggingOut(false);
    }
  };

  return (
    <div className="flex items-center gap-1 border-l border-[var(--border)] pl-1.5 sm:gap-1.5 sm:pl-2">
      <span
        title={status.username || "signed in"}
        className="inline-flex max-w-[7rem] items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-1.5 py-1 font-mono text-[11px] text-[var(--foreground)] sm:max-w-none sm:px-2"
      >
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-[var(--accent)]" />
        <span className="hidden truncate sm:inline">{status.username || "signed in"}</span>
      </span>
      <button
        type="button"
        onClick={handleLogout}
        disabled={loggingOut}
        title="Sign Out"
        aria-label="Sign out"
        className="ak-btn-ghost p-1.5"
      >
        <LogOut className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
