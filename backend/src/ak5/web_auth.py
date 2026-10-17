"""Lazy compatibility shim — prefer ``ak5.server.web_auth``."""
from __future__ import annotations

from ak5._lazy_shim import make_module_getattr

__getattr__ = make_module_getattr(__name__, "ak5.server.web_auth")


def __dir__() -> list[str]:
    import importlib

    mod = importlib.import_module("ak5.server.web_auth")
    return sorted(set(dir(mod)) | set(globals()))
