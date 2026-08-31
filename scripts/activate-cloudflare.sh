#!/usr/bin/env bash
# Activate Cloudflare Tunnel only after an Access policy protects the hostname.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR=/var/lib/paddock/cloudflare
COMPOSE=(sudo docker compose -f "$PROJECT_DIR/compose.yaml" --profile cloudflare)

if [[ "${1:-}" != --access-policy-ready ]]; then
    printf 'Usage: %s --access-policy-ready\n' "$0" >&2
    printf 'Create and test a Cloudflare Access policy for the tunnel hostname first.\n' >&2
    exit 2
fi
if [[ ! -t 0 ]]; then
    printf 'Run this script in a terminal so the tunnel token can be entered securely.\n' >&2
    exit 2
fi

read -r -s -p 'Cloudflare tunnel token: ' TUNNEL_TOKEN
printf '\n'
if [[ -z "$TUNNEL_TOKEN" ]]; then
    printf 'Tunnel token may not be empty.\n' >&2
    exit 2
fi

umask 077
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"; unset TUNNEL_TOKEN' EXIT
printf '%s' "$TUNNEL_TOKEN" > "$TEMP_DIR/tunnel-token"

sudo install -d -o root -g root -m 0700 "$STATE_DIR"
sudo install -o root -g root -m 0400 "$TEMP_DIR/tunnel-token" "$STATE_DIR/tunnel-token"

"${COMPOSE[@]}" up -d cloudflared
for ((attempt = 1; attempt <= 45; attempt++)); do
    status=$(sudo docker inspect --format '{{.State.Health.Status}}' paddock-cloudflared 2>/dev/null || true)
    if [[ "$status" == healthy ]]; then
        printf 'Cloudflare Tunnel is healthy. Verify the Access policy before using the MCP URL.\n'
        exit 0
    fi
    if [[ "$status" == unhealthy ]]; then
        break
    fi
    sleep 2
done

logs=$("${COMPOSE[@]}" logs --no-color --tail=50 cloudflared 2>&1)
printf '%s\n' "${logs//"$TUNNEL_TOKEN"/'<redacted>'}" >&2
printf 'Cloudflare Tunnel did not become healthy.\n' >&2
exit 1
