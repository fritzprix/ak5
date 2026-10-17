"""Map Principal IDs ↔ legacy Actor rows during #23 transition."""

from __future__ import annotations

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


def actor_id_for_principal(principal_id: str) -> str:
    return PRINCIPAL_TO_ACTOR.get(principal_id, principal_id)


def principal_id_for_actor(actor_id: str) -> str:
    return ACTOR_TO_PRINCIPAL.get(actor_id, actor_id)
