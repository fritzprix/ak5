"use client";

import React, { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { fetchBoards } from "@/lib/api";
import { BoardSummary } from "@/lib/types";

export const BoardSwitcher: React.FC = () => {
  const pathname = usePathname();
  const router = useRouter();
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchBoards()
      .then(setBoards)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load boards");
      });
  }, []);

  const activeBoardId = pathname?.startsWith("/board/")
    ? decodeURIComponent(pathname.slice("/board/".length).split("/")[0] || "")
    : "";

  if (error) {
    return (
      <span className="max-w-[180px] truncate text-[11px] text-[var(--danger)]" title={error}>
        Boards unavailable
      </span>
    );
  }

  if (boards.length === 0) {
    return <span className="text-[11px] text-[var(--muted)]">Loading boards…</span>;
  }

  return (
    <label className="flex min-w-[12rem] max-w-[20rem] flex-1 items-center gap-2">
      <span className="sr-only">Board</span>
      <select
        className="ak-input py-1.5 text-xs"
        value={activeBoardId}
        aria-label="Select board"
        onChange={(e) => {
          const id = e.target.value;
          if (id) router.push(`/board/${encodeURIComponent(id)}/`);
        }}
      >
        {!activeBoardId ? <option value="">Select board…</option> : null}
        {boards.map((board) => (
          <option key={board.board_id} value={board.board_id}>
            {board.name}
          </option>
        ))}
      </select>
    </label>
  );
};
