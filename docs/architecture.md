# Architecture

Paddock separates transport, tools, interactive access, and internet egress so
no single container needs every privilege.

## Components

```text
ChatGPT <--- OpenAI-hosted tunnel <--- tunnel-client (.5) ---+
Tailscale client <--- tailnet HTTPS <--- tailscale sidecar ---+--> MCP API (.4)
Cloudflare client <--- Access + edge <--- cloudflared (.7) ---+       |
                                                                    v
                                                               /workspace
                                                                    ^
                                                                    |
MCP/SSH client :30222 -> socket proxy -> sshd (10.88.0.2) -----------+

MCP API / sshd ---> Squid (10.89.0.3 / 10.88.0.3)
                           |
                           v
                public IPv4 HTTP/HTTPS only (10.90.0.3)
```

| Component | Trust and job |
| --- | --- |
| `paddock-api` | Unprivileged MCP server; owns no host credential and shares only the workspace |
| `paddock-box` | Root starts sshd, which drops to UID 11000 for key-authenticated sessions |
| `paddock-egress` | Squid policy enforcement; the only component with an internet bridge |
| `paddock-openai-tunnel` | Official pinned client; polls OpenAI and forwards requests to the private MCP API |
| `paddock-tailscale` | Optional pinned userspace node; serves private tailnet HTTPS from the API network namespace |
| `paddock-cloudflared` | Optional pinned connector; forwards an Access-protected edge hostname to the API |
| `paddock-firewall.service` | Host input/forward boundary loaded before Docker during boot |
| `paddock-ssh.socket` | Public TCP listener proxied to the internal sshd address |
| `paddock.slice` | Aggregate compute cgroup for the SSH and MCP workloads |

## Fixed topology

The topology is intentionally literal so the Compose, Squid, nftables, tunnel,
and systemd policies can be audited side by side.

| Network | Bridge | Members |
| --- | --- | --- |
| `10.88.0.0/24` | `br-paddock-ssh` | sshd `.2`, Squid `.3` |
| `10.89.0.0/24` | `br-paddock-api` | Squid `.3`, MCP `.4`, OpenAI `.5`, optional Cloudflare `.7`; Tailscale shares `.4` |
| `10.90.0.0/24` | `br-paddock-net` | Squid `.3`, optional Cloudflare `.4`; public internet uplink |

The first two networks are Docker `internal` networks. OpenAI and Tailscale
control-plane requests use Squid. The Cloudflare profile needs TCP 7844 and is
allowed to reach only Cloudflare's documented tunnel-edge IPv4 addresses.

## Request paths

### ChatGPT tool call

1. ChatGPT sends an MCP request to the OpenAI-hosted endpoint.
2. `tunnel-client` receives it over an outbound long poll.
3. The client forwards JSON-RPC to `http://10.89.0.4:8000/mcp`.
4. The unprivileged MCP process accesses `/workspace` or starts a bounded Bash
   process as the same UID.
5. The response returns along the same path. No inbound public connection
   reaches the host.

### Web request from a command

1. The command inherits only a small environment containing the proxy settings.
2. The API network permits it to contact Squid at `10.89.0.3:3128` and drops
   other forwarded traffic.
3. Squid permits only HTTP/HTTPS ports and rejects private, local, metadata-like,
   multicast, reserved, and IPv6 destinations.
4. The host firewall independently rejects private forwarding and attempts by
   the proxy to reach the host itself.

### SSH session

1. systemd owns public TCP port `30222`.
2. `systemd-socket-proxyd` connects to `10.88.0.2:22`.
3. sshd authenticates a mounted public key and starts UID 11000 in the shared
   workspace.
4. Forwarding and tunnels are disabled. Web traffic follows the SSH-side Squid
   path.

### SSH/stdio MCP session

1. A local MCP client launches the configured `ssh` command.
2. sshd authenticates the operator key and runs `paddock-mcp-stdio` as UID
   11000.
3. The wrapper verifies the workspace mount before starting stdio MCP.
4. MCP messages and responses use the encrypted SSH stdin/stdout stream.

### Optional connector

OpenAI and Tailscale establish outbound control connections through Squid.
Tailscale Serve terminates private tailnet HTTPS in the API network namespace.
Cloudflare establishes outbound HTTP/2 directly to its tightly allowlisted edge
and forwards requests from an externally configured Access-protected hostname.
None of these profiles publishes a host or Docker port.

## Persistence and boot

The installer creates a sparse ext4 image at
`/var/lib/paddock/workspace.ext4`, mounts it at `/srv/paddock-workspace`, and
bind-mounts it into both compute containers as `/workspace`. Container startup
fails unless the mount is loop-backed ext4 with the expected capacity.

The nftables service is ordered before Docker, so restart policies cannot bring
the workloads up first during boot. The socket-proxy service cannot reach its
container until Docker starts behind that boundary. Docker container roots are
disposable; workspace data, SSH host keys, the operator public key, connector
credentials, and the allowed ingress hostnames live outside them.

## Why two compute containers?

The SSH daemon needs startup privileges that the MCP process does not. Keeping
them in separate PID/network namespaces means the MCP server receives no SSH
host key, operator key, tunnel key, bind capability, or root process. They
share only UID `11000`, the resource slice, and the intended workspace.
