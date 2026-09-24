"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Kanban, Shield, Lock, AlertCircle, ArrowRight, Loader2 } from "lucide-react";

function LoginForm() {
  const searchParams = useSearchParams();
  const rawFrom = searchParams.get("from") || "/";
  const redirectTarget = rawFrom.startsWith("/") && !rawFrom.startsWith("//") ? rawFrom : "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lockSeconds, setLockSeconds] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLockSeconds(null);
    setIsLoading(true);

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });

      const data = await res.json();

      if (!res.ok) {
        const retry = res.headers.get("Retry-After");
        if (res.status === 429 && retry) {
          setLockSeconds(Number(retry) || null);
        }
        setError(data.error || "Authentication failed. Please verify credentials.");
        setIsLoading(false);
        return;
      }

      window.location.href = redirectTarget;
    } catch {
      setError("Network or server connection error. Please try again.");
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="ak-panel w-full max-w-md p-8">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--accent)] text-white">
            <Kanban className="h-6 w-6" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-[var(--foreground)]">Sign in to AK5</h1>
          <p className="mt-1 text-xs text-[var(--muted)]">Kanban web dashboard</p>
        </div>

        {error ? (
          <div className="mb-5 flex items-start gap-2 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 p-3 text-xs text-[var(--danger)]">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p>{error}</p>
              {lockSeconds ? (
                <p className="mt-1 text-[var(--muted)]">Try again in about {lockSeconds}s.</p>
              ) : null}
            </div>
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--muted)]">Username</label>
            <input
              type="text"
              required
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
              className="ak-input"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--muted)]">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
              className="ak-input"
            />
          </div>
          <button type="submit" disabled={isLoading} className="ak-btn-primary w-full py-2.5">
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Authenticating…
              </>
            ) : (
              <>
                Sign In
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </form>

        <div className="mt-8 flex items-center justify-between border-t border-[var(--border)] pt-5 text-[11px] text-[var(--muted)]">
          <span className="inline-flex items-center gap-1.5">
            <Shield className="h-3.5 w-3.5 text-[var(--accent)]" />
            Rate-limit protected
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Lock className="h-3.5 w-3.5" />
            HttpOnly session
          </span>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-sm text-[var(--muted)]">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          Loading authentication…
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
