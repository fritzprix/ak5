import type { CSSProperties, ReactNode } from "react";
import type { Metadata } from "next";
import { Outfit, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-outfit",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AK5 — Agent-Orchestrated Kanban",
  description: "Human and Autonomous AI Agent Collaborative Kanban System",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="en" className={`${outfit.variable} ${plexMono.variable}`}>
      <body
        className="min-h-screen font-sans text-[var(--foreground)] antialiased"
        style={
          {
            "--font-sans": "var(--font-outfit), ui-sans-serif, system-ui, sans-serif",
            "--font-mono": "var(--font-plex-mono), ui-monospace, monospace",
          } as CSSProperties
        }
      >
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
