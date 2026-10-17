"""Lazy compatibility shim — prefer ``ak5.server.services``.

``event_context`` and ``templates`` are real client-safe submodules.
"""

from __future__ import annotations

import importlib
from typing import Any

from ak5._lazy_shim import install_submodule_shim

install_submodule_shim(alias_prefix="ak5.services", target_prefix="ak5.server.services")


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    mod = importlib.import_module(f"ak5.server.services.{name}")
    return mod
