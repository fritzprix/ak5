"""Import boundary contracts (cli.md §1 / issue #18).

``ak5.contracts``, ``ak5.client``, and ``ak5.daemon`` must not import the
gateway server package (or transitional shims that alias into it).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "ak5"

LAYER_DIRS = ("contracts", "client", "daemon")

# Direct imports of the server package, or of modules that now live under it
# (including compatibility shims at the old top-level paths).
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
    }
)


def _module_prefix_forbidden(name: str) -> bool:
    return any(name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN_MODULES)


def _iter_py_files(layer: str) -> list[Path]:
    root = SRC_ROOT / layer
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _forbidden_imports_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_prefix_forbidden(alias.name):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and _module_prefix_forbidden(node.module):
            hits.append(node.module)
    return hits


@pytest.mark.parametrize("layer", LAYER_DIRS)
def test_layer_sources_do_not_import_server(layer: str) -> None:
    violations: list[str] = []
    for path in _iter_py_files(layer):
        for mod in _forbidden_imports_in(path):
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
