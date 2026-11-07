#!/usr/bin/env bash
set -euo pipefail

# AK5 agent harness setup (v2): register an agent profile via device grant.
# A human must approve the printed code in the browser (workspace admin).
# Usage: ./harness_setup.sh <PROFILE> <ROLE> [CAPABILITIES] [SERVER_URL]
#   ROLE: triage | worker | gatekeeper

PROFILE="${1:?usage: harness_setup.sh <PROFILE> <ROLE> [CAPS] [SERVER_URL]}"
ROLE="${2:?usage: harness_setup.sh <PROFILE> <ROLE> [CAPS] [SERVER_URL]}"
CAPS="${3:-}"
SERVER="${4:-${AK5_API_URL:-http://127.0.0.1:8000/api/v1}}"
AK5="${AK5_BIN:-ak5}"

echo "AK5 harness setup: profile=${PROFILE} role=${ROLE} caps=${CAPS:-<none>} server=${SERVER}"

if AK5_PROFILE="${PROFILE}" "${AK5}" --json whoami >/dev/null 2>&1; then
    echo "Profile ${PROFILE} is already logged in."
else
    args=(login --url "${SERVER}" --agent "${PROFILE}" --role "${ROLE}")
    if [ -n "${CAPS}" ]; then
        args+=(--caps "${CAPS}")
    fi
    echo "Starting device grant — ask a human to approve the code below."
    "${AK5}" "${args[@]}"
fi

AK5_PROFILE="${PROFILE}" "${AK5}" whoami
echo
echo "Ready. Give your harness:  export AK5_PROFILE=${PROFILE}"
echo "Loop:  ak5 next --wait 25 --json → ak5 ticket claim <id> → work → ak5 ticket submit|verdict|route"
