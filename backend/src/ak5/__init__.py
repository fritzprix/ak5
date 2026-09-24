"""AK5 - Agent-Orchestrated Kanban System."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ak5")
except PackageNotFoundError:
    __version__ = "1.0.3"

__all__ = ["__version__"]
