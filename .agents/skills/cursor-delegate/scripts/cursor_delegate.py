#!/usr/bin/env python3
"""
Cursor Agent Delegation Runner for AK5
Executes headless tasks via Cursor Agent CLI and synchronizes results back to AK5 tickets.
"""

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_cursor_agent_bin() -> str | None:
    """Find available Cursor Agent CLI binary."""
    # Check PATH first
    for name in ["agent", "cursor"]:
        path = shutil.which(name)
        if path:
            return path
    # Check common fallback locations
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "agent",
        home / ".local" / "bin" / "cursor",
        Path("/usr/local/bin/agent"),
        Path("/usr/local/bin/cursor"),
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def run_cmd(
    cmd: list[str], check: bool = False, capture: bool = True, stdin: str | None = None
) -> subprocess.CompletedProcess:
    """Helper to run subprocess safely."""
    return subprocess.run(
        cmd,
        text=True,
        capture_output=capture,
        check=check,
        input=stdin,
    )


def find_ak5_bin() -> str | None:
    ak5_bin = shutil.which("ak5")
    if ak5_bin:
        return ak5_bin
    venv_ak5 = Path.cwd() / ".venv" / "bin" / "ak5"
    return str(venv_ak5) if venv_ak5.is_file() else None


def build_deliverable(title: str, output: str, attachment_id: str | None) -> dict:
    """AK5 deliverable payload (workflow.md §5.2) from the Cursor run."""
    first_line = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
    summary = (first_line or title or "Cursor Agent run completed")[:500]
    artifacts = []
    if attachment_id:
        artifacts.append({"kind": "attachment", "ref": attachment_id})
    else:
        artifacts.append({"kind": "text", "ref": output.strip()[:4000]})
    return {"summary": summary, "artifacts": artifacts, "criteria_self_check": []}


def craft_prompt(mode: str, title: str, ticket_id: str, custom_prompt: str | None) -> str:
    """Construct an actionable prompt for Cursor Agent based on mode and context."""
    if custom_prompt:
        return custom_prompt

    if mode == "review":
        return (
            f"You are acting as an autonomous code reviewer for AK5 ticket [{ticket_id or 'WORKSPACE'}]: '{title}'.\n"
            "Review the latest git diff (or uncommitted working tree changes) in the workspace.\n"
            "Do NOT edit or modify any files. Perform a read-only code review covering:\n"
            "1. Architectural & Logic Assessment\n"
            "2. Potential Bugs, Edge Cases, or Security/Injection Risks\n"
            "3. Code Quality & Test Coverage\n"
            "4. Clear Actionable Recommendations (prioritized)\n"
            "Be specific, cite filenames and line numbers where appropriate."
        )
    elif mode == "plan":
        return (
            f"You are acting as a software architect for AK5 ticket [{ticket_id}]: '{title}'.\n"
            "Analyze the workspace and draft a step-by-step implementation plan.\n"
            "Include required file changes, new interfaces, risk assessment, and verification steps."
        )
    elif mode == "fix":
        return (
            f"You are resolving AK5 ticket [{ticket_id}]: '{title}'.\n"
            "Analyze the problem, implement the necessary code changes, and verify with tests.\n"
            "Provide a concise summary of the fix and the test results when completed."
        )
    else:
        return f"Process AK5 ticket [{ticket_id}]: '{title}'."


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delegate tasks to Cursor Agent and sync findings with AK5 Kanban."
    )
    parser.add_argument(
        "--ticket-id",
        default=os.environ.get("AK5_TICKET_ID", ""),
        help="Target AK5 Ticket ID (defaults to $AK5_TICKET_ID)",
    )
    parser.add_argument(
        "--board-id",
        default=os.environ.get("AK5_BOARD_ID", ""),
        help="Target AK5 Board ID (defaults to $AK5_BOARD_ID)",
    )
    parser.add_argument(
        "--title",
        default=os.environ.get("AK5_TITLE", ""),
        help="Ticket Title / Summary (defaults to $AK5_TITLE)",
    )
    parser.add_argument(
        "--mode",
        choices=["review", "plan", "fix", "custom"],
        default="review",
        help="Delegation mode: review (read-only review), plan (planning), fix (code edits), custom",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Custom prompt string for Cursor Agent",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Cursor Agent model override (e.g. claude-3.7-sonnet, gpt-5)",
    )
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Run in an isolated git worktree via Cursor's -w flag",
    )
    parser.add_argument(
        "--comment",
        action="store_true",
        help="Post the Cursor Agent output as a comment to the AK5 ticket",
    )
    parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach the full Cursor Agent output report to the AK5 ticket",
    )
    parser.add_argument(
        "--claim",
        action="store_true",
        help="Claim the ticket (worker profile) before running; stop if someone else holds it",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Submit the run as the ticket deliverable (→ agent_review); needs a claim by this profile",
    )
    parser.add_argument(
        "--move-to",
        default=None,
        help=argparse.SUPPRESS,  # v1; stage changes go through --submit (workflow.md §3)
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display the crafted command without running Cursor Agent",
    )

    args = parser.parse_args()
    if args.move_to:
        parser.error("--move-to was removed in v2: use --claim/--submit (stages change only via workflow verbs)")
    if (args.claim or args.submit) and not args.ticket_id:
        parser.error("--claim/--submit need --ticket-id (or $AK5_TICKET_ID)")

    agent_bin = find_cursor_agent_bin()
    if not agent_bin:
        sys.stderr.write("Error: Cursor Agent CLI ('agent' or 'cursor') not found in PATH or ~/.local/bin.\n")
        return 1

    prompt_text = craft_prompt(args.mode, args.title, args.ticket_id, args.prompt)

    # Build agent command line
    cmd = [agent_bin]
    if Path(agent_bin).name == "cursor":
        cmd.append("agent")

    cmd.append("-p")

    if args.mode == "plan":
        cmd.extend(["--mode", "plan"])
    elif args.mode == "fix":
        cmd.append("--force")

    if args.model:
        cmd.extend(["--model", args.model])

    if args.worktree:
        cmd.append("-w")

    cmd.append(prompt_text)

    if args.dry_run:
        print("[DRY RUN] Would execute command:")
        print(" ".join(f'"{c}"' if " " in c or "\n" in c else c for c in cmd))
        return 0

    if args.claim:
        ak5_bin = find_ak5_bin()
        if not ak5_bin:
            sys.stderr.write("Error: --claim needs the 'ak5' CLI in PATH or .venv.\n")
            return 1
        claim = run_cmd([ak5_bin, "ticket", "claim", args.ticket_id])
        if claim.returncode != 0:
            # 3 = claimed by someone else, 4 = wrong role/stage, 5 = blocked: nothing to do.
            sys.stderr.write(f"[!] Could not claim {args.ticket_id} (exit {claim.returncode}):\n{claim.stdout}{claim.stderr}")
            return claim.returncode

    print(f"[*] Dispatching task to Cursor Agent ({agent_bin})...")
    print(f"[*] Mode: {args.mode} | Ticket: {args.ticket_id or 'N/A'}")

    result = run_cmd(cmd, capture=True)
    stdout_output = result.stdout or ""
    stderr_output = result.stderr or ""

    if result.returncode != 0:
        sys.stderr.write(f"Cursor Agent failed with return code {result.returncode}:\n{stderr_output}\n")
        return result.returncode

    if not stdout_output.strip():
        sys.stderr.write("⚠️ Warning: Cursor Agent returned empty output. Check prompt parameters, CLI auth status, and network connectivity.\n")

    # Print output to terminal
    print("\n--- Cursor Agent Output ---")
    print(stdout_output.strip() or "(Empty response)")
    print("---------------------------\n")

    # Sync with AK5 if ticket_id is present
    if args.ticket_id:
        ak5_bin = find_ak5_bin()

        if ak5_bin:
            # Save output to a temp file for attachment or comments
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="cursor_report_") as tmp:
                tmp.write(f"# Cursor Agent Delegation Report\n\n")
                tmp.write(f"- **Ticket**: {args.ticket_id}\n")
                tmp.write(f"- **Mode**: {args.mode}\n")
                tmp.write(f"- **Summary**: {args.title}\n\n")
                tmp.write("## Findings & Output\n\n")
                tmp.write(stdout_output)
                temp_report_path = tmp.name

            try:
                # 1. Post Comment
                if args.comment:
                    comment_summary = f"[Cursor Agent @{args.mode}]\n"
                    # Include up to first 1200 characters in comment body
                    trimmed = stdout_output.strip()
                    if len(trimmed) > 1200:
                        trimmed = trimmed[:1200] + "\n\n...(full report attached)"
                    comment_summary += trimmed

                    print(f"[*] Posting comment to ticket {args.ticket_id}...")
                    run_cmd([ak5_bin, "comment", args.ticket_id, comment_summary])

                # 2. Attach Report
                attachment_id = None
                if args.attach:
                    print(f"[*] Attaching report to ticket {args.ticket_id}...")
                    attached = run_cmd([ak5_bin, "--json", "ticket", "attach", args.ticket_id, temp_report_path])
                    if attached.returncode == 0:
                        with contextlib.suppress(json.JSONDecodeError):
                            attachment_id = json.loads(attached.stdout).get("attachment_id")

                # 3. Submit deliverable (worker T9: in_progress → agent_review)
                if args.submit:
                    print(f"[*] Submitting deliverable for {args.ticket_id}...")
                    payload = json.dumps(build_deliverable(args.title, stdout_output, attachment_id))
                    submitted = run_cmd(
                        [ak5_bin, "ticket", "submit", args.ticket_id, "--deliverable", "-"], stdin=payload
                    )
                    if submitted.returncode != 0:
                        sys.stderr.write(
                            f"[!] Submit failed (exit {submitted.returncode}):\n{submitted.stdout}{submitted.stderr}"
                        )
                        return submitted.returncode
            finally:
                if os.path.exists(temp_report_path):
                    os.unlink(temp_report_path)
        else:
            print("[!] Note: 'ak5' CLI not detected in PATH or .venv; skipping AK5 ticket update.")

    print("[✓] Cursor Agent task completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
