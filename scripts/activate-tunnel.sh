#!/usr/bin/env bash
# Activate the optional OpenAI Secure MCP Tunnel with a hidden API key prompt.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR=/var/lib/paddock/openai-tunnel
COMPOSE=(sudo docker compose -f "$PROJECT_DIR/compose.yaml" --profile openai-tunnel)
TUNNEL_ID=${1:-}

if [[ ! "$TUNNEL_ID" =~ ^tunnel_[0-9a-f]{32}$ ]]; then
    printf 'Usage: %s tunnel_<32 lowercase hex characters>\n' "$0" >&2
    exit 2
fi
if [[ ! -t 0 ]]; then
    printf 'Run this script in a terminal so the runtime API key can be entered securely.\n' >&2
    exit 2
fi

read -r -s -p 'OpenAI tunnel runtime API key: ' API_KEY
printf '\n'
if [[ -z "$API_KEY" ]]; then
    printf 'Runtime API key may not be empty.\n' >&2
    exit 2
fi

umask 077
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"; unset API_KEY' EXIT

sed "s/tunnel_REPLACE_WITH_32_LOWERCASE_HEX/$TUNNEL_ID/" \
    "$PROJECT_DIR/deploy/tunnel-client.yaml.example" > "$TEMP_DIR/config.yaml"
printf '%s' "$API_KEY" > "$TEMP_DIR/api-key"

sudo install -d -o root -g root -m 0700 "$STATE_DIR"
sudo install -o root -g root -m 0600 "$TEMP_DIR/config.yaml" "$STATE_DIR/config.yaml"
sudo install -o root -g root -m 0400 "$TEMP_DIR/api-key" "$STATE_DIR/api-key"

"${COMPOSE[@]}" up -d openai_tunnel

# Health alone is not enough: require the control-plane handshake marker in
# the logs, and treat rejection status codes as immediate failure.
redact() {
    local text=$1
    if [[ -n "${API_KEY:-}" ]]; then
        text=${text//"$API_KEY"/'<redacted>'}
    fi
    printf '%s\n' "$text"
}

logs_for_service() {
    "${COMPOSE[@]}" logs --no-color openai_tunnel 2>&1
}

for ((attempt = 1; attempt <= 45; attempt++)); do
    status=$(sudo docker inspect --format '{{.State.Health.Status}}' paddock-openai-tunnel 2>/dev/null || true)
    logs=$(logs_for_service)
    if [[ "$logs" =~ invalid_api_key \
        || "$logs" =~ '"status_code":401' \
        || "$logs" =~ '"status_code":403' \
        || "$logs" =~ '"status_code":404' ]]; then
        redact "$logs"
        printf 'OpenAI rejected the tunnel id, runtime key, or its permissions.\n' >&2
        exit 1
    fi
    if [[ "$status" == healthy && "$logs" == *'tunnel metadata fetched'* ]]; then
        sleep 2
        printf 'OpenAI Secure MCP Tunnel is healthy and serving the Paddock API.\n'
        exit 0
    fi
    if [[ "$status" == unhealthy ]]; then
        break
    fi
    sleep 2
done

redact "$(logs_for_service | tail -n 50)" >&2
printf 'Tunnel did not become healthy; inspect the redacted logs above.\n' >&2
exit 1
