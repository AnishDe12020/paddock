# Threat Model

Paddock assumes the model can make mistakes, follow malicious instructions in
workspace content, and run hostile-looking shell commands. Its purpose is to
make the resulting authority legible and bounded.

It is not a hardened malware-analysis platform, a multi-tenant isolation
system, or a defense against a Linux kernel/container-runtime escape.

## Assets to protect

- Host files, processes, devices, credentials, and Docker socket
- Services on the host, LAN, private cloud networks, metadata endpoints, and
  sibling container networks
- Tunnel and SSH credentials
- Host availability beyond the advertised CPU, memory, task, storage, timeout,
  and output budgets
- Workspace integrity from accidental overwrites when a tool did not explicitly
  request one

The workspace's **confidentiality and integrity are not protected from the
model**. The model is intentionally authorized to read, modify, delete, and
send workspace data to public HTTP/HTTPS destinations.

## Trust boundaries

| Actor or component | Treatment |
| --- | --- |
| ChatGPT prompts and retrieved content | Untrusted input with full advertised MCP tool authority |
| MCP and SSH processes as UID 11000 | One trust domain; both intentionally control the workspace |
| Optional OpenAI, Tailscale, and Cloudflare images | Trusted pinned transport components with profile-specific network paths |
| Squid | Trusted egress policy component |
| Docker, systemd, nftables, host kernel | Trusted computing base |
| Operator SSH private key and connector credentials | Secrets that must remain outside the workspace and repository |

## Enforced invariants

### The MCP process is not root

- Compose sets `user: 11000:11000`, drops all capabilities, enables
  `no-new-privileges`, and uses a read-only root filesystem.
- `paddock-server` refuses to start with effective UID 0.
- It mounts only the workspace; SSH and tunnel credentials are absent.

### Workspace paths stay in the workspace

- Tool paths must be relative and cannot contain `..`.
- Existing paths and parent directories are resolved before use and rejected
  when they escape the configured root.
- Recursive listing does not follow symlinked directories.
- Writes use an adjacent temporary file and atomic replacement.
- Tests cover absolute paths, traversal, and symlinked-parent escapes.

The SSH and MCP processes share a UID and can race each other's filesystem
operations. Escaping into the API container's writable `/tmp` would not grant
host access, but the design does not claim race-free multi-user filesystem
isolation.

### Commands have bounded process authority

- Commands run as UID 11000 with no capabilities and no privilege escalation.
- They receive a small environment, not the MCP server's complete environment.
- Each call has a maximum 300-second timeout and 256 KiB captured per output
  stream; four commands may run concurrently.
- Container and aggregate cgroups cap CPU, memory, swap, and task count.
- Root filesystems are read-only; writable tmpfs mounts and the workspace are
  size bounded.

A child that deliberately creates a new session may survive the per-command
process-group kill until the container or cgroup is restarted. The task cap
still bounds this as a denial-of-service vector.

### The workload cannot choose arbitrary network destinations directly

- Compute containers attach only to internal Docker bridges.
- nftables permits compute-origin forwarding only to the local Squid address.
- Squid allows public IPv4 HTTP/HTTPS on ports 80/443 and denies private,
  loopback, link-local, carrier-grade NAT, reserved, multicast, metadata-like,
  and all IPv6 destinations.
- The proxy's internet-side address is blocked from host input and private
  forwarding as a second boundary.
- The optional Cloudflare connector is the only exception to proxy-only
  internet egress: it may use TCP 7844 only to Cloudflare's documented tunnel
  edge IPv4 addresses.
- SSH forwarding, tunneling, and agent forwarding are disabled.

This is destination filtering, not content filtering. Public websites can
receive workspace data and return malicious packages or instructions.

### Credentials do not enter the agent workspace

- The operator SSH public key and host keys mount only into the SSH container.
- The OpenAI runtime key mounts only into `tunnel-client` as a root-owned,
  read-only file.
- Optional Tailscale and Cloudflare credentials mount only into their connector
  containers as root-owned, read-only files.
- The MCP process receives neither mount.
- Child commands inherit no arbitrary server environment variables.

### MCP ingress is explicit and closed by default

- Compose publishes no MCP port.
- The MCP bridge sits on an internal network and validates allowed Host values.
- OpenAI transport is outbound-only through the official client and Squid.
- SSH/stdio reuses key-only SSH and opens no additional listener.
- Tailscale Serve is tailnet-only; Tailscale grants or ACLs authorize clients.
- Cloudflare creates a public edge route only when explicitly activated. A
  restrictive Cloudflare Access policy is mandatory and remains the operator's
  responsibility.
- Private MCP hops have no OAuth by design. Each connector's external identity
  policy is the authorization boundary.

## Out of scope and residual risk

- Kernel, Docker, runc, systemd, nftables, or Squid vulnerabilities
- Malicious or compromised pinned base images and packages
- Misconfigured Tailscale grants, ACLs, Cloudflare DNS, or Access policies
- Physical access or a hostile host administrator
- Protecting data deliberately placed in `/workspace` from ChatGPT
- Preventing destructive but authorized workspace operations
- Availability after filling the workspace or exhausting the allowed task/time
  budgets
- Strong isolation between the MCP caller and an operator logged in as `ai`
- General UDP, arbitrary TCP, inbound servers, GPU/device access, or private
  package registries

Do not mount home directories, cloud credentials, source-control credentials,
Docker sockets, or production secrets into Paddock. Use a VM boundary in
addition to Paddock when the host kernel must not be in the trusted computing
base.

## Review checklist for changes

Any change that adds a mount, capability, network, published port, inherited
environment variable, allowed proxy destination, system call exception, or
credential path changes this threat model. Such changes should include:

1. the exact new authority;
2. why a narrower alternative is insufficient;
3. a failing test or verification command before the change; and
4. corresponding updates to this document and the architecture diagram.
