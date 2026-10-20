"""create_app / database import must not mkdir or seed (deployment.md §4 / #110)."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def test_import_create_app_does_not_mkdir_sqlite_parent(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    nested_db = tmp_path / "nested" / "deep" / "ak5.db"
    monkeypatch.setenv("AK5_DATA_DIR", str(data))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{nested_db.as_posix()}")
    monkeypatch.setenv("AK5_SKIP_DOTENV", "1")
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)

    for name in list(sys.modules):
        if name == "ak5" or name.startswith("ak5."):
            del sys.modules[name]

    main = importlib.import_module("ak5.server.main")
    assert not nested_db.parent.exists(), "import must not create SQLite parent dirs"
    assert not data.exists(), "import must not create AK5_DATA_DIR"
    app = main.create_app()
    assert app is not None
    assert not nested_db.parent.exists()
    assert not data.exists()
