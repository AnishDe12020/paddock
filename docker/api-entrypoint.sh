#!/bin/bash
# API image entrypoint: verify the workspace mount, then serve MCP as uid 11000.
set -euo pipefail

/usr/local/bin/paddock-verify-workspace

exec /usr/local/bin/paddock-server
