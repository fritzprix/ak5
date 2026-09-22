from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ak5.models.actor import Actor


async def discover_agents(
    db: AsyncSession,
    capability: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> list[Actor]:
    """Find agents matching capability tag, status, or search query."""
    stmt = select(Actor).where(Actor.actor_type == "agent")

    if status:
        stmt = stmt.where(Actor.status == status)

    result = await db.execute(stmt)
    agents: Sequence[Actor] = result.scalars().all()

    filtered: list[tuple[int, Actor]] = []

    cap_norm = capability.lower().strip() if capability else None
    query_terms = [q.lower().strip() for q in query.split()] if query else []

    for agent in agents:
        score = 0
        agent_caps = [c.lower() for c in agent.capability_list]
        desc_lower = (agent.description or "").lower()
        role_lower = agent.role.lower()
        name_lower = agent.name.lower()

        # Capability filter
        if cap_norm:
            if cap_norm in agent_caps:
                score += 10
            elif any(cap_norm in c for c in agent_caps):
                score += 5
            else:
                # If specific capability was requested and agent lacks it, skip
                continue

        # Status bonus (idle agents preferred)
        if agent.status == "idle":
            score += 3
        elif agent.status == "busy":
            score += 1

        # Query search matching
        if query_terms:
            matched_terms = 0
            for term in query_terms:
                term_matched = False
                if any(term in c for c in agent_caps):
                    score += 5
                    term_matched = True
                if term in role_lower:
                    score += 4
                    term_matched = True
                if term in desc_lower:
                    score += 3
                    term_matched = True
                if term in name_lower:
                    score += 2
                    term_matched = True
                if term_matched:
                    matched_terms += 1

            if matched_terms == 0 and not cap_norm:
                continue

        filtered.append((score, agent))

    # Sort by descending score
    filtered.sort(key=lambda item: item[0], reverse=True)
    return [agent for _, agent in filtered]
