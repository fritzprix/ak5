"""Compatibility shim — prefer ``ak5.server.schemas``."""
from __future__ import annotations

import pkgutil
import sys
from importlib import import_module

_server = import_module("ak5.server.schemas")
sys.modules[__name__] = _server

# Ensure ``import ak5.schemas.X`` resolves to the already-loaded server submodule
# (avoids re-executing model modules and duplicating SQLAlchemy tables).
_prefix = "ak5.server.schemas."
_alias = "ak5.schemas."
if getattr(_server, "__path__", None) is not None:
    for info in pkgutil.walk_packages(_server.__path__, _prefix):
        mod = import_module(info.name)
        sys.modules[_alias + info.name[len(_prefix) :]] = mod
