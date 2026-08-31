# Ingress Options

Paddock's tools are transport-neutral. The HTTP MCP server remains on the
private `api_sandbox` bridge, and optional connectors bring requests to it
without publishing a Docker or host port. SSH/stdio runs the same tools in the
SSH container instead.

Choose one or more ingress paths, but treat every identity allowed through any
of them as having full read, write, delete, and command authority in the
workspace.

## Compatibility

| Path | Typical clients | Public URL | Authentication authority |
| --- | --- | --- | --- |
| SSH/stdio | Desktop and IDE clients that can launch a command | No | Operator SSH key |
| OpenAI Secure Tunnel | ChatGPT developer-mode apps | OpenAI-hosted | OpenAI runtime key and workspace permissions |
| Tailscale Serve | Devices and clients on your tailnet | No | Tailscale identity plus grants or ACLs |
| Cloudflare Tunnel | Clients that support Access login or service-token headers | Yes | Cloudflare Access policy |
| External HTTPS/OAuth | Hosted remote-MCP clients | Usually | OAuth 2.1 authorization server |

## SSH/stdio

The install already exposes key-only SSH on TCP 30222. The
`paddock-mcp-stdio` remote command verifies the workspace mount, starts MCP as
UID 11000, and speaks MCP over stdin/stdout:

```json
{
  "mcpServers": {
    "paddock": {
      "command": "ssh",
      "args": [
        "-T",
        "-p",
        "30222",
        "ai@paddock.example.com",
        "paddock-mcp-stdio"
      ]
    }
  }
}
```

This is the smallest and most portable option for local clients. It opens no
new port, stores no new credential, and has the same authority as an
interactive `ai` shell. It does not help a hosted client that cannot execute
the local `ssh` command.

## OpenAI Secure Tunnel

Run `scripts/activate-tunnel.sh` as described in the README. The pinned OpenAI
client reaches its control plane through Squid and forwards only to the private
MCP API. The private HTTP hop deliberately has no OAuth because OpenAI's
runtime key and tunnel workspace permissions form the authorization boundary.

## Tailscale Serve

Requirements:

- HTTPS enabled for the tailnet
- A Tailscale OAuth client allowed to create `tag:paddock` devices, with the
  secret suffixed by `?ephemeral=false` for persistent registration
- A `tagOwners` entry allowing that OAuth client's tag workflow
- A Tailscale grant or ACL that limits TCP 443 on that node to intended users
  and devices

Activate it with the node's expected full MagicDNS name:

```bash
./scripts/activate-tailscale.sh paddock.your-tailnet.ts.net OAUTH_CLIENT_ID
```

The script stores the client ID and secret under `/var/lib/paddock/tailscale`
with mode 0400, adds the exact hostname to the API Host allowlist, and starts
the digest-pinned Tailscale image. The official container reads both credentials
through its `file:` interface. Tailscale runs without `NET_ADMIN` or
`/dev/net/tun`; userspace networking and Serve terminate tailnet HTTPS and proxy to
`http://127.0.0.1:8000/mcp` in the API network namespace.

Connect to:

```text
https://paddock.your-tailnet.ts.net/mcp
```

Tailscale may relay through DERP because Paddock does not grant direct UDP
egress. This costs performance but preserves the network boundary. Tailscale
identity controls who can reach the node; Paddock does not add per-user tool
permissions after that connection is accepted.

## Cloudflare Tunnel

This option creates a public edge URL and therefore needs an authentication
policy before the connector starts.

1. Create a remotely managed Cloudflare Tunnel and add a public hostname.
2. Set that hostname's service to `HTTP` at `10.89.0.4:8000`. Under additional
   application settings, set **HTTP Host Header** to `10.89.0.4` so the private
   origin's Host allowlist accepts the request.
3. Create a Cloudflare Access application for that hostname.
4. Add and test a restrictive Access policy. For non-browser MCP clients, use
   a service token only when the client can send the required Access headers.
5. Run:

```bash
./scripts/activate-cloudflare.sh --access-policy-ready
```

The confirmation flag cannot inspect the Cloudflare account; it exists to make
the security dependency explicit. The script stores the tunnel token under
`/var/lib/paddock/cloudflare` with mode 0400 and starts a digest-pinned
`cloudflared` image.

No host port opens. `cloudflared` can reach the private API at
`10.89.0.4:8000`. Its internet-side address may connect only over TCP 7844 to
the Cloudflare Tunnel edge IPv4 addresses documented when this release was
built. If Cloudflare changes that list, the connector fails closed until the
nftables policy is reviewed and updated.

Cloudflare Access is not MCP OAuth. Hosted clients that cannot complete Access
login or send service-token headers will not work with this option.

## External HTTPS and OAuth

Do not put a generic reverse proxy in front of Paddock's current HTTP endpoint
and expose it publicly. That endpoint intentionally trusts its private
transport and has no bearer-token verifier.

A broadly interoperable public deployment needs an external TLS gateway and an
OAuth 2.1 authorization server. Under the current MCP authorization
specification, the protected resource must:

- publish RFC 9728 Protected Resource Metadata;
- advertise an OAuth or OpenID Connect authorization server;
- use the MCP resource URL in authorization requests;
- validate token issuer, audience/resource, expiry, and required scopes; and
- keep the unauthenticated Paddock origin reachable only by the trusted
  gateway.

The MCP Python SDK exposes `AuthSettings` and `TokenVerifier` for this design,
but Paddock does not bundle an authorization server or choose an identity
provider. That integration is intentionally left operator-specific rather than
shipping a static token or incomplete OAuth facade as a universal profile.

See the [MCP authorization specification][mcp-auth],
[Tailscale Docker parameters][tailscale-docker], and
[Cloudflare Tunnel firewall requirements][cloudflare-firewall].

[cloudflare-firewall]: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/
[mcp-auth]: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
[tailscale-docker]: https://tailscale.com/docs/features/containers/docker/docker-params
