"use client";

import { useEffect, useState } from "react";
import { LogOut, ShieldCheck } from "lucide-react";

interface AuthStatus {
  authEnabled: boolean;
  authenticated: boolean;
  username: string | null;
}

export function AuthStatusButton() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    fetch("/api/auth/status")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) setStatus(data);
      })
      .catch(() => {});
  }, []);

  if (!status || !status.authEnabled || !status.authenticated) {
    return null;
  }

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
      window.location.href = "/login";
    } catch {
      setLoggingOut(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5 border-l border-[var(--border)] pl-2">
      <span className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2 py-1 font-mono text-[11px] text-[var(--foreground)]">
        <ShieldCheck className="h-3.5 w-3.5 text-[var(--accent)]" />
        {status.username || "admin"}
      </span>
      <button
        type="button"
        onClick={handleLogout}
        disabled={loggingOut}
        title="Sign Out"
        className="ak-btn-ghost p-1.5"
      >
        <LogOut className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
