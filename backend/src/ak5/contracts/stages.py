"""Workflow stages, actor roles, and blocked overlay kinds (workflow.md §1–§2)."""

from enum import StrEnum


class Stage(StrEnum):
    """Kanban stage — source of truth for ticket placement (workflow.md §2)."""

    UNCLASSIFIED = "unclassified"
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    AGENT_REVIEW = "agent_review"
    POSTMORTEM_PENDING = "postmortem_pending"
    HUMAN_REVIEW = "human_review"
    DONE = "done"


class Role(StrEnum):
    """Actor role for transition authorization (workflow.md §1)."""

    TRIAGE = "triage"
    WORKER = "worker"
    GATEKEEPER = "gatekeeper"
    HUMAN = "human"


class BlockedKind(StrEnum):
    """Orthogonal blocked overlay kinds (workflow.md §2 / §4)."""

    NEEDS_HUMAN = "needs_human"
    DEPENDENCY = "dependency"
    EXTERNAL = "external"
    ESCALATION = "escalation"


# All stage values as a frozenset (useful for CHECK constraints / validation).
STAGE_VALUES: frozenset[str] = frozenset(s.value for s in Stage)

# Stages allowed on a standard (non-inbox) board.
STANDARD_STAGES: tuple[Stage, ...] = (
    Stage.BACKLOG,
    Stage.IN_PROGRESS,
    Stage.AGENT_REVIEW,
    Stage.POSTMORTEM_PENDING,
    Stage.HUMAN_REVIEW,
    Stage.DONE,
)
