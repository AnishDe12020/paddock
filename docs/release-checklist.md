# Alpha release verification

Record the Git revision, host OS, Docker version, exact commands, and results
for a candidate. A skipped deployment test is an unverified gate.

## Checks that do not need an installed Paddock

Run the commands in CONTRIBUTING.md on Linux. The complete unit suite must pass
without `prlimit` skips. Build both images and parse the Squid configuration:

```bash
docker run --rm --network none paddock-proxy:local squid -k parse -f /etc/squid/squid.conf
bash scripts/verify-container.sh paddock:local
```

The container smoke test uses real MCP HTTP, six tools, annotations, file
write/read/list/delete, overwrite refusal, traversal refusal, output limits,
and both ordinary and closed-output command timeouts. It also checks the
unprivileged UID, effective cgroup CPU/memory/task limits, refusal to write to
the root filesystem, and cleanup of its temporary workspace.

Its offline container has a small tmpfs workspace and starts `paddock-server`
directly. This does **not** validate the installer's ext4 mount check, 100 GB
storage quota, systemd aggregate slice, firewall, SSH, proxy egress, or ingress
connectors. CI runs this smoke test rather than silently skipping integration.
The temporary test image installs pytest; it is never a deployment image.

If the Docker build network has no DNS, investigate the host configuration.
On a trusted verification host, the operator can explicitly select
`PADDOCK_TEST_BUILD_NETWORK=host` for the temporary pytest image build. The
running smoke container still uses `--network none`.

## Fresh-install gate: disposable Ubuntu 24.04 VM

Use a disposable VM with its own rootful Docker daemon, systemd, nftables,
unused 10.88/10.89/10.90 networks, and a throwaway SSH key. Do not run the
installer over an existing sandbox or modify a shared host firewall to test it.

1. Install using `scripts/install.sh --ssh-key <throwaway-public-key>`.
2. Check healthy box/API/egress services, the active aggregate slice and SSH
   socket, and the loaded firewall. Inspect effective container **and parent**
   cgroup limits. Confirm the loop-mounted ext4 workspace size and mount flags.
3. Check that no MCP port is published, API UID is 11000, capabilities are
   dropped, root filesystems are read-only, and credentials are absent from API
   mounts. Test key-only SSH and verify forwarding is refused.
4. Set `MCP_URL` explicitly and run `pytest tests/integration`. Leave
   `MCP_MIN_CAPACITY_BYTES` unset so the 100 GB capacity assertion applies.
   Confirm the unique `.paddock-self-test-*` directory was removed.
5. From compute, verify public HTTP/HTTPS succeeds through Squid while direct
   egress, private/loopback/link-local destinations, metadata addresses, IPv6,
   and non-web ports are denied. Probe only VM-owned endpoints or deliberate
   test fixtures. Record both allowed and denied results.
6. Re-run the installer, then reboot the VM. Verify workspace persistence,
   service recovery, and that a missing/wrong workspace mount fails closed.
7. Exercise only the ingress profiles intended for release; record the others
   as unverified. Never place runtime credentials in the report.

Do not describe the candidate as installation-verified until these gates pass.

## Candidate release notes

- Command deadlines remain enforced when a command closes both output streams.
- Live tests require an explicit endpoint and use a unique temporary directory.
- CI exercises the built application through real MCP HTTP in an offline,
  resource-limited container.
- Existing threat-model limitations remain, including detached subprocesses
  surviving a process-group timeout and the shared UID workspace trust domain.

Publish or tag only after reviewing the recorded evidence and outstanding gates.
