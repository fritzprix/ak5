"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Kanban, Shield, Lock, AlertCircle, ArrowRight, Loader2, ExternalLink } from "lucide-react";
import { fetchAuthMe, type AuthMeResponse } from "@/lib/webSession";

function LoginForm() {
  const searchParams = useSearchParams();
  const rawFrom = searchParams.get("from") || "/";
  const redirectTarget = rawFrom.startsWith("/") && !rawFrom.startsWith("//") ? rawFrom : "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lockSeconds, setLockSeconds] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [authMe, setAuthMe] = useState<AuthMeResponse | null>(null);

  useEffect(() => {
    fetchAuthMe().then((me) => {
      if (me?.authenticated) {
        window.location.href = redirectTarget;
        return;
      }
      setAuthMe(me);
    });
  }, [redirectTarget]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLockSeconds(null);
    setIsLoading(true);

    try {
      // Prefer gateway local-owner login when only password is used (identity.md §8).
      const useLocalOwner = !username.trim() && Boolean(authMe?.local_owner_configured);
      const res = await fetch(useLocalOwner ? "/auth/local/login" : "/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          useLocalOwner
            ? { password }
            : { username: username.trim(), password }
        ),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        const retry = res.headers.get("Retry-After");
        if (res.status === 429 && retry) {
          setLockSeconds(Number(retry) || null);
        }
        setError(
          (data as { error?: string; detail?: string }).error ||
            (data as { detail?: string }).detail ||
            "Authentication failed. Please verify credentials."
        );
        setIsLoading(false);
        return;
      }

      window.location.href = redirectTarget;
    } catch {
      setError("Network or server connection error. Please try again.");
      setIsLoading(false);
    }
  };

  const providers = authMe?.providers ?? [];

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

        {providers.length > 0 ? (
          <div className="mb-6 space-y-2">
            <p className="text-xs font-medium text-[var(--muted)]">Continue with</p>
            {providers.map((p) => (
              <a
                key={p.id}
                href={`/auth/login?provider=${encodeURIComponent(p.id)}`}
                className="ak-btn-ghost flex w-full items-center justify-center gap-2 py-2.5 text-sm"
              >
                <ExternalLink className="h-4 w-4" />
                {p.id}
              </a>
            ))}
            <div className="relative my-4 text-center text-[11px] text-[var(--muted)]">
              <span className="bg-[var(--panel)] px-2 relative z-10">or password</span>
              <span className="absolute inset-x-0 top-1/2 border-t border-[var(--border)]" />
            </div>
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="space-y-4">
          {!authMe?.local_owner_configured || username !== "" || providers.length > 0 ? (
            <div>
              <label className="mb-1.5 block text-xs font-medium text-[var(--muted)]">Username</label>
              <input
                type="text"
                autoFocus={!authMe?.local_owner_configured}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={authMe?.local_owner_configured ? "optional (local owner)" : "admin"}
                className="ak-input"
                required={!authMe?.local_owner_configured}
              />
            </div>
          ) : null}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--muted)]">Password</label>
            <input
              type="password"
              required
              autoFocus={Boolean(authMe?.local_owner_configured)}
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
            {authMe?.oidc_configured ? "OIDC ready" : "Rate-limit protected"}
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
          Loading…
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
