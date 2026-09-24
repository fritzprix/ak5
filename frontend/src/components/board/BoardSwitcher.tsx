"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutGrid } from "lucide-react";
import { fetchBoards } from "@/lib/api";
import { BoardSummary } from "@/lib/types";

export const BoardSwitcher: React.FC = () => {
  const pathname = usePathname();
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
    : null;

  if (error) {
    return (
      <span className="text-[11px] text-rose-400 truncate max-w-[180px]" title={error}>
        Boards unavailable
      </span>
    );
  }

  if (boards.length === 0) {
    return (
      <span className="text-[11px] text-slate-500 inline-flex items-center gap-1">
        <LayoutGrid className="w-3 h-3" />
        Loading boards…
      </span>
    );
  }

  return (
    <nav className="flex items-center gap-1.5 max-w-[420px] overflow-x-auto" aria-label="Boards">
      {boards.map((board) => {
        const isActive = board.board_id === activeBoardId;
        return (
          <Link
            key={board.board_id}
            href={`/board/${encodeURIComponent(board.board_id)}/`}
            className={`shrink-0 px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors ${
              isActive
                ? "bg-cyan-950/70 text-cyan-300 border-cyan-700/60"
                : "bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700"
            }`}
            title={board.description || board.name}
          >
            {board.name}
          </Link>
        );
      })}
    </nav>
  );
};
