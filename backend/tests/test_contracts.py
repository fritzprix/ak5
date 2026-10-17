"""Contracts package isolation and payload validation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from ak5.contracts.errors import EXIT_CODE, HTTP_STATUS, ErrorCode, ErrorEnvelope
from ak5.contracts.events import EventType, WorkAvailableData
from ak5.contracts.payloads import Finding, RoutePlan, RouteTargetBoard, Verdict
from ak5.contracts.stages import BlockedKind, Role, Stage
from ak5.contracts.version import API_VERSION, MIN_CLIENT_VERSION
from pydantic import ValidationError

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "src" / "ak5" / "contracts"

FORBIDDEN_IMPORT_PREFIXES = (
    "sqlalchemy",
    "fastapi",
    "ak5.models",
    "ak5.routers",
    "ak5.services",
)


def _module_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_contracts_forbid_heavy_imports():
    py_files = sorted(CONTRACTS_DIR.glob("*.py"))
    assert py_files, "contracts package missing"
    for path in py_files:
        for mod in _module_imports(path):
            for banned in FORBIDDEN_IMPORT_PREFIXES:
                assert not (mod == banned or mod.startswith(banned + ".")), (
                    f"{path.name} imports forbidden module {mod!r}"
                )


def test_stage_role_blocked_kind_values():
    assert set(Stage) == {
        Stage.UNCLASSIFIED,
        Stage.BACKLOG,
        Stage.IN_PROGRESS,
        Stage.AGENT_REVIEW,
        Stage.POSTMORTEM_PENDING,
        Stage.HUMAN_REVIEW,
        Stage.DONE,
    }
    assert set(Role) == {Role.TRIAGE, Role.WORKER, Role.GATEKEEPER, Role.HUMAN}
    assert set(BlockedKind) == {
        BlockedKind.NEEDS_HUMAN,
        BlockedKind.DEPENDENCY,
        BlockedKind.EXTERNAL,
        BlockedKind.ESCALATION,
    }


def test_error_maps_cover_all_codes():
    assert set(HTTP_STATUS) == set(ErrorCode)
    assert set(EXIT_CODE) == set(ErrorCode)
    assert HTTP_STATUS[ErrorCode.SYSTEM_BOARD] == 403
    assert EXIT_CODE[ErrorCode.VERSION_MISMATCH] == 7
    assert EXIT_CODE[ErrorCode.TICKET_BLOCKED] == 5


def test_error_envelope_shape():
    body = ErrorEnvelope.model_validate(
        {"error": {"code": "SYSTEM_BOARD", "message": "nope", "details": {"board_id": "inbox"}}}
    )
    assert body.error.code == ErrorCode.SYSTEM_BOARD
    assert body.error.details["board_id"] == "inbox"


def test_verdict_approve_requires_all_pass():
    Verdict(
        decision="approve",
        findings=[Finding(**{"criterion": "a", "pass": True}), Finding(**{"criterion": "b", "pass": True})],
    )
    with pytest.raises(ValidationError):
        Verdict(
            decision="approve",
            findings=[Finding(**{"criterion": "a", "pass": False})],
        )


def test_verdict_reject_requires_fail_finding():
    Verdict(
        decision="reject",
        findings=[Finding(**{"criterion": "a", "pass": False})],
    )
    with pytest.raises(ValidationError):
        Verdict(
            decision="reject",
            findings=[Finding(**{"criterion": "a", "pass": True})],
        )
    # misrouted reject may omit fail findings
    Verdict(decision="reject", misrouted=True, reason="wrong board")


def test_route_plan_and_work_available():
    plan = RoutePlan(
        refined_title="T",
        refined_spec="S",
        acceptance_criteria=["c1"],
        target=RouteTargetBoard(board_id="b1"),
        confidence=0.9,
    )
    assert plan.target.board_id == "b1"
    data = WorkAvailableData(
        board_id="inbox",
        stage=Stage.UNCLASSIFIED,
        required_role=Role.TRIAGE,
        capabilities=[],
    )
    assert data.required_role == Role.TRIAGE
    assert EventType.WORK_AVAILABLE.value == "WORK_AVAILABLE"


def test_api_version_constants():
    assert API_VERSION == "2"
    assert MIN_CLIENT_VERSION == "2.0.0"
