"""#75: every API route with a path parameter must consult the caller's workspace.

Heuristic (source-level): the endpoint body has to reference the workspace
(``workspace_id``, ``_reject_cross_workspace…``, ``scoped(`` …). This is how the
attachment download leak (#75) was found. New exceptions need a reason here.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re

import ak5.server.routers as routers_pkg
from fastapi.routing import APIRoute

_USES_WORKSPACE = re.compile(
    r"workspace_id|_reject_cross_workspace|_workspace_forbidden|require_\w*workspace|scoped\("
)

# (router module, method, path) → why the route needs no workspace check.
_ALLOWED = {
    ("actors", "PATCH", "/actors/{actor_id}"): "self-or-admin only; admin ids/roles are reserved",
    ("templates", "GET", "/templates/{name}"): "built-in template registry, no workspace data",
}


def _routes_with_path_params() -> list[tuple[str, str, str, APIRoute]]:
    out = []
    for mod in pkgutil.iter_modules(routers_pkg.__path__):
        module = importlib.import_module(f"{routers_pkg.__name__}.{mod.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            if isinstance(route, APIRoute) and re.search(r"\{\w+\}", route.path):
                for method in sorted(route.methods):
                    out.append((mod.name, method, route.path, route))
    return out


def test_audit_sees_routes():
    assert len(_routes_with_path_params()) >= 40


def test_path_param_routes_consult_workspace():
    missing = [
        (mod, method, path)
        for mod, method, path, route in _routes_with_path_params()
        if (mod, method, path) not in _ALLOWED
        and not _USES_WORKSPACE.search(inspect.getsource(route.endpoint))
    ]
    assert not missing, (
        "routes with path params that never reference the workspace "
        f"(add a check, or an _ALLOWED entry with a reason): {missing}"
    )


def test_allowlist_has_no_stale_entries():
    present = {(mod, method, path) for mod, method, path, _ in _routes_with_path_params()}
    assert set(_ALLOWED) <= present
