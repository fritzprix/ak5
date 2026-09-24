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
    return <main className="min-h-dvh">{children}</main>;
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="sticky top-0 z-40 shrink-0 border-b border-[var(--border)] bg-[var(--background)]/90 pt-[env(safe-area-inset-top)] backdrop-blur-md">
        <div className="flex h-14 w-full items-center gap-2 px-3 sm:gap-4 sm:px-4 lg:px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2 sm:gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--accent)] text-white">
              <Kanban className="h-4 w-4" />
            </span>
            <span className="text-sm font-semibold tracking-tight">AK5</span>
          </Link>

          <BoardSwitcher />

          <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2">
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
      <main className="flex min-h-0 w-full flex-1 flex-col overflow-hidden px-3 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:px-4">
        {children}
      </main>
    </div>
  );
}
