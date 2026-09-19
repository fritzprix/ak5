#!/usr/bin/env bash
set -euo pipefail

# AK5 Agent Harness Setup Helper Script
# Usage: ./harness_setup.sh [AGENT_ID] [ROLE] [CAPABILITIES]

AGENT_ID="${1:-agent_orchestrator}"
ROLE="${2:-Lead Orchestrator}"
CAPS="${3:-orchestration,delegation,code-review}"
API_URL="${AK5_API_URL:-http://127.0.0.1:8000/api/v1}"

echo "=================================================="
echo "🤖 AK5 Agent Harness Initializer"
echo "=================================================="
echo "Agent ID:     @${AGENT_ID}"
echo "Role:         ${ROLE}"
echo "Capabilities: ${CAPS}"
echo "Gateway URL:  ${API_URL}"
echo "--------------------------------------------------"

# 1. Health check
if ! curl -sf "http://127.0.0.1:8000/health" > /dev/null 2>&1; then
    echo "⚠️ AK5 Gateway is not responding at http://127.0.0.1:8000."
    echo "Starting local gateway in background..."
    uv run uvicorn ak5.main:app --host 127.0.0.1 --port 8000 &
    sleep 2
fi

# 2. Login / Register
echo "🔑 Authenticating as @${AGENT_ID}..."
uv run ak5 login --id "${AGENT_ID}" --role "${ROLE}" --caps "${CAPS}" --type agent --url "${API_URL}"

# 3. Check Peer Agents
echo "👥 Discovering peer agents..."
uv run ak5 agents

echo "--------------------------------------------------"
echo "✅ AK5 Harness Ready for Agent @${AGENT_ID}."
echo "Use 'ak5 board' to inspect tickets or 'ak5 delegate' to assign subtasks."
echo "=================================================="
