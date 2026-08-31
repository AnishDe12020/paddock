# Security Policy

## Supported versions

Paddock is pre-1.0 alpha software. Security fixes are applied to the latest
release and the `main` branch; older snapshots are not maintained.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** private reporting flow for this
repository. Do not open a public issue for a suspected escape, credential leak,
authentication bypass, network-policy bypass, or remotely triggerable denial
of service.

Include:

- the affected commit or version;
- the assumed host and Docker configuration;
- a minimal reproduction;
- the boundary you expected Paddock to enforce; and
- any suggested mitigation.

You should receive an acknowledgement within seven days. Please allow time for
a fix and coordinated disclosure before publishing exploit details.

## Security expectations

The model is authorized to execute arbitrary Bash, read and destroy workspace
data, and contact public HTTP/HTTPS services. Reports that rely only on those
documented capabilities are not security vulnerabilities. The boundaries we
intend to enforce are described in [docs/threat-model.md](docs/threat-model.md).
