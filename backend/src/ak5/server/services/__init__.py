"""Server services package (lazy exports to avoid pulling SQLAlchemy on import)."""

from __future__ import annotations

from typing import Any

__all__ = [
    "discover_agents",
    "event_bus",
    "initial_rank",
    "rank_between",
    "rebalance_ranks",
]


def __getattr__(name: str) -> Any:
    if name == "event_bus":
        from ak5.server.services import event_bus as mod

        return getattr(mod, name)
    if name == "discover_agents":
        from ak5.server.services.discovery import discover_agents

        return discover_agents
    if name in {"initial_rank", "rank_between", "rebalance_ranks"}:
        from ak5.server.services import lexorank as mod

        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
