"""Compatibility shim — prefer ``ak5.server.web_ui_static``."""
from __future__ import annotations

import sys
from importlib import import_module

_server = import_module("ak5.server.web_ui_static")
sys.modules[__name__] = _server
