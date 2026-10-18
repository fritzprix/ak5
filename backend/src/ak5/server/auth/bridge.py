"""Map Principal IDs ↔ legacy Actor rows during #23 transition."""

from __future__ import annotations

import re

from ak5.server.seed import (
    LOCAL_AGENT_REVIEWER_ID,
    LOCAL_AGENT_WORKER_ID,
    LOCAL_USER_ID,
)

# Principal → v1/v2 Actor.actor_id (seed + identify still use Actor FKs).
PRINCIPAL_TO_ACTOR: dict[str, str] = {
    LOCAL_USER_ID: "user_pm",
    LOCAL_AGENT_WORKER_ID: "agent_image_worker",
    LOCAL_AGENT_REVIEWER_ID: "agent_code_reviewer",
}

ACTOR_TO_PRINCIPAL: dict[str, str] = {v: k for k, v in PRINCIPAL_TO_ACTOR.items()}


def register_bridge(principal_id: str, actor_id: str) -> None:
    """Register or refresh a Principal ↔ Actor mapping (idempotent)."""
    PRINCIPAL_TO_ACTOR[principal_id] = actor_id
    ACTOR_TO_PRINCIPAL[actor_id] = principal_id


def actor_id_for_principal(principal_id: str) -> str:
    return PRINCIPAL_TO_ACTOR.get(principal_id, principal_id)


def principal_id_for_actor(actor_id: str) -> str:
    return ACTOR_TO_PRINCIPAL.get(actor_id, actor_id)


def _slug_actor_id(actor_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", actor_id).strip("_").lower()
    return slug or "unknown"


def principal_id_for_v1_actor(*, actor_id: str, actor_type: str) -> str:
    """Stable principal id for a v1 Actor row (identity.md §9)."""
    existing = ACTOR_TO_PRINCIPAL.get(actor_id)
    if existing:
        return existing
    if actor_id.startswith(("usr_", "agt_")):
        return actor_id
    prefix = "usr" if actor_type == "human" else "agt"
    return f"{prefix}_v1_{_slug_actor_id(actor_id)}"
