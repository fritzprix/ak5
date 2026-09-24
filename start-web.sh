#!/usr/bin/env bash
# ==============================================================================
# DEPRECATED: prefer the packaged single-port launcher:
#   ak5 web
#   uvx ak5 web
#
# This script remains for monorepo developers who want Next.js on :3000
# (hot reload) alongside the gateway. New features (Tailscale banner, web auth)
# are implemented in Python — see `ak5 web`.
# ==============================================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--legacy" || "${AK5_LEGACY_WEB:-}" == "1" ]]; then
  echo "AK5: starting legacy Next.js + gateway via remaining script body..."
else
  echo "================================================================"
  echo "  start-web.sh is deprecated for end users."
  echo "  Use:  ak5 web   (or: uvx ak5 web)"
  echo "  Single port 8000 · embedded UI · Tailscale auto-detect · optional auth"
  echo "================================================================"
  echo ""
  if command -v uv >/dev/null 2>&1; then
    cd "$PROJECT_ROOT"
    exec uv run ak5 web "$@"
  elif command -v ak5 >/dev/null 2>&1; then
    exec ak5 web "$@"
  else
    echo "Neither 'uv' nor 'ak5' found. Install with: pip install ak5   or   uv tool install ak5"
    echo "Or re-run with AK5_LEGACY_WEB=1 for the old Next.js dual-port flow."
    exit 1
  fi
fi

# ---- Legacy dual-port path (AK5_LEGACY_WEB=1) ----
FRONTEND_DIR="$PROJECT_ROOT/frontend"
PID_DIR="$PROJECT_ROOT/.run"
mkdir -p "$PID_DIR"

# Load .env if present
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
BACKEND_LOG="$PID_DIR/backend.log"
FRONTEND_LOG="$PID_DIR/frontend.log"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

get_tailscale_ip() {
    if command -v tailscale >/dev/null 2>&1; then
        tailscale ip -4 2>/dev/null | head -n 1 || echo ""
    else
        echo ""
    fi
}

get_tailscale_dns() {
    if command -v tailscale >/dev/null 2>&1; then
        tailscale status --json 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("Self") or {}).get("DNSName","").rstrip("."))' 2>/dev/null || echo ""
    else
        echo ""
    fi
}

print_urls() {
    local TS_IP TS_DNS
    TS_IP="$(get_tailscale_ip)"
    TS_DNS="$(get_tailscale_dns)"

    echo -e "\n${BOLD}${GREEN}================================================================${NC}"
    echo -e "   ${BOLD}AK5 Kanban Web Dashboard (legacy :3000)${NC}"
    echo -e "${BOLD}${GREEN}================================================================${NC}"
    echo -e "  ${BOLD}Local Browser:${NC}       ${CYAN}http://localhost:3000${NC}"
    if [ -n "$TS_DNS" ]; then
        echo -e "  ${BOLD}Tailscale Domain:${NC}    ${CYAN}http://${TS_DNS}:3000${NC}"
    fi
    if [ -n "$TS_IP" ]; then
        echo -e "  ${BOLD}Tailscale IP:${NC}        ${CYAN}http://${TS_IP}:3000${NC}"
    fi
    echo -e "  ${BOLD}Backend API Docs:${NC}     ${CYAN}http://localhost:8000/docs${NC}"
    if [ -n "${AK5_AUTH_PASSWORD:-}${AK5_WEB_PASSWORD:-}" ]; then
        local AUTH_USER="${AK5_AUTH_USERNAME:-${AK5_WEB_USER:-admin}}"
        echo -e "  ${BOLD}Web Authentication:${NC}   ${GREEN}ENABLED${NC} (User: ${AUTH_USER})"
    else
        echo -e "  ${BOLD}Web Authentication:${NC}   ${YELLOW}DISABLED${NC}"
    fi
    echo -e "${BOLD}${GREEN}================================================================${NC}\n"
}

is_backend_running() {
    curl -s -m 1 http://127.0.0.1:8000/health >/dev/null 2>&1
}

is_frontend_running() {
    curl -s -m 1 http://127.0.0.1:3000 >/dev/null 2>&1
}

start_backend_if_needed() {
    if is_backend_running; then
        echo -e "${GREEN}✓${NC} Backend already on :8000"
    else
        echo -e "${CYAN}▶ Starting backend 0.0.0.0:8000...${NC}"
        cd "$PROJECT_ROOT"
        nohup uv run ak5 serve --host 0.0.0.0 --port 8000 </dev/null > "$BACKEND_LOG" 2>&1 &
        echo $! > "$BACKEND_PID_FILE"
        for _ in {1..20}; do
            if is_backend_running; then
                echo -e "${GREEN}✓${NC} Backend ready"
                return 0
            fi
            sleep 0.5
        done
        echo -e "${YELLOW}Backend slow to start; see $BACKEND_LOG${NC}"
    fi
}

start_daemon() {
    start_backend_if_needed
    if is_frontend_running; then
        echo -e "${GREEN}✓${NC} Frontend already on :3000"
    else
        cd "$FRONTEND_DIR"
        if [ ! -d ".next" ]; then
            npm run build
        fi
        nohup npm run start -- -H 0.0.0.0 -p 3000 > "$FRONTEND_LOG" 2>&1 &
        echo $! > "$FRONTEND_PID_FILE"
    fi
    print_urls
}

stop_services() {
    if [ -f "$FRONTEND_PID_FILE" ]; then
        kill "$(cat "$FRONTEND_PID_FILE")" 2>/dev/null || true
        rm -f "$FRONTEND_PID_FILE"
    fi
    if [ -f "$BACKEND_PID_FILE" ]; then
        kill "$(cat "$BACKEND_PID_FILE")" 2>/dev/null || true
        rm -f "$BACKEND_PID_FILE"
    fi
    local P8000
    P8000="$(ss -tulpn 2>/dev/null | grep :8000 | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -n 1 || true)"
    if [ -n "$P8000" ]; then
        kill "$P8000" 2>/dev/null || true
    fi
    echo -e "${GREEN}✓${NC} All AK5 services stopped."
}

case "${1:-}" in
    start|--daemon|-d) shift || true; start_daemon ;;
    stop) stop_services ;;
    restart) stop_services; sleep 1; start_daemon ;;
    status)
        is_backend_running && echo "backend: running" || echo "backend: stopped"
        is_frontend_running && echo "frontend: running" || echo "frontend: stopped"
        ;;
    *)
        start_backend_if_needed
        print_urls
        cd "$FRONTEND_DIR"
        exec npm run dev -- -H 0.0.0.0 -p 3000
        ;;
esac
