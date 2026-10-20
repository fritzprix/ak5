#!/usr/bin/env bash
# Smoke-test a built ak5 wheel the same way end users run it via uvx:
# install ONLY the wheel (+ declared Requires-Dist) in an isolated venv,
# then import the FastAPI app and exercise CLI entrypoints.
#
# Usage:
#   ./scripts/smoke_uvx_wheel.sh              # uses newest dist/ak5-*.whl
#   ./scripts/smoke_uvx_wheel.sh path/to.whl
#
# Exit non-zero on any failure — callers (release.py, CI) must treat this as a hard gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WHEEL="${1:-}"
if [[ -z "$WHEEL" ]]; then
  WHEEL="$(ls -1t dist/ak5-*.whl 2>/dev/null | head -1 || true)"
fi
if [[ -z "$WHEEL" || ! -f "$WHEEL" ]]; then
  echo "error: no wheel found. Build with: uv build backend --out-dir dist" >&2
  exit 1
fi

echo "==> Smoke-testing wheel as isolated install: $WHEEL"

# --- Metadata gate: critical runtime deps must be declared (not just transitive) ---
META="$(unzip -p "$WHEEL" '*/METADATA' 2>/dev/null || true)"
if [[ -z "$META" ]]; then
  echo "error: could not read METADATA from wheel" >&2
  exit 1
fi

required_base=(
  "fastapi"
  "uvicorn"
  "click"
  "httpx"
  "pydantic"
  "mcp"
  "rich"
)
for dep in "${required_base[@]}"; do
  if ! echo "$META" | grep -qiE "^Requires-Dist:[[:space:]]*${dep}"; then
    echo "error: wheel METADATA missing base dependency: ${dep}" >&2
    echo "       (uvx only installs Requires-Dist — transitive lockfile deps do not ship)" >&2
    exit 1
  fi
  echo "  ✓ Requires-Dist includes base ${dep}"
done

required_server=(
  "greenlet"
  "sqlalchemy"
  "aiosqlite"
  "sse-starlette"
  "python-multipart"
  "pydantic-settings"
  "pyjwt"
)
for dep in "${required_server[@]}"; do
  if ! echo "$META" | grep -qiE "^Requires-Dist:[[:space:]]*${dep}.*extra[[:space:]]*==[[:space:]]*['\"]server['\"]"; then
    echo "error: wheel METADATA missing server-extra dependency: ${dep}" >&2
    exit 1
  fi
  echo "  ✓ Requires-Dist includes server extra ${dep}"
done

if ! echo "$META" | grep -qiE "^Requires-Dist:[[:space:]]*asyncpg.*extra[[:space:]]*==[[:space:]]*['\"]postgres['\"]"; then
  echo "error: wheel METADATA missing postgres-extra dependency: asyncpg" >&2
  exit 1
fi
echo "  ✓ Requires-Dist includes postgres extra asyncpg"

# --- Isolated venv install (mirrors uvx: only wheel + declared deps) ---
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

uv venv "$TMP/venv" --python 3.13 --quiet
# Prefer -f over -x: Git Bash mounts often omit the execute bit on .exe files.
if [[ -f "$TMP/venv/bin/python" ]]; then
  PY="$TMP/venv/bin/python"
  BIN_DIR="$TMP/venv/bin"
elif [[ -f "$TMP/venv/Scripts/python.exe" ]]; then
  # Windows / Git Bash layout
  PY="$TMP/venv/Scripts/python.exe"
  BIN_DIR="$TMP/venv/Scripts"
else
  echo "error: could not find python in isolated venv under $TMP/venv" >&2
  exit 1
fi

echo "==> Base install (no server extra)"
uv pip install --python "$PY" --quiet "$WHEEL"

AK5=""
for candidate in "$BIN_DIR/ak5.exe" "$BIN_DIR/ak5"; do
  if [[ -f "$candidate" ]]; then
    AK5="$candidate"
    break
  fi
done
if [[ -z "$AK5" ]]; then
  echo "error: could not find ak5 CLI entrypoint under $BIN_DIR" >&2
  exit 1
fi

"$AK5" --version
"$AK5" boards --help >/dev/null
"$AK5" serve --help >/dev/null
set +e
SERVE_ERR="$("$AK5" serve --host 127.0.0.1 --port 9 2>&1)"
SERVE_RC=$?
set -e
if [[ "$SERVE_RC" -eq 0 ]]; then
  echo "error: 'ak5 serve' succeeded without server extra" >&2
  exit 1
fi
if ! echo "$SERVE_ERR" | grep -qi "ak5\\[server\\]"; then
  echo "error: missing install hint for server extra; got:" >&2
  echo "$SERVE_ERR" >&2
  exit 1
fi
echo "  ✓ base CLI works; serve prints ak5[server] hint (exit $SERVE_RC)"

echo "==> Server extra install"
uv pip install --python "$PY" --quiet "${WHEEL}[server]"

echo "==> Import gate (SQLAlchemy asyncio / greenlet / FastAPI app)"
"$PY" - <<'PY'
import greenlet  # noqa: F401 — must be installable from wheel Requires-Dist
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401
from ak5.server.app import app, create_app
from ak5.server.web_ui_static import web_ui_available

assert app is not None
assert create_app is not None
assert web_ui_available(), "embedded web_ui/index.html missing from wheel"
from ak5.server.web_ui_static import web_ui_assets_built
assert web_ui_assets_built(), "embedded web_ui/_next static chunks missing from wheel"
print("  ✓ greenlet + sqlalchemy.ext.asyncio + ak5.server.app:app + web_ui")
PY

echo "==> CLI entrypoints (with server)"
"$AK5" --version
"$AK5" web --help >/dev/null
"$AK5" boards --help >/dev/null
echo "  ✓ ak5 --version / web --help / boards --help"

echo "==> Smoke OK (isolated wheel install matches uvx contract)"
