"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Kanban } from "lucide-react";
import { BoardSwitcher } from "@/components/board/BoardSwitcher";
import { AuthStatusButton } from "@/components/auth/AuthStatusButton";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login" || pathname?.startsWith("/login/");

  if (isLogin) {
    return <main className="min-h-screen">{children}</main>;
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-[var(--border)] bg-[var(--background)]/90 backdrop-blur-md">
        <div className="flex h-14 w-full items-center gap-4 px-4 lg:px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--accent)] text-white">
              <Kanban className="h-4 w-4" />
            </span>
            <span className="text-sm font-semibold tracking-tight">AK5</span>
          </Link>

          <BoardSwitcher />

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <a
              href="/docs"
              target="_blank"
              rel="noreferrer"
              className="ak-btn-ghost hidden text-xs sm:inline-flex"
            >
              API Docs
            </a>
            <AuthStatusButton />
          </div>
        </div>
      </header>
      <main className="w-full flex-1 px-3 py-3 lg:px-4">{children}</main>
    </div>
  );
}
