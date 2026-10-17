"""Compatibility shim — prefer ``ak5.server.config``."""
from __future__ import annotations

import sys
from importlib import import_module

_server = import_module("ak5.server.config")
sys.modules[__name__] = _server
