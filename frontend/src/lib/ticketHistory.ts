import type {
  TicketHistory,
  TicketPostmortem,
  TicketReview,
  TicketRevision,
} from "./types";

export type TimelineKind = "revision" | "review" | "postmortem";

export type TimelineEntry = {
  kind: TimelineKind;
  id: string;
  at: string;
  title: string;
  summary: string;
  actor: string;
};

function formatIso(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function revisionToEntry(r: TicketRevision): TimelineEntry {
  const criteria =
    r.acceptance_criteria.length > 0
      ? ` · ${r.acceptance_criteria.length} criteria`
      : "";
  return {
    kind: "revision",
    id: `rev-${r.revision_id}`,
    at: r.created_at,
    title: `Revision · ${r.source}`,
    summary: `${(r.spec || "").slice(0, 160)}${(r.spec || "").length > 160 ? "…" : ""}${criteria}${
      r.reason ? ` — ${r.reason}` : ""
    }`,
    actor: r.author,
  };
}

export function reviewToEntry(r: TicketReview): TimelineEntry {
  const mis = r.misrouted ? " (misrouted)" : "";
  return {
    kind: "review",
    id: `review-${r.review_id}`,
    at: r.created_at,
    title: `Review · ${r.decision}${mis} @ ${r.stage}`,
    summary: r.reason?.trim() || `${r.findings.length} finding(s) · attempt ${r.attempt}`,
    actor: r.reviewer,
  };
}

export function postmortemToEntry(p: TicketPostmortem): TimelineEntry {
  return {
    kind: "postmortem",
    id: `pm-${p.postmortem_id}`,
    at: p.created_at,
    title: "Postmortem",
    summary: `${p.what_went_wrong.slice(0, 120)}${p.what_went_wrong.length > 120 ? "…" : ""} · root: ${p.root_cause.slice(0, 80)}`,
    actor: p.author,
  };
}

/** Merge revisions · reviews · postmortems newest-first for the drawer timeline. */
export function buildHistoryTimeline(history: TicketHistory): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    ...history.revisions.map(revisionToEntry),
    ...history.reviews.map(reviewToEntry),
    ...history.postmortems.map(postmortemToEntry),
  ];
  entries.sort((a, b) => {
    const ta = Date.parse(a.at) || 0;
    const tb = Date.parse(b.at) || 0;
    return tb - ta;
  });
  return entries;
}

export function formatTimelineWhen(iso: string): string {
  return formatIso(iso);
}

/** Whether unblock needs a resolution string (B3). */
export function unblockRequiresResolution(blockedKind: string | null | undefined): boolean {
  return blockedKind === "needs_human" || blockedKind === "escalation";
}
