"""Principal model (identity.md §2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ak5.contracts.stages import Role


@dataclass(frozen=True)
class Principal:
    principal_id: str  # usr_… | agt_…
    principal_type: Literal["user", "agent"]
    workspace_id: str
    workflow_role: Role  # user → HUMAN, agent → agents.workflow_role
    membership_role: str | None = None  # user only: owner|admin|member
    board_scope: tuple[str, ...] = ()  # agent only; empty = all boards
    presence_at: datetime | None = None  # user only: last WebAuthn assertion (UTC, #28)


def workflow_role_for_principal(principal: Principal) -> Role:
    """Sole workflow-role rule (identity.md §2)."""
    return principal.workflow_role
