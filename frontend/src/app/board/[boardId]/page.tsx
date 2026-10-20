import { BoardPageClient } from "./BoardPageClient";

interface BoardPageProps {
  params: Promise<{ boardId: string }>;
}

/** Pre-render shells for static export; unknown IDs fall back via FastAPI SPA. */
export function generateStaticParams() {
  return [{ boardId: "inbox" }, { boardId: "proj-core-engine" }, { boardId: "_" }];
}

export default async function BoardPage({ params }: BoardPageProps) {
  const { boardId } = await params;
  return <BoardPageClient initialBoardId={decodeURIComponent(boardId)} />;
}
