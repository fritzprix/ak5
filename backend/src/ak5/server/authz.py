"""Authorization utilities and workflow role checks for AK5."""

from __future__ import annotations

from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.inbox import inbox_board_id_for
from ak5.server.models.actor import Actor

RESERVED_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "system_admin", "root", "superuser"})
RESERVED_ADMIN_IDS: frozenset[str] = frozenset({"admin", "system", "root"})

# Request-scoped Principal attached by AuthContext (not an ORM column).
_ACTOR_PRINCIPAL_ATTR = "_ak5_principal"


def attach_principal(actor: Actor, principal: Principal) -> None:
    """Bind a Principal to an Actor for this request (identity.md §2)."""
    actor.__dict__[_ACTOR_PRINCIPAL_ATTR] = principal


def principal_of(actor: Actor) -> Principal | None:
    """Return the Principal attached for this request, if any."""
    value = actor.__dict__.get(_ACTOR_PRINCIPAL_ATTR)
    return value if isinstance(value, Principal) else None


def is_admin(actor: Actor) -> bool:
    """Check if the given actor possesses administrative privileges."""
    return actor.role.lower() in RESERVED_ADMIN_ROLES or actor.actor_id.lower() in RESERVED_ADMIN_IDS


def same_subject(ref: str | None, actor: Actor) -> bool:
    """True when ``ref`` and ``actor`` denote the same principal/actor identity."""
    if ref is None:
        return False
    if ref == actor.actor_id:
        return True
    principal = principal_of(actor)
    if principal is not None and ref == principal.principal_id:
        return True
    from ak5.server.auth.bridge import principal_id_for_actor

    return principal_id_for_actor(ref) == principal_id_for_actor(actor.actor_id)


def is_admin_or_owner(actor: Actor, created_by: str | None) -> bool:
    """Check if the actor has administrative privileges or matches the owner/creator ID."""
    if is_admin(actor):
        return True
    return same_subject(created_by, actor)


def resolve_workflow_role(actor: Actor) -> Role | None:
    """Map an actor's stored role to a workflow :class:`Role` (legacy Actor JWTs only).

    Prefer :func:`workflow_role_of` — Principal JWTs must not use Actor columns
    for role (identity.md §2).
    """
    raw = (actor.role or "").strip().lower()
    actor_type = (actor.actor_type or "").strip().lower()
    agent_roles = {Role.TRIAGE.value, Role.WORKER.value, Role.GATEKEEPER.value}

    if actor_type == "human":
        if raw in agent_roles:
            return None
        return Role.HUMAN

    if actor_type == "agent":
        if raw in agent_roles:
            return Role(raw)
        return None

    return None


def workflow_role_of(subject: Actor | Principal) -> Role | None:
    """Sole workflow-role rule for authz (identity.md §2).

    - :class:`Principal` → ``workflow_role`` directly.
    - :class:`Actor` with attached Principal → that Principal's ``workflow_role``.
    - Legacy Actor JWT (no Principal) → :func:`resolve_workflow_role`.
    """
    if isinstance(subject, Principal):
        return subject.workflow_role
    attached = principal_of(subject)
    if attached is not None:
        return attached.workflow_role
    return resolve_workflow_role(subject)


class RoleForbiddenError(Exception):
    """Raised when an actor lacks a required workflow role."""

    def __init__(self, message: str) -> None:
        self.code = ErrorCode.ROLE_FORBIDDEN
        self.message = message
        super().__init__(message)


def require_roles(subject: Actor | Principal, *allowed: Role) -> Role:
    """Return the subject's workflow role or raise :class:`RoleForbiddenError`.

    Principal JWTs use :attr:`Principal.workflow_role` only (no Actor role path).
    """
    role = workflow_role_of(subject)
    if isinstance(subject, Principal):
        label = f"{subject.principal_type}:{subject.principal_id}"
    else:
        attached = principal_of(subject)
        label = (
            f"{attached.principal_type}:{attached.principal_id}"
            if attached is not None
            else subject.role
        )
    if role is None or role not in allowed:
        allowed_names = ", ".join(sorted(r.value for r in allowed))
        raise RoleForbiddenError(
            f"Role '{label}' cannot perform this action (requires one of: {allowed_names})",
        )
    return role


def board_in_scope(
    subject: Actor | Principal,
    board_id: str,
) -> bool:
    """True when an agent may touch ``board_id`` (identity.md §4.3).

    Empty ``board_scope`` means all boards. The agent's own workspace inbox is
    always in scope (#76).
    Users and legacy Actor JWTs (no Principal) are unrestricted here.
    """
    principal = subject if isinstance(subject, Principal) else principal_of(subject)
    if principal is None or principal.principal_type != "agent":
        return True
    if not principal.board_scope:
        return True
    if board_id == inbox_board_id_for(principal.workspace_id):
        return True
    return board_id in principal.board_scope


def require_board_scope(
    subject: Actor | Principal,
    board_id: str,
) -> None:
    """Raise :class:`RoleForbiddenError` when ``board_id`` is outside agent scope."""
    if board_in_scope(subject, board_id):
        return
    if isinstance(subject, Principal):
        label = f"{subject.principal_type}:{subject.principal_id}"
        scope = subject.board_scope
    else:
        attached = principal_of(subject)
        label = (
            f"{attached.principal_type}:{attached.principal_id}"
            if attached is not None
            else subject.actor_id
        )
        scope = attached.board_scope if attached is not None else ()
    raise RoleForbiddenError(
        f"Agent '{label}' cannot access board '{board_id}' "
        f"(board_scope={list(scope)})",
    )


def actor_has_capabilities(actor: Actor, required: list[str] | tuple[str, ...] | None) -> bool:
    """True when ``required`` is empty or every required capability is on the actor."""
    if not required:
        return True
    have = {c.lower() for c in actor.capability_list}
    return all(c.lower() in have for c in required)
