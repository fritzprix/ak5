"""Unit tests for pipeline ownership root mapping (#38)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from pipeline_lib import ownership_roots  # noqa: E402


def test_ownership_roots_backend_and_packages():
    assert ownership_roots("backend/src/ak5/server/main.py") == {"backend"}
    assert ownership_roots("packages/sdk/src/errors.ts") == {"packages/sdk"}
    assert ownership_roots("docs/ops/backup-restore.md") == {"docs/ops"}
    assert ownership_roots("docs/pipeline.md") == {"docs"}
    assert ownership_roots("scripts/pipeline_tick.py") == {"scripts"}
    assert ownership_roots("api/index.py") == {"api"}


def test_ownership_roots_empty_and_docker():
    assert ownership_roots("") == set()
    assert ownership_roots("docker-compose.postgres.yml") == {"docker"}
