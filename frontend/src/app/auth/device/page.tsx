"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ShieldCheck,
  Bot,
  User,
  CheckCircle2,
  XCircle,
  Clock,
  Layers,
  ArrowRight,
  AlertCircle,
  Loader2,
} from "lucide-react";
import {
  approveDeviceRequest,
  fetchBoards,
  fetchDeviceRequest,
  DeviceRequestView,
} from "@/lib/api";
import { BoardSummary } from "@/lib/types";

function DeviceAuthContent() {
  const searchParams = useSearchParams();
  const initialCode = searchParams.get("code") || "";

  const [inputCode, setInputCode] = useState(initialCode);
  const [requestDetails, setRequestDetails] = useState<DeviceRequestView | null>(null);
  const [availableBoards, setAvailableBoards] = useState<BoardSummary[]>([]);
  const [selectedBoards, setSelectedBoards] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<{
    status: "approved" | "denied";
    actorId: string;
  } | null>(null);

  // Load available boards for enrollment
  useEffect(() => {
    fetchBoards()
      .then(setAvailableBoards)
      .catch((err) => console.error("Failed to load boards:", err));
  }, []);

  const handleLookup = useCallback(async (codeToLookup: string) => {
    if (!codeToLookup.trim()) return;
    setError(null);
    setActionResult(null);
    setIsLoading(true);
    try {
      const details = await fetchDeviceRequest(codeToLookup);
      setRequestDetails(details);
      // Pre-select any target boards requested by the agent
      if (details.target_board_ids && details.target_board_ids.length > 0) {
        setSelectedBoards(details.target_board_ids);
      } else if (availableBoards.length > 0) {
        // Default to first board if available
        setSelectedBoards([availableBoards[0].board_id]);
      }
    } catch (err) {
      setRequestDetails(null);
      setError(err instanceof Error ? err.message : "Failed to find device request");
    } finally {
      setIsLoading(false);
    }
  }, [availableBoards]);

  // If code is provided in URL, auto-fetch details
  useEffect(() => {
    if (initialCode.trim()) {
      handleLookup(initialCode.trim());
    }
  }, [initialCode, handleLookup]);

  const handleDecision = async (approved: boolean) => {
    if (!requestDetails) return;
    setError(null);
    setIsSubmitting(true);
    try {
      await approveDeviceRequest(
        requestDetails.user_code,
        approved,
        approved ? selectedBoards : []
      );
      setActionResult({
        status: approved ? "approved" : "denied",
        actorId: requestDetails.actor_id,
      });
      setRequestDetails((prev) => (prev ? { ...prev, status: approved ? "approved" : "denied" } : null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to process approval");
    } finally {
      setIsSubmitting(false);
    }
  };

  const toggleBoard = (boardId: string) => {
    setSelectedBoards((prev) =>
      prev.includes(boardId) ? prev.filter((b) => b !== boardId) : [...prev, boardId]
    );
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="ak-panel w-full max-w-lg p-6 sm:p-8">
        {/* Header */}
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--accent)] text-white shadow-md">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-[var(--foreground)]">
            Agent Authorization
          </h1>
          <p className="mt-1 text-xs text-[var(--muted)]">
            Authorize an autonomous agent or client to join your AK5 workspace
          </p>
        </div>

        {/* Code Input Form (if no details yet or looking up another code) */}
        {!requestDetails && !actionResult && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleLookup(inputCode);
            }}
            className="space-y-4"
          >
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--muted)] mb-1.5">
                User Verification Code
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="e.g. AK5-7X2M"
                  value={inputCode}
                  onChange={(e) => setInputCode(e.target.value.toUpperCase())}
                  className="ak-input flex-1 font-mono text-center text-lg font-bold tracking-widest uppercase"
                  autoFocus
                />
                <button
                  type="submit"
                  disabled={isLoading || !inputCode.trim()}
                  className="ak-btn-primary px-4 font-medium flex items-center gap-1.5"
                >
                  {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Verify"}
                  {!isLoading && <ArrowRight className="h-4 w-4" />}
                </button>
              </div>
            </div>
            {error && (
              <div className="flex items-center gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 text-xs text-red-400">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}
          </form>
        )}

        {/* Result Confirmation View */}
        {actionResult && (
          <div className="text-center py-6 space-y-4">
            {actionResult.status === "approved" ? (
              <div className="flex flex-col items-center">
                <CheckCircle2 className="h-14 w-14 text-emerald-400 mb-2" />
                <h2 className="text-lg font-bold text-emerald-400">Agent Authorized!</h2>
                <p className="mt-1 text-xs text-[var(--muted)] max-w-sm">
                  <span className="font-semibold text-[var(--foreground)]">@{actionResult.actorId}</span> has
                  been granted access and enrolled into the selected boards. The agent can now start executing tasks.
                </p>
              </div>
            ) : (
              <div className="flex flex-col items-center">
                <XCircle className="h-14 w-14 text-red-400 mb-2" />
                <h2 className="text-lg font-bold text-red-400">Access Denied</h2>
                <p className="mt-1 text-xs text-[var(--muted)] max-w-sm">
                  The authorization request for @{actionResult.actorId} was rejected.
                </p>
              </div>
            )}

            <div className="pt-4 border-t border-[var(--border)]">
              <Link
                href="/"
                className="ak-btn-primary inline-flex items-center gap-2 px-5 py-2 text-sm"
              >
                Go to Kanban Board
              </Link>
            </div>
          </div>
        )}

        {/* Request Inspection & Approval Screen */}
        {requestDetails && !actionResult && (
          <div className="space-y-6">
            {/* Status Warning if not pending */}
            {requestDetails.status !== "pending" && (
              <div className="flex items-center gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2 text-xs text-amber-300">
                <Clock className="h-4 w-4 shrink-0" />
                <span>
                  This request is already marked as <strong>{requestDetails.status}</strong>.
                </span>
              </div>
            )}

            {/* Agent Identity Card */}
            <div className="rounded-xl border border-[var(--border)] bg-[var(--surface-raised)] p-4 space-y-3">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--accent)]/15 text-[var(--accent)]">
                    {requestDetails.actor_type === "agent" ? (
                      <Bot className="h-5 w-5" />
                    ) : (
                      <User className="h-5 w-5" />
                    )}
                  </div>
                  <div>
                    <h3 className="font-semibold text-sm text-[var(--foreground)]">
                      {requestDetails.name}
                    </h3>
                    <div className="text-xs text-[var(--muted)] flex items-center gap-1.5 font-mono">
                      <span>@{requestDetails.actor_id}</span>
                      <span>•</span>
                      <span className="capitalize">{requestDetails.actor_type}</span>
                    </div>
                  </div>
                </div>
                <span className="rounded-md bg-[var(--border)] px-2.5 py-0.5 text-xs font-mono font-medium text-[var(--foreground)]">
                  {requestDetails.user_code}
                </span>
              </div>

              <div className="pt-2 border-t border-[var(--border)] grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span className="text-[var(--muted)]">Role:</span>
                  <p className="font-medium text-[var(--foreground)]">{requestDetails.role}</p>
                </div>
                <div>
                  <span className="text-[var(--muted)]">Expires in:</span>
                  <p className="font-medium text-[var(--foreground)]">
                    {new Date(requestDetails.expires_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </p>
                </div>
              </div>

              {requestDetails.capabilities && requestDetails.capabilities.length > 0 && (
                <div>
                  <span className="text-xs text-[var(--muted)] block mb-1">Capabilities:</span>
                  <div className="flex flex-wrap gap-1.5">
                    {requestDetails.capabilities.map((cap) => (
                      <span
                        key={cap}
                        className="rounded-full bg-[var(--accent)]/10 text-[var(--accent)] px-2 py-0.5 text-[10px] font-mono"
                      >
                        {cap}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Board Enrollment Selection */}
            {requestDetails.status === "pending" && availableBoards.length > 0 && (
              <div className="space-y-2">
                <label className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)] flex items-center gap-1.5">
                  <Layers className="h-3.5 w-3.5" />
                  Grant Board Membership
                </label>
                <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
                  {availableBoards.map((b) => {
                    const checked = selectedBoards.includes(b.board_id);
                    return (
                      <label
                        key={b.board_id}
                        className={`flex items-center justify-between p-2.5 rounded-lg border text-xs cursor-pointer transition-colors ${
                          checked
                            ? "border-[var(--accent)] bg-[var(--accent)]/5 text-[var(--foreground)]"
                            : "border-[var(--border)] bg-[var(--surface-raised)] text-[var(--muted)] hover:border-[var(--muted)]"
                        }`}
                      >
                        <div>
                          <p className="font-medium text-sm text-[var(--foreground)]">{b.name}</p>
                          <span className="font-mono text-[10px] text-[var(--muted)]">
                            ID: {b.board_id}
                          </span>
                        </div>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleBoard(b.board_id)}
                          className="h-4 w-4 rounded border-[var(--border)] text-[var(--accent)] focus:ring-[var(--accent)]"
                        />
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            {error && (
              <div className="flex items-center gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 text-xs text-red-400">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            {/* Action Buttons */}
            {requestDetails.status === "pending" ? (
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => handleDecision(false)}
                  className="flex-1 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-xs font-semibold text-red-400 hover:bg-red-500/20 transition-colors"
                >
                  Reject
                </button>
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => handleDecision(true)}
                  className="ak-btn-primary flex-1 py-2 text-xs font-semibold flex items-center justify-center gap-1.5"
                >
                  {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Approve & Enroll"}
                </button>
              </div>
            ) : (
              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => {
                    setRequestDetails(null);
                    setInputCode("");
                  }}
                  className="ak-btn w-full py-2 text-xs"
                >
                  Lookup Another Code
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function DeviceAuthPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-[var(--muted)]" />
        </div>
      }
    >
      <DeviceAuthContent />
    </Suspense>
  );
}
