"use client";

import { useEffect, useState } from "react";
import { LogOut, ShieldCheck, User } from "lucide-react";

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
    <div className="flex items-center gap-2 pl-2 border-l border-slate-800">
      <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-slate-900 border border-slate-800 text-[11px] text-slate-300">
        <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
        <span className="font-mono text-slate-200">{status.username || "admin"}</span>
      </div>
      <button
        onClick={handleLogout}
        disabled={loggingOut}
        title="Sign Out"
        className="p-1.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-rose-400 hover:border-rose-900/50 transition-colors disabled:opacity-50"
      >
        <LogOut className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
