"""create_app / database import must not mkdir or seed (deployment.md §4 / #110)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROBE = r"""
import os
from pathlib import Path

data = Path(os.environ["AK5_PROBE_DATA"])
nested_db = Path(os.environ["AK5_PROBE_DB"])
assert not nested_db.parent.exists()
assert not data.exists()

from ak5.server.main import create_app

assert not nested_db.parent.exists(), "import must not create SQLite parent dirs"
assert not data.exists(), "import must not create AK5_DATA_DIR"
app = create_app()
assert app is not None
assert not nested_db.parent.exists()
assert not data.exists()
"""


def test_import_create_app_does_not_mkdir_sqlite_parent(tmp_path: Path):
    """Run in a subprocess so reloaded modules cannot poison the suite."""
    data = tmp_path / "data"
    nested_db = tmp_path / "nested" / "deep" / "ak5.db"
    env = os.environ.copy()
    env["AK5_DATA_DIR"] = str(data)
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{nested_db.as_posix()}"
    env["AK5_SKIP_DOTENV"] = "1"
    env["AK5_PROBE_DATA"] = str(data)
    env["AK5_PROBE_DB"] = str(nested_db)
    env.pop("AK5_DEPLOY_PROFILE", None)
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"import side-effect probe failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
