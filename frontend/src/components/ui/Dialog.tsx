"use client";

import React, { useEffect, useId, useRef } from "react";
import { X } from "lucide-react";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "md" | "lg" | "xl";
}

const sizeClass: Record<NonNullable<DialogProps["size"]>, string> = {
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-2xl",
};

export function Dialog({ open, onClose, title, children, footer, size = "md" }: DialogProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);

    const previous = document.activeElement as HTMLElement | null;
    const focusable = panelRef.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    focusable?.focus();

    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(6, 10, 16, 0.72)" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`ak-panel flex w-full flex-col shadow-xl ${sizeClass[size]} max-h-[85vh]`}
      >
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <h3 id={titleId} className="text-base font-semibold tracking-tight text-[var(--foreground)]">
            {title}
          </h3>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onClose}
            className="ak-btn-ghost p-1.5"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
