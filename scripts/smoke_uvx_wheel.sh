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

required_declared=(
  "greenlet"
  "sqlalchemy"
  "fastapi"
  "uvicorn"
  "aiosqlite"
)
for dep in "${required_declared[@]}"; do
  if ! echo "$META" | grep -qiE "^Requires-Dist:[[:space:]]*${dep}"; then
    echo "error: wheel METADATA missing declared dependency: ${dep}" >&2
    echo "       (uvx only installs Requires-Dist — transitive lockfile deps do not ship)" >&2
    exit 1
  fi
  echo "  ✓ Requires-Dist includes ${dep}"
done

# --- Isolated venv install (mirrors uvx: only wheel + declared deps) ---
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

uv venv "$TMP/venv" --python 3.13 --quiet
# shellcheck disable=SC1091
source "$TMP/venv/bin/activate"
uv pip install --python "$TMP/venv/bin/python" --quiet "$WHEEL"

PY="$TMP/venv/bin/python"
AK5="$TMP/venv/bin/ak5"

echo "==> Import gate (SQLAlchemy asyncio / greenlet / FastAPI app)"
"$PY" - <<'PY'
import greenlet  # noqa: F401 — must be installable from wheel Requires-Dist
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401
from ak5.main import app
from ak5.web_ui_static import web_ui_available

assert app is not None
assert web_ui_available(), "embedded web_ui/index.html missing from wheel"
print("  ✓ greenlet + sqlalchemy.ext.asyncio + ak5.main:app + web_ui")
PY

echo "==> CLI entrypoints"
"$AK5" --version
"$AK5" web --help >/dev/null
"$AK5" boards --help >/dev/null
echo "  ✓ ak5 --version / web --help / boards --help"

echo "==> Smoke OK (isolated wheel install matches uvx contract)"
