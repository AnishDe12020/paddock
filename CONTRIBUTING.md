# Contributing

Paddock welcomes focused fixes, tests, documentation, and narrowly justified
features. Security boundaries are part of the product, not incidental Compose
configuration.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest tests/unit
shellcheck scripts/*.sh docker/*.sh
docker compose --profile openai-tunnel --profile tailscale --profile cloudflare config --quiet
sudo nft --check --file deploy/systemd/paddock-firewall.nft
docker build -f docker/box.Dockerfile -t paddock:local .
docker build -f docker/proxy.Dockerfile -t paddock-proxy:local .
```

Run `pytest tests/integration` only when a Paddock MCP endpoint is reachable.
Set `MCP_URL` to override its default private address.

## Change discipline

- Keep changes small and make policy changes obvious in review.
- Add tests for path, process, environment, or network invariants.
- Do not add broad host mounts, the Docker socket, privileged containers,
  wildcard egress, or secrets in environment variables.
- Keep the fixed IP topology synchronized across Compose, Squid, nftables,
  systemd, the tunnel profile, and transport security settings.
- Update the architecture and threat model when authority changes.
- Never commit runtime keys, private keys, workspace images, credentials, or
  generated tunnel configuration.

Security issues should follow [SECURITY.md](SECURITY.md), not the public issue
tracker.
