"""Shared v2 contracts: stages, errors, payloads, events, API version.

This package must remain free of sqlalchemy / fastapi / ak5.models /
ak5.routers / ak5.services imports (pydantic only).
"""

from ak5.contracts.errors import (
    EXIT_CODE,
    HTTP_STATUS,
    ErrorCode,
    ErrorDetail,
    ErrorEnvelope,
)
from ak5.contracts.events import EventType, WorkAvailableData
from ak5.contracts.payloads import (
    Artifact,
    CriteriaSelfCheck,
    Deliverable,
    Finding,
    Postmortem,
    RouteCreateBoard,
    RoutePlan,
    RouteTargetBoard,
    RouteTargetCreate,
    Verdict,
)
from ak5.contracts.stages import BlockedKind, Role, Stage
from ak5.contracts.version import API_VERSION, MIN_CLIENT_VERSION

__all__ = [
    "API_VERSION",
    "MIN_CLIENT_VERSION",
    "Stage",
    "Role",
    "BlockedKind",
    "ErrorCode",
    "HTTP_STATUS",
    "EXIT_CODE",
    "ErrorDetail",
    "ErrorEnvelope",
    "RoutePlan",
    "RouteTargetBoard",
    "RouteTargetCreate",
    "RouteCreateBoard",
    "Deliverable",
    "Artifact",
    "CriteriaSelfCheck",
    "Finding",
    "Verdict",
    "Postmortem",
    "EventType",
    "WorkAvailableData",
]
