import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ak5.database import AsyncSessionLocal
from ak5.models.subscription import Subscription
from ak5.models.ticket import Ticket
from ak5.services.event_bus import BoardEvent, event_bus
from ak5.services.event_context import (
    build_hook_environ,
    extract_event_context,
    matches_agent_filter,
    matches_event_filter,
    normalize_actor_id,
    resolve_hook_session_actor,
)

logger = logging.getLogger("ak5.subscription_service")


class SubscriptionService:
    def __init__(self, session_factory=None) -> None:
        self._last_exec: dict[str, float] = {}
        self._worker_task: asyncio.Task | None = None
        self._active_tasks: set[asyncio.Task] = set()
        self._active_processes: set[asyncio.subprocess.Process] = set()
        self._semaphore = asyncio.Semaphore(10)
        self.session_factory = session_factory or AsyncSessionLocal
        self.project_root = str(Path(__file__).resolve().parents[3])

    async def start(self) -> None:
        """Start background listener for EventBus events."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._event_listener_loop())
            logger.info("SubscriptionService event listener started.")

    async def stop(self) -> None:
        """Stop background listener, kill child subprocesses, and cancel in-flight tasks."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            logger.info("SubscriptionService event listener stopped.")

        for proc in list(self._active_processes):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        self._active_processes.clear()

        for task in list(self._active_tasks):
            if not task.done():
                task.cancel()
        if self._active_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
            self._active_tasks.clear()

    async def _event_listener_loop(self) -> None:
        """Consume events from event_bus and trigger registered subscriptions."""
        async for board_event in event_bus.subscribe():
            try:
                await self.handle_board_event(board_event)
            except Exception as e:
                logger.error(f"Error handling board event in SubscriptionService: {e}", exc_info=True)

    async def handle_board_event(self, board_event: BoardEvent) -> None:
        event_type = board_event.event_type
        if event_type.upper() == "CONNECTED":
            return

        data = board_event.data or {}
        context = extract_event_context(event_type, data)

        async with self.session_factory() as db:
            result = await db.execute(select(Subscription))
            subscriptions = result.scalars().all()

        for sub in subscriptions:
            # 1. Board ID filter
            if sub.board_id and sub.board_id not in ("*", "all") and context["board_id"] != sub.board_id:
                continue

            # 2. Event type filter (supports ALL, DEFAULT, TICKET, aliases, and auto-matching DELEGATED for CREATED)
            if sub.events and not matches_event_filter(sub.events, event_type):
                continue

            # 3. Ignore actor filter (normalized without @)
            if sub.ignore_actor:
                actor_id = context.get("actor_id") or ""
                if normalize_actor_id(actor_id) == normalize_actor_id(sub.ignore_actor):
                    continue

            # 4. Target agent filter (normalized without @, checks ticket, subtask, DB fallback, or comment mention)
            if sub.for_agent:
                ticket_data = data.get("ticket") or data.get("subtask")
                if not isinstance(ticket_data, dict):
                    # DB fallback if event didn't carry ticket data directly (e.g. comment/attachment events)
                    ticket_id = context.get("ticket_id")
                    if ticket_id:
                        async with self.session_factory() as db:
                            t_obj = await db.get(Ticket, ticket_id)
                            if t_obj:
                                ticket_data = {
                                    "ticket_id": t_obj.ticket_id,
                                    "assigned_to": t_obj.assigned_to,
                                    "board_id": t_obj.board_id,
                                    "title": t_obj.title,
                                    "status": t_obj.status,
                                }
                if not matches_agent_filter(sub.for_agent, ticket_data, data):
                    continue

            # 5. Debounce check (include board_id so board-level events don't collide)
            ticket_part = context.get("ticket_id") or ""
            board_part = context.get("board_id") or ""
            debounce_key = f"{sub.subscription_id}:{board_part}:{ticket_part}:{event_type}"
            now = time.time()
            # Drop expired debounce entries (memory bound)
            if len(self._last_exec) > 10_000:
                cutoff = now - 3600
                self._last_exec = {k: v for k, v in self._last_exec.items() if v >= cutoff}
            if (
                sub.debounce_seconds > 0
                and debounce_key in self._last_exec
                and now - self._last_exec[debounce_key] < sub.debounce_seconds
            ):
                continue
            self._last_exec[debounce_key] = now

            # 6. Run the registered hook command literally (payload via env + stdin)
            session_actor_id = resolve_hook_session_actor(
                for_agent=sub.for_agent,
                created_by=sub.created_by,
            )
            task = asyncio.create_task(
                self._execute_subprocess(
                    sub_id=sub.subscription_id,
                    command_str=sub.exec_command,
                    context=context,
                    session_actor_id=session_actor_id,
                    raw_event={"event": event_type, "data": data},
                )
            )
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def _execute_subprocess(
        self,
        sub_id: str,
        command_str: str,
        context: dict[str, str],
        session_actor_id: str,
        raw_event: dict[str, Any],
    ) -> int:
        """Execute the registered hook literally; event payload is env vars + stdin JSON."""
        async with self._semaphore:
            logger.info(f"[Subscription {sub_id}] Executing: {command_str}")
            env = build_hook_environ(context, session_actor_id=session_actor_id)
            stdin_bytes = json.dumps(raw_event, ensure_ascii=False).encode("utf-8")
            timeout_seconds = 30.0

            try:
                proc = await asyncio.create_subprocess_shell(
                    command_str,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.project_root,
                    env=env,
                )
                self._active_processes.add(proc)
                started = time.monotonic()
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=stdin_bytes),
                        timeout=timeout_seconds,
                    )
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    logger.error(
                        f"[Subscription {sub_id}] Command timed out after {timeout_seconds}s and was terminated: {command_str}"
                    )
                    return -1
                except RuntimeError as e:
                    # uvloop raises a bare RuntimeError ("... the handler is
                    # closed") instead of BrokenPipeError/ConnectionResetError
                    # when the child process exits (or closes stdin) before
                    # asyncio finishes writing stdin_bytes. asyncio's own
                    # _feed_stdin() only guards against the latter two, so this
                    # uvloop-specific exception otherwise escapes as a scary
                    # traceback even though the child process itself may have
                    # run and exited normally. Treat it as a harmless race.
                    if "handler is closed" not in str(e):
                        raise
                    # Keep the original deadline: a child that closed stdin but
                    # is still running must not hang a semaphore slot forever.
                    remaining = max(0.0, timeout_seconds - (time.monotonic() - started))
                    if proc.stdin is not None and not proc.stdin.is_closing():
                        with contextlib.suppress(OSError, RuntimeError):
                            proc.stdin.close()
                    try:
                        # Drain stdout/stderr under the remaining budget so a
                        # live child cannot deadlock on a full pipe buffer.
                        await asyncio.wait_for(proc.communicate(), timeout=remaining)
                    except TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            proc.kill()
                        await proc.wait()
                        logger.error(
                            f"[Subscription {sub_id}] Command timed out after {timeout_seconds}s and was terminated: {command_str}"
                        )
                        return -1
                    returncode = proc.returncode or 0
                    logger.warning(
                        f"[Subscription {sub_id}] stdin write raced with early process exit "
                        f"(child likely exited without reading stdin); returncode={returncode}: {command_str}"
                    )
                    return returncode
                finally:
                    self._active_processes.discard(proc)

                returncode = proc.returncode or 0
                if returncode == 0:
                    logger.info(f"[Subscription {sub_id}] Completed successfully (code 0)")
                else:
                    logger.warning(
                        f"[Subscription {sub_id}] Exited with code {returncode}. stderr: {stderr.decode(errors='replace')[:200]}"
                    )
                return returncode
            except Exception as e:
                logger.error(f"[Subscription {sub_id}] Subprocess execution failed: {e}", exc_info=True)
                return -1


# Global singleton instance
subscription_service = SubscriptionService()
