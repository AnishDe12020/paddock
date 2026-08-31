#!/bin/bash
# Box image entrypoint: verify the workspace mount, then serve SSH.
set -euo pipefail

install -d -m 0755 /run/sshd
/usr/local/bin/paddock-verify-workspace

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
