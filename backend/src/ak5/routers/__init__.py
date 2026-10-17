"""Lazy compatibility shim — prefer ``ak5.server.routers``."""

from __future__ import annotations

import importlib
from typing import Any

from ak5._lazy_shim import install_submodule_shim

install_submodule_shim(alias_prefix="ak5.routers", target_prefix="ak5.server.routers")


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    mod = importlib.import_module("ak5.server.routers")
    return getattr(mod, name)
