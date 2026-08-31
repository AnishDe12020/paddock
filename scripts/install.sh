#!/usr/bin/env bash
# Idempotent installer for Paddock on an Ubuntu host with systemd, nftables,
# and rootful Docker. Run as a user with sudo access.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR=/var/lib/paddock
WORKSPACE_IMAGE=$STATE_DIR/workspace.ext4
WORKSPACE_MOUNT=/srv/paddock-workspace
SSH_PORT=30222
SSH_KEY=$HOME/.ssh/id_ed25519.pub
SSH_HOST=$(hostname -f)

usage() {
    printf 'Usage: %s [--ssh-key FILE] [--ssh-host HOST]\n' "$0"
    printf '  --ssh-key FILE  Public key for the sandbox ai user (default: %s)\n' "$SSH_KEY"
    printf '  --ssh-host HOST Advertised SSH host for status output (default: hostname -f)\n'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ssh-key)
            [[ $# -ge 2 ]] || { printf 'missing value for %s\n' "$1" >&2; exit 2; }
            SSH_KEY=$2
            shift 2
            ;;
        --ssh-host)
            [[ $# -ge 2 ]] || { printf 'missing value for %s\n' "$1" >&2; exit 2; }
            SSH_HOST=$2
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

# Preflight: every required command must exist before anything changes.
missing=()
for required in sudo docker nft ssh-keygen findmnt truncate mkfs.ext4 mountpoint systemctl hostname getent; do
    command -v "$required" >/dev/null 2>&1 || missing+=("$required")
done
if ((${#missing[@]})); then
    printf 'Missing required commands: %s\n' "${missing[*]}" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    printf 'docker compose is required (rootful Docker with the compose plugin).\n' >&2
    exit 1
fi
if ! sudo docker info >/dev/null 2>&1; then
    printf 'The Docker daemon is not reachable.\n' >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then
    printf 'Cannot identify the host operating system; Ubuntu 24.04 is required.\n' >&2
    exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
    printf 'Paddock currently supports Ubuntu 24.04; found %s %s.\n' \
        "${ID:-unknown}" "${VERSION_ID:-unknown}" >&2
    exit 1
fi
if [[ ! -x /usr/lib/systemd/systemd-socket-proxyd ]]; then
    printf 'systemd-socket-proxyd is missing from the expected Ubuntu path.\n' >&2
    exit 1
fi
if [[ "$(sudo docker info --format '{{json .SecurityOptions}}')" == *rootless* ]]; then
    printf 'Paddock requires rootful Docker for named bridges and the host firewall boundary.\n' >&2
    exit 1
fi
if existing_user=$(getent passwd 11000); then
    printf 'Host uid 11000 is already assigned (%s); choose a host without this collision.\n' \
        "$existing_user" >&2
    exit 1
fi

# The SSH key must be a valid, readable public key.
if [[ ! -r "$SSH_KEY" ]]; then
    printf 'SSH public key %s is not readable.\n' "$SSH_KEY" >&2
    exit 1
fi
if ! ssh-keygen -l -f "$SSH_KEY" >/dev/null 2>&1; then
    printf '%s is not a valid OpenSSH public key.\n' "$SSH_KEY" >&2
    exit 1
fi

umask 077

# Workspace: sparse 100G ext4 loop image, mounted nodev,nosuid, owned by ai.
sudo install -d -m 0700 "$STATE_DIR" "$WORKSPACE_MOUNT"
if ! sudo test -f "$WORKSPACE_IMAGE"; then
    sudo truncate -s 100G "$WORKSPACE_IMAGE"
    sudo mkfs.ext4 -q -m 0 -L paddock-workspace "$WORKSPACE_IMAGE"
fi
sudo chmod 0600 "$WORKSPACE_IMAGE"

FSTAB_LINE="$WORKSPACE_IMAGE $WORKSPACE_MOUNT ext4 loop,nodev,nosuid,discard,nofail,x-systemd.device-timeout=10 0 2"
if ! sudo grep -Fqx "$FSTAB_LINE" /etc/fstab; then
    printf '%s\n' "$FSTAB_LINE" | sudo tee -a /etc/fstab >/dev/null
fi
if ! mountpoint -q "$WORKSPACE_MOUNT"; then
    sudo mount "$WORKSPACE_MOUNT"
fi
sudo chown 11000:11000 "$WORKSPACE_MOUNT"
sudo chmod 0700 "$WORKSPACE_MOUNT"

# Host keys for sshd and the root-only authorized_keys file.
sudo install -d -m 0700 "$STATE_DIR/ssh"
if ! sudo test -f "$STATE_DIR/ssh/ssh_host_ed25519_key"; then
    sudo ssh-keygen -q -t ed25519 -N '' -f "$STATE_DIR/ssh/ssh_host_ed25519_key"
fi
if ! sudo test -f "$STATE_DIR/ssh/ssh_host_rsa_key"; then
    sudo ssh-keygen -q -t rsa -b 4096 -N '' -f "$STATE_DIR/ssh/ssh_host_rsa_key"
fi
sudo chmod 0600 \
    "$STATE_DIR/ssh/ssh_host_ed25519_key" \
    "$STATE_DIR/ssh/ssh_host_rsa_key"
sudo chmod 0644 \
    "$STATE_DIR/ssh/ssh_host_ed25519_key.pub" \
    "$STATE_DIR/ssh/ssh_host_rsa_key.pub"
sudo install -o root -g root -m 0600 "$SSH_KEY" "$STATE_DIR/authorized_keys"

# Images.
sudo docker build -f "$PROJECT_DIR/docker/box.Dockerfile" -t paddock:local "$PROJECT_DIR"
sudo docker build -f "$PROJECT_DIR/docker/proxy.Dockerfile" -t paddock-proxy:local "$PROJECT_DIR"

# Slice, firewall, and SSH socket units.
sudo install -m 0644 "$PROJECT_DIR/deploy/systemd/paddock.slice" /etc/systemd/system/paddock.slice
sudo install -m 0600 "$PROJECT_DIR/deploy/systemd/paddock-firewall.nft" /etc/paddock-firewall.nft
sudo install -m 0644 "$PROJECT_DIR/deploy/systemd/paddock-firewall.service" /etc/systemd/system/paddock-firewall.service
sudo install -m 0644 "$PROJECT_DIR/deploy/systemd/paddock-ssh.socket" /etc/systemd/system/paddock-ssh.socket
sudo install -m 0644 "$PROJECT_DIR/deploy/systemd/paddock-ssh.service" /etc/systemd/system/paddock-ssh.service
sudo systemctl daemon-reload
sudo systemctl start paddock.slice
sudo systemctl enable paddock-firewall.service
sudo systemctl restart paddock-firewall.service

# Stack. Reuse the tunnel profile when it was configured previously.
COMPOSE=(sudo docker compose -f "$PROJECT_DIR/compose.yaml")
if sudo test -f "$STATE_DIR/openai-tunnel/config.yaml" \
    && sudo test -f "$STATE_DIR/openai-tunnel/api-key"; then
    "${COMPOSE[@]}" --profile openai-tunnel up -d
else
    "${COMPOSE[@]}" up -d
fi

sudo systemctl enable paddock-ssh.socket
sudo systemctl restart paddock-ssh.socket

# Only touch UFW when it exists and is active.
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q 'Status: active'; then
    sudo ufw limit "$SSH_PORT/tcp" comment 'rate-limited isolated Paddock SSH'
else
    printf 'UFW is not installed or inactive; skipped the port %s/tcp rule.\n' "$SSH_PORT"
fi

"${COMPOSE[@]}" ps
printf '\nPaddock deployed.\n'
printf 'SSH:       ssh -p %s ai@%s\n' "$SSH_PORT" "$SSH_HOST"
printf 'Workspace: %s (100G ext4 loop, mode 0700, uid 11000)\n' "$WORKSPACE_MOUNT"
printf 'State:     %s\n' "$STATE_DIR"
printf 'MCP:       internal only (paddock-api on the sandbox network, port 8000)\n'
