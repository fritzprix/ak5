"""Authorization utilities and workflow role checks for AK5."""

from __future__ import annotations

from ak5.contracts.errors import ErrorCode
from ak5.contracts.stages import Role
from ak5.models.actor import Actor

RESERVED_ADMIN_ROLES: frozenset[str] = frozenset({"admin", "system_admin", "root", "superuser"})
RESERVED_ADMIN_IDS: frozenset[str] = frozenset({"admin", "system", "root"})

# Roles accepted by login / workflow transitions (cli.md §4.2, workflow.md §1).
WORKFLOW_ROLES: frozenset[str] = frozenset(r.value for r in Role)


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

    Explicit ``triage|worker|gatekeeper|human`` values win. Legacy human
    actors (e.g. role ``PM``) are treated as :attr:`Role.HUMAN` so existing
    seeds keep working until identities are re-logged with v2 roles.
    """
    raw = (actor.role or "").strip().lower()
    if raw in WORKFLOW_ROLES:
        return Role(raw)
    if actor.actor_type == "human":
        return Role.HUMAN
    return None


class RoleForbiddenError(Exception):
    """Raised when an actor lacks a required workflow role."""

    def __init__(self, message: str) -> None:
        self.code = ErrorCode.ROLE_FORBIDDEN
        self.message = message
        super().__init__(message)


def require_roles(actor: Actor, *allowed: Role) -> Role:
    """Return the actor's workflow role or raise :class:`RoleForbiddenError`."""
    role = resolve_workflow_role(actor)
    if role is None or role not in allowed:
        allowed_names = ", ".join(sorted(r.value for r in allowed))
        raise RoleForbiddenError(
            f"Role '{actor.role}' cannot perform this action (requires one of: {allowed_names})",
        )
    return role


def actor_has_capabilities(actor: Actor, required: list[str] | tuple[str, ...] | None) -> bool:
    """True when ``required`` is empty or every required capability is on the actor."""
    if not required:
        return True
    have = {c.lower() for c in actor.capability_list}
    return all(c.lower() in have for c in required)
