/**
 * Lightweight focus-trap helpers for modal dialogs and drawers.
 * Pure functions are unit-tested; the hook wires DOM listeners.
 */

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

export function listFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((el) => {
    if (el.closest("[hidden], [inert]")) return false;
    if (el.getAttribute("aria-hidden") === "true") return false;
    return true;
  });
}

/** Cycle Tab / Shift+Tab within a container. Returns true if the event was handled. */
export function trapTabKey(root: HTMLElement, event: KeyboardEvent): boolean {
  if (event.key !== "Tab") return false;
  const focusables = listFocusable(root);
  if (focusables.length === 0) {
    event.preventDefault();
    root.focus();
    return true;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement as HTMLElement | null;

  if (event.shiftKey) {
    if (!active || active === first || !root.contains(active)) {
      event.preventDefault();
      last.focus();
      return true;
    }
  } else if (!active || active === last || !root.contains(active)) {
    event.preventDefault();
    first.focus();
    return true;
  }
  return false;
}

export function preferInitialFocus(root: HTMLElement): HTMLElement {
  const preferred = root.querySelector<HTMLElement>(
    "textarea:not([disabled]), input:not([disabled]):not([type='hidden']), select:not([disabled])"
  );
  if (preferred) return preferred;
  const focusables = listFocusable(root);
  return focusables[0] ?? root;
}
