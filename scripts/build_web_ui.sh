#!/usr/bin/env bash
# Build the Next.js dashboard into backend/src/ak5/web_ui for PyPI packaging.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"
TARGET="$ROOT/backend/src/ak5/web_ui"
MIDDLEWARE="$FRONTEND/src/middleware.ts"
MIDDLEWARE_BAK="$FRONTEND/src/middleware.ts.embed_bak"

restore_middleware() {
  if [ -f "$MIDDLEWARE_BAK" ]; then
    mv -f "$MIDDLEWARE_BAK" "$MIDDLEWARE"
  fi
}
trap restore_middleware EXIT

echo "==> Building AK5 web UI (static export)"
cd "$FRONTEND"
if [ ! -d node_modules ]; then
  npm ci
fi

# Static export does not support Next middleware — park it for the embed build.
if [ -f "$MIDDLEWARE" ]; then
  mv "$MIDDLEWARE" "$MIDDLEWARE_BAK"
fi

export AK5_STATIC_EXPORT=1
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-/api/v1}"
npm run build

if [ ! -f "$FRONTEND/out/index.html" ]; then
  echo "error: frontend/out/index.html missing after build" >&2
  exit 1
fi

echo "==> Syncing into $TARGET"
rm -rf "$TARGET"
mkdir -p "$TARGET"
cp -a "$FRONTEND/out/." "$TARGET/"
touch "$TARGET/.keep"

echo "==> Done. Assets ready for hatchling wheel include."
