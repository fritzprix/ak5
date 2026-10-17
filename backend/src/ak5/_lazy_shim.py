"""Helpers for top-level compatibility shims that must not import server eagerly."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any


def install_submodule_shim(*, alias_prefix: str, target_prefix: str) -> None:
    """Route missing ``alias_prefix.*`` submodule imports to ``target_prefix.*``."""

    class _Loader(Loader):
        def __init__(self, target: str) -> None:
            self._target = target

        def create_module(self, spec: ModuleSpec) -> ModuleType | None:
            return None

        def exec_module(self, module: ModuleType) -> None:
            target = importlib.import_module(self._target)
            sys.modules[module.__name__] = target

    class _Finder(MetaPathFinder):
        _ak5_shim_marker: str

        def find_spec(
            self,
            fullname: str,
            path: Sequence[str] | None,
            target: ModuleType | None = None,
        ) -> ModuleSpec | None:
            if fullname == alias_prefix or not fullname.startswith(alias_prefix + "."):
                return None
            # If a real file exists under the shim package (e.g. event_context.py),
            # let the normal path-based importer win by returning None only when
            # the submodule is already loadable without us — checked via path.
            suffix = fullname[len(alias_prefix) + 1 :]
            pkg = sys.modules.get(alias_prefix)
            pkg_paths = getattr(pkg, "__path__", None) if pkg is not None else None
            if pkg_paths is not None:
                for root in pkg_paths:
                    candidate_py = f"{root}/{suffix.replace('.', '/')}.py"
                    candidate_pkg = f"{root}/{suffix.replace('.', '/')}/__init__.py"
                    import os

                    if os.path.isfile(candidate_py) or os.path.isfile(candidate_pkg):
                        return None
            return ModuleSpec(fullname, _Loader(f"{target_prefix}.{suffix}"))

    marker = f"_ak5_shim_{alias_prefix}"
    for existing in sys.meta_path:
        if getattr(existing, "_ak5_shim_marker", None) == marker:
            return
    finder = _Finder()
    finder._ak5_shim_marker = marker
    sys.meta_path.insert(0, finder)


def make_module_getattr(module_name: str, target: str) -> Callable[[str], Any]:
    """``__getattr__`` that swaps ``sys.modules[module_name]`` for ``target`` on first use.

    Also rebinds the attribute on the parent package so ``from ak5 import database``
    (and test monkeypatches on that handle) see the real module, not the shim.
    """

    def __getattr__(name: str) -> Any:
        mod = importlib.import_module(target)
        sys.modules[module_name] = mod
        parent_name, _, child = module_name.rpartition(".")
        if parent_name and child:
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, child, mod)
        return getattr(mod, name)

    return __getattr__
