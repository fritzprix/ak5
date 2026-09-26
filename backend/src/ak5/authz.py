"""Authorization utilities and role-based access control helpers for AK5."""

from ak5.models.actor import Actor

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
