import { redirect } from "next/navigation";

const DEFAULT_BOARD_ID = "proj-core-engine";

export default function Home() {
  redirect(`/board/${DEFAULT_BOARD_ID}/`);
}
