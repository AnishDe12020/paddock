# Paddock

**Give ChatGPT room to run, not the whole farm.**

Paddock is a persistent Linux workbench that ChatGPT can use through the
[OpenAI Secure MCP Tunnel][secure-tunnel]. It provides files, Bash, SSH/SFTP,
and 100 GB of durable storage while keeping the MCP server private and fencing
the workload away from the host, private networks, and unbounded resources.

```text
ChatGPT
   |
   | OpenAI Secure MCP Tunnel (outbound HTTPS)
   v
+------------------------ Ubuntu host -------------------------+
|  tunnel-client --> private MCP API --> /workspace (100 GB)   |
|                         |                                    |
|  SSH :30222 ----------> | isolated containers, 4 CPU / 16 GB |
|                         v                                    |
|                    filtered web proxy --> public HTTP(S)     |
+--------------------------------------------------------------+
```

There is no public MCP port. The tunnel client calls out to OpenAI, the MCP
server listens only on an internal Docker bridge, and commands can reach the
web only through a denylisted HTTP/HTTPS proxy.

> [!WARNING]
> Paddock grants an AI arbitrary Bash access inside its workspace. It can
> overwrite files, delete data, download software, and send workspace data to
> public websites. Paddock limits the blast radius; it does not make an
> untrusted command harmless. Read the [threat model](docs/threat-model.md).

## What is in the paddock?

| Tool | What it does |
| --- | --- |
| `workspace_status` | Reports capacity and enforced compute limits |
| `list_workspace` | Lists bounded directory trees without following symlinked directories |
| `read_workspace_file` | Reads text or base64 in chunks |
| `write_workspace_file` | Creates or atomically replaces bounded files |
| `delete_workspace_path` | Deletes a file or an explicitly requested directory tree |
| `run_workspace_command` | Runs Bash with time, output, process, CPU, and memory limits |

The same workspace is available over key-only SSH/SFTP for human inspection,
large transfers, and recovery when a chat takes an unfortunate turn.

## Requirements

Paddock currently targets one specific, inspectable deployment:

- Ubuntu 24.04 with systemd
- Rootful Docker Engine and the Compose v2 plugin
- nftables, ext4 tools, util-linux, OpenSSH client tools, and `sudo`
- A readable Ed25519 or RSA public key, defaulting to
  `~/.ssh/id_ed25519.pub`
- Three unused Docker subnets: `10.88.0.0/24`, `10.89.0.0/24`, and
  `10.90.0.0/24`
- Enough disk for a sparse 100 GB workspace image as it fills
- For ChatGPT: Secure MCP Tunnel access and ChatGPT developer-mode apps

Docker Desktop, rootless Docker, non-systemd hosts, and hosts where UID `11000`
is already assigned are intentionally rejected rather than partially secured.

## Quick start

### 1. Install Paddock

Install Docker Engine using Docker's Ubuntu instructions, then install the host
tools:

```bash
sudo apt-get update
sudo apt-get install -y e2fsprogs nftables openssh-client util-linux
```

Clone and install:

```bash
git clone https://github.com/AnishDe12020/paddock.git
cd paddock
./scripts/install.sh --ssh-host paddock.example.com
```

Use `--ssh-key /path/to/key.pub` when your public key is elsewhere. The
installer:

1. checks the host and key before changing anything;
2. creates a sparse ext4 workspace at `/var/lib/paddock/workspace.ext4`;
3. installs the systemd slice, nftables boundary, and SSH socket;
4. builds and starts the containers; and
5. prints the SSH command when the stack is healthy.

The SSH listener is `30222/tcp`. The installer adds a rate-limited UFW rule
only when UFW is already active. With another host firewall, allow that port
yourself, preferably from trusted source addresses only.

### 2. Create the private tunnel

Create a tunnel in [OpenAI Platform tunnel settings][tunnels]. Associate it
with both the Platform organization and the ChatGPT workspace that should see
it.

Create a **restricted runtime API key** in [Runtime API keys][runtime-keys]
with only **Tunnels: Read + Use**. Do not use an admin key or an unrestricted
model API key for the daemon.

Activate it:

```bash
./scripts/activate-tunnel.sh tunnel_0123456789abcdef0123456789abcdef
```

The key prompt is hidden. The script installs it as a root-only file, starts
the pinned official `tunnel-client` image, and waits for an authenticated
control-plane handshake.

### 3. Add it to ChatGPT

Open [ChatGPT apps][chatgpt-apps], create a developer-mode app, and choose:

- **Connection:** Tunnel
- **Tunnel:** your Paddock tunnel
- **Authentication:** None / No authentication

The runtime API key authenticates the tunnel daemon. Paddock deliberately does
not implement user OAuth on the private MCP hop.

Try:

```text
Use Paddock. Show its workspace status, create hello.txt, run `uname -a`,
read the file back, and then tell me exactly what you changed.
```

## The fence posts

Paddock relies on several independent boundaries rather than one magic
"sandbox" switch:

- **Private MCP transport:** only `tunnel-client` and the host can reach the
  MCP bridge; there is no published container port or public reverse proxy.
- **Unprivileged tools:** MCP file and command operations run as UID `11000`
  with every Linux capability dropped and `no-new-privileges` enabled.
- **Read-only roots:** containers can write only to `/workspace` and bounded
  tmpfs mounts.
- **Filtered egress:** workloads have internal-only networks and can reach the
  internet only through Squid on ports 80/443. Private, loopback, link-local,
  metadata-like, and all IPv6 destinations are denied.
- **Host firewall:** nftables blocks sandbox-initiated host/sibling traffic and
  blocks the proxy from looping back into the host or private networks.
- **Resource boundary:** a systemd slice caps aggregate compute at 4 CPUs,
  16 GiB RAM, no swap, and 1024 tasks. Commands also have a 300-second maximum
  and bounded captured output.
- **Dedicated storage:** startup fails closed unless `/workspace` is the
  expected loop-backed ext4 filesystem.
- **Key-only SSH:** password login, root login, forwarding, tunneling, agent
  forwarding, and user startup hooks are disabled.

See [Architecture](docs/architecture.md) for the packet paths and
[Threat model](docs/threat-model.md) for what these controls do and do not
protect.

## Operations

```bash
# Service state
sudo docker compose ps

# Recent logs
sudo docker compose logs --tail=100

# Host boundaries
sudo systemctl status paddock-firewall.service paddock-ssh.socket
sudo nft list table inet paddock

# Run the live MCP protocol test from this checkout
MCP_URL=http://10.89.0.4:8000/mcp pytest tests/integration

# Redeploy after an update; data and keys are preserved
./scripts/install.sh --ssh-host paddock.example.com
```

Back up `/srv/paddock-workspace` like any other live filesystem. The sparse
image grows with use; "100 GB sparse" is a capacity limit, not a promise that
it consumes no host disk.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest tests/unit
docker compose config --quiet
```

The integration suite skips when no private MCP endpoint is reachable. See
[CONTRIBUTING.md](CONTRIBUTING.md) before changing a security boundary.

## Project status

Paddock is alpha software and intentionally narrow. It is a useful deployment
reference, not a multi-tenant service or a hardened malware-analysis VM.
Security reports are welcome through GitHub private vulnerability reporting;
see [SECURITY.md](SECURITY.md).

Paddock is licensed under the [MIT License](LICENSE). OpenAI's
`tunnel-client` is pulled as a separately licensed, digest-pinned container and
is not redistributed in this repository.

[chatgpt-apps]: https://chatgpt.com/plugins
[runtime-keys]: https://platform.openai.com/settings/organization/api-keys
[secure-tunnel]: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
[tunnels]: https://platform.openai.com/settings/organization/tunnels
