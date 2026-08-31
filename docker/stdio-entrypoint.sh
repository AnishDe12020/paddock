#!/bin/bash
# SSH command entrypoint: verify persistent storage before starting stdio MCP.
set -euo pipefail

/usr/local/bin/paddock-verify-workspace

exec /usr/local/bin/paddock-server-stdio
