"use client";

import { usePathname } from "next/navigation";
import { KanbanBoard } from "@/components/board/KanbanBoard";

interface BoardPageClientProps {
  initialBoardId: string;
}

export function BoardPageClient({ initialBoardId }: BoardPageClientProps) {
  const pathname = usePathname();
  const match = pathname?.match(/^\/board\/([^/]+)/);
  const fromPath = match?.[1] ? decodeURIComponent(match[1]) : null;
  const raw = fromPath || initialBoardId;
  const boardId = raw === "_" ? "proj-core-engine" : raw;
  return <KanbanBoard boardId={boardId} />;
}
