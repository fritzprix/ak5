import { redirect } from "next/navigation";

/** Global inbox is the v2 default landing board (workflow.md §2.1). */
const DEFAULT_BOARD_ID = "inbox";

export default function Home() {
  redirect(`/board/${DEFAULT_BOARD_ID}/`);
}
