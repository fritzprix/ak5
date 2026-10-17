"""Workflow state machine and transition commands (workflow.md §3–§8).

Transition rules are declared as a data table (:data:`TRANSITION_TABLE`).
Each public command validates ``(stage, role, claim, blocked)`` against that
table and the §6 invariants, then applies side effects (column move, claim,
revision/review/postmortem, audit, event log).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ak5.authz import (
    RoleForbiddenError,
    actor_has_capabilities,
    require_roles,
    resolve_workflow_role,
)
from ak5.config import settings
from ak5.contracts.errors import ErrorCode
from ak5.contracts.events import EventType, WorkAvailableData
from ak5.contracts.payloads import Deliverable, Postmortem, RoutePlan, RouteTargetCreate, Verdict
from ak5.contracts.stages import BlockedKind, Role, Stage
from ak5.models.actor import Actor
from ak5.models.audit import AuditLog
from ak5.models.board import Board
from ak5.models.board_member import BoardMember
from ak5.models.column import Column
from ak5.models.idempotency import IdempotencyKey
from ak5.models.ticket import Ticket
from ak5.models.ticket_postmortem import TicketPostmortem
from ak5.models.ticket_review import TicketReview
from ak5.models.ticket_revision import TicketRevision
from ak5.seed import INBOX_BOARD_ID, SYSTEM_ACTOR_ID
from ak5.services.event_log import record_event
from ak5.services.lexorank import rank_between
from ak5.services.templates import get_template, template_column_dicts
from ak5.services.ticket_status import derive_status

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorkflowError(Exception):
    """Raised when a workflow command violates the state machine."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


# ---------------------------------------------------------------------------
# Transition table (workflow.md §3 / §4) — data, not just documentation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransitionSpec:
    """One row of the stage transition / blocked overlay table."""

    op: str
    from_stage: Stage | None  # None = create (no prior stage)
    to_stage: Stage | None  # None = stay / overlay-only
    roles: frozenset[Role]
    requires_own_claim: bool = False
    requires_unclaimed: bool = False
    allows_blocked: bool = False
    note: str = ""


TRANSITION_TABLE: tuple[TransitionSpec, ...] = (
    TransitionSpec("create", None, Stage.UNCLASSIFIED, frozenset({Role.HUMAN}), note="T1"),
    TransitionSpec(
        "create_direct",
        None,
        Stage.BACKLOG,
        frozenset({Role.HUMAN, Role.TRIAGE}),
        note="T2",
    ),
    TransitionSpec(
        "claim",
        Stage.UNCLASSIFIED,
        Stage.UNCLASSIFIED,
        frozenset({Role.TRIAGE}),
        requires_unclaimed=True,
        note="T3",
    ),
    TransitionSpec(
        "route",
        Stage.UNCLASSIFIED,
        Stage.BACKLOG,
        frozenset({Role.TRIAGE}),
        requires_own_claim=True,
        note="T4",
    ),
    TransitionSpec(
        "claim",
        Stage.BACKLOG,
        Stage.IN_PROGRESS,
        frozenset({Role.WORKER}),
        requires_unclaimed=True,
        note="T5",
    ),
    TransitionSpec(
        "heartbeat",
        Stage.IN_PROGRESS,
        Stage.IN_PROGRESS,
        frozenset({Role.WORKER}),
        requires_own_claim=True,
        note="T6",
    ),
    TransitionSpec(
        "release",
        Stage.IN_PROGRESS,
        Stage.BACKLOG,
        frozenset({Role.WORKER}),
        requires_own_claim=True,
        note="T7",
    ),
    TransitionSpec(
        "submit",
        Stage.IN_PROGRESS,
        Stage.AGENT_REVIEW,
        frozenset({Role.WORKER}),
        requires_own_claim=True,
        note="T9",
    ),
    TransitionSpec(
        "bounce",
        Stage.IN_PROGRESS,
        Stage.UNCLASSIFIED,
        frozenset({Role.WORKER}),
        requires_own_claim=True,
        note="T10",
    ),
    TransitionSpec(
        "claim",
        Stage.AGENT_REVIEW,
        Stage.AGENT_REVIEW,
        frozenset({Role.GATEKEEPER}),
        requires_unclaimed=True,
        note="T11",
    ),
    TransitionSpec(
        "verdict_approve",
        Stage.AGENT_REVIEW,
        Stage.HUMAN_REVIEW,
        frozenset({Role.GATEKEEPER}),
        requires_own_claim=True,
        note="T12",
    ),
    TransitionSpec(
        "verdict_reject",
        Stage.AGENT_REVIEW,
        Stage.BACKLOG,
        frozenset({Role.GATEKEEPER}),
        requires_own_claim=True,
        note="T13",
    ),
    TransitionSpec(
        "verdict_reject_misrouted",
        Stage.AGENT_REVIEW,
        Stage.UNCLASSIFIED,
        frozenset({Role.GATEKEEPER}),
        requires_own_claim=True,
        note="T14",
    ),
    TransitionSpec(
        "verdict_approve",
        Stage.HUMAN_REVIEW,
        Stage.DONE,
        frozenset({Role.HUMAN}),
        note="T15",
    ),
    TransitionSpec(
        "verdict_reject",
        Stage.HUMAN_REVIEW,
        Stage.POSTMORTEM_PENDING,
        frozenset({Role.HUMAN}),
        note="T16",
    ),
    TransitionSpec(
        "reopen",
        Stage.DONE,
        Stage.POSTMORTEM_PENDING,
        frozenset({Role.HUMAN}),
        note="T17",
    ),
    TransitionSpec(
        "claim",
        Stage.POSTMORTEM_PENDING,
        Stage.POSTMORTEM_PENDING,
        frozenset({Role.GATEKEEPER}),
        requires_unclaimed=True,
        note="T18",
    ),
    TransitionSpec(
        "submit_postmortem",
        Stage.POSTMORTEM_PENDING,
        Stage.BACKLOG,
        frozenset({Role.GATEKEEPER}),
        requires_own_claim=True,
        note="T19",
    ),
    TransitionSpec(
        "reroute",
        Stage.UNCLASSIFIED,
        Stage.UNCLASSIFIED,
        frozenset({Role.HUMAN}),
        note="T21-any",
    ),
    TransitionSpec(
        "block",
        Stage.UNCLASSIFIED,
        None,
        frozenset({Role.HUMAN, Role.TRIAGE, Role.WORKER, Role.GATEKEEPER}),
        note="B1",
    ),
    TransitionSpec(
        "unblock",
        Stage.UNCLASSIFIED,
        None,
        frozenset({Role.HUMAN}),
        allows_blocked=True,
        note="B2/B3",
    ),
)

# Human PATCH /move stage changes that mirror §3 human commands (workflow.md §8).
HUMAN_MOVE_STAGE_PAIRS: frozenset[tuple[Stage, Stage]] = frozenset(
    {
        (Stage.HUMAN_REVIEW, Stage.DONE),  # T15
        (Stage.HUMAN_REVIEW, Stage.POSTMORTEM_PENDING),  # T16
        (Stage.DONE, Stage.POSTMORTEM_PENDING),  # T17
    }
)

_CLAIMABLE_STAGES: frozenset[Stage] = frozenset(
    {
        Stage.UNCLASSIFIED,
        Stage.BACKLOG,
        Stage.AGENT_REVIEW,
        Stage.POSTMORTEM_PENDING,
    }
)

_BLOCKABLE_STAGES: frozenset[Stage] = frozenset(
    {
        Stage.UNCLASSIFIED,
        Stage.BACKLOG,
        Stage.IN_PROGRESS,
        Stage.AGENT_REVIEW,
        Stage.POSTMORTEM_PENDING,
    }
)

_WORK_AVAILABLE_ROLE: dict[Stage, Role] = {
    Stage.UNCLASSIFIED: Role.TRIAGE,
    Stage.BACKLOG: Role.WORKER,
    Stage.AGENT_REVIEW: Role.GATEKEEPER,
    Stage.POSTMORTEM_PENDING: Role.GATEKEEPER,
}


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _naive(dt: datetime) -> datetime:
    """Store UTC timestamps as naive (matches existing SQLite columns)."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def _load_ticket(db: AsyncSession, ticket_id: str, *, for_update: bool = False) -> Ticket:
    stmt = (
        select(Ticket)
        .where(Ticket.ticket_id == ticket_id)
        .options(selectinload(Ticket.column), selectinload(Ticket.board))
    )
    if for_update:
        stmt = stmt.with_for_update()
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if ticket is None:
        raise WorkflowError(ErrorCode.NOT_FOUND, f"Ticket '{ticket_id}' not found")
    return ticket


async def _reload_ticket(db: AsyncSession, ticket_id: str) -> Ticket:
    """Reload ticket + column after flush (onupdate may expire ``updated_at``).

    Does **not** call ``expire_all()`` — that would expire the request actor
    and other loaded entities, causing MissingGreenlet on later attribute access.
    """
    await db.flush()
    existing = await db.get(Ticket, ticket_id)
    if existing is not None:
        await db.refresh(existing)
    return await _load_ticket(db, ticket_id)


def ticket_stage(ticket: Ticket) -> Stage:
    col = ticket.column
    if col is None or not col.stage:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            f"Ticket '{ticket.ticket_id}' has no resolvable stage",
        )
    try:
        return Stage(col.stage)
    except ValueError as exc:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            f"Ticket '{ticket.ticket_id}' has unknown stage '{col.stage}'",
        ) from exc


def is_blocked(ticket: Ticket) -> bool:
    return bool(ticket.blocked_kind) or bool(ticket.blocked_by)


def is_claim_valid(ticket: Ticket, *, now: datetime | None = None) -> bool:
    if not ticket.claim_token:
        return False
    expires = _as_utc(ticket.lease_expires_at)
    if expires is None:
        return True
    ref = now or _now()
    return expires > ref


def _sync_status(ticket: Ticket) -> None:
    ticket.status = derive_status(ticket, now=_now())


async def _column_for_stage(
    db: AsyncSession,
    board_id: str,
    stage: Stage,
) -> Column:
    col = (
        await db.execute(
            select(Column)
            .where(Column.board_id == board_id, Column.stage == stage.value)
            .order_by(Column.position.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if col is None:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            f"Board '{board_id}' has no column for stage '{stage.value}'",
            details={"board_id": board_id, "stage": stage.value},
        )
    return col


async def _next_rank(db: AsyncSession, column_id: str) -> str:
    last = (
        await db.execute(
            select(Ticket).where(Ticket.column_id == column_id).order_by(Ticket.rank.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return rank_between(last.rank if last else None, None)


async def _assert_wip(db: AsyncSession, column: Column, *, excluding_ticket_id: str | None = None) -> None:
    if column.wip_limit <= 0:
        return
    stmt = select(func.count()).select_from(Ticket).where(
        Ticket.column_id == column.column_id,
        Ticket.is_archived.is_(False),
    )
    if excluding_ticket_id:
        stmt = stmt.where(Ticket.ticket_id != excluding_ticket_id)
    count = (await db.execute(stmt)).scalar_one() or 0
    if count >= column.wip_limit:
        raise WorkflowError(
            ErrorCode.WIP_LIMIT,
            f"WIP limit ({column.wip_limit}) exceeded for column '{column.name}'",
            details={"column_id": column.column_id, "wip_limit": column.wip_limit},
        )


async def _generate_ticket_id(db: AsyncSession) -> str:
    count = (await db.execute(select(func.count(Ticket.ticket_id)))).scalar_one() or 0
    for offset in range(1, 50):
        candidate = f"TK-{(count + offset):03d}"
        if await db.get(Ticket, candidate) is None:
            return candidate
    return f"TK-{uuid.uuid4().hex[:6].upper()}"


def _clear_claim(ticket: Ticket) -> None:
    ticket.claim_token = None
    ticket.claimed_by = None
    ticket.lease_expires_at = None


def _issue_claim(ticket: Ticket, actor: Actor, *, now: datetime | None = None) -> str:
    token = f"clm_{uuid.uuid4().hex}"
    ref = now or _now()
    ticket.claim_token = token
    ticket.claimed_by = actor.actor_id
    ticket.lease_expires_at = _naive(ref + timedelta(seconds=settings.CLAIM_LEASE_TTL_SECONDS))
    return token


async def _move_ticket(
    db: AsyncSession,
    ticket: Ticket,
    *,
    board_id: str,
    stage: Stage,
) -> Column:
    column = await _column_for_stage(db, board_id, stage)
    if ticket.column_id != column.column_id:
        await _assert_wip(db, column, excluding_ticket_id=ticket.ticket_id)
        ticket.rank = await _next_rank(db, column.column_id)
    ticket.board_id = board_id
    ticket.column_id = column.column_id
    ticket.column = column
    return column


async def _write_audit(
    db: AsyncSession,
    *,
    actor_id: str,
    ticket_id: str,
    op: str,
    from_stage: str | None,
    to_stage: str | None,
    reason: str | None,
    attempt: int,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "from": from_stage,
        "to": to_stage,
        "op": op,
        "actor": actor_id,
        "reason": reason,
        "attempt": attempt,
    }
    if extra:
        payload.update(extra)
    db.add(
        AuditLog(
            actor_id=actor_id,
            action="TRANSITION",
            target_type="ticket",
            target_id=ticket_id,
            payload=json.dumps(payload, default=str),
        )
    )


async def _emit_transitioned(
    db: AsyncSession,
    ticket: Ticket,
    *,
    actor_id: str,
    op: str,
    from_stage: str | None,
    to_stage: str,
    from_board_id: str | None = None,
) -> None:
    """Emit TICKET_TRANSITIONED on the destination board; dual-emit for board moves (#31)."""
    data = {
        "op": op,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "attempt": ticket.attempt,
        "board_id": ticket.board_id,
        "ticket_id": ticket.ticket_id,
        "from_board_id": from_board_id,
    }
    await record_event(
        db,
        EventType.TICKET_TRANSITIONED,
        board_id=ticket.board_id,
        ticket_id=ticket.ticket_id,
        actor_id=actor_id,
        data=data,
    )
    if from_board_id and from_board_id != ticket.board_id:
        await record_event(
            db,
            EventType.TICKET_TRANSITIONED,
            board_id=from_board_id,
            ticket_id=ticket.ticket_id,
            actor_id=actor_id,
            data=data,
        )


async def _emit_work_available(db: AsyncSession, ticket: Ticket, *, actor_id: str | None) -> None:
    stage = ticket_stage(ticket)
    role = _WORK_AVAILABLE_ROLE.get(stage)
    if role is None:
        return
    if is_blocked(ticket) or is_claim_valid(ticket):
        return
    caps: list[str] = []
    if stage == Stage.BACKLOG and ticket.board is not None and ticket.board.template:
        try:
            caps = list(get_template(ticket.board.template).worker_capabilities)
        except KeyError:
            caps = []
    data = WorkAvailableData(
        board_id=ticket.board_id,
        stage=stage,
        required_role=role,
        capabilities=caps,
    )
    await record_event(
        db,
        EventType.WORK_AVAILABLE,
        board_id=ticket.board_id,
        ticket_id=ticket.ticket_id,
        actor_id=actor_id,
        data=data.model_dump(mode="json"),
    )


async def _emit_human_action(db: AsyncSession, ticket: Ticket, *, actor_id: str, reason: str) -> None:
    await record_event(
        db,
        EventType.HUMAN_ACTION_REQUIRED,
        board_id=ticket.board_id,
        ticket_id=ticket.ticket_id,
        actor_id=actor_id,
        data={
            "board_id": ticket.board_id,
            "ticket_id": ticket.ticket_id,
            "stage": ticket_stage(ticket).value,
            "blocked_kind": ticket.blocked_kind,
            "reason": reason,
        },
    )


async def _finish_transition(
    db: AsyncSession,
    ticket: Ticket,
    *,
    actor: Actor,
    op: str,
    from_stage: Stage | None,
    to_stage: Stage,
    reason: str | None = None,
    work_available: bool = False,
    human_action: bool = False,
    human_reason: str | None = None,
    extra_audit: dict[str, Any] | None = None,
    from_board_id: str | None = None,
) -> Ticket:
    _sync_status(ticket)
    await _write_audit(
        db,
        actor_id=actor.actor_id,
        ticket_id=ticket.ticket_id,
        op=op,
        from_stage=from_stage.value if from_stage else None,
        to_stage=to_stage.value,
        reason=reason,
        attempt=ticket.attempt,
        extra=extra_audit,
    )
    await _emit_transitioned(
        db,
        ticket,
        actor_id=actor.actor_id,
        op=op,
        from_stage=from_stage.value if from_stage else None,
        to_stage=to_stage.value,
        from_board_id=from_board_id,
    )
    if work_available:
        await _emit_work_available(db, ticket, actor_id=actor.actor_id)
    if human_action:
        await _emit_human_action(
            db,
            ticket,
            actor_id=actor.actor_id,
            reason=human_reason or reason or op,
        )
    return await _reload_ticket(db, ticket.ticket_id)


def _require_own_claim(ticket: Ticket, actor: Actor, token: str | None) -> None:
    if not is_claim_valid(ticket):
        raise WorkflowError(ErrorCode.STALE_CLAIM, "Claim token missing or lease expired")
    if ticket.claimed_by != actor.actor_id:
        raise WorkflowError(ErrorCode.STALE_CLAIM, "Claim is held by another actor")
    if not token or token != ticket.claim_token:
        raise WorkflowError(ErrorCode.STALE_CLAIM, "Claim token mismatch")


def _require_unblocked(ticket: Ticket) -> None:
    if is_blocked(ticket):
        raise WorkflowError(
            ErrorCode.TICKET_BLOCKED,
            f"Ticket '{ticket.ticket_id}' is blocked",
            details={"blocked_kind": ticket.blocked_kind, "reason": ticket.blocked_by},
        )


async def _latest_revision(db: AsyncSession, ticket_id: str) -> TicketRevision | None:
    return (
        await db.execute(
            select(TicketRevision)
            .where(TicketRevision.ticket_id == ticket_id)
            .order_by(TicketRevision.revision_id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _list_postmortems(db: AsyncSession, ticket_id: str) -> list[TicketPostmortem]:
    return list(
        (
            await db.execute(
                select(TicketPostmortem)
                .where(TicketPostmortem.ticket_id == ticket_id)
                .order_by(TicketPostmortem.postmortem_id.asc())
            )
        )
        .scalars()
        .all()
    )


async def _apply_escalation(db: AsyncSession, ticket: Ticket, actor: Actor) -> bool:
    """E1: if attempt >= max_attempts, block with escalation. Returns True if applied."""
    if ticket.attempt < ticket.max_attempts:
        return False
    now = _naive(_now())
    ticket.blocked_kind = BlockedKind.ESCALATION.value
    ticket.blocked_by = f"attempt {ticket.attempt} reached max_attempts ({ticket.max_attempts})"
    ticket.blocked_since = now
    ticket.blocked_actor = "system"
    _sync_status(ticket)
    await record_event(
        db,
        EventType.TICKET_BLOCKED,
        board_id=ticket.board_id,
        ticket_id=ticket.ticket_id,
        actor_id=actor.actor_id,
        data={
            "kind": BlockedKind.ESCALATION.value,
            "reason": ticket.blocked_by,
            "board_id": ticket.board_id,
            "ticket_id": ticket.ticket_id,
        },
    )
    await _emit_human_action(db, ticket, actor_id=actor.actor_id, reason=ticket.blocked_by or "escalation")
    return True


# ---------------------------------------------------------------------------
# Lease reclaim (T8 / T20) — lazy
# ---------------------------------------------------------------------------


async def reclaim_expired_leases(
    db: AsyncSession,
    *,
    board_id: str | None = None,
) -> list[str]:
    """Reclaim expired claims (T8 / T20). Returns affected ticket ids."""
    now = _naive(_now())
    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.column), selectinload(Ticket.board))
        .where(
            Ticket.claim_token.is_not(None),
            Ticket.lease_expires_at.is_not(None),
            Ticket.lease_expires_at <= now,
        )
    )
    if board_id:
        stmt = stmt.where(Ticket.board_id == board_id)
    tickets = (await db.execute(stmt)).scalars().all()
    affected: list[str] = []
    system_actor_id = SYSTEM_ACTOR_ID
    for ticket in tickets:
        stage = ticket_stage(ticket)
        from_stage = stage
        _clear_claim(ticket)
        if stage == Stage.IN_PROGRESS:
            ticket.lease_expirations = (ticket.lease_expirations or 0) + 1
            await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.BACKLOG)
            to_stage = Stage.BACKLOG
            op = "lease_expire_t8"
        else:
            # T20: stay on stage, just unclaim
            to_stage = stage
            op = "lease_expire_t20"
        _sync_status(ticket)
        await _write_audit(
            db,
            actor_id=system_actor_id,
            ticket_id=ticket.ticket_id,
            op=op,
            from_stage=from_stage.value,
            to_stage=to_stage.value,
            reason="lease expired",
            attempt=ticket.attempt,
        )
        await _emit_transitioned(
            db,
            ticket,
            actor_id=system_actor_id,
            op=op,
            from_stage=from_stage.value,
            to_stage=to_stage.value,
        )
        await _emit_work_available(db, ticket, actor_id=system_actor_id)
        affected.append(ticket.ticket_id)
    if affected:
        await db.flush()
    return affected


async def recompute_all_ticket_statuses(db: AsyncSession) -> int:
    """Idempotent startup pass: rewrite stored ``status`` via :func:`derive_status`."""
    tickets = (
        await db.execute(select(Ticket).options(selectinload(Ticket.column)))
    ).scalars().all()
    changed = 0
    now = _now()
    for ticket in tickets:
        new_status = derive_status(ticket, now=now)
        if ticket.status != new_status:
            ticket.status = new_status
            changed += 1
    if changed:
        await db.flush()
    return changed


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

_IDEM_FP_KEY = "_fp"
_IDEM_BODY_KEY = "_response"


def compute_idempotency_fingerprint(
    ticket_id: str | None,
    body: Any,
) -> str:
    """Stable request fingerprint: ticket_id + canonical body hash."""
    payload = json.dumps(
        {"ticket_id": ticket_id, "body": body},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unwrap_idempotent_payload(
    parsed: dict[str, Any],
    *,
    fingerprint: str,
) -> dict[str, Any]:
    """Compare stored fingerprint; return the cached response body."""
    if _IDEM_FP_KEY in parsed or _IDEM_BODY_KEY in parsed:
        stored_fp = parsed.get(_IDEM_FP_KEY)
        if stored_fp != fingerprint:
            raise WorkflowError(
                ErrorCode.PAYLOAD_INVALID,
                "Idempotency-Key reused with a different request fingerprint",
                details={"expected_fingerprint": stored_fp},
            )
        body = parsed.get(_IDEM_BODY_KEY)
        if not isinstance(body, dict):
            raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "Corrupt idempotency response cache")
        return body
    # Legacy rows without fingerprint: treat as mismatch if a fingerprint was required.
    # Accept only when fingerprint matches an empty legacy sentinel (same key/op replay
    # of identical create bodies still works via store always writing the envelope).
    raise WorkflowError(
        ErrorCode.PAYLOAD_INVALID,
        "Idempotency-Key reused with a different request fingerprint",
    )


async def get_idempotent_response(
    db: AsyncSession,
    *,
    key: str,
    actor_id: str,
    op: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    row = await db.get(IdempotencyKey, key)
    if row is None:
        return None
    if row.actor != actor_id or row.op != op:
        raise WorkflowError(
            ErrorCode.PAYLOAD_INVALID,
            "Idempotency-Key already used with a different actor or operation",
            details={"key": key},
        )
    try:
        parsed = json.loads(row.response_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise WorkflowError(
            ErrorCode.PAYLOAD_INVALID,
            "Corrupt idempotency response cache",
        ) from exc
    if not isinstance(parsed, dict):
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "Corrupt idempotency response cache")
    return _unwrap_idempotent_payload(parsed, fingerprint=fingerprint)


async def store_idempotent_response(
    db: AsyncSession,
    *,
    key: str,
    actor_id: str,
    op: str,
    response: dict[str, Any],
    fingerprint: str,
) -> None:
    envelope = {_IDEM_FP_KEY: fingerprint, _IDEM_BODY_KEY: response}
    db.add(
        IdempotencyKey(
            key=key,
            actor=actor_id,
            op=op,
            response_json=json.dumps(envelope, default=str),
        )
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def create_inbox(
    db: AsyncSession,
    actor: Actor,
    *,
    original_request: str,
    title: str | None = None,
    priority: str = "medium",
    labels: list[str] | None = None,
    parent_ticket_id: str | None = None,
) -> Ticket:
    """T1 — create unclassified ticket on the global inbox."""
    try:
        require_roles(actor, Role.HUMAN)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc

    request = original_request.strip()
    if not request:
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "original_request is required")

    inbox = await db.get(Board, INBOX_BOARD_ID)
    if inbox is None:
        raise WorkflowError(ErrorCode.SYSTEM_BOARD, "Global inbox board is missing")

    column = await _column_for_stage(db, INBOX_BOARD_ID, Stage.UNCLASSIFIED)
    ticket_id = await _generate_ticket_id(db)
    ticket = Ticket(
        ticket_id=ticket_id,
        board_id=INBOX_BOARD_ID,
        column_id=column.column_id,
        parent_ticket_id=parent_ticket_id,
        title=(title or request[:256]).strip() or request[:256],
        description=request,
        priority=priority,
        rank=await _next_rank(db, column.column_id),
        labels=json.dumps(labels or []),
        created_by=actor.actor_id,
        original_request=request,
        attempt=0,
        max_attempts=3,
    )
    ticket.column = column
    ticket.board = inbox
    _sync_status(ticket)
    db.add(ticket)
    await db.flush()
    await record_event(
        db,
        EventType.TICKET_CREATED,
        board_id=INBOX_BOARD_ID,
        ticket_id=ticket_id,
        actor_id=actor.actor_id,
        data={"ticket_id": ticket_id, "board_id": INBOX_BOARD_ID, "stage": Stage.UNCLASSIFIED.value},
    )
    await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="create",
        from_stage=None,
        to_stage=Stage.UNCLASSIFIED,
        reason=None,
        work_available=True,
    )
    return ticket


async def create_direct(
    db: AsyncSession,
    actor: Actor,
    *,
    board_id: str,
    title: str,
    spec: str,
    acceptance_criteria: list[str],
    priority: str = "medium",
    labels: list[str] | None = None,
    parent_ticket_id: str | None = None,
    max_attempts: int | None = None,
) -> Ticket:
    """T2 — create ticket directly onto a board backlog with revision r1."""
    # T2 is human/triage; parented create_direct (delegate, cli.md §5) allows any
    # identified actor so agents can spawn pull-model subtasks.
    if parent_ticket_id is None:
        try:
            require_roles(actor, Role.HUMAN, Role.TRIAGE)
        except RoleForbiddenError as exc:
            raise WorkflowError(exc.code, exc.message) from exc

    if board_id == INBOX_BOARD_ID:
        raise WorkflowError(
            ErrorCode.SYSTEM_BOARD,
            "create_direct cannot target the inbox board; omit board_id for T1",
        )
    if not spec.strip():
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "spec is required")
    if not acceptance_criteria:
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "acceptance_criteria must be non-empty")

    board = await db.get(Board, board_id)
    if board is None:
        raise WorkflowError(ErrorCode.NOT_FOUND, f"Board '{board_id}' not found")
    if board.kind == "inbox":
        raise WorkflowError(ErrorCode.SYSTEM_BOARD, "Cannot create_direct onto an inbox board")

    tmpl = get_template(board.template or "default")
    column = await _column_for_stage(db, board_id, Stage.BACKLOG)
    ticket_id = await _generate_ticket_id(db)
    ticket = Ticket(
        ticket_id=ticket_id,
        board_id=board_id,
        column_id=column.column_id,
        parent_ticket_id=parent_ticket_id,
        title=title.strip(),
        description=spec,
        priority=priority,
        rank=await _next_rank(db, column.column_id),
        labels=json.dumps(labels or []),
        created_by=actor.actor_id,
        attempt=0,
        max_attempts=max_attempts if max_attempts is not None else tmpl.max_attempts,
    )
    ticket.column = column
    ticket.board = board
    _sync_status(ticket)
    db.add(ticket)
    await db.flush()

    revision = TicketRevision(
        ticket_id=ticket_id,
        spec=spec,
        author=actor.actor_id,
        reason="create_direct",
        source="direct",
    )
    revision.acceptance_criteria = list(acceptance_criteria)
    db.add(revision)
    await db.flush()

    await record_event(
        db,
        EventType.TICKET_CREATED,
        board_id=board_id,
        ticket_id=ticket_id,
        actor_id=actor.actor_id,
        data={"ticket_id": ticket_id, "board_id": board_id, "stage": Stage.BACKLOG.value},
    )
    await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="create_direct",
        from_stage=None,
        to_stage=Stage.BACKLOG,
        work_available=True,
        extra_audit={"revision_id": revision.revision_id},
    )
    return ticket


async def claim_ticket(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """T3 / T5 / T11 / T18 — claim a ticket. Returns claim payload dict."""
    await reclaim_expired_leases(db)
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    _require_unblocked(ticket)
    # Valid claim (incl. in_progress) → conflict for losers, not INVALID_TRANSITION.
    if is_claim_valid(ticket):
        raise WorkflowError(ErrorCode.CLAIM_CONFLICT, "Ticket is already claimed")
    stage = ticket_stage(ticket)

    role_map: dict[Stage, Role] = {
        Stage.UNCLASSIFIED: Role.TRIAGE,
        Stage.BACKLOG: Role.WORKER,
        Stage.AGENT_REVIEW: Role.GATEKEEPER,
        Stage.POSTMORTEM_PENDING: Role.GATEKEEPER,
    }
    required = role_map.get(stage)
    if required is None:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            f"Cannot claim ticket in stage '{stage.value}'",
        )
    try:
        require_roles(actor, required)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc

    if stage == Stage.AGENT_REVIEW and ticket.submitted_by == actor.actor_id:
        raise WorkflowError(
            ErrorCode.SEPARATION_OF_DUTIES,
            "Submitter cannot claim their own submission for agent review",
        )

    if stage == Stage.BACKLOG:
        board = ticket.board or await db.get(Board, ticket.board_id)
        required_caps: list[str] = []
        if board and board.template:
            try:
                required_caps = list(get_template(board.template).worker_capabilities)
            except KeyError:
                required_caps = []
        check_caps = capabilities if capabilities is not None else actor.capability_list
        # Actor must possess board-required capabilities; request caps are advisory filter.
        if required_caps and not actor_has_capabilities(actor, required_caps):
            raise WorkflowError(
                ErrorCode.ROLE_FORBIDDEN,
                "Actor lacks required board capabilities",
                details={"required": required_caps, "have": actor.capability_list},
            )
        _ = check_caps

    to_stage = Stage.IN_PROGRESS if stage == Stage.BACKLOG else stage
    if to_stage == Stage.IN_PROGRESS:
        target = await _column_for_stage(db, ticket.board_id, Stage.IN_PROGRESS)
        await _assert_wip(db, target, excluding_ticket_id=ticket.ticket_id)

    # Atomic claim: only succeed if still unclaimed.
    token = f"clm_{uuid.uuid4().hex}"
    lease = _naive(_now() + timedelta(seconds=settings.CLAIM_LEASE_TTL_SECONDS))
    values: dict[str, Any] = {
        "claim_token": token,
        "claimed_by": actor.actor_id,
        "lease_expires_at": lease,
    }
    result = await db.execute(
        update(Ticket)
        .where(
            Ticket.ticket_id == ticket_id,
            or_(Ticket.claim_token.is_(None), Ticket.lease_expires_at <= _naive(_now())),
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise WorkflowError(ErrorCode.CLAIM_CONFLICT, "Ticket is already claimed")

    # Re-load with relationships after atomic update (avoid stale / lazy IO).
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    if to_stage == Stage.IN_PROGRESS:
        await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.IN_PROGRESS)
    ticket.claim_token = token
    ticket.claimed_by = actor.actor_id
    ticket.lease_expires_at = lease
    ticket.assigned_to = actor.actor_id

    await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="claim",
        from_stage=stage,
        to_stage=to_stage,
    )

    revision = await _latest_revision(db, ticket_id)
    postmortems = await _list_postmortems(db, ticket_id)
    reviews: list[TicketReview] = []
    if stage == Stage.POSTMORTEM_PENDING:
        reviews = list(
            (
                await db.execute(
                    select(TicketReview)
                    .where(TicketReview.ticket_id == ticket_id)
                    .order_by(TicketReview.review_id.desc())
                )
            )
            .scalars()
            .all()
        )

    # Final reload so callers can format without touching expired attrs.
    ticket = await _load_ticket(db, ticket_id)
    return {
        "ticket": ticket,
        "claim_token": token,
        "lease_expires_at": lease,
        "revision": revision,
        "postmortems": postmortems,
        "reviews": reviews,
    }


async def heartbeat(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str,
) -> Ticket:
    """T6 — extend lease on an in-progress claim."""
    try:
        require_roles(actor, Role.WORKER)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    if ticket_stage(ticket) != Stage.IN_PROGRESS:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "heartbeat requires in_progress stage")
    _require_own_claim(ticket, actor, claim_token)
    ticket.lease_expires_at = _naive(_now() + timedelta(seconds=settings.CLAIM_LEASE_TTL_SECONDS))
    _sync_status(ticket)
    return await _reload_ticket(db, ticket.ticket_id)


async def release(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str,
    reason: str,
) -> Ticket:
    """T7 — release in_progress claim back to backlog."""
    try:
        require_roles(actor, Role.WORKER)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    if not reason.strip():
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "reason is required")
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage != Stage.IN_PROGRESS:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "release requires in_progress stage")
    _require_own_claim(ticket, actor, claim_token)
    _clear_claim(ticket)
    await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.BACKLOG)
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="release",
        from_stage=stage,
        to_stage=Stage.BACKLOG,
        reason=reason,
        work_available=True,
    )


async def submit(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str,
    deliverable: Deliverable,
) -> Ticket:
    """T9 — submit deliverable → agent_review."""
    try:
        require_roles(actor, Role.WORKER)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage != Stage.IN_PROGRESS:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "submit requires in_progress stage")
    _require_own_claim(ticket, actor, claim_token)
    _clear_claim(ticket)
    ticket.submitted_by = actor.actor_id
    ctx = ticket.execution_context_dict or {}
    ctx["deliverable"] = deliverable.model_dump(mode="json", by_alias=True)
    ticket.execution_context_dict = ctx
    await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.AGENT_REVIEW)
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="submit",
        from_stage=stage,
        to_stage=Stage.AGENT_REVIEW,
        work_available=True,
    )


async def bounce(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str,
    reason: str,
) -> Ticket:
    """T10 — bounce in_progress work back to inbox."""
    try:
        require_roles(actor, Role.WORKER)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    if not reason.strip():
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "reason is required")
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage != Stage.IN_PROGRESS:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "bounce requires in_progress stage")
    _require_own_claim(ticket, actor, claim_token)
    _clear_claim(ticket)
    from_board_id = ticket.board_id
    await _move_ticket(db, ticket, board_id=INBOX_BOARD_ID, stage=Stage.UNCLASSIFIED)
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="bounce",
        from_stage=stage,
        to_stage=Stage.UNCLASSIFIED,
        reason=reason,
        work_available=True,
        from_board_id=from_board_id,
    )


async def _create_auto_board(
    db: AsyncSession,
    actor: Actor,
    *,
    name: str,
    template: str,
    description: str,
) -> Board:
    count = (
        await db.execute(select(func.count()).select_from(Board).where(Board.auto_created.is_(True)))
    ).scalar_one() or 0
    if count >= settings.MAX_AUTO_BOARDS:
        raise WorkflowError(
            ErrorCode.PAYLOAD_INVALID,
            f"max_auto_boards ({settings.MAX_AUTO_BOARDS}) reached",
            details={"max_auto_boards": settings.MAX_AUTO_BOARDS},
        )
    try:
        tmpl = get_template(template)
    except KeyError as exc:
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, str(exc)) from exc
    if tmpl.name == "inbox":
        raise WorkflowError(ErrorCode.SYSTEM_BOARD, "Cannot auto-create the inbox template")

    board_id = f"auto_{uuid.uuid4().hex[:10]}"
    board = Board(
        board_id=board_id,
        name=name,
        description=description,
        created_by=actor.actor_id,
        kind="standard",
        template=tmpl.name,
        auto_created=True,
    )
    db.add(board)
    for def_col in template_column_dicts(tmpl):
        db.add(
            Column(
                column_id=f"{board_id}_{def_col['stage']}",
                board_id=board_id,
                name=def_col["name"],
                stage=def_col["stage"],
                position=def_col["position"],
                wip_limit=def_col["wip_limit"],
            )
        )
    db.add(BoardMember(board_id=board_id, actor_id=actor.actor_id, role="admin"))
    await db.flush()
    return board


async def route_ticket(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str,
    plan: RoutePlan,
) -> Ticket:
    """T4 — triage routes unclassified ticket to a board backlog."""
    try:
        require_roles(actor, Role.TRIAGE)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage != Stage.UNCLASSIFIED:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "route requires unclassified stage")
    _require_own_claim(ticket, actor, claim_token)
    _require_unblocked(ticket)

    if isinstance(plan.target, RouteTargetCreate):
        board = await _create_auto_board(
            db,
            actor,
            name=plan.target.create_board.name,
            template=plan.target.create_board.template,
            description=plan.target.create_board.description,
        )
        target_board_id = board.board_id
    else:
        target_board_id = plan.target.board_id
        board = await db.get(Board, target_board_id)
        if board is None:
            raise WorkflowError(ErrorCode.NOT_FOUND, f"Board '{target_board_id}' not found")
        if board.kind == "inbox" or target_board_id == INBOX_BOARD_ID:
            raise WorkflowError(ErrorCode.SYSTEM_BOARD, "Cannot route onto the inbox board")

    ticket.title = plan.refined_title
    ticket.description = plan.refined_spec
    _clear_claim(ticket)
    from_board_id = ticket.board_id
    await _move_ticket(db, ticket, board_id=target_board_id, stage=Stage.BACKLOG)

    revision = TicketRevision(
        ticket_id=ticket.ticket_id,
        spec=plan.refined_spec,
        author=actor.actor_id,
        reason="route",
        source="route",
    )
    revision.acceptance_criteria = list(plan.acceptance_criteria)
    db.add(revision)
    await db.flush()

    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="route",
        from_stage=stage,
        to_stage=Stage.BACKLOG,
        work_available=True,
        extra_audit={"revision_id": revision.revision_id, "target_board_id": target_board_id},
        from_board_id=from_board_id,
    )


async def verdict(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str | None,
    body: Verdict,
) -> Ticket:
    """T12–T16 — gatekeeper / human verdict."""
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    _require_unblocked(ticket)

    if stage == Stage.AGENT_REVIEW:
        try:
            require_roles(actor, Role.GATEKEEPER)
        except RoleForbiddenError as exc:
            raise WorkflowError(exc.code, exc.message) from exc
        _require_own_claim(ticket, actor, claim_token)
        if ticket.submitted_by == actor.actor_id:
            raise WorkflowError(
                ErrorCode.SEPARATION_OF_DUTIES,
                "Submitter cannot review their own submission",
            )

        if body.decision == "approve":
            if not body.findings:
                raise WorkflowError(
                    ErrorCode.PAYLOAD_INVALID,
                    "gatekeeper approve requires at least one finding",
                )
            if any(not f.pass_ for f in body.findings):
                raise WorkflowError(
                    ErrorCode.PAYLOAD_INVALID,
                    "approve verdict requires all findings to pass",
                )
            review = TicketReview(
                ticket_id=ticket.ticket_id,
                stage=stage.value,
                decision="approve",
                reviewer=actor.actor_id,
                attempt=ticket.attempt,
                reason=body.reason or None,
                misrouted=False,
            )
            review.findings = [f.model_dump(mode="json", by_alias=True) for f in body.findings]
            db.add(review)
            _clear_claim(ticket)
            await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.HUMAN_REVIEW)
            return await _finish_transition(
                db,
                ticket,
                actor=actor,
                op="verdict_approve",
                from_stage=stage,
                to_stage=Stage.HUMAN_REVIEW,
                reason=body.reason or None,
                human_action=True,
                human_reason="agent review approved; human review required",
            )

        # reject
        if body.misrouted:
            review = TicketReview(
                ticket_id=ticket.ticket_id,
                stage=stage.value,
                decision="reject",
                reviewer=actor.actor_id,
                attempt=ticket.attempt,
                reason=body.reason or "misrouted",
                misrouted=True,
            )
            review.findings = [f.model_dump(mode="json", by_alias=True) for f in body.findings]
            db.add(review)
            _clear_claim(ticket)
            from_board_id = ticket.board_id
            await _move_ticket(db, ticket, board_id=INBOX_BOARD_ID, stage=Stage.UNCLASSIFIED)
            return await _finish_transition(
                db,
                ticket,
                actor=actor,
                op="verdict_reject_misrouted",
                from_stage=stage,
                to_stage=Stage.UNCLASSIFIED,
                reason=body.reason or "misrouted",
                work_available=True,
                from_board_id=from_board_id,
            )

        if not any(not f.pass_ for f in body.findings):
            raise WorkflowError(
                ErrorCode.PAYLOAD_INVALID,
                "reject verdict requires at least one fail finding",
            )
        review = TicketReview(
            ticket_id=ticket.ticket_id,
            stage=stage.value,
            decision="reject",
            reviewer=actor.actor_id,
            attempt=ticket.attempt,
            reason=body.reason or None,
            misrouted=False,
        )
        review.findings = [f.model_dump(mode="json", by_alias=True) for f in body.findings]
        db.add(review)
        ticket.attempt += 1
        _clear_claim(ticket)
        await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.BACKLOG)
        escalated = await _apply_escalation(db, ticket, actor)
        return await _finish_transition(
            db,
            ticket,
            actor=actor,
            op="verdict_reject",
            from_stage=stage,
            to_stage=Stage.BACKLOG,
            reason=body.reason or None,
            work_available=not escalated,
            extra_audit={"escalated": escalated},
        )

    if stage == Stage.HUMAN_REVIEW:
        try:
            require_roles(actor, Role.HUMAN)
        except RoleForbiddenError as exc:
            raise WorkflowError(exc.code, exc.message) from exc

        if body.decision == "approve":
            # T15 — only path to done
            if is_claim_valid(ticket):
                _clear_claim(ticket)
            review = TicketReview(
                ticket_id=ticket.ticket_id,
                stage=stage.value,
                decision="approve",
                reviewer=actor.actor_id,
                attempt=ticket.attempt,
                reason=body.reason or None,
                misrouted=False,
            )
            review.findings = []
            db.add(review)
            await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.DONE)
            ticket.blocked_kind = None
            ticket.blocked_by = None
            ticket.blocked_since = None
            ticket.blocked_actor = None
            return await _finish_transition(
                db,
                ticket,
                actor=actor,
                op="verdict_approve",
                from_stage=stage,
                to_stage=Stage.DONE,
                reason=body.reason or None,
            )

        if not (body.reason or "").strip():
            raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "human reject requires reason")
        review = TicketReview(
            ticket_id=ticket.ticket_id,
            stage=stage.value,
            decision="reject",
            reviewer=actor.actor_id,
            attempt=ticket.attempt,
            reason=body.reason,
            misrouted=False,
        )
        review.findings = []
        db.add(review)
        if is_claim_valid(ticket):
            _clear_claim(ticket)
        await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.POSTMORTEM_PENDING)
        return await _finish_transition(
            db,
            ticket,
            actor=actor,
            op="verdict_reject",
            from_stage=stage,
            to_stage=Stage.POSTMORTEM_PENDING,
            reason=body.reason,
            work_available=True,
        )

    raise WorkflowError(
        ErrorCode.INVALID_TRANSITION,
        f"verdict not allowed in stage '{stage.value}'",
    )


async def submit_postmortem(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    claim_token: str,
    body: Postmortem,
) -> Ticket:
    """T19 — gatekeeper submits postmortem → backlog (+ E1)."""
    try:
        require_roles(actor, Role.GATEKEEPER)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage != Stage.POSTMORTEM_PENDING:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            "postmortem requires postmortem_pending stage",
        )
    _require_own_claim(ticket, actor, claim_token)

    human_review = (
        await db.execute(
            select(TicketReview)
            .where(
                TicketReview.ticket_id == ticket_id,
                TicketReview.stage == Stage.HUMAN_REVIEW.value,
                TicketReview.decision == "reject",
            )
            .order_by(TicketReview.review_id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    revision = TicketRevision(
        ticket_id=ticket.ticket_id,
        spec=body.revised_spec,
        author=actor.actor_id,
        reason="postmortem",
        source="postmortem",
    )
    revision.acceptance_criteria = list(body.revised_acceptance_criteria)
    db.add(revision)
    await db.flush()

    pm = TicketPostmortem(
        ticket_id=ticket.ticket_id,
        what_went_wrong=body.what_went_wrong,
        root_cause=body.root_cause,
        revision_id=revision.revision_id,
        human_review_id=human_review.review_id if human_review else None,
        author=actor.actor_id,
    )
    db.add(pm)

    ticket.title = ticket.title  # keep
    ticket.description = body.revised_spec
    ticket.attempt += 1
    _clear_claim(ticket)
    await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.BACKLOG)
    escalated = await _apply_escalation(db, ticket, actor)
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="submit_postmortem",
        from_stage=stage,
        to_stage=Stage.BACKLOG,
        work_available=not escalated,
        extra_audit={"revision_id": revision.revision_id, "escalated": escalated},
    )


async def reopen(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    reason: str,
) -> Ticket:
    """T17 — human reopens done → postmortem_pending."""
    try:
        require_roles(actor, Role.HUMAN)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    if not reason.strip():
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "reason is required")
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage != Stage.DONE:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "reopen requires done stage")
    review = TicketReview(
        ticket_id=ticket.ticket_id,
        stage=stage.value,
        decision="reject",
        reviewer=actor.actor_id,
        attempt=ticket.attempt,
        reason=reason,
        misrouted=False,
    )
    review.findings = []
    db.add(review)
    await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.POSTMORTEM_PENDING)
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="reopen",
        from_stage=stage,
        to_stage=Stage.POSTMORTEM_PENDING,
        reason=reason,
        work_available=True,
    )


async def reroute(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    reason: str,
    to_inbox: bool = False,
    target_board_id: str | None = None,
) -> Ticket:
    """T21 — human reroutes any non-done ticket to inbox or another board backlog."""
    try:
        require_roles(actor, Role.HUMAN)
    except RoleForbiddenError as exc:
        raise WorkflowError(exc.code, exc.message) from exc
    if not reason.strip():
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "reason is required")
    if to_inbox == bool(target_board_id):
        raise WorkflowError(
            ErrorCode.PAYLOAD_INVALID,
            "reroute requires exactly one of to_inbox or target_board_id",
        )

    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage == Stage.DONE:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "cannot reroute a done ticket")

    if is_claim_valid(ticket):
        _clear_claim(ticket)

    from_board_id = ticket.board_id
    if to_inbox:
        await _move_ticket(db, ticket, board_id=INBOX_BOARD_ID, stage=Stage.UNCLASSIFIED)
        to_stage = Stage.UNCLASSIFIED
    else:
        assert target_board_id is not None
        if target_board_id == INBOX_BOARD_ID:
            raise WorkflowError(
                ErrorCode.SYSTEM_BOARD,
                "Use to_inbox=true to send a ticket to the inbox",
            )
        board = await db.get(Board, target_board_id)
        if board is None:
            raise WorkflowError(ErrorCode.NOT_FOUND, f"Board '{target_board_id}' not found")
        await _move_ticket(db, ticket, board_id=target_board_id, stage=Stage.BACKLOG)
        to_stage = Stage.BACKLOG

    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="reroute",
        from_stage=stage,
        to_stage=to_stage,
        reason=reason,
        work_available=True,
        from_board_id=from_board_id,
    )


async def block_ticket(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    kind: BlockedKind,
    reason: str,
    claim_token: str | None = None,
) -> Ticket:
    """B1 — apply blocked overlay."""
    if kind == BlockedKind.ESCALATION:
        raise WorkflowError(
            ErrorCode.PAYLOAD_INVALID,
            "escalation blocks are system-only (E1)",
        )
    if not reason.strip():
        raise WorkflowError(ErrorCode.PAYLOAD_INVALID, "reason is required")

    ticket = await _load_ticket(db, ticket_id, for_update=True)
    stage = ticket_stage(ticket)
    if stage not in _BLOCKABLE_STAGES:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            f"Cannot block ticket in stage '{stage.value}'",
        )

    role = resolve_workflow_role(actor)
    is_holder = is_claim_valid(ticket) and ticket.claimed_by == actor.actor_id
    # Humans and claim holders may always block. Unclaimed tickets also accept
    # external/dependency blocks from any authenticated actor (MCP report-block).
    unclaimed = not is_claim_valid(ticket)
    allow_unclaimed_external = unclaimed and kind in (
        BlockedKind.EXTERNAL,
        BlockedKind.DEPENDENCY,
    )
    if role != Role.HUMAN and not is_holder and not allow_unclaimed_external:
        raise WorkflowError(
            ErrorCode.ROLE_FORBIDDEN,
            "Only the claim holder or a human can block this ticket",
        )
    if is_holder and claim_token and claim_token != ticket.claim_token:
        raise WorkflowError(ErrorCode.STALE_CLAIM, "Claim token mismatch")

    from_stage = stage
    if is_claim_valid(ticket):
        _clear_claim(ticket)
    if stage == Stage.IN_PROGRESS:
        await _move_ticket(db, ticket, board_id=ticket.board_id, stage=Stage.BACKLOG)
        stage = Stage.BACKLOG

    ticket.blocked_kind = kind.value
    ticket.blocked_by = reason
    ticket.blocked_since = _naive(_now())
    ticket.blocked_actor = actor.actor_id
    _sync_status(ticket)

    await record_event(
        db,
        EventType.TICKET_BLOCKED,
        board_id=ticket.board_id,
        ticket_id=ticket.ticket_id,
        actor_id=actor.actor_id,
        data={"kind": kind.value, "reason": reason, "board_id": ticket.board_id},
    )
    human_action = kind in (BlockedKind.NEEDS_HUMAN, BlockedKind.ESCALATION)
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="block",
        from_stage=from_stage,
        to_stage=stage,
        reason=reason,
        human_action=human_action,
        human_reason=reason,
        extra_audit={"blocked_kind": kind.value},
    )


async def unblock_ticket(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    resolution: str | None = None,
) -> Ticket:
    """B2 / B3 — clear blocked overlay."""
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    if not is_blocked(ticket):
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, "Ticket is not blocked")

    kind = ticket.blocked_kind
    role = resolve_workflow_role(actor)
    needs_human_resolution = kind in (
        BlockedKind.NEEDS_HUMAN.value,
        BlockedKind.ESCALATION.value,
    )

    if needs_human_resolution:
        try:
            require_roles(actor, Role.HUMAN)
        except RoleForbiddenError as exc:
            raise WorkflowError(exc.code, exc.message) from exc
        if not (resolution or "").strip():
            raise WorkflowError(
                ErrorCode.PAYLOAD_INVALID,
                "resolution is required to unblock needs_human / escalation",
            )
        rev = TicketRevision(
            ticket_id=ticket.ticket_id,
            spec=ticket.description or "",
            author=actor.actor_id,
            reason=resolution,
            source="unblock",
        )
        latest = await _latest_revision(db, ticket.ticket_id)
        rev.acceptance_criteria = latest.acceptance_criteria if latest else []
        # Record resolution into spec note when no prior revision body.
        if resolution and not rev.spec:
            rev.spec = resolution
        db.add(rev)
    else:
        # dependency / external — blocker or human
        if role != Role.HUMAN and ticket.blocked_actor != actor.actor_id:
            raise WorkflowError(
                ErrorCode.ROLE_FORBIDDEN,
                "Only the blocking actor or a human can unblock dependency/external",
            )

    stage = ticket_stage(ticket)
    ticket.blocked_kind = None
    ticket.blocked_by = None
    ticket.blocked_since = None
    ticket.blocked_actor = None
    _sync_status(ticket)

    await record_event(
        db,
        EventType.TICKET_UNBLOCKED,
        board_id=ticket.board_id,
        ticket_id=ticket.ticket_id,
        actor_id=actor.actor_id,
        data={
            "previous_kind": kind,
            "resolution": resolution,
            "board_id": ticket.board_id,
        },
    )
    return await _finish_transition(
        db,
        ticket,
        actor=actor,
        op="unblock",
        from_stage=stage,
        to_stage=stage,
        reason=resolution,
        work_available=stage in _CLAIMABLE_STAGES,
    )


async def move_ticket_constrained(
    db: AsyncSession,
    actor: Actor,
    ticket_id: str,
    *,
    target_column_id: str,
    previous_ticket_id: str | None = None,
    next_ticket_id: str | None = None,
    reason: str | None = None,
) -> Ticket:
    """PATCH move under workflow.md §8 constraints.

    Stage-changing human moves delegate to the corresponding command
    (``verdict`` / ``reopen``) so review records and reject reasons are
    recorded. Same-stage reorders only adjust column/rank.
    """
    ticket = await _load_ticket(db, ticket_id, for_update=True)
    target = await db.get(Column, target_column_id)
    if target is None:
        raise WorkflowError(ErrorCode.INVALID_TRANSITION, f"Column '{target_column_id}' not found")

    from_stage = ticket_stage(ticket)
    try:
        to_stage = Stage(target.stage)
    except ValueError as exc:
        raise WorkflowError(
            ErrorCode.INVALID_TRANSITION,
            f"Unknown target stage '{target.stage}'",
        ) from exc

    if from_stage != to_stage:
        role = resolve_workflow_role(actor)
        if role != Role.HUMAN:
            raise WorkflowError(
                ErrorCode.INVALID_TRANSITION,
                "Stage-changing moves require a workflow transition command",
                details={"from": from_stage.value, "to": to_stage.value},
            )
        if (from_stage, to_stage) not in HUMAN_MOVE_STAGE_PAIRS:
            raise WorkflowError(
                ErrorCode.INVALID_TRANSITION,
                "This stage change is not an allowed human move; use verdict/reopen/reroute",
                details={"from": from_stage.value, "to": to_stage.value},
            )
        if target.board_id != ticket.board_id:
            raise WorkflowError(
                ErrorCode.SYSTEM_BOARD,
                "Cross-board stage moves must use reroute",
            )

        # Delegate to T15 / T16 / T17 so side effects (reviews, reasons) apply.
        if (from_stage, to_stage) == (Stage.HUMAN_REVIEW, Stage.DONE):
            ticket = await verdict(
                db,
                actor,
                ticket_id,
                claim_token=None,
                body=Verdict.model_construct(
                    decision="approve",
                    reason=reason or "",
                    misrouted=False,
                    findings=[],
                ),
            )
        elif (from_stage, to_stage) == (Stage.HUMAN_REVIEW, Stage.POSTMORTEM_PENDING):
            if not (reason or "").strip():
                raise WorkflowError(
                    ErrorCode.PAYLOAD_INVALID,
                    "reason is required for human reject move (T16)",
                )
            ticket = await verdict(
                db,
                actor,
                ticket_id,
                claim_token=None,
                body=Verdict.model_construct(
                    decision="reject",
                    reason=reason.strip(),
                    misrouted=False,
                    findings=[],
                ),
            )
        elif (from_stage, to_stage) == (Stage.DONE, Stage.POSTMORTEM_PENDING):
            if not (reason or "").strip():
                raise WorkflowError(
                    ErrorCode.PAYLOAD_INVALID,
                    "reason is required for reopen move (T17)",
                )
            ticket = await reopen(db, actor, ticket_id, reason=reason.strip())
        else:
            raise WorkflowError(
                ErrorCode.INVALID_TRANSITION,
                "Unhandled human move stage pair",
                details={"from": from_stage.value, "to": to_stage.value},
            )

        # Snap to the requested column within the resulting stage when needed.
        if ticket.column_id != target_column_id and ticket_stage(ticket) == to_stage:
            if target.board_id != ticket.board_id:
                raise WorkflowError(
                    ErrorCode.SYSTEM_BOARD,
                    "Cross-board stage moves must use reroute",
                )
            await _assert_wip(db, target, excluding_ticket_id=ticket.ticket_id)
            prev_rank: str | None = None
            next_rank: str | None = None
            if previous_ticket_id:
                prev = await db.get(Ticket, previous_ticket_id)
                if prev:
                    prev_rank = prev.rank
            if next_ticket_id:
                nxt = await db.get(Ticket, next_ticket_id)
                if nxt:
                    next_rank = nxt.rank
            ticket.column_id = target_column_id
            ticket.column = target
            ticket.rank = rank_between(prev_rank, next_rank)
            _sync_status(ticket)
            await db.flush()
        return await _reload_ticket(db, ticket.ticket_id)

    if ticket.column_id != target_column_id:
        await _assert_wip(db, target, excluding_ticket_id=ticket.ticket_id)

    prev_rank = None
    next_rank = None
    if previous_ticket_id:
        prev = await db.get(Ticket, previous_ticket_id)
        if prev:
            prev_rank = prev.rank
    if next_ticket_id:
        nxt = await db.get(Ticket, next_ticket_id)
        if nxt:
            next_rank = nxt.rank

    ticket.column_id = target_column_id
    ticket.board_id = target.board_id
    ticket.column = target
    ticket.rank = rank_between(prev_rank, next_rank)
    _sync_status(ticket)

    await _write_audit(
        db,
        actor_id=actor.actor_id,
        ticket_id=ticket.ticket_id,
        op="move",
        from_stage=from_stage.value,
        to_stage=to_stage.value,
        reason="same-stage reorder",
        attempt=ticket.attempt,
    )
    return await _reload_ticket(db, ticket.ticket_id)


async def next_ticket(
    db: AsyncSession,
    actor: Actor,
    board_id: str,
    *,
    capabilities: list[str] | None = None,
) -> Ticket | None:
    """Return the oldest unclaimed, unblocked claimable ticket (or None → 204)."""
    await reclaim_expired_leases(db, board_id=board_id)
    board = await db.get(Board, board_id)
    if board is None:
        raise WorkflowError(ErrorCode.NOT_FOUND, f"Board '{board_id}' not found")

    role = resolve_workflow_role(actor)
    if board_id == INBOX_BOARD_ID or board.kind == "inbox":
        stages = [Stage.UNCLASSIFIED.value]
    else:
        # Prefer stage matching the caller's role; fall back to all claimable.
        if role == Role.WORKER:
            stages = [Stage.BACKLOG.value]
        elif role == Role.GATEKEEPER:
            stages = [Stage.AGENT_REVIEW.value, Stage.POSTMORTEM_PENDING.value]
        elif role == Role.TRIAGE:
            stages = [Stage.UNCLASSIFIED.value]
        else:
            stages = [s.value for s in _CLAIMABLE_STAGES]

    now = _naive(_now())
    stmt = (
        select(Ticket)
        .join(Column, Ticket.column_id == Column.column_id)
        .options(selectinload(Ticket.column), selectinload(Ticket.board))
        .where(
            Ticket.board_id == board_id,
            Ticket.is_archived.is_(False),
            Column.stage.in_(stages),
            Ticket.blocked_kind.is_(None),
            or_(Ticket.blocked_by.is_(None), Ticket.blocked_by == ""),
            or_(
                Ticket.claim_token.is_(None),
                Ticket.lease_expires_at.is_not(None) & (Ticket.lease_expires_at <= now),
            ),
        )
        .order_by(Ticket.created_at.asc())
        .limit(50)
    )
    candidates = (await db.execute(stmt)).scalars().all()

    required_caps: list[str] = []
    if board.template and Stage.BACKLOG.value in stages:
        try:
            required_caps = list(get_template(board.template).worker_capabilities)
        except KeyError:
            required_caps = []

    for ticket in candidates:
        if is_claim_valid(ticket):
            continue
        if is_blocked(ticket):
            continue
        if ticket_stage(ticket) == Stage.BACKLOG:
            if required_caps and capabilities is not None:
                have = {c.lower() for c in capabilities}
                if not all(c.lower() in have for c in required_caps):
                    continue
            if required_caps and not actor_has_capabilities(actor, required_caps):
                continue
        if (
            ticket_stage(ticket) == Stage.AGENT_REVIEW
            and ticket.submitted_by == actor.actor_id
        ):
            continue
        return ticket
    return None


async def ticket_history(db: AsyncSession, ticket_id: str) -> dict[str, Any]:
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise WorkflowError(ErrorCode.NOT_FOUND, f"Ticket '{ticket_id}' not found")

    revisions = list(
        (
            await db.execute(
                select(TicketRevision)
                .where(TicketRevision.ticket_id == ticket_id)
                .order_by(TicketRevision.revision_id.asc())
            )
        )
        .scalars()
        .all()
    )
    reviews = list(
        (
            await db.execute(
                select(TicketReview)
                .where(TicketReview.ticket_id == ticket_id)
                .order_by(TicketReview.review_id.asc())
            )
        )
        .scalars()
        .all()
    )
    postmortems = await _list_postmortems(db, ticket_id)
    audits = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "ticket", AuditLog.target_id == ticket_id)
                .order_by(AuditLog.log_id.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "ticket_id": ticket_id,
        "revisions": revisions,
        "reviews": reviews,
        "postmortems": postmortems,
        "audit": audits,
    }
