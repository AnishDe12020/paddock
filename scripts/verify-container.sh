#!/usr/bin/env bash
# Exercise a built image over real MCP HTTP in a disposable, offline container.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BASE_IMAGE=${1:-paddock:local}
BUILD_NETWORK=${PADDOCK_TEST_BUILD_NETWORK:-default}
TEST_DIR=$(mktemp -d)
TEST_NAME="paddock-verify-$(basename "$TEST_DIR" | tr '[:upper:]' '[:lower:]')"
TEST_IMAGE="$TEST_NAME:local"
cleanup() {
    docker rm -f "$TEST_NAME" >/dev/null 2>&1 || true
    docker image rm "$TEST_IMAGE" >/dev/null 2>&1 || true
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cat > "$TEST_DIR/Dockerfile" <<'DOCKERFILE'
ARG BASE_IMAGE=paddock:local
FROM ${BASE_IMAGE}
RUN python3 -m pip install --break-system-packages --no-cache-dir 'pytest>=8'
DOCKERFILE
docker build --network "$BUILD_NETWORK" --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    -t "$TEST_IMAGE" "$TEST_DIR"

# This tests the application image, not the installer's ext4 mount or firewall.
# No host workspace, credentials, network, published ports, or Docker socket.
docker run -d --name "$TEST_NAME" --network none --read-only \
    --user 11000:11000 --cap-drop ALL --security-opt no-new-privileges:true \
    --cpus 1 --memory 512m --memory-swap 512m --pids-limit 64 \
    --tmpfs /workspace:rw,nosuid,nodev,size=32m,uid=11000,gid=11000,mode=700 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777 \
    --mount "type=bind,source=$PROJECT_DIR/tests/integration,target=/tests,readonly" \
    --env MCP_URL=http://127.0.0.1:8000/mcp --env MCP_MIN_CAPACITY_BYTES=1 \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --entrypoint /usr/local/bin/paddock-server "$TEST_IMAGE" >/dev/null

ready=false
for ((attempt = 0; attempt < 30; attempt++)); do
    if docker exec "$TEST_NAME" curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    docker logs "$TEST_NAME"
    printf 'Isolated MCP server did not become ready.\n' >&2
    exit 1
fi
docker exec "$TEST_NAME" python3 -m pytest -p no:cacheprovider /tests -q
docker exec "$TEST_NAME" python3 -c '
import os
from pathlib import Path
assert os.geteuid() == 11000
assert os.statvfs("/").f_flag & os.ST_RDONLY
assert Path("/sys/fs/cgroup/memory.max").read_text().strip() == "536870912"
assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "64"
quota, period = map(int, Path("/sys/fs/cgroup/cpu.max").read_text().split())
assert quota == period
assert not list(Path("/workspace").iterdir()), "smoke test left workspace files"
try:
    Path("/usr/paddock-write-probe").touch()
except OSError:
    pass
else:
    raise AssertionError("container root filesystem accepted a write")
print("Verified uid, cgroup limits, root filesystem refusal, and workspace cleanup")
'
