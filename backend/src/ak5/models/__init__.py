"""Compatibility shim — prefer ``ak5.server.models``."""
from __future__ import annotations

import pkgutil
import sys
from importlib import import_module

_server = import_module("ak5.server.models")
sys.modules[__name__] = _server

# Ensure ``import ak5.models.X`` resolves to the already-loaded server submodule
# (avoids re-executing model modules and duplicating SQLAlchemy tables).
_prefix = "ak5.server.models."
_alias = "ak5.models."
if getattr(_server, "__path__", None) is not None:
    for info in pkgutil.walk_packages(_server.__path__, _prefix):
        mod = import_module(info.name)
        sys.modules[_alias + info.name[len(_prefix) :]] = mod
