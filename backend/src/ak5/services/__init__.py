"""Compatibility shim — prefer ``ak5.server.services``."""
from __future__ import annotations

import pkgutil
import sys
from importlib import import_module

_server = import_module("ak5.server.services")
sys.modules[__name__] = _server

# Ensure ``import ak5.services.X`` resolves to the already-loaded server submodule
# (avoids re-executing model modules and duplicating SQLAlchemy tables).
_prefix = "ak5.server.services."
_alias = "ak5.services."
if getattr(_server, "__path__", None) is not None:
    for info in pkgutil.walk_packages(_server.__path__, _prefix):
        mod = import_module(info.name)
        sys.modules[_alias + info.name[len(_prefix) :]] = mod
