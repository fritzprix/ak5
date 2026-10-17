"""Import boundary contracts (cli.md §1 / issue #18).

``ak5.contracts``, ``ak5.client``, ``ak5.daemon``, and module-level ``ak5.cli``
must not import the gateway server package (or transitional shims into it).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "ak5"

LAYER_DIRS = ("contracts", "client", "daemon", "cli")

FORBIDDEN_MODULES = frozenset(
    {
        "ak5.server",
        "ak5.models",
        "ak5.routers",
        "ak5.services",
        "ak5.schemas",
        "ak5.database",
        "ak5.main",
        "ak5.authz",
        "ak5.seed",
        "ak5.web_auth",
        "ak5.web_ui_static",
        "ak5.config",
        "ak5.security",
    }
)

# Allowed at module level in cli (client-safe re-exports / contracts).
_CLI_ALLOWED = frozenset(
    {
        "ak5.contracts.event_context",
        "ak5.services.templates",
    }
)


def _module_prefix_forbidden(name: str) -> bool:
    return any(name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN_MODULES)


def _iter_py_files(layer: str) -> list[Path]:
    root = SRC_ROOT / layer
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _forbidden_imports_in(path: Path, *, module_level_only: bool) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []

    def _check(node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_prefix_forbidden(alias.name):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and _module_prefix_forbidden(node.module):
            if (path.parts[-2:] == ("cli",) or "cli" in path.parts) and node.module in _CLI_ALLOWED:
                return
            hits.append(node.module)

    if module_level_only:
        for node in tree.body:
            _check(node)
    else:
        for node in ast.walk(tree):
            _check(node)
    return hits


@pytest.mark.parametrize("layer", LAYER_DIRS)
def test_layer_sources_do_not_import_server(layer: str) -> None:
    violations: list[str] = []
    module_level_only = layer == "cli"
    for path in _iter_py_files(layer):
        for mod in _forbidden_imports_in(path, module_level_only=module_level_only):
            rel = path.relative_to(SRC_ROOT.parent)
            violations.append(f"{rel}: imports {mod}")
    assert violations == [], "Forbidden server imports:\n" + "\n".join(violations)


def test_runtime_import_of_layers_does_not_load_server_package() -> None:
    """Fresh interpreter: importing client/daemon/contracts must not load ak5.server."""
    script = r"""
import sys
import ak5.contracts  # noqa: F401
import ak5.client  # noqa: F401
import ak5.daemon  # noqa: F401
loaded = [k for k in sys.modules if k == "ak5.server" or k.startswith("ak5.server.")]
assert not loaded, loaded
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(SRC_ROOT.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout
