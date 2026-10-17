"""Compatibility shim — prefer ``ak5.server.authz``."""
from __future__ import annotations

import sys
from importlib import import_module

_server = import_module("ak5.server.authz")
sys.modules[__name__] = _server
