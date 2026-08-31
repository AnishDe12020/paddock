#!/usr/bin/env bash
# Activate private MCP ingress through Tailscale Serve.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR=/var/lib/paddock/tailscale
ALLOWED_HOSTS=/var/lib/paddock/allowed-hosts
COMPOSE=(sudo docker compose -f "$PROJECT_DIR/compose.yaml" --profile tailscale)
TAILSCALE_HOST=${1:-}
CLIENT_ID=${2:-}

if [[ ! "$TAILSCALE_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*\.ts\.net$ \
    || -z "$CLIENT_ID" || "$CLIENT_ID" =~ [[:space:]] ]]; then
    printf 'Usage: %s paddock.your-tailnet.ts.net OAUTH_CLIENT_ID\n' "$0" >&2
    exit 2
fi
if [[ ! -t 0 ]]; then
    printf 'Run this script in a terminal so the OAuth client secret can be entered securely.\n' >&2
    exit 2
fi

read -r -s -p 'Tailscale OAuth client secret: ' CLIENT_SECRET
printf '\n'
if [[ "$CLIENT_SECRET" != tskey-client-* ]]; then
    printf 'Expected a Tailscale OAuth secret beginning with tskey-client-.\n' >&2
    exit 2
fi

umask 077
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"; unset CLIENT_SECRET' EXIT
printf '%s' "$CLIENT_ID" > "$TEMP_DIR/client-id"
printf '%s' "$CLIENT_SECRET" > "$TEMP_DIR/client-secret"
if sudo test -f "$ALLOWED_HOSTS"; then
    # sudo reads the root-owned source; the current user owns the temporary output.
    # shellcheck disable=SC2024
    sudo cat "$ALLOWED_HOSTS" > "$TEMP_DIR/allowed-hosts"
else
    : > "$TEMP_DIR/allowed-hosts"
fi
if ! grep -Fqx "$TAILSCALE_HOST" "$TEMP_DIR/allowed-hosts"; then
    printf '%s\n' "$TAILSCALE_HOST" >> "$TEMP_DIR/allowed-hosts"
fi

sudo install -d -o root -g root -m 0700 "$STATE_DIR" "$STATE_DIR/state"
sudo install -o root -g root -m 0400 "$TEMP_DIR/client-id" "$STATE_DIR/client-id"
sudo install -o root -g root -m 0400 "$TEMP_DIR/client-secret" "$STATE_DIR/client-secret"
sudo install -o root -g root -m 0644 "$TEMP_DIR/allowed-hosts" "$ALLOWED_HOSTS"

"${COMPOSE[@]}" up -d --force-recreate api tailscale
for ((attempt = 1; attempt <= 45; attempt++)); do
    status=$(sudo docker inspect --format '{{.State.Health.Status}}' paddock-tailscale 2>/dev/null || true)
    if [[ "$status" == healthy ]]; then
        printf 'Tailscale ingress is healthy at https://%s/mcp\n' "$TAILSCALE_HOST"
        exit 0
    fi
    if [[ "$status" == unhealthy ]]; then
        break
    fi
    sleep 2
done

logs=$("${COMPOSE[@]}" logs --no-color --tail=50 tailscale 2>&1)
printf '%s\n' "${logs//"$CLIENT_SECRET"/'<redacted>'}" >&2
printf 'Tailscale ingress did not become healthy.\n' >&2
exit 1
