import { KanbanBoard } from "@/components/board/KanbanBoard";

interface BoardPageProps {
  params: Promise<{ boardId: string }>;
}

export default async function BoardPage({ params }: BoardPageProps) {
  const { boardId } = await params;
  return <KanbanBoard boardId={decodeURIComponent(boardId)} />;
}
