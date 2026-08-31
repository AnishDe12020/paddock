#!/bin/bash
# Fail closed unless /workspace is the sparse 100G ext4 loop workspace.
set -euo pipefail

read -r source_path filesystem_type filesystem_size < <(
    findmnt -b -n -o SOURCE,FSTYPE,SIZE --target /workspace
)
if [[ "$source_path" != /dev/loop* \
    || "$filesystem_type" != ext4 \
    || "$filesystem_size" -lt 100000000000 \
    || "$filesystem_size" -gt 108000000000 ]]; then
    printf 'Refusing to start: /workspace is not the quota-backed Paddock workspace filesystem\n' >&2
    exit 1
fi
