"""Authorization utilities and workflow role checks for AK5."""

from __future__ import annotations

from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.server.auth.principal import Principal
from ak5.server.models.actor import Actor

RESERVED_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "system_admin", "root", "superuser"})
RESERVED_ADMIN_IDS: frozenset[str] = frozenset({"admin", "system", "root"})

def is_admin(actor: Actor) -> bool:
    """Check if the given actor possesses administrative privileges."""
    return actor.role.lower() in RESERVED_ADMIN_ROLES or actor.actor_id.lower() in RESERVED_ADMIN_IDS


def is_admin_or_owner(actor: Actor, created_by: str | None) -> bool:
    """Check if the actor has administrative privileges or matches the owner/creator ID."""
    if is_admin(actor):
        return True
    return created_by == actor.actor_id


def resolve_workflow_role(actor: Actor) -> Role | None:
    """Map an actor's stored role to a workflow :class:`Role`.

    Role and ``actor_type`` must agree:

    - :attr:`Role.HUMAN` only when ``actor_type == "human"`` (legacy display
      roles like ``PM`` still count as human).
    - ``triage`` / ``worker`` / ``gatekeeper`` only when ``actor_type == "agent"``.

    Mismatches (e.g. agent with stored role ``human``) return ``None`` so
    callers raise :class:`~ak5.contracts.errors.ErrorCode.ROLE_FORBIDDEN`.

    Prefer :class:`~ak5.server.auth.principal.Principal` when available
    (identity.md §2 — single role rule). Principal JWTs overlay Actor fields
    for the request via :func:`ak5.server.routers.auth._overlay_actor_from_principal`.
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


class RoleForbiddenError(Exception):
    """Raised when an actor lacks a required workflow role."""

    def __init__(self, message: str) -> None:
        self.code = ErrorCode.ROLE_FORBIDDEN
        self.message = message
        super().__init__(message)


def require_roles(subject: Actor | Principal, *allowed: Role) -> Role:
    """Return the subject's workflow role or raise :class:`RoleForbiddenError`.

    :class:`Principal` uses ``workflow_role`` directly (identity.md §2).
    :class:`Actor` uses :func:`resolve_workflow_role` (legacy / overlaid).
    """
    if isinstance(subject, Principal):
        role: Role | None = subject.workflow_role
        label = f"{subject.principal_type}:{subject.principal_id}"
    else:
        role = resolve_workflow_role(subject)
        label = subject.role
    if role is None or role not in allowed:
        allowed_names = ", ".join(sorted(r.value for r in allowed))
        raise RoleForbiddenError(
            f"Role '{label}' cannot perform this action (requires one of: {allowed_names})",
        )
    return role


def actor_has_capabilities(actor: Actor, required: list[str] | tuple[str, ...] | None) -> bool:
    """True when ``required`` is empty or every required capability is on the actor."""
    if not required:
        return True
    have = {c.lower() for c in actor.capability_list}
    return all(c.lower() in have for c in required)
