import type { Metadata } from "next";
import "./globals.css";
import { Kanban, Sparkles } from "lucide-react";

export const metadata: Metadata = {
  title: "AK5 — Agent-Orchestrated Kanban",
  description: "Human and Autonomous AI Agent Collaborative Kanban System",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="bg-[#090d16] text-slate-100 min-h-screen flex flex-col">
        {/* Navigation Bar */}
        <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-md sticky top-0 z-40">
          <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-500 to-purple-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
                <Kanban className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="font-bold text-base tracking-tight flex items-center gap-1.5">
                  <span>AK5</span>
                  <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-purple-950/80 border border-purple-800 text-purple-300">
                    Agent-Orchestrated
                  </span>
                </h1>
                <p className="text-[11px] text-slate-400">Human & AI Dual Protocol Kanban</p>
              </div>
            </div>

            <div className="flex items-center gap-4 text-xs">
              <span className="text-slate-400 hidden sm:inline">
                Protocols: <span className="text-slate-200 font-mono">REST + SSE + MCP</span>
              </span>
              <a
                href="http://127.0.0.1:8000/docs"
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:text-white hover:border-slate-700 transition-colors"
              >
                API Docs
              </a>
            </div>
          </div>
        </header>

        {/* Main Content */}
        <main className="flex-1 max-w-7xl w-full mx-auto p-6">{children}</main>
      </body>
    </html>
  );
}
